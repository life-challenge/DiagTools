"""CAN硬件抽象层 - PCAN适配器"""

from typing import Optional
from src.can_layer.can_interface import CanInterfaceBase
from src.models.can_message import CanMessage, CanMessageType, CanDirection


class PcanInterface(CanInterfaceBase):
    """PCAN USB适配器
    
    基于python-can的pcan接口实现。
    需要安装PCAN驱动和python-can库。
    """

    # PCAN 默认通道列表（按优先级排序）
    DEFAULT_CHANNELS = [
        "PCAN_USBBUS1", "PCAN_USBBUS2", "PCAN_USBBUS3", "PCAN_USBBUS4",
        "PCAN_USBBUS5", "PCAN_USBBUS6", "PCAN_USBBUS7", "PCAN_USBBUS8",
        "PCAN_PCIBUS1", "PCAN_PCIBUS2", "PCAN_PCIBUS3", "PCAN_PCIBUS4",
        "PCAN_DNGBUS1",
    ]

    def __init__(self):
        super().__init__()
        self._bus = None
        self._connected = False
        self._channel = ""
        self._bitrate = 500000
        self._tx_count = 0
        self._rx_count = 0
        self._last_error = ""

    @staticmethod
    def detect_channels() -> list[dict]:
        """扫描系统中可用的PCAN通道
        
        Returns:
            通道信息列表，每项包含 channel, device_name 等字段。
            扫描失败返回空列表。
        """
        try:
            import can
            configs = can.detect_available_configs(interfaces=["pcan"])
            channels = []
            for cfg in configs:
                channels.append({
                    "channel": cfg.get("channel", ""),
                    "device_name": cfg.get("device_name", ""),
                    "device_id": cfg.get("device_id", -1),
                })
            return channels
        except Exception:
            return []

    def connect(self, config: dict) -> bool:
        """连接CAN接口
        
        Args:
            config: 连接配置字典
            
        Returns:
            连接成功返回True，失败返回False（错误信息可通过last_error获取）
        """
        try:
            import can
            self._channel = config.get("channel", "PCAN_USBBUS1")
            self._bitrate = config.get("bitrate", 500000)
            self._bus = can.Bus(
                channel=self._channel,
                interface="pcan",
                bitrate=self._bitrate,
            )
            self._connected = True
            self._tx_count = 0
            self._rx_count = 0
            self._last_error = ""
            return True
        except Exception as e:
            self._connected = False
            self._bus = None
            self._last_error = str(e)
            return False

    def disconnect(self) -> None:
        if self._bus:
            self._bus.shutdown()
            self._bus = None
        self._connected = False

    def send(self, msg: CanMessage) -> bool:
        if not self._connected or not self._bus:
            return False
        try:
            import can
            can_msg = can.Message(
                arbitration_id=msg.can_id,
                data=msg.data,
                is_extended_id=msg.is_extended,
            )
            self._bus.send(can_msg)
            msg.direction = CanDirection.TX
            self._tx_count += 1
            self._notify_message("TX", msg)
            return True
        except Exception:
            return False

    def receive(self, timeout: float = 1.0) -> Optional[CanMessage]:
        if not self._connected or not self._bus:
            return None
        try:
            can_msg = self._bus.recv(timeout=timeout)
            if can_msg is None:
                return None
            msg = CanMessage(
                can_id=can_msg.arbitration_id,
                data=bytes(can_msg.data),
                dlc=can_msg.dlc,
                msg_type=CanMessageType.EXTENDED if can_msg.is_extended_id else CanMessageType.STANDARD,
                direction=CanDirection.RX,
                timestamp=can_msg.timestamp,
            )
            self._rx_count += 1
            self._notify_message("RX", msg)
            return msg
        except Exception:
            return None

    @property
    def last_error(self) -> str:
        """最后一次连接/操作的错误信息"""
        return self._last_error

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def interface_name(self) -> str:
        return "PCAN"

    @property
    def channel_info(self) -> str:
        return f"PCAN ({self._channel} @ {self._bitrate}bps)"

    def get_stats(self) -> dict:
        return {
            "tx_count": self._tx_count,
            "rx_count": self._rx_count,
            "interface": "PCAN",
            "channel": self._channel,
        }
