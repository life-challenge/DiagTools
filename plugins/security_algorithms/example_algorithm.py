"""示例安全算法实现

演示如何实现安全算法插件。
"""

import sys
import os
# 确保插件目录在Python路径中
plugin_dir = os.path.dirname(os.path.abspath(__file__))
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)
from algorithm_base import SecurityAlgorithmBase


class ExampleAlgorithm(SecurityAlgorithmBase):
    """示例算法：种子按位异或固定密钥"""

    @property
    def name(self) -> str:
        return "Example XOR Algorithm"

    @property
    def description(self) -> str:
        return "示例算法：种子按位异或固定密钥0xA55A"

    @property
    def level(self) -> int:
        return 1

    def generate_key(self, seed: bytes) -> bytes:
        FIXED_KEY = 0xA55A
        seed_val = int.from_bytes(seed, 'big')
        key_val = seed_val ^ FIXED_KEY
        return key_val.to_bytes(len(seed), 'big')
