# -*- coding: utf-8 -*-
"""ISO 15765-2 传输层测试（基于虚拟CAN，无需硬件）"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

from src.can_layer.virtual_interface import VirtualCanInterface
from src.protocol.transport_layer import TransportLayer


class TestTransportLayer(unittest.TestCase):

    def setUp(self):
        self.iface = VirtualCanInterface()
        self.assertTrue(self.iface.connect({}))
        self.tp = TransportLayer(self.iface, tx_id=0x7E0, rx_id=0x7E8,
                                 timeout=2.0)

    def tearDown(self):
        try:
            self.iface.close()
        except Exception:
            pass

    def test_single_frame_roundtrip(self):
        """TesterPresent 3E00 -> 7E00 单帧往返"""
        self.assertTrue(self.tp.send_tp(bytes([0x3E, 0x00])))
        resp = self.tp.receive_tp(timeout=2.0)
        self.assertIsNotNone(resp)
        self.assertEqual(resp[:2], bytes([0x7E, 0x00]))

    def test_multi_frame_reassembly(self):
        """读VIN(F190)响应超过7字节，走首帧+连续帧重组"""
        self.assertTrue(self.tp.send_tp(bytes([0x22, 0xF1, 0x90])))
        resp = self.tp.receive_tp(timeout=3.0)
        self.assertIsNotNone(resp)
        self.assertEqual(resp[:3], bytes([0x62, 0xF1, 0x90]))
        self.assertGreater(len(resp), 7)  # 必须经历多帧重组

    def test_receive_timeout(self):
        """无响应时超时返回None"""
        resp = self.tp.receive_tp(timeout=0.3)
        self.assertIsNone(resp)


if __name__ == "__main__":
    unittest.main()
