# -*- coding: utf-8 -*-
"""CanFactory 工厂测试（virtual/pcan/vector注册与实例化，无需硬件）"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

from src.can_layer.can_factory import CanFactory
from src.can_layer.can_interface import CanInterfaceBase
from src.can_layer.virtual_interface import VirtualCanInterface
from src.can_layer.pcan_interface import PcanInterface
from src.can_layer.vector_interface import VectorInterface


class TestCanFactory(unittest.TestCase):

    def test_available_interfaces(self):
        names = CanFactory.available_interfaces()
        for expected in ("virtual", "pcan", "vector"):
            self.assertIn(expected, names)

    def test_create_virtual(self):
        iface = CanFactory.create("virtual")
        self.assertIsInstance(iface, VirtualCanInterface)
        self.assertIsInstance(iface, CanInterfaceBase)

    def test_create_is_case_insensitive_and_trimmed(self):
        iface = CanFactory.create("  Virtual  ")
        self.assertIsInstance(iface, VirtualCanInterface)

    def test_create_pcan_without_hardware(self):
        """PCAN实例化不触碰硬件（驱动延迟到connect才加载）"""
        iface = CanFactory.create("pcan")
        self.assertIsInstance(iface, PcanInterface)
        self.assertFalse(iface.is_connected)

    def test_create_vector_without_hardware(self):
        iface = CanFactory.create("vector")
        self.assertIsInstance(iface, VectorInterface)
        self.assertFalse(iface.is_connected)

    def test_create_unknown_raises(self):
        with self.assertRaises(ValueError) as ctx:
            CanFactory.create("kvaser")
        self.assertIn("不支持", str(ctx.exception))
        self.assertIn("virtual", str(ctx.exception))

    def test_register_custom_interface(self):
        class DummyInterface(CanInterfaceBase):
            def __init__(self, channel="x"):
                super().__init__()
                self.channel = channel

            def connect(self, config):
                return True

            def disconnect(self):
                pass

            def send(self, msg):
                return True

            def receive(self, timeout=1.0):
                return None

            @property
            def is_connected(self):
                return True

            @property
            def interface_name(self):
                return "Dummy"

            @property
            def channel_info(self):
                return "Dummy"

        CanFactory.register("dummy", DummyInterface)
        self.addCleanup(CanFactory._registry.pop, "dummy", None)
        iface = CanFactory.create("dummy", channel="can0")
        self.assertIsInstance(iface, DummyInterface)
        self.assertEqual(iface.channel, "can0")


if __name__ == "__main__":
    unittest.main()
