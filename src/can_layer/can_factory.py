"""CAN硬件抽象层 - 工厂类"""

from src.can_layer.can_interface import CanInterfaceBase


class CanFactory:
    """CAN接口工厂
    
    根据配置创建对应的CAN接口实例。
    支持: Virtual, PCAN, Vector
    """

    _registry = {}

    @classmethod
    def register(cls, name: str, interface_class):
        """注册CAN接口类型"""
        cls._registry[name.lower()] = interface_class

    @classmethod
    def create(cls, interface_type: str, **kwargs) -> CanInterfaceBase:
        """创建CAN接口实例
        
        Args:
            interface_type: 接口类型 ('virtual', 'pcan', 'vector')
            **kwargs: 传递给接口构造函数的参数
            
        Returns:
            CanInterfaceBase实例
            
        Raises:
            ValueError: 不支持的接口类型
        """
        # 延迟导入以避免循环依赖
        if not cls._registry:
            cls._auto_register()

        key = interface_type.lower().strip()
        if key not in cls._registry:
            raise ValueError(
                f"不支持的CAN接口类型: '{interface_type}'. "
                f"可用类型: {list(cls._registry.keys())}"
            )

        return cls._registry[key](**kwargs)

    @classmethod
    def available_interfaces(cls) -> list[str]:
        """返回可用的接口类型列表"""
        if not cls._registry:
            cls._auto_register()
        return list(cls._registry.keys())

    @classmethod
    def _auto_register(cls):
        """自动注册内置的接口类型"""
        from src.can_layer.virtual_interface import VirtualCanInterface
        from src.can_layer.pcan_interface import PcanInterface
        from src.can_layer.vector_interface import VectorInterface

        cls.register("virtual", VirtualCanInterface)
        cls.register("pcan", PcanInterface)
        cls.register("vector", VectorInterface)
