"""UDS客户端 - 服务调度、超时处理、NRC解析"""

import time
import threading
from typing import Optional, Callable
from src.can_layer.can_interface import CanInterfaceBase
from src.protocol.transport_layer import TransportLayer
from src.protocol.uds_services import UdsService, ServiceID
from src.models.uds_message import UdsMessage, UdsMessageType
from src.log.log_manager import get_log_manager


class UdsClient:
    """UDS客户端
    
    封装UDS服务调用，处理超时、NRC解析、TesterPresent保活。
    """

    def __init__(self, can_interface: Optional[CanInterfaceBase] = None,
                 tx_id: int = 0x7E0, rx_id: int = 0x7E8,
                 p2_timeout: float = 0.5, p2_star_timeout: float = 5.0,
                 transport_layer=None):
        """初始化UDS客户端

        Args:
            can_interface: CAN接口实例（CAN传输时必传，DoIP时可传None）
            tx_id: 发送CAN ID（仅CAN传输）
            rx_id: 接收CAN ID（仅CAN传输）
            p2_timeout: P2超时（秒）
            p2_star_timeout: P2*超时（秒）
            transport_layer: 可选，注入自定义传输层（如DoipTransportLayer），
                与CAN TransportLayer保持send_tp/receive_tp同构接口
        """
        self._can = can_interface
        if transport_layer is not None:
            self._tp = transport_layer
        else:
            self._tp = TransportLayer(can_interface, tx_id, rx_id)
        self._p2_timeout = p2_timeout
        self._p2_star_timeout = p2_star_timeout
        self._tester_present_timer: Optional[threading.Timer] = None
        self._tester_present_running = False
        self._lock = threading.Lock()
        self._logger = get_log_manager().get_diag_logger()

    @property
    def transport_layer(self) -> TransportLayer:
        """底层传输层（CAN TransportLayer或DoipTransportLayer）"""
        return self._tp

    @property
    def tx_id(self) -> int:
        return self._tp.tx_id

    @tx_id.setter
    def tx_id(self, value: int):
        self._tp.tx_id = value

    @property
    def rx_id(self) -> int:
        return self._tp.rx_id

    @rx_id.setter
    def rx_id(self, value: int):
        self._tp.rx_id = value

    def send_raw(self, data: bytes, timeout: float = None,
                 wait_response: bool = True) -> Optional[bytes]:
        """发送原始UDS请求并等待响应
        
        Args:
            data: 请求数据
            timeout: 超时时间（秒）
            wait_response: False时发送后不等待（用于抑制正响应的请求）
            
        Returns:
            响应数据，超时返回None
        """
        if timeout is None:
            timeout = self._p2_timeout

        self._log_request(data)

        if not self._tp.send_tp(data):
            self._logger.error(f"发送失败: {self._bytes_to_hex(data)}")
            return None

        if not wait_response:
            return None

        response = self._receive_with_pending(timeout, data[0])
        if response:
            self._log_response(response)
        else:
            self._logger.warning("响应超时")

        return response

    def _receive_with_pending(self, timeout: float,
                             req_sid: int = None) -> Optional[bytes]:
        """接收响应，处理pending (0x78)响应与陈旧帧

        仅接受与当前请求SID匹配的响应（正响应=req_sid+0x40，
        负响应=0x7F+req_sid），丢弃其他无关帧（如后台保活
        3E 80 的历史响应），避免错配导致服务误判失败。
        """
        deadline = time.time() + self._p2_star_timeout

        while time.time() < deadline:
            response = self._tp.receive_tp(timeout=timeout)
            if response is None:
                return None

            # 检查是否为pending响应
            if len(response) >= 3 and response[0] == 0x7F and response[2] == 0x78:
                self._logger.info(f"收到pending响应 (NRC=0x78), 继续等待...")
                continue

            # SID关联校验: 丢弃与当前请求无关的陈旧响应
            if req_sid is not None and response:
                pos_match = response[0] == ((req_sid + 0x40) & 0xFF)
                neg_match = (response[0] == 0x7F and len(response) >= 2
                             and response[1] == req_sid)
                if not (pos_match or neg_match):
                    self._logger.warning(
                        f"丢弃无关响应: {self._bytes_to_hex(response)} "
                        f"(期望请求SID=0x{req_sid:02X}的响应)")
                    continue

            return response

        return None

    # --- 高级UDS服务接口 ---

    def diagnostic_session_control(self, session_type: int) -> Optional[bytes]:
        """切换诊断会话"""
        data = UdsService.encode_diagnostic_session(session_type)
        return self.send_raw(data)

    def ecu_reset(self, reset_type: int) -> Optional[bytes]:
        """ECU复位"""
        data = UdsService.encode_ecu_reset(reset_type)
        return self.send_raw(data)

    def security_access_request_seed(self, level: int) -> Optional[bytes]:
        """请求安全访问种子"""
        data = UdsService.encode_security_access_request_seed(level)
        return self.send_raw(data)

    def security_access_send_key(self, level: int, key: bytes) -> Optional[bytes]:
        """发送安全访问密钥"""
        data = UdsService.encode_security_access_send_key(level, key)
        return self.send_raw(data)

    def read_data_by_identifier(self, did_id: int) -> Optional[bytes]:
        """读取DID"""
        data = UdsService.encode_read_did(did_id)
        return self.send_raw(data)

    def write_data_by_identifier(self, did_id: int, value: bytes) -> Optional[bytes]:
        """写入DID"""
        data = UdsService.encode_write_did(did_id, value)
        return self.send_raw(data)

    def read_memory_by_address(self, address: int, size: int) -> Optional[bytes]:
        """按地址读内存"""
        data = UdsService.encode_read_memory(address, size)
        return self.send_raw(data)

    def io_control(self, did_id: int, control_option: int, data: bytes = b"") -> Optional[bytes]:
        """IO控制"""
        req = UdsService.encode_io_control(did_id, control_option, data)
        return self.send_raw(req)

    def routine_control(self, sub_func: int, routine_id: int, data: bytes = b"") -> Optional[bytes]:
        """例程控制"""
        req = UdsService.encode_routine_control(sub_func, routine_id, data)
        return self.send_raw(req)

    def request_download(self, addr: int, size: int, data_format: int = 0x00) -> Optional[bytes]:
        """请求下载"""
        req = UdsService.encode_request_download(data_format, addr, size)
        return self.send_raw(req)

    def transfer_data(self, block_seq: int, data: bytes) -> Optional[bytes]:
        """传输数据"""
        req = UdsService.encode_transfer_data(block_seq, data)
        return self.send_raw(req)

    def request_transfer_exit(self) -> Optional[bytes]:
        """请求传输退出"""
        req = UdsService.encode_request_transfer_exit()
        return self.send_raw(req)

    def tester_present(self, suppress_response: bool = False) -> Optional[bytes]:
        """TesterPresent保活"""
        data = UdsService.encode_tester_present(suppress_response)
        if suppress_response:
            # 抑制正响应: ECU不回复，发送后无需等待（也避免占用接收队列）
            return self.send_raw(data, wait_response=False)
        return self.send_raw(data)

    def control_dtc_setting(self, on: bool = True) -> Optional[bytes]:
        """DTC设置控制"""
        data = UdsService.encode_control_dtc_setting(on)
        return self.send_raw(data)

    def read_dtc_by_status(self, mask: int = 0xFF) -> Optional[bytes]:
        """按状态读DTC"""
        data = UdsService.encode_read_dtc_by_status(mask)
        return self.send_raw(data)

    def read_dtc_count(self) -> Optional[bytes]:
        """读取DTC数量"""
        data = UdsService.encode_read_dtc_count()
        return self.send_raw(data)

    def clear_dtc(self, group: int = 0xFFFFFF) -> Optional[bytes]:
        """清除DTC"""
        data = UdsService.encode_clear_dtc(group)
        return self.send_raw(data)

    def communication_control(self, control_type: int, comm_type: int = 0x01) -> Optional[bytes]:
        """通信控制"""
        data = UdsService.encode_communication_control(control_type, comm_type)
        return self.send_raw(data)

    # --- TesterPresent 保活 ---

    def start_tester_present(self, interval_ms: int = 2000):
        """启动TesterPresent自动保活"""
        self.stop_tester_present()
        self._tester_present_running = True
        self._schedule_tester_present(interval_ms)

    def stop_tester_present(self):
        """停止TesterPresent自动保活"""
        self._tester_present_running = False
        if self._tester_present_timer:
            self._tester_present_timer.cancel()
            self._tester_present_timer = None

    def _schedule_tester_present(self, interval_ms: int):
        if not self._tester_present_running:
            return
        self._tester_present_timer = threading.Timer(
            interval_ms / 1000.0,
            self._send_tester_present,
            args=[interval_ms]
        )
        self._tester_present_timer.daemon = True
        self._tester_present_timer.start()

    def _send_tester_present(self, interval_ms: int):
        if self._tester_present_running and self._is_transport_connected():
            try:
                self.tester_present(suppress_response=True)
            except Exception:
                pass
            self._schedule_tester_present(interval_ms)

    def _is_transport_connected(self) -> bool:
        """检查底层传输是否可用（兼容CAN接口与DoIP传输层）"""
        # DoIP等注入传输层提供is_connected属性
        if hasattr(self._tp, "is_connected"):
            return self._tp.is_connected
        return bool(self._can and self._can.is_connected)

    # --- 日志辅助 ---

    def _log_request(self, data: bytes):
        hex_str = " ".join(f"{b:02X}" for b in data)
        sid = data[0] if data else 0
        name = UdsService.get_service_name(sid)
        self._logger.info(f"TX >> {hex_str}  # {name}")

    def _log_response(self, data: bytes):
        hex_str = " ".join(f"{b:02X}" for b in data)
        if data and data[0] == 0x7F and len(data) >= 3:
            nrc_desc = UdsService.get_nrc_description(data[2])
            self._logger.warning(f"RX << {hex_str}  # NRC: {nrc_desc}")
        else:
            self._logger.info(f"RX << {hex_str}")

    @staticmethod
    def _bytes_to_hex(data: bytes) -> str:
        return " ".join(f"{b:02X}" for b in data)
