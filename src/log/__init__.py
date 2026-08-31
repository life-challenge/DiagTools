"""日志模块：按业务域独立记录，文件名按模块/时间自动生成。

diag/ 诊断操作、comm/ 报文通信、flash/ 刷写流程、sequence/ 序列。
"""

from src.log.log_manager import LogManager

__all__ = ["LogManager"]
