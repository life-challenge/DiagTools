"""诊断日志 - UDS请求/响应记录"""

import logging
from src.log.log_manager import get_log_manager


def get_logger() -> logging.Logger:
    return get_log_manager().get_diag_logger()


def log_request(data: bytes, description: str = ""):
    """记录UDS请求"""
    hex_str = " ".join(f"{b:02X}" for b in data)
    msg = f"TX >> {hex_str}"
    if description:
        msg += f"  # {description}"
    get_logger().info(msg)


def log_response(data: bytes, description: str = ""):
    """记录UDS响应"""
    hex_str = " ".join(f"{b:02X}" for b in data)
    if data and data[0] == 0x7F:
        msg = f"RX << {hex_str}"
        if description:
            msg += f"  # {description}"
        get_logger().warning(msg)
    else:
        msg = f"RX << {hex_str}"
        if description:
            msg += f"  # {description}"
        get_logger().info(msg)


def log_error(message: str):
    """记录诊断错误"""
    get_logger().error(message)
