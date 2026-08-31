"""DoIP (Diagnosis over IP, ISO 13400-2) 传输层实现

实现基于 TCP 的 DoIP 诊断通信：
- 报文格式: 协议版本(1B) + 逆版本(1B) + Payload类型(2B) + 长度(4B) + Payload
- 路由激活 (0x0005/0x0006): 建立TCP连接后激活诊断路由
- 诊断消息 (0x8001/0x8002/0x8003): 承载UDS报文，无CAN TP分段限制
- 车辆发现 (0x0001/0x0004): UDP广播识别ECU IP与逻辑地址

DoipTransportLayer 与 CAN 的 TransportLayer 保持同名接口
(send_tp/receive_tp)，使 UdsClient 可无缝切换底层传输；
同时实现 add_message_listener 等接口，主窗口的报文监听、
Trace视图与日志面板可直接复用。

VirtualDoipEcu 为内置的回环模拟ECU（复用 VirtualEcuSimulator），
用于无硬件环境下的开发与测试。
"""

import socket
import struct
import threading
import time
from typing import Optional, Callable
from src.models.can_message import CanMessage, CanDirection
from src.log.log_manager import get_log_manager


class DoipPayloadType:
    """DoIP Payload 类型 (ISO 13400-2 Table 21/22)"""
    VEHICLE_IDENT_REQ = 0x0001          # 车辆识别请求
    VEHICLE_IDENT_RESP = 0x0004         # 车辆识别响应
    ROUTING_ACTIVATION_REQ = 0x0005     # 路由激活请求
    ROUTING_ACTIVATION_RESP = 0x0006    # 路由激活响应
    ALIVE_CHECK_REQ = 0x0007            # 存活检查请求(ECU->Tester)
    ALIVE_CHECK_RESP = 0x0008           # 存活检查响应
    DIAG_MESSAGE = 0x8001               # 诊断消息
    DIAG_MESSAGE_POS_ACK = 0x8002       # 诊断消息正确认
    DIAG_MESSAGE_NEG_ACK = 0x8003       # 诊断消息否定确认


class DoipRoutingCode:
    """路由激活响应码"""
    SUCCESS = 0x10
    UNKNOWN_SOURCE = 0x00
    NO_FREE_SOCKET = 0x01
    WRONG_SOURCE = 0x02
    AUTHENTICATION_MISSING = 0x04


# 协议版本 0x02 = ISO 13400-2:2012（业界部署最广）
_DOIP_VERSION = 0x02
_HEADER_FMT = "!BBHI"   # version, inverse, payload type, length
_HEADER_SIZE = 8


def _build_doip_frame(payload_type: int, payload: bytes) -> bytes:
    """构造完整DoIP帧（头 + payload）"""
    header = struct.pack(_HEADER_FMT, _DOIP_VERSION, 0xFF ^ _DOIP_VERSION,
                         payload_type, len(payload))
    return header + payload


