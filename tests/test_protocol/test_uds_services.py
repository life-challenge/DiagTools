# -*- coding: utf-8 -*-
"""UDS 服务编解码测试（ISO 14229-1）"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

from src.protocol.uds_services import (
    UdsService, ServiceID,
)


class TestUdsServiceEncode(unittest.TestCase):
    """请求编码"""

    def test_session_control(self):
        self.assertEqual(UdsService.encode_diagnostic_session(0x03),
                         bytes([0x10, 0x03]))

    def test_ecu_reset(self):
        self.assertEqual(UdsService.encode_ecu_reset(0x01),
                         bytes([0x11, 0x01]))

    def test_security_seed_subfunction(self):
        # level为实际子功能值（奇数）: 1/9 → 27 01/27 09
        self.assertEqual(UdsService.encode_security_access_request_seed(1),
                         bytes([0x27, 0x01]))
        self.assertEqual(UdsService.encode_security_access_request_seed(9),
                         bytes([0x27, 0x09]))

    def test_security_key_subfunction_pairs(self):
        # 发送密钥子功能 = 请求种子等级+1（奇→偶）
        self.assertEqual(
            UdsService.encode_security_access_send_key(1, b"\xAA\xBB"),
            bytes([0x27, 0x02, 0xAA, 0xBB]))
        self.assertEqual(
            UdsService.encode_security_access_send_key(9, b"\xAA\xBB"),
            bytes([0x27, 0x0A, 0xAA, 0xBB]))

    def test_read_write_did(self):
        self.assertEqual(UdsService.encode_read_did(0xF190),
                         bytes([0x22, 0xF1, 0x90]))
        self.assertEqual(UdsService.encode_write_did(0xF190, b"VIN"),
                         bytes([0x2E, 0xF1, 0x90]) + b"VIN")


class TestUdsServiceClassify(unittest.TestCase):
    """响应分类"""

    def test_positive_response(self):
        self.assertTrue(UdsService.is_positive_response(
            bytes([0x62, 0xF1, 0x90])))
        self.assertFalse(UdsService.is_positive_response(
            bytes([0x7F, 0x22, 0x31])))

    def test_negative_response(self):
        self.assertTrue(UdsService.is_negative_response(
            bytes([0x7F, 0x10, 0x12])))
        self.assertFalse(UdsService.is_negative_response(
            bytes([0x50, 0x03])))

    def test_pending_response(self):
        # NRC 0x78 = requestCorrectlyReceived-ResponsePending
        self.assertTrue(UdsService.is_pending_response(
            bytes([0x7F, 0x31, 0x78])))
        self.assertFalse(UdsService.is_pending_response(
            bytes([0x7F, 0x31, 0x11])))

    def test_service_name(self):
        self.assertIn("DiagnosticSessionControl",
                      UdsService.get_service_name(0x10))
        self.assertIn("Unknown", UdsService.get_service_name(0xFF))


class TestServiceID(unittest.TestCase):

    def test_enum_values(self):
        self.assertEqual(ServiceID.DIAGNOSTIC_SESSION_CONTROL, 0x10)
        self.assertEqual(int(ServiceID.READ_DATA_BY_IDENTIFIER), 0x22)


if __name__ == "__main__":
    unittest.main()
