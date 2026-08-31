"""安全算法插件基类

所有安全算法插件必须继承此基类并实现generate_key方法。
"""

from abc import ABC, abstractmethod


class SecurityAlgorithmBase(ABC):
    """安全算法插件基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """算法名称"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """算法描述"""
        pass

    @property
    @abstractmethod
    def level(self) -> int:
        """安全等级"""
        pass

    @abstractmethod
    def generate_key(self, seed: bytes) -> bytes:
        """根据种子计算密钥
        
        Args:
            seed: ECU返回的种子数据
            
        Returns:
            计算得到的密钥数据
        """
        raise NotImplementedError
