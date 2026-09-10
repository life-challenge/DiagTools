"""ECU自动扫描模块

广播扫描：向预设地址范围发送0x10 01，收集正响应的ECU。
"""

import time
from typing import Optional, Callable
from src.log.log_manager import get_log_manager


class EcuInfo:
    """ECU信息"""

    def __init__(self, tx_id: int, rx_id: int, name: str = ""):
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.name = name or f"ECU_0x{tx_id:03X}"
        self.session_type = 0x01
        self.software_version = ""
        self.hardware_version = ""
        self.is_connected = False
        self.last_response_time = 0.0

    def to_dict(self) -> dict:
        return {
            "tx_id": f"0x{self.tx_id:03X}",
            "rx_id": f"0x{self.rx_id:03X}",
            "name": self.name,
            "session_type": f"0x{self.session_type:02X}",
            "software_version": self.software_version,
        }


class EcuScanner:
    """ECU扫描器"""

    # 常见UDS地址对 (TX, RX)
    DEFAULT_ADDRESS_PAIRS = [
        (0x7E0, 0x7E8), (0x7E1, 0x7E9), (0x7E2, 0x7EA), (0x7E3, 0x7EB),
        (0x7E4, 0x7EC), (0x7E5, 0x7ED), (0x7E6, 0x7EE), (0x7E7, 0x7EF),
        (0x7DF, 0x7DF),  # 功能寻址
    ]

    def __init__(self, can_interface=None, uds_client_factory=None):
        self._can = can_interface
        self._uds_client_factory = uds_client_factory
        self._scanning = False
        self._scan_results: list[EcuInfo] = []
        self._progress_callback: Optional[Callable] = None
        self._logger = get_log_manager().get_app_logger()

    @property
    def is_scanning(self) -> bool:
        return self._scanning

    @property
    def scan_results(self) -> list[EcuInfo]:
        return list(self._scan_results)

    def scan(self, address_pairs: list = None, timeout: float = 0.2,
             progress_callback: Callable = None) -> list[EcuInfo]:
        """执行ECU扫描

        Args:
            address_pairs: 地址对列表 [(tx_id, rx_id), ...]
            timeout: 每个地址等待响应时间
            progress_callback: 进度回调 (current, total, tx_id) -> None
        """
        if address_pairs is None:
            address_pairs = self.DEFAULT_ADDRESS_PAIRS

        self._scanning = True
        self._scan_results = []
        self._progress_callback = progress_callback
        total = len(address_pairs)

        self._logger.info(f"开始ECU扫描: {total} 个地址")

        for idx, (tx_id, rx_id) in enumerate(address_pairs):
            if not self._scanning:
                break

            if progress_callback:
                progress_callback(idx + 1, total, tx_id)

            try:
                if self._uds_client_factory:
                    client = self._uds_client_factory(tx_id, rx_id)
                else:
                    from src.protocol.uds_client import UdsClient
                    client = UdsClient(self._can, tx_id=tx_id, rx_id=rx_id,
                                       p2_timeout=timeout)

                resp = client.diagnostic_session_control(0x01)
                if resp and len(resp) >= 2 and resp[0] == 0x50:
                    ecu = EcuInfo(tx_id, rx_id)
                    ecu.session_type = resp[1] if len(resp) > 1 else 0x01
                    ecu.is_connected = True
                    ecu.last_response_time = time.time()
                    self._scan_results.append(ecu)
                    self._logger.info(
                        f"发现ECU: TX=0x{tx_id:03X} RX=0x{rx_id:03X} "
                        f"会话=0x{ecu.session_type:02X}")

            except Exception as e:
                self._logger.debug(f"扫描 0x{tx_id:03X} 失败: {e}")

        self._scanning = False
        self._logger.info(f"扫描完成: 发现 {len(self._scan_results)} 个ECU")
        return self._scan_results

    def stop_scan(self):
        """停止扫描"""
        self._scanning = False
