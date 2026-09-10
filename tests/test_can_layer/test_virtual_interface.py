# -*- coding: utf-8 -*-
"""VirtualCanInterface / VirtualEcuSimulator 测试（内存队列，无需硬件）"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

from src.models.can_message import CanMessage
from src.can_layer.virtual_interface import (
    VirtualCanInterface, VirtualEcuSimulator)


def sf_frame(data: bytes, can_id: int = 0x7E0) -> CanMessage:
    """UDS数据包装为ISO-TP单帧CanMessage"""
    return CanMessage(can_id=can_id,
                       data=bytes([len(data)]) + data + bytes(7 - len(data)),
                       dlc=8)


def collect_response_frames(iface, timeout=2.0):
    """收取iface接收队列中的全部响应帧"""
    frames = []
    while True:
        msg = iface.receive(timeout=timeout)
        if msg is None:
            break
        frames.append(msg)
        timeout = 0.1   # 后续帧已在队列，短超时
    return frames


class TestVirtualCanInterface(unittest.TestCase):

    def setUp(self):
        self.iface = VirtualCanInterface()
        self.assertTrue(self.iface.connect({}))

    def tearDown(self):
        self.iface.disconnect()

    def test_connect_disconnect(self):
        self.assertTrue(self.iface.is_connected)
        self.assertEqual(self.iface.interface_name, "Virtual")
        self.assertIn("Virtual CAN", self.iface.channel_info)
        self.iface.disconnect()
        self.assertFalse(self.iface.is_connected)

    def test_connect_with_custom_ids(self):
        iface = VirtualCanInterface()
        self.assertTrue(iface.connect({"req_id": 0x710, "resp_id": 0x77A}))
        try:
            self.assertTrue(iface.send(sf_frame(bytes([0x3E, 0x00]),
                                                can_id=0x710)))
            msg = iface.receive(timeout=2.0)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.can_id, 0x77A)
        finally:
            iface.disconnect()

    def test_send_disconnected_returns_false(self):
        self.iface.disconnect()
        msg = sf_frame(bytes([0x3E, 0x00]))
        self.assertFalse(self.iface.send(msg))

    def test_single_frame_roundtrip(self):
        """单帧: 3E 00 → 7E 00"""
        self.assertTrue(self.iface.send(sf_frame(bytes([0x3E, 0x00]))))
        msg = self.iface.receive(timeout=2.0)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.can_id, 0x7E8)
        self.assertEqual(msg.data[0] & 0xF0, 0x00)   # 单帧PCI
        self.assertEqual(msg.data[1], 0x7E)
        self.assertEqual(msg.data[2], 0x00)

    def test_multi_frame_response(self):
        """多帧响应: 读VIN(F190) → FF+CF重组"""
        self.assertTrue(self.iface.send(sf_frame(bytes([0x22, 0xF1, 0x90]))))
        frames = collect_response_frames(self.iface)
        self.assertGreaterEqual(len(frames), 3)   # FF + 2×CF
        self.assertEqual(frames[0].data[0] & 0xF0, 0x10)   # 首帧
        total = ((frames[0].data[0] & 0x0F) << 8) | frames[0].data[1]
        self.assertEqual(total, 3 + 17)   # 62 F1 90 + 17字节VIN
        data = frames[0].data[2:8]
        seq = 1
        for cf in frames[1:]:
            self.assertEqual(cf.data[0], 0x20 | (seq & 0x0F))
            data += cf.data[1:8]
            seq += 1
        self.assertEqual(data[:3], bytes([0x62, 0xF1, 0x90]))
        self.assertEqual(len(data), total)

    def test_multi_frame_request_reassembly(self):
        """多帧请求: 写DID数据>7字节 → 首帧回流控、连续帧按序重组"""
        payload = bytes([0x2E, 0xF1, 0x90]) + bytes(range(8))   # 11字节请求
        # 首帧（携带前6字节）
        ff = CanMessage(can_id=0x7E0,
                        data=bytes([0x10, len(payload)]) + payload[:6]
                        + bytes(8 - 2 - 6),
                        dlc=8)
        self.assertTrue(self.iface.send(ff))
        fc = self.iface.receive(timeout=2.0)
        self.assertIsNotNone(fc)
        self.assertEqual(fc.data[0] & 0xF0, 0x30)   # 流控帧
        self.assertEqual(fc.data[1], 0x00)            # BS=0 不限块数
        # 连续帧（携带剩余5字节，帧尾3字节填充）
        cf1 = CanMessage(can_id=0x7E0,
                         data=bytes([0x21]) + payload[6:] + bytes(3), dlc=8)
        self.assertTrue(self.iface.send(cf1))
        # 重组完成 → 模拟器写DID正响应（单帧）
        resp = self.iface.receive(timeout=2.0)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.data[0], 0x03)            # 单帧PCI，长度3
        self.assertEqual(resp.data[1], 0x6E)
        self.assertEqual(resp.data[2:4], bytes([0xF1, 0x90]))
        # 回读验证写入生效（11字节响应 → 多帧）
        self.assertTrue(self.iface.send(sf_frame(bytes([0x22, 0xF1, 0x90]))))
        frames = collect_response_frames(self.iface)
        total = ((frames[0].data[0] & 0x0F) << 8) | frames[0].data[1]
        self.assertEqual(total, 11)
        data = frames[0].data[2:8]
        seq = 1
        for cf in frames[1:]:
            self.assertEqual(cf.data[0], 0x20 | (seq & 0x0F))
            data += cf.data[1:8]
            seq += 1
        self.assertEqual(data[:3], bytes([0x62, 0xF1, 0x90]))
        # 帧尾填充字节不计入：只比较总长度内的数据
        self.assertEqual(data[:total][3:], bytes(range(8)))

    def test_functional_addressing(self):
        """功能寻址(0x7DF)请求同样得到响应"""
        self.assertTrue(self.iface.send(
            sf_frame(bytes([0x3E, 0x00]), can_id=0x7DF)))
        msg = self.iface.receive(timeout=2.0)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.data[1], 0x7E)

    def test_flow_control_frame_no_response(self):
        """流控帧(0x3x)不是UDS请求，不触发ECU响应"""
        fc = CanMessage(can_id=0x7E0,
                        data=bytes([0x30, 0x00, 0x00, 0, 0, 0, 0, 0]), dlc=8)
        self.assertTrue(self.iface.send(fc))
        self.assertIsNone(self.iface.receive(timeout=0.3))

    def test_message_listener_notified(self):
        events = []
        self.iface.add_message_listener(
            lambda d, m: events.append((d, m.can_id)))
        self.iface.send(sf_frame(bytes([0x3E, 0x00])))
        self.iface.receive(timeout=2.0)
        directions = [d for d, _ in events]
        self.assertIn("TX", directions)
        self.assertIn("RX", directions)
        self.iface.remove_message_listener(
            lambda d, m: events.append((d, m.can_id)))

    def test_get_stats(self):
        self.iface.send(sf_frame(bytes([0x3E, 0x00])))
        stats = self.iface.get_stats()
        self.assertEqual(stats["tx_count"], 1)
        self.assertEqual(stats["interface"], "Virtual")


class TestVirtualEcuSimulator(unittest.TestCase):

    def setUp(self):
        self.ecu = VirtualEcuSimulator()

    def test_empty_request(self):
        self.assertEqual(self.ecu.process_request(b""),
                         bytes([0x7F, 0x00, 0x13]))

    def test_unsupported_service(self):
        self.assertEqual(self.ecu.process_request(bytes([0xAA])),
                         bytes([0x7F, 0xAA, 0x11]))

    def test_diagnostic_session(self):
        resp = self.ecu.process_request(bytes([0x10, 0x03]))
        self.assertEqual(resp[0], 0x50)
        self.assertEqual(resp[1], 0x03)

    def test_ecu_reset_restores_defaults(self):
        self.ecu.process_request(bytes([0x10, 0x03]))
        self.ecu.process_request(bytes([0x27, 0x01]))
        resp = self.ecu.process_request(bytes([0x11, 0x01]))
        self.assertEqual(resp, bytes([0x51, 0x01]))
        self.assertEqual(self.ecu._session, 0x01)
        self.assertFalse(self.ecu._security_unlocked)

    def test_read_did_valid_and_invalid(self):
        resp = self.ecu.process_request(bytes([0x22, 0xF1, 0x90]))
        self.assertEqual(resp[:3], bytes([0x62, 0xF1, 0x90]))
        # 无效DID → requestOutOfRange
        resp = self.ecu.process_request(bytes([0x22, 0xAB, 0xCD]))
        self.assertEqual(resp, bytes([0x7F, 0x22, 0x31]))
        # 长度错误
        resp = self.ecu.process_request(bytes([0x22, 0xF1]))
        self.assertEqual(resp, bytes([0x7F, 0x22, 0x13]))

    def test_write_did_then_read_back(self):
        resp = self.ecu.process_request(
            bytes([0x2E, 0x01, 0x10]) + b"\x01\x02")
        self.assertEqual(resp, bytes([0x6E, 0x01, 0x10]))
        resp = self.ecu.process_request(bytes([0x22, 0x01, 0x10]))
        self.assertEqual(resp, bytes([0x62, 0x01, 0x10, 0x01, 0x02]))

    def test_dtc_count_and_clear(self):
        resp = self.ecu.process_request(bytes([0x19, 0x01]))
        self.assertEqual(resp[0], 0x59)
        confirmed = sum(1 for d in self.ecu._dtc_list if d["status"] & 0x08)
        self.assertEqual(resp[3], confirmed)
        # 按状态掩码读取
        resp = self.ecu.process_request(bytes([0x19, 0x02, 0x08]))
        self.assertEqual(resp[0], 0x59)
        self.assertEqual(resp[1], 0x02)
        # 清除后状态归零
        self.assertEqual(self.ecu.process_request(
            bytes([0x14, 0xFF, 0xFF, 0xFF])), bytes([0x54]))
        self.assertTrue(all(d["status"] == 0 for d in self.ecu._dtc_list))

    def test_dtc_unsupported_subfunction(self):
        self.assertEqual(self.ecu.process_request(bytes([0x19, 0x7F])),
                         bytes([0x7F, 0x19, 0x12]))

    def test_security_access_full_flow(self):
        """请求种子→XOR 0xA55A算钥→发密钥解锁"""
        seed_resp = self.ecu.process_request(bytes([0x27, 0x09]))
        self.assertEqual(seed_resp[:2], bytes([0x67, 0x09]))
        seed = seed_resp[2:]
        key = (int.from_bytes(seed, "big") ^ 0xA55A).to_bytes(
            len(seed), "big")
        resp = self.ecu.process_request(bytes([0x27, 0x0A]) + key)
        self.assertEqual(resp, bytes([0x67, 0x0A]))
        self.assertTrue(self.ecu._security_unlocked)
        self.assertEqual(self.ecu._security_level, 0x0A)

    def test_security_access_invalid_key(self):
        seed_resp = self.ecu.process_request(bytes([0x27, 0x09]))
        seed = seed_resp[2:]
        wrong = bytes(~b & 0xFF for b in seed)
        resp = self.ecu.process_request(bytes([0x27, 0x0A]) + wrong)
        self.assertEqual(resp, bytes([0x7F, 0x27, 0x35]))
        self.assertFalse(self.ecu._security_unlocked)

    def test_tester_present_with_suppress_bit(self):
        """抑制正响应位(0x80): ECU不回复（空响应）"""
        self.assertEqual(self.ecu.process_request(bytes([0x3E, 0x80])), b"")
        resp = self.ecu.process_request(bytes([0x3E, 0x00]))
        self.assertEqual(resp, bytes([0x7E, 0x00]))

    def test_routine_control(self):
        resp = self.ecu.process_request(bytes([0x31, 0x01, 0x02, 0x03]))
        self.assertEqual(resp, bytes([0x71, 0x01, 0x02, 0x03]))
        # 长度不足 → incorrectMessageLength
        self.assertEqual(self.ecu.process_request(bytes([0x31, 0x01, 0x02])),
                         bytes([0x7F, 0x31, 0x13]))

    def test_download_transfer_exit(self):
        resp = self.ecu.process_request(
            bytes([0x34, 0x00, 0x44, 0x08, 0x00, 0x00, 0x00, 0x00, 0x10, 0x00]))
        self.assertEqual(resp, bytes([0x74, 0x20, 0x10, 0x00]))
        resp = self.ecu.process_request(bytes([0x36, 0x01]) + b"\xAA" * 4)
        self.assertEqual(resp, bytes([0x76, 0x01]))
        self.assertEqual(self.ecu.process_request(bytes([0x37])),
                         bytes([0x77]))

    def test_communication_control_and_dtc_setting(self):
        self.assertEqual(self.ecu.process_request(bytes([0x28, 0x03, 0x03])),
                         bytes([0x68, 0x03]))
        self.assertEqual(self.ecu.process_request(bytes([0x85, 0x02])),
                         bytes([0xC5, 0x02]))
        self.assertFalse(self.ecu._dtc_settings_enabled)


if __name__ == "__main__":
    unittest.main()
