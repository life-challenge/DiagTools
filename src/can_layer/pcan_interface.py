"""CAN硬件抽象层 - PCAN适配器"""

import time
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
            err_text = str(e)
            # Initialize失败(如0x40: 通道未初始化/初始化过程失败)常见于
            # 残留句柄或驱动状态异常——先CAN_Uninitialize清理再重试。
            # 驱动释放句柄需要时间，立即重Initialize仍会失败，需短暂延时
            for delay in (0.2, 0.5):
                if not self._uninitialize_handle(self._channel):
                    break
                time.sleep(delay)
                try:
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
                except Exception as e2:
                    err_text = str(e2)
            self._connected = False
            self._bus = None
            self._last_error = (
                f"{err_text}\n\n建议: 重新插拔PCAN设备后重试; "
                "若仍失败请重启应用")
            return False

    _PCAN_HANDLES = {
        **{f"PCAN_USBBUS{i}": 0x50 + i for i in range(1, 9)},
        **{f"PCAN_PCIBUS{i}": 0x40 + i - 1 for i in range(1, 5)},
        "PCAN_DNGBUS1": 0x81,
    }

    def disconnect(self) -> None:
        if self._bus:
            try:
                self._bus.shutdown()
            except Exception:
                # shutdown失败也要释放bus引用，避免句柄泄漏卡死后续重连
                self._uninitialize_handle(self._channel)
            self._bus = None
        self._connected = False

    def _uninitialize_handle(self, channel: str) -> bool:
        """直接调PCAN驱动CAN_Uninitialize清理残留句柄

        断开时序异常或驱动状态坏(如0x40)时，同进程内重新Initialize
        可能一直失败——先强制释放通道句柄再重试。
        """
        handle = self._PCAN_HANDLES.get(str(channel).strip().upper())
        if handle is None:
            return False
        try:
            import ctypes
            dll = ctypes.WinDLL("PCANBasic.dll")
            dll.CAN_Uninitialize(ctypes.c_ubyte(handle))
            return True
        except Exception:
            return False

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
