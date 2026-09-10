"""CAN硬件抽象层 - 虚拟CAN接口（模拟ECU响应）"""

import time
import threading
import queue
import random
from typing import Optional
from src.can_layer.can_interface import CanInterfaceBase
from src.models.can_message import CanMessage, CanMessageType, CanDirection


class VirtualEcuSimulator:
    """虚拟ECU模拟器
    
    模拟ECU对UDS请求的响应，用于无硬件环境下的测试和开发。
    """

    def __init__(self):
        self._session = 0x01  # 默认会话
        self._security_unlocked = False
        self._security_level = 0
        self._dtc_settings_enabled = True
        self._did_data = self._init_did_data()
        self._dtc_list = self._init_dtc_list()
        self._flash_memory = {}  # 模拟刷写内存

    def _init_did_data(self) -> dict:
        """初始化预定义DID数据"""
        return {
            0xF190: b"WF0ABCD1234567890",  # VIN
            0xF191: b"SW_V2.1.3_20260101",  # ECU软件版本
            0xF192: b"HW_V1.0.0",           # ECU硬件版本
            0xF193: b"DiagTools_ECU_001",   # ECU名称
            0xF195: b"20260101",            # 刷写日期
            0xF187: b"VBF_001_002_003",    # 零件号
            0x0100: b"\x03\xE8",            # 发动机转速 1000rpm (0.25 scaling)
            0x0101: b"\x00\x5A",            # 车速 90 km/h
            0x0102: b"\x5A",                # 水温 90°C (offset -40)
            0x0103: b"\x04\xB0",            # 电池电压 12.0V (0.01 scaling)
        }

    def _init_dtc_list(self) -> list:
        """初始化模拟DTC列表"""
        return [
            {"dtc_id": 0xC00100, "status": 0x09, "occurrence": 3},   # 已确认+测试失败
            {"dtc_id": 0xC00200, "status": 0x08, "occurrence": 1},   # 已确认
            {"dtc_id": 0xC00300, "status": 0x04, "occurrence": 5},   # 待确认
            {"dtc_id": 0xC00400, "status": 0x2C, "occurrence": 12},  # 历史(已修复)
        ]

    def process_request(self, data: bytes) -> bytes:
        """处理UDS请求并返回响应"""
        if not data:
            return bytes([0x7F, 0x00, 0x13])  # incorrectMessageLength

        sid = data[0]

        # 服务分发
        handlers = {
            0x10: self._handle_diagnostic_session,
            0x11: self._handle_ecu_reset,
            0x14: self._handle_clear_dtc,
            0x19: self._handle_read_dtc,
            0x22: self._handle_read_did,
            0x27: self._handle_security_access,
            0x28: self._handle_communication_control,
            0x2E: self._handle_write_did,
            0x2F: self._handle_io_control,
            0x31: self._handle_routine_control,
            0x34: self._handle_request_download,
            0x36: self._handle_transfer_data,
            0x37: self._handle_request_transfer_exit,
            0x3E: self._handle_tester_present,
            0x85: self._handle_control_dtc_setting,
        }

        handler = handlers.get(sid)
        if handler:
            return handler(data)
        else:
            return bytes([0x7F, sid, 0x11])  # serviceNotSupported

    def _handle_diagnostic_session(self, data: bytes) -> bytes:
        sub_func = data[1] if len(data) > 1 else 0
        self._session = sub_func
        return bytes([0x50, sub_func, 0x00, 0x32, 0x01, 0xF4])

    def _handle_ecu_reset(self, data: bytes) -> bytes:
        sub_func = data[1] if len(data) > 1 else 0x01
        if sub_func == 0x01:
            self._session = 0x01
            self._security_unlocked = False
        return bytes([0x51, sub_func])

    def _handle_clear_dtc(self, data: bytes) -> bytes:
        for dtc in self._dtc_list:
            dtc["status"] = 0x00
            dtc["occurrence"] = 0
        return bytes([0x54])

    def _handle_read_dtc(self, data: bytes) -> bytes:
        sub_func = data[1] if len(data) > 1 else 0x02

        if sub_func == 0x01:
            # 读取DTC数量
            count = sum(1 for d in self._dtc_list if d["status"] & 0x08)
            return bytes([0x59, 0x01, 0x00, count & 0xFF])

        elif sub_func == 0x02:
            # 按状态掩码读取DTC
            mask = data[2] if len(data) > 2 else 0xFF
            response = bytes([0x59, 0x02, 0xFF])
            for dtc in self._dtc_list:
                if dtc["status"] & mask:
                    dtc_id = dtc["dtc_id"]
                    response += bytes([
                        (dtc_id >> 16) & 0xFF,
                        (dtc_id >> 8) & 0xFF,
                        dtc_id & 0xFF,
                        dtc["status"],
                    ])
            return response

        elif sub_func == 0x04:
            # 读取快照数据
            return bytes([0x59, 0x04, 0x01, 0x00, 0x01, 0x00, 0x5A, 0x5A])

        elif sub_func == 0x06:
            # 读取扩展数据
            return bytes([0x59, 0x06, 0x01]) + bytes(10)

        elif sub_func == 0x0A:
            # 读取所有DTC
            response = bytes([0x59, 0x0A, 0xFF])
            for dtc in self._dtc_list:
                dtc_id = dtc["dtc_id"]
                response += bytes([
                    (dtc_id >> 16) & 0xFF,
                    (dtc_id >> 8) & 0xFF,
                    dtc_id & 0xFF,
                    dtc["status"],
                ])
            return response

        return bytes([0x7F, 0x19, 0x12])  # subFunctionNotSupported

    def _handle_read_did(self, data: bytes) -> bytes:
        if len(data) < 3:
            return bytes([0x7F, 0x22, 0x13])
        did_id = (data[1] << 8) | data[2]
        if did_id in self._did_data:
            did_data = self._did_data[did_id]
            return bytes([0x62, data[1], data[2]]) + did_data
        return bytes([0x7F, 0x22, 0x31])  # requestOutOfRange

    def _handle_security_access(self, data: bytes) -> bytes:
        sub_func = data[1] if len(data) > 1 else 0
        if sub_func % 2 == 1:
            # 请求种子 (奇数子功能)
            seed = bytes([random.randint(0, 255), random.randint(0, 255)])
            self._pending_seed = seed
            # 正响应: 0x67 (0x27+0x40) + sub_func + seed
            return bytes([0x67, sub_func]) + seed
        else:
            # 发送密钥 (偶数子功能)
            key_data = data[2:] if len(data) > 2 else b""
            # 简单验证: 种子按位异或 0xA55A
            if hasattr(self, '_pending_seed') and self._pending_seed:
                seed_val = int.from_bytes(self._pending_seed, 'big')
                expected_key = (seed_val ^ 0xA55A).to_bytes(len(self._pending_seed), 'big')
                if key_data == expected_key:
                    self._security_unlocked = True
                    self._security_level = sub_func
                    # 正响应: 0x67 (0x27+0x40) + sub_func
                    return bytes([0x67, sub_func])
                else:
                    return bytes([0x7F, 0x27, 0x35])  # invalidKey
            return bytes([0x7F, 0x27, 0x35])

    def _handle_communication_control(self, data: bytes) -> bytes:
        """通信控制 (0x28): 刷写刷前禁止/刷后恢复非诊断报文收发"""
        sub_func = data[1] if len(data) > 1 else 0x01
        return bytes([0x68, sub_func])

    def _handle_write_did(self, data: bytes) -> bytes:
        if len(data) < 3:
            return bytes([0x7F, 0x2E, 0x13])
        did_id = (data[1] << 8) | data[2]
        did_data = data[3:] if len(data) > 3 else b""
        self._did_data[did_id] = did_data
        return bytes([0x6E, data[1], data[2]])

    def _handle_io_control(self, data: bytes) -> bytes:
        if len(data) < 3:
            return bytes([0x7F, 0x2F, 0x13])
        return bytes([0x6F, data[1], data[2], 0x00])

    def _handle_routine_control(self, data: bytes) -> bytes:
        sub_func = data[1] if len(data) > 1 else 0x01
        if len(data) >= 4:
            response = bytes([0x71, sub_func, data[2], data[3]])
            if sub_func == 0x03:
                response += b"\x00\x00"  # 模拟例程结果
            return response
        return bytes([0x7F, 0x31, 0x13])

    def _handle_request_download(self, data: bytes) -> bytes:
        # lengthFormatIdentifier=0x20（maxBlockLength占2字节），
        # maxNumberOfBlockLength=0x1000
        return bytes([0x74, 0x20, 0x10, 0x00])

    def _handle_transfer_data(self, data: bytes) -> bytes:
        block_seq = data[1] if len(data) > 1 else 0
        return bytes([0x76, block_seq])

    def _handle_request_transfer_exit(self, data: bytes) -> bytes:
        return bytes([0x77])

    def _handle_tester_present(self, data: bytes) -> bytes:
        sub_func = data[1] if len(data) > 1 else 0x00
        if sub_func & 0x80:
            # 抑制正响应位: ECU不应回复（回空即不发帧）
            return b""
        return bytes([0x7E, sub_func & 0x7F])

    def _handle_control_dtc_setting(self, data: bytes) -> bytes:
        sub_func = data[1] if len(data) > 1 else 0x01
        self._dtc_settings_enabled = (sub_func == 0x01)
        return bytes([0xC5, sub_func])


