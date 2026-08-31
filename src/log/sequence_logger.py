"""序列执行日志 - 自定义序列执行过程记录"""

import logging
from src.log.log_manager import get_log_manager


def get_logger() -> logging.Logger:
    return get_log_manager().get_sequence_logger()


def log_sequence_start(name: str, total_steps: int):
    """记录序列开始"""
    logger = get_logger()
    logger.info("=" * 50)
    logger.info(f"=== 序列开始: \"{name}\" === ({total_steps} 步骤)")


def log_step(current: int, total: int, step_name: str, request_hex: str,
             send_count: int, response_hex: str = "", elapsed_ms: float = 0,
             success: bool = True):
    """记录序列步骤"""
    status = "OK" if success else "FAIL"
    msg = f"Step {current}/{total}: {step_name} | TX: {request_hex} | 发送次数: {send_count}"
    get_logger().info(msg)

    if response_hex:
        detail = f"Step {current}/{total}: {status} | RX: {response_hex}"
        if elapsed_ms > 0:
            detail += f" | 耗时: {elapsed_ms:.0f}ms"
        get_logger().info(detail)


def log_sequence_end(name: str, success_count: int, total: int, elapsed_ms: float):
    """记录序列结束"""
    logger = get_logger()
    logger.info(f"=== 序列完成: {success_count}/{total}步骤成功, 总耗时: {elapsed_ms:.0f}ms ===")
    logger.info("=" * 50)
