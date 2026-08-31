"""CAN报文数据模型"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class CanMessageType(Enum):
    """CAN报文类型"""
    STANDARD = "standard"  # 标准帧 (11-bit ID)
    EXTENDED = "extended"  # 扩展帧 (29-bit ID)


class CanDirection(Enum):
    """报文方向"""
    TX = "TX"  # 发送
    RX = "RX"  # 接收


@dataclass
class CanMessage:
    """CAN报文模型
    
    Attributes:
        can_id: CAN标识符 (11-bit 或 29-bit)
        data: 数据载荷 (最多8字节 for classic CAN)
        dlc: 数据长度
        msg_type: 帧类型 (标准/扩展)
        direction: 报文方向 (TX/RX)
        timestamp: 时间戳 (秒)
        channel: CAN通道
        is_error: 是否为错误帧
        is_remote: 是否为远程帧
    """
    can_id: int
    data: bytes = b""
    dlc: int = 0
    msg_type: CanMessageType = CanMessageType.STANDARD
    direction: CanDirection = CanDirection.TX
    timestamp: float = field(default_factory=time.time)
    channel: int = 0
    is_error: bool = False
    is_remote: bool = False

    def __post_init__(self):
        if self.dlc == 0 and self.data:
            self.dlc = len(self.data)

    @property
    def data_hex(self) -> str:
        """数据HEX字符串表示"""
        return " ".join(f"{b:02X}" for b in self.data)

    @property
    def can_id_hex(self) -> str:
        """CAN ID HEX字符串"""
        if self.msg_type == CanMessageType.EXTENDED:
            return f"{self.can_id:08X}"
        return f"{self.can_id:03X}"

    @property
    def is_extended(self) -> bool:
        """是否为扩展帧"""
        return self.msg_type == CanMessageType.EXTENDED

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "can_id": self.can_id,
            "data": list(self.data),
            "dlc": self.dlc,
            "msg_type": self.msg_type.value,
            "direction": self.direction.value,
            "timestamp": self.timestamp,
            "channel": self.channel,
            "is_error": self.is_error,
            "is_remote": self.is_remote,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CanMessage":
        """从字典创建"""
        return cls(
            can_id=d["can_id"],
            data=bytes(d.get("data", [])),
            dlc=d.get("dlc", 0),
            msg_type=CanMessageType(d.get("msg_type", "standard")),
            direction=CanDirection(d.get("direction", "TX")),
            timestamp=d.get("timestamp", time.time()),
            channel=d.get("channel", 0),
            is_error=d.get("is_error", False),
            is_remote=d.get("is_remote", False),
        )

    def __str__(self) -> str:
        direction_str = self.direction.value
        return (
            f"[{direction_str}] CAN_ID=0x{self.can_id_hex}  "
            f"DLC={self.dlc}  Data={self.data_hex}"
        )
