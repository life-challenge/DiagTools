"""DoIP传输层单元测试（回环，无需硬件）

覆盖: 帧构造/解析、路由激活、UDS诊断回环（经UdsClient）、
连接拒绝、超时行为、UDP车辆发现、报文监听器。
"""

import os
import sys
import struct
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.protocol.doip_layer import (
    DoipTransportLayer, VirtualDoipEcu, DoipPayloadType, _build_doip_frame,
    _HEADER_FMT, _DOIP_VERSION,
)
from src.protocol.uds_client import UdsClient


class TestDoipFrame(unittest.TestCase):
    """DoIP帧构造与解析"""

    def test_build_frame_header(self):
        frame = _build_doip_frame(DoipPayloadType.DIAG_MESSAGE, b"\x22\xF1\x90")
        version, inverse, ptype, length = struct.unpack(
            _HEADER_FMT, frame[:8])
        self.assertEqual(version, _DOIP_VERSION)
        self.assertEqual(inverse, 0xFF ^ _DOIP_VERSION)
        self.assertEqual(ptype, 0x8001)
        self.assertEqual(length, 3)
        self.assertEqual(frame[8:], b"\x22\xF1\x90")


class TestDoipRoundtrip(unittest.TestCase):
    """虚拟DoIP ECU回环测试"""

    @classmethod
    def setUpClass(cls):
        cls.ecu = VirtualDoipEcu(logical_address=0x1000)
        cls.host, cls.port = cls.ecu.start()

    @classmethod
    def tearDownClass(cls):
        cls.ecu.stop()

    def _make_layer(self, **kwargs) -> DoipTransportLayer:
        layer = DoipTransportLayer(
            target_ip=self.host, tcp_port=self.port,
            tester_address=0x0E80, ecu_address=0x1000, timeout=2.0)
        self.assertTrue(layer.connect(), layer.last_error)
        return layer

    def test_routing_activation(self):
        """路由激活成功并协商ECU地址"""
        layer = self._make_layer()
        try:
            self.assertTrue(layer.is_connected)
            self.assertEqual(layer.ecu_address, 0x1000)
            self.assertEqual(layer.tester_address, 0x0E80)
            self.assertEqual(layer.interface_name, "DoIP")
            self.assertIn("DoIP", layer.channel_info)
        finally:
            layer.disconnect()
            self.assertFalse(layer.is_connected)

    def test_uds_session_control_via_client(self):
        """UdsClient注入DoIP传输层: 会话切换"""
        layer = self._make_layer()
        try:
            client = UdsClient(transport_layer=layer, p2_timeout=2.0)
            resp = client.diagnostic_session_control(0x03)
            self.assertIsNotNone(resp)
            self.assertEqual(resp[0], 0x50)
            self.assertEqual(resp[1], 0x03)
        finally:
            layer.disconnect()

    def test_uds_read_did(self):
        """UdsClient注入DoIP传输层: 读取VIN (F190)"""
        layer = self._make_layer()
        try:
            client = UdsClient(transport_layer=layer, p2_timeout=2.0)
            resp = client.read_data_by_identifier(0xF190)
            self.assertIsNotNone(resp)
            self.assertEqual(resp[0], 0x62)
            self.assertEqual(resp[1], 0xF1)
            self.assertEqual(resp[2], 0x90)
            self.assertIn(b"WF0ABCD1234567890", resp)
        finally:
            layer.disconnect()

    def test_negative_response(self):
        """不支持的DID返回NRC 0x31"""
        layer = self._make_layer()
        try:
            client = UdsClient(transport_layer=layer, p2_timeout=2.0)
            resp = client.read_data_by_identifier(0xFFFF)
            self.assertIsNotNone(resp)
            self.assertEqual(resp[0], 0x7F)
            self.assertEqual(resp[2], 0x31)
        finally:
            layer.disconnect()

    def test_receive_timeout(self):
        """无响应时receive_tp超时返回None"""
        layer = self._make_layer()
        try:
            # 直连传输层发送后丢弃响应，再次接收应超时
            layer.send_tp(b"\x22\xF1\x90")
            layer.receive_tp(timeout=0.5)  # 消费掉响应
            self.assertIsNone(layer.receive_tp(timeout=0.3))
        finally:
            layer.disconnect()

    def test_message_listener(self):
        """报文监听器收到TX/RX通知（CanMessage封装）"""
        layer = self._make_layer()
        events = []
        try:
            layer.add_message_listener(
                lambda d, m: events.append((d, m.data)))
            client = UdsClient(transport_layer=layer, p2_timeout=2.0)
            client.tester_present()
            directions = [d for d, _ in events]
            self.assertIn("TX", directions)
            self.assertIn("RX", directions)
        finally:
            layer.disconnect()

    def test_send_not_connected(self):
        """未连接时send_tp返回False"""
        layer = DoipTransportLayer(target_ip="127.0.0.1", tcp_port=1)
        self.assertFalse(layer.send_tp(b"\x10\x03"))
        self.assertIsNotNone(layer.last_error)


class TestDoipConnectionFailures(unittest.TestCase):
    """连接失败路径"""

    def test_tcp_refused(self):
        """连接不存在的端口失败并携带错误信息"""
        # 绑定后立即释放，确保端口空闲
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        layer = DoipTransportLayer(target_ip="127.0.0.1", tcp_port=port,
                                   timeout=1.0)
        self.assertFalse(layer.connect())
        self.assertIsNotNone(layer.last_error)
        self.assertIn("TCP", layer.last_error)

    def test_activation_timeout(self):
        """连接到不回路由激活响应的服务器: 超时失败"""
        import socket
        import threading
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def _hold():
            try:
                conn, _ = server.accept()
                conn.recv(1024)  # 接受但不响应
                conn.close()
            except OSError:
                pass

        t = threading.Thread(target=_hold, daemon=True)
        t.start()
        try:
            layer = DoipTransportLayer(target_ip="127.0.0.1", tcp_port=port,
                                       timeout=0.5)
            self.assertFalse(layer.connect())
            self.assertIn("路由激活", layer.last_error or "")
        finally:
            server.close()
            t.join(timeout=1.0)


class TestDoipDiscovery(unittest.TestCase):
    """UDP车辆发现"""

    def test_discover_virtual_ecu(self):
        ecu = VirtualDoipEcu(logical_address=0x1234)
        host, _ = ecu.start()
        try:
            nodes = DoipTransportLayer.discover(
                timeout=1.0, broadcast=host, udp_port=ecu.udp_port)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0]["ip"], host)
            self.assertEqual(nodes[0]["logical_addr"], 0x1234)
            self.assertEqual(nodes[0]["vin"], "VIRTUALDOIP-ECU01")
        finally:
            ecu.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
