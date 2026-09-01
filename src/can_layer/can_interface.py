"""CAN硬件抽象层 - 抽象基类"""

from abc import ABC, abstractmethod
from typing import Optional, Callable
from src.models.can_message import CanMessage
from src.log import comm_logger


class CanInterfaceBase(ABC):
    """CAN接口抽象基类
    
    所有CAN硬件适配器必须实现此接口，提供统一的连接、发送、接收方法。
    """

    def __init__(self):
        # 报文监听器列表: callback(direction: "TX"/"RX", msg: CanMessage)
        # 注意: 回调可能在非GUI线程中被调用，监听方需自行处理线程安全
        self._message_listeners: list[Callable[[str, "CanMessage"], None]] = []

    def add_message_listener(self, callback: Callable[[str, "CanMessage"], None]):
        """注册报文监听器，每次发送/接收到报文时回调"""
        if callback not in self._message_listeners:
            self._message_listeners.append(callback)

    def remove_message_listener(self, callback: Callable[[str, "CanMessage"], None]):
        """移除报文监听器"""
        if callback in self._message_listeners:
            self._message_listeners.remove(callback)

    def _notify_message(self, direction: str, msg: CanMessage):
        """通知所有监听器（子类在send/receive成功后调用）

        同时将帧写入comm日志文件（logs/comm/），保留总线级收发证据:
        仅靠界面日志无法区分"ECU未回"与"驱动未收到"，帧级日志是
        物理层问题定位的关键证据。
        """
        try:
            if direction == "RX":
                comm_logger.log_rx(msg.can_id, msg.data, msg.dlc)
            else:
                comm_logger.log_tx(msg.can_id, msg.data, msg.dlc)
        except Exception:
            pass  # 日志失败不影响通信主流程
        for cb in self._message_listeners:
            try:
                cb(direction, msg)
            except Exception:
                pass

    @abstractmethod
    def connect(self, config: dict) -> bool:
        """连接CAN接口
        
        Args:
            config: 连接配置字典，包含:
                - channel: 通道标识 (如 "PCAN_USBBUS1" 或 0)
                - bitrate: 波特率 (如 500000)
                - 其他硬件特定参数
                
        Returns:
            连接成功返回True，失败返回False
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """断开CAN连接"""
        pass

    @abstractmethod
    def send(self, msg: CanMessage) -> bool:
        """发送CAN报文
        
        Args:
            msg: 要发送的CAN报文
            
        Returns:
            发送成功返回True，失败返回False
        """
        pass

    @abstractmethod
    def receive(self, timeout: float = 1.0) -> Optional[CanMessage]:
        """接收CAN报文
        
        Args:
            timeout: 接收超时时间（秒）
            
        Returns:
            接收到报文返回CanMessage，超时返回None
        """
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """连接状态"""
        pass

    @property
    @abstractmethod
    def interface_name(self) -> str:
        """接口名称（如 'PCAN', 'Vector', 'Virtual'）"""
        pass

    @property
    @abstractmethod
    def channel_info(self) -> str:
        """当前通道信息描述"""
        pass

    def get_stats(self) -> dict:
        """获取接口统计信息（可选实现）"""
        return {}