class VirtualCanInterface(CanInterfaceBase):
    """虚拟CAN接口
    
    使用内存队列模拟CAN通信，内置虚拟ECU模拟器。
    适用于无硬件环境下的开发、学习和测试。
    """

    def __init__(self):
        super().__init__()
        self._connected = False
        self._channel = ""
        self._bitrate = 500000
        self._tx_queue = queue.Queue()
        self._rx_queue = queue.Queue()
        self._ecu_simulator = VirtualEcuSimulator()
        self._running = False
        self._lock = threading.Lock()
        self._tx_count = 0
        self._rx_count = 0
        self._req_id = 0x7E0   # 默认请求地址
        self._resp_id = 0x7E8  # 默认响应地址
        # 多帧请求接收重组状态（写入DID等>7字节请求）
        self._req_buf = None       # bytearray，None表示当前无进行中的多帧接收
        self._req_total = 0
        self._req_seq = 1

    def connect(self, config: dict) -> bool:
        with self._lock:
            if self._connected:
                return True
            self._channel = config.get("channel", "Virtual_0")
            self._bitrate = config.get("bitrate", 500000)
            self._req_id = config.get("req_id", 0x7E0)
            self._resp_id = config.get("resp_id", 0x7E8)
            self._connected = True
            self._running = True
            self._tx_count = 0
            self._rx_count = 0
            self._req_buf = None
            self._req_total = 0
            self._req_seq = 1
            return True

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            self._running = False
            # 清空队列
            while not self._tx_queue.empty():
                try:
                    self._tx_queue.get_nowait()
                except queue.Empty:
                    break
            while not self._rx_queue.empty():
                try:
                    self._rx_queue.get_nowait()
                except queue.Empty:
                    break

    def send(self, msg: CanMessage) -> bool:
        if not self._connected:
            return False

        msg.direction = CanDirection.TX
        msg.timestamp = time.time()
        self._tx_count += 1
        self._notify_message("TX", msg)

        # 如果是发送到请求地址或功能寻址地址(0x7DF)，模拟ECU响应
        # （功能寻址是刷写刷前准备/刷后恢复步骤的发送方式）
        if msg.can_id in (self._req_id, 0x7DF) and msg.data:
            frame_type = msg.data[0] & 0xF0
            if frame_type == 0x30:
                # 流控帧：TP层协议帧，不是UDS请求
                return True
            if frame_type == 0x10:
                # 首帧: 开始多帧接收，回流控帧(CTS, BS=0不限, STmin=0)
                self._req_total = ((msg.data[0] & 0x0F) << 8) | msg.data[1]
                self._req_buf = bytearray(msg.data[2:8])
                self._req_seq = 1
                fc = CanMessage(
                    can_id=self._resp_id,
                    data=bytes([0x30, 0x00, 0x00, 0, 0, 0, 0, 0]),
                    dlc=8, msg_type=CanMessageType.STANDARD,
                    direction=CanDirection.RX, timestamp=time.time(), channel=0)
                self._rx_queue.put(fc)
                return True
            if frame_type == 0x20:
                # 连续帧: 按序重组，收满后交模拟器处理
                seq = msg.data[0] & 0x0F
                if (self._req_buf is not None
                        and seq == (self._req_seq & 0x0F)):
                    remaining = self._req_total - len(self._req_buf)
                    self._req_buf.extend(msg.data[1:1 + min(7, remaining)])
                    self._req_seq = (self._req_seq + 1) & 0x0F
                    if len(self._req_buf) >= self._req_total:
                        uds_data = bytes(self._req_buf[:self._req_total])
                        self._req_buf = None
                        response_data = self._ecu_simulator.process_request(
                            uds_data)
                        if response_data:
                            self._queue_tp_response(response_data)
                return True
            # 单帧: 直接提取UDS数据处理
            uds_data = self._extract_uds_from_tp(msg.data)
            if uds_data:
                response_data = self._ecu_simulator.process_request(uds_data)
                if response_data:
                    self._queue_tp_response(response_data)

        return True

    def _queue_tp_response(self, uds_response: bytes):
        """将UDS响应包装为TP帧并放入接收队列"""
        if len(uds_response) <= 7:
            # 单帧响应
            tp_frame = self._wrap_tp_single_frame(uds_response)
            resp_msg = CanMessage(
                can_id=self._resp_id,
                data=tp_frame,
                dlc=len(tp_frame),
                msg_type=CanMessageType.STANDARD,
                direction=CanDirection.RX,
                timestamp=time.time(),
                channel=0,
            )
            self._rx_queue.put(resp_msg)
            self._rx_count += 1
        else:
            # 多帧响应：发送首帧(FF) + 所有连续帧(CF)
            # 首帧 (FF)
            total_length = len(uds_response)
            ff_pci = 0x10 | ((total_length >> 8) & 0x0F)
            ff_data = bytes([ff_pci, total_length & 0xFF]) + uds_response[:6]
            ff_data = ff_data + bytes(8 - len(ff_data))
            ff_msg = CanMessage(
                can_id=self._resp_id,
                data=ff_data,
                dlc=8,
                msg_type=CanMessageType.STANDARD,
                direction=CanDirection.RX,
                timestamp=time.time(),
                channel=0,
            )
            self._rx_queue.put(ff_msg)
            self._rx_count += 1

            # 连续帧 (CF) - 全部预入队列
            offset = 6
            seq = 1
            while offset < total_length:
                chunk_size = min(7, total_length - offset)
                cf_pci = 0x20 | (seq & 0x0F)
                cf_data = bytes([cf_pci]) + uds_response[offset:offset + chunk_size]
                cf_data = cf_data + bytes(8 - len(cf_data))
                cf_msg = CanMessage(
                    can_id=self._resp_id,
                    data=cf_data,
                    dlc=8,
                    msg_type=CanMessageType.STANDARD,
                    direction=CanDirection.RX,
                    timestamp=time.time(),
                    channel=0,
                )
                self._rx_queue.put(cf_msg)
                self._rx_count += 1
                offset += chunk_size
                seq = (seq + 1) & 0x0F

    def _extract_uds_from_tp(self, tp_data: bytes) -> bytes:
        """从TP单帧中提取UDS数据（多帧由send()中的重组状态机处理）"""
        if not tp_data:
            return b""
        frame_type = tp_data[0] & 0xF0
        if frame_type == 0x00:  # 单帧
            sf_length = tp_data[0] & 0x0F
            return bytes(tp_data[1:1 + sf_length])
        return b""

    def _wrap_tp_single_frame(self, uds_data: bytes) -> bytes:
        """将UDS响应包装为TP单帧"""
        if len(uds_data) <= 7:
            pci = len(uds_data) & 0x0F
            frame = bytes([pci]) + uds_data
            # 填充到8字节
            frame = frame + bytes(8 - len(frame))
            return frame
        else:
            # 对于超过7字节的数据，使用首帧格式（简化处理）
            total_length = len(uds_data)
            ff_pci = 0x10 | ((total_length >> 8) & 0x0F)
            frame = bytes([ff_pci, total_length & 0xFF]) + uds_data[:6]
            frame = frame + bytes(8 - len(frame))
            return frame

    def receive(self, timeout: float = 1.0) -> Optional[CanMessage]:
        if not self._connected:
            return None
        try:
            msg = self._rx_queue.get(timeout=timeout)
            self._rx_count += 1
            self._notify_message("RX", msg)
            return msg
        except queue.Empty:
            return None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def interface_name(self) -> str:
        return "Virtual"

    @property
    def channel_info(self) -> str:
        return f"Virtual CAN ({self._channel} @ {self._bitrate}bps)"

    def get_stats(self) -> dict:
        return {
            "tx_count": self._tx_count,
            "rx_count": self._rx_count,
            "interface": "Virtual",
            "channel": self._channel,
        }

    @property
    def ecu_simulator(self) -> VirtualEcuSimulator:
        """获取虚拟ECU模拟器实例（用于外部配置）"""
        return self._ecu_simulator
