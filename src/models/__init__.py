"""数据模型层：跨层传递的报文数据结构。"""

from src.models.can_message import CanMessage
from src.models.uds_message import UdsMessage

__all__ = ["CanMessage", "UdsMessage"]
