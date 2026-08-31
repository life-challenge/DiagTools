"""CAN 硬件抽象层：统一接口 + 工厂创建。

实现: VirtualCanInterface（内置仿真）、PcanInterface（32位桥接）、
VectorCanInterface。通过 CanFactory.create(类型, 通道) 获取实例。
"""

from src.can_layer.can_interface import CanInterfaceBase
from src.can_layer.can_factory import CanFactory

__all__ = ["CanInterfaceBase", "CanFactory"]
