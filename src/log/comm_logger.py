"""通信日志 - CAN报文收发记录"""

import logging
from src.log.log_manager import get_log_manager


def get_logger() -> logging.Logger:
    return get_log_manager().get_comm_logger()


def log_tx(can_id: int, data: bytes, dlc: int = 0):
    """记录发送的CAN报文"""
    hex_str = " ".join(f"{b:02X}" for b in data)
    if dlc == 0:
        dlc = len(data)
    get_logger().info(f"[TX] CAN_ID=0x{can_id:03X}  DLC={dlc}  Data={hex_str}")


def log_rx(can_id: int, data: bytes, dlc: int = 0):
    """记录接收的CAN报文"""
    hex_str = " ".join(f"{b:02X}" for b in data)
    if dlc == 0:
        dlc = len(data)
    get_logger().info(f"[RX] CAN_ID=0x{can_id:03X}  DLC={dlc}  Data={hex_str}")
