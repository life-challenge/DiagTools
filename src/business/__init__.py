"""业务逻辑层：诊断业务管理器集合。

包含安全访问、DID、DTC、刷写、序列执行、ECU扫描、报告生成，
以及解析器（DBC/ODX/A2L）与 32 位 PCAN 桥接进程管理。
"""

from src.business.security_manager import SecurityManager
from src.business.did_manager import DidManager
from src.business.dtc_manager import DtcManager
from src.business.flash_manager import FlashManager
from src.business.sequence_manager import SequenceManager
from src.business.ecu_scanner import EcuScanner
from src.business.report_generator import ReportGenerator

__all__ = [
    "SecurityManager", "DidManager", "DtcManager", "FlashManager",
    "SequenceManager", "EcuScanner", "ReportGenerator",
]
