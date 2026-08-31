"""刷写日志 - 刷写流程详细记录"""

import logging
from src.log.log_manager import get_log_manager


def get_logger() -> logging.Logger:
    return get_log_manager().get_flash_logger()


def log_flash_start(file_info: dict):
    """记录刷写开始"""
    logger = get_logger()
    logger.info("=" * 50)
    logger.info("=== 刷写开始 ===")
    logger.info(f"文件: {file_info.get('file_path', 'N/A')}, 大小: {file_info.get('data_size', 0)} bytes")
    logger.info(f"目标地址: 0x{file_info.get('start_address', 0):08X}")


def log_step(step_num: int, step_name: str, status: str, elapsed_ms: float = 0, detail: str = ""):
    """记录刷写步骤"""
    msg = f"Step {step_num}: {step_name} - {status}"
    if elapsed_ms > 0:
        msg += f" (耗时 {elapsed_ms:.1f}ms)"
    if detail:
        msg += f" | {detail}"
    get_logger().info(msg)


def log_transfer_progress(block_num: int, total_blocks: int, block_size: int):
    """记录传输进度"""
    pct = (block_num / total_blocks * 100) if total_blocks > 0 else 0
    get_logger().debug(f"Step 3: TransferData - Block {block_num}/{total_blocks} ({block_size} bytes) [{pct:.1f}%]")


def log_flash_end(success: bool, elapsed_s: float, error_msg: str = ""):
    """记录刷写结束"""
    logger = get_logger()
    if success:
        logger.info(f"=== 刷写成功 === 总耗时: {elapsed_s:.1f}s")
    else:
        logger.error(f"=== 刷写失败 === 总耗时: {elapsed_s:.1f}s, 错误: {error_msg}")
    logger.info("=" * 50)