class DoipTransportLayer:
    """DoIP传输层（TCP客户端）

    与 CAN TransportLayer 同构的 send_tp/receive_tp 接口，上层 UdsClient
    无需感知底层是 CAN 还是 Ethernet。

    依赖的CanInterfaceBase兼容接口: connect/disconnect/send/is_connected/
    interface_name/channel_info/add_message_listener/remove_message_listener。
    """

    def __init__(self, target_ip: str = "127.0.0.1", tcp_port: int = 13400,
                 tester_address: int = 0x0E80, ecu_address: int = 0x1000,
                 timeout: float = 2.0):
        """
        Args:
            target_ip: ECU的IP地址
            tcp_port: DoIP TCP端口（标准13400）
            tester_address: Tester逻辑地址
            ecu_address: 目标ECU逻辑地址（路由激活成功后会被实际值覆盖）
            timeout: 路由激活/通信超时（秒）
        """
        self._ip = target_ip
        self._port = int(tcp_port)
        self._tester_address = tester_address & 0xFFFF
        self._ecu_address = ecu_address & 0xFFFF
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._connected = False
        self._last_error: Optional[str] = None
        self._rx_buffer = bytearray()
        self._lock = threading.Lock()
        self._message_listeners: list[Callable[[str, CanMessage], None]] = []
        self._logger = get_log_manager().get_comm_logger()

    # ---------------- 属性 ----------------

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def interface_name(self) -> str:
        return "DoIP"

    @property
    def channel_info(self) -> str:
        return f"DoIP {self._ip}:{self._port} (0x{self._ecu_address:04X})"

    @property
    def ecu_address(self) -> int:
        """ECU逻辑地址（路由激活后为实际协商值）"""
        return self._ecu_address

    @property
    def tester_address(self) -> int:
        return self._tester_address

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    # ---------------- 连接管理 ----------------

    def connect(self, config: dict = None) -> bool:
        """建立TCP连接并完成路由激活

        Args:
            config: 可选配置（覆盖构造参数: target_ip/tcp_port/
                tester_address/ecu_address）

        Returns:
            连接且路由激活成功返回True
        """
        config = config or {}
        self._ip = str(config.get("target_ip", self._ip))
        self._port = int(config.get("tcp_port", self._port))
        self._tester_address = int(config.get("tester_address",
                                              self._tester_address)) & 0xFFFF
        self._ecu_address = int(config.get("ecu_address",
                                           self._ecu_address)) & 0xFFFF

        self._last_error = None
        try:
            self.disconnect()
            sock = socket.create_connection((self._ip, self._port),
                                            timeout=self._timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock = sock
        except OSError as e:
            self._last_error = f"TCP连接失败: {e}"
            self._logger.error(self._last_error)
            return False

        # 路由激活: 源地址(2) + 激活类型(1) + 保留(4)
        payload = struct.pack("!HBBB",
                              self._tester_address, 0x00, 0x00, 0x00)[:2] \
            + bytes([0x00]) + bytes(4)
        payload = struct.pack("!H", self._tester_address) + bytes([0x00]) + bytes(4)
        if not self._send_frame(DoipPayloadType.ROUTING_ACTIVATION_REQ, payload):
            self._last_error = "路由激活请求发送失败"
            self.disconnect()
            return False

        # 等待路由激活响应
        frame = self._recv_frame(self._timeout)
        if frame is None:
            self._last_error = "路由激活响应超时"
            self.disconnect()
            return False

        ptype, payload = frame
        if ptype != DoipPayloadType.ROUTING_ACTIVATION_RESP:
            self._last_error = f"意外的路由激活响应类型: 0x{ptype:04X}"
            self.disconnect()
            return False

        if len(payload) < 5:
            self._last_error = "路由激活响应长度错误"
            self.disconnect()
            return False

        # 响应: tester地址(2) + ECU地址(2) + 响应码(1) + 保留
        code = payload[4]
        if code != DoipRoutingCode.SUCCESS:
            self._last_error = f"路由激活被拒绝 (code=0x{code:02X})"
            self._logger.error(self._last_error)
            self.disconnect()
            return False

        # 采用ECU返回的实际地址
        self._ecu_address = struct.unpack("!H", payload[2:4])[0]
        self._connected = True
        self._logger.info(
            f"DoIP已连接 {self._ip}:{self._port}, "
            f"Tester=0x{self._tester_address:04X}, ECU=0x{self._ecu_address:04X}")
        return True

    def disconnect(self) -> None:
        """断开TCP连接"""
        self._connected = False
        self._rx_buffer.clear()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    # ---------------- UDS数据收发（与TransportLayer同构） ----------------

    def send_tp(self, data: bytes, can_id: int = None) -> bool:
        """发送UDS数据（包装为DoIP诊断消息0x8001）

        Args:
            data: UDS请求数据
            can_id: 忽略（兼容TransportLayer接口签名）

        Returns:
            发送成功返回True
        """
        if not self._connected or self._sock is None:
            self._last_error = "DoIP未连接"
            return False

        # 诊断消息: 源地址(2) + 目标地址(2) + UDS数据
        payload = (struct.pack("!H", self._tester_address)
                   + struct.pack("!H", self._ecu_address) + data)
        if not self._send_frame(DoipPayloadType.DIAG_MESSAGE, payload):
            return False

        self._notify_message("TX", data)
        return True

    def receive_tp(self, timeout: float = None) -> Optional[bytes]:
        """接收UDS数据（解包DoIP诊断正确认0x8002）

        Args:
            timeout: 超时时间（秒）

        Returns:
            完整UDS响应数据，超时返回None
        """
        if not self._connected or self._sock is None:
            return None
        if timeout is None:
            timeout = self._timeout

        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            frame = self._recv_frame(remaining)
            if frame is None:
                return None

            ptype, payload = frame
            if ptype == DoipPayloadType.DIAG_MESSAGE_POS_ACK:
                if len(payload) < 4:
                    continue
                uds_data = payload[4:]
                if uds_data:
                    self._notify_message("RX", uds_data)
                return uds_data
            elif ptype == DoipPayloadType.DIAG_MESSAGE_NEG_ACK:
                if len(payload) >= 5:
                    self._last_error = (f"DoIP否定确认 (code=0x{payload[4]:02X})")
                    self._logger.warning(self._last_error)
                return None
            elif ptype == DoipPayloadType.ALIVE_CHECK_REQ:
                # 存活检查: 立即回复以保持路由
                self._send_frame(DoipPayloadType.ALIVE_CHECK_RESP,
                                 struct.pack("!H", self._tester_address))
                continue
            # 其他类型（如0x8001回环）忽略继续等待

    def send(self, msg: CanMessage) -> bool:
        """兼容CanInterfaceBase.send：将报文data作为UDS请求发送（不等响应）"""
        return self.send_tp(msg.data)

    # ---------------- 报文监听（兼容CanInterfaceBase） ----------------

    def add_message_listener(self, callback: Callable[[str, CanMessage], None]):
        """注册报文监听器（TX/RX时回调，线程安全由监听方保证）"""
        if callback not in self._message_listeners:
            self._message_listeners.append(callback)

    def remove_message_listener(self, callback: Callable[[str, CanMessage], None]):
        if callback in self._message_listeners:
            self._message_listeners.remove(callback)

    def _notify_message(self, direction: str, uds_data: bytes):
        """将UDS数据封装为CanMessage通知监听器（can_id用对端逻辑地址）"""
        addr = self._ecu_address if direction == "TX" else self._tester_address
        msg = CanMessage(can_id=addr, data=uds_data,
                         direction=(CanDirection.TX if direction == "TX"
                                    else CanDirection.RX),
                         timestamp=time.time())
        for cb in self._message_listeners:
            try:
                cb(direction, msg)
            except Exception:
                pass

    # ---------------- 帧级收发 ----------------

    def _send_frame(self, payload_type: int, payload: bytes) -> bool:
        """发送DoIP帧"""
        with self._lock:
            if self._sock is None:
                return False
            try:
                self._sock.sendall(_build_doip_frame(payload_type, payload))
                return True
            except OSError as e:
                self._last_error = f"DoIP发送失败: {e}"
                self._logger.error(self._last_error)
                return False

    def _recv_frame(self, timeout: float) -> Optional[tuple]:
        """接收一个完整DoIP帧

        Returns:
            (payload_type, payload) 元组，超时/断开返回None
        """
        # 头部
        header = self._recv_exact(_HEADER_SIZE, timeout)
        if header is None:
            return None
        version, inverse, ptype, length = struct.unpack(_HEADER_FMT, header)
        if version != _DOIP_VERSION or inverse != (0xFF ^ _DOIP_VERSION):
            self._logger.warning(f"DoIP版本不匹配: 0x{version:02X}")
            return None

        if length > 4 * 1024 * 1024:
            self._logger.error(f"DoIP帧长度异常: {length}")
            return None

        payload = self._recv_exact(length, timeout) if length else b""
        if payload is None:
            return None
        return ptype, payload

    def _recv_exact(self, n: int, timeout: float) -> Optional[bytes]:
        """从TCP流中精确读取n字节（处理分片返回）"""
        sock = self._sock
        if sock is None or n == 0:
            return b""
        buf = bytearray()
        deadline = time.time() + timeout
        try:
            while len(buf) < n:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                sock.settimeout(remaining)
                chunk = sock.recv(n - len(buf))
                if not chunk:  # 对端关闭
                    self._connected = False
                    self._last_error = "DoIP连接被对端关闭"
                    return None
                buf.extend(chunk)
        except socket.timeout:
            return None
        except OSError as e:
            self._last_error = f"DoIP接收失败: {e}"
            self._logger.error(self._last_error)
            return None
        return bytes(buf)

    # ---------------- 车辆发现（UDP） ----------------

    @staticmethod
    def discover(timeout: float = 1.0, broadcast: str = "255.255.255.255",
                 udp_port: int = 13400) -> list:
        """UDP广播车辆识别请求，发现局域网内的DoIP节点

        Args:
            timeout: 等待响应的总时长（秒）
            broadcast: 广播地址

        Returns:
            节点列表 [{'ip': ..., 'port': ..., 'vin': ..., 'logical_addr': ...}]
        """
        nodes = []
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)
        try:
            sock.sendto(_build_doip_frame(DoipPayloadType.VEHICLE_IDENT_REQ,
                                          b""), (broadcast, udp_port))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                if len(data) < _HEADER_SIZE:
                    continue
                _, _, ptype, length = struct.unpack(_HEADER_FMT, data[:_HEADER_SIZE])
                payload = data[_HEADER_SIZE:_HEADER_SIZE + length]
                if ptype != DoipPayloadType.VEHICLE_IDENT_RESP:
                    continue
                # VIN响应: VIN(17) + 逻辑地址(2) + ...
                node = {"ip": addr[0], "port": addr[1], "vin": "", "logical_addr": 0}
                if len(payload) >= 19:
                    node["vin"] = payload[:17].decode("ascii", errors="replace")
                    node["logical_addr"] = struct.unpack("!H", payload[17:19])[0]
                nodes.append(node)
        finally:
            sock.close()
        return nodes


