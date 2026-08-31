"""CAN硬件抽象层 - Vector适配器"""

from typing import Optional
from src.can_layer.can_interface import CanInterfaceBase
from src.models.can_message import CanMessage, CanMessageType, CanDirection


class VectorInterface(CanInterfaceBase):
    """Vector CAN适配器
    
    基于python-can的vector接口实现。
    需要安装Vector驱动和python-can库。
    """

    def __init__(self):
        super().__init__()
        self._bus = None
        self._connected = False
        self._channel = ""
        self._bitrate = 500000
        self._tx_count = 0
        self._rx_count = 0

    def connect(self, config: dict) -> bool:
        try:
            import can
            self._channel = config.get("channel", "0")
            self._bitrate = config.get("bitrate", 500000)
            app_name = config.get("app_name", "DiagTools")
            self._bus = can.Bus(
                channel=self._channel,
                interface="vector",
                bitrate=self._bitrate,
                app_name=app_name,
            )
            self._connected = True
            self._tx_count = 0
            self._rx_count = 0
            return True
        except Exception:
            self._connected = False
            self._bus = None
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
    def is_connected(self) -> bool:
        return self._connected

    @property
    def interface_name(self) -> str:
        return "Vector"

    @property
    def channel_info(self) -> str:
        return f"Vector (CH{self._channel} @ {self._bitrate}bps)"

    def get_stats(self) -> dict:
        return {
            "tx_count": self._tx_count,
            "rx_count": self._rx_count,
            "interface": "Vector",
            "channel": self._channel,
        }