class VirtualDoipEcu:
    """虚拟DoIP ECU（回环模拟）

    在本地TCP端口模拟DoIP ECU：处理路由激活与诊断消息，
    UDS请求交由 VirtualEcuSimulator 处理后回送。
    用于无硬件环境下的开发、学习与测试。
    """

    def __init__(self, logical_address: int = 0x1000,
                 host: str = "127.0.0.1"):
        from src.can_layer.virtual_interface import VirtualEcuSimulator
        self._logical_address = logical_address & 0xFFFF
        self._host = host
        self._ecu = VirtualEcuSimulator()
        self._server: Optional[socket.socket] = None
        self._udp_server: Optional[socket.socket] = None
        self._conn: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._udp_thread: Optional[threading.Thread] = None
        self._running = False
        self._port = 0
        self._udp_port = 0
        self._tester_address = 0
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        """实际监听TCP端口（start()后有效）"""
        return self._port

    @property
    def udp_port(self) -> int:
        """实际监听UDP端口（start()后有效）"""
        return self._udp_port

    @property
    def ecu_simulator(self):
        """底层UDS模拟器（用于外部配置DID/DTC等）"""
        return self._ecu

    def start(self) -> tuple:
        """启动模拟ECU（TCP诊断 + UDP车辆发现）

        Returns:
            (host, tcp_port) 监听地址
        """
        if self._running:
            return self._host, self._port
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self._host, 0))
        self._server.listen(1)
        self._server.settimeout(0.2)
        self._port = self._server.getsockname()[1]

        # UDP车辆识别响应
        self._udp_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_server.bind((self._host, 0))
        self._udp_server.settimeout(0.2)
        self._udp_port = self._udp_server.getsockname()[1]

        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self._udp_thread = threading.Thread(target=self._serve_udp, daemon=True)
        self._udp_thread.start()
        return self._host, self._port

    def stop(self) -> None:
        """停止模拟ECU并释放资源"""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._udp_thread is not None:
            self._udp_thread.join(timeout=1.0)
            self._udp_thread = None
        for sock in (self._conn, self._server, self._udp_server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._conn = None
        self._server = None
        self._udp_server = None

    def _serve(self):
        """接受连接并处理（单连接模型，断开后继续等待新连接）"""
        while self._running:
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                self._conn = conn
            try:
                self._handle_connection(conn)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                with self._lock:
                    if self._conn is conn:
                        self._conn = None

    def _serve_udp(self):
        """处理UDP车辆识别请求（0x0001 -> 0x0004）"""
        while self._running:
            try:
                data, addr = self._udp_server.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < _HEADER_SIZE:
                continue
            _, _, ptype, _ = struct.unpack(_HEADER_FMT, data[:_HEADER_SIZE])
            if ptype != DoipPayloadType.VEHICLE_IDENT_REQ:
                continue
            # 响应: VIN(17) + 逻辑地址(2)
            payload = b"VIRTUALDOIP-ECU01" + struct.pack(
                "!H", self._logical_address)
            try:
                self._udp_server.sendto(
                    _build_doip_frame(DoipPayloadType.VEHICLE_IDENT_RESP,
                                      payload), addr)
            except OSError:
                break

    def _handle_connection(self, conn: socket.socket):
        """处理单个TCP连接：读DoIP帧并响应"""
        conn.settimeout(None)
        while self._running:
            frame = self._read_frame(conn)
            if frame is None:
                return
            ptype, payload = frame
            if ptype == DoipPayloadType.ROUTING_ACTIVATION_REQ:
                self._handle_routing_activation(conn, payload)
            elif ptype == DoipPayloadType.DIAG_MESSAGE:
                self._handle_diag_message(conn, payload)
            elif ptype == DoipPayloadType.ALIVE_CHECK_RESP:
                continue
            else:
                # 通用否定响应: 源(2) + 目标(2) + 码0x03(不支持payload类型)
                conn.sendall(_build_doip_frame(
                    DoipPayloadType.DIAG_MESSAGE_NEG_ACK,
                    struct.pack("!HH", 0x0000, 0x0000) + bytes([0x03])))

    def _handle_routing_activation(self, conn: socket.socket, payload: bytes):
        """处理路由激活请求: 记录Tester地址并返回成功"""
        if len(payload) < 2:
            return
        self._tester_address = struct.unpack("!H", payload[:2])[0]
        # 响应: tester地址(2) + ECU地址(2) + 成功码(1) + 保留(4)
        resp = (struct.pack("!H", self._tester_address)
                + struct.pack("!H", self._logical_address)
                + bytes([DoipRoutingCode.SUCCESS]) + bytes(4))
        conn.sendall(_build_doip_frame(
            DoipPayloadType.ROUTING_ACTIVATION_RESP, resp))

    def _handle_diag_message(self, conn: socket.socket, payload: bytes):
        """处理诊断消息: 调UDS模拟器并回送正确认"""
        if len(payload) < 4:
            return
        uds_request = payload[4:]
        uds_response = self._ecu.process_request(uds_request)
        # 正确认: ECU地址(2) + Tester地址(2) + UDS响应
        resp = (struct.pack("!H", self._logical_address)
                + struct.pack("!H", self._tester_address)
                + uds_response)
        conn.sendall(_build_doip_frame(
            DoipPayloadType.DIAG_MESSAGE_POS_ACK, resp))

    @staticmethod
    def _read_frame(conn: socket.socket) -> Optional[tuple]:
        """从连接读取一个完整DoIP帧（阻塞）"""
        header = b""
        while len(header) < _HEADER_SIZE:
            chunk = conn.recv(_HEADER_SIZE - len(header))
            if not chunk:
                return None
            header += chunk
        _, _, ptype, length = struct.unpack(_HEADER_FMT, header)
        payload = b""
        while len(payload) < length:
            chunk = conn.recv(length - len(payload))
            if not chunk:
                return None
            payload += chunk
        return ptype, payload
