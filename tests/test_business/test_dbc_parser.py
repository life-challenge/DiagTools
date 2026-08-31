# -*- coding: utf-8 -*-
"""DBC 解析测试：报文/信号加载 + 大小端/有符号解码"""

import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

from src.business.dbc_parser import DbcParser

DBC_TEXT = """VERSION "1.0"

BS_:

BU_: TESTER ECU

BO_ 100 EngineStatus: 8 ECU
 SG_ EngineSpeed : 0|16@1+ (0.25,0) [0|16383.75] "rpm" TESTER
 SG_ CoolantTemp : 16|8@1+ (1,-40) [-40|215] "degC" TESTER
 SG_ TorqueSigned : 24|8@1- (1,0) [-128|127] "Nm" TESTER

BO_ 200 BigEndianMsg: 8 ECU
 SG_ Voltage : 31|8@0+ (0.1,0) [0|25.5] "V" TESTER
"""


class TestDbcParser(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        fd, cls.dbc_path = tempfile.mkstemp(suffix=".dbc")
        os.close(fd)
        with open(cls.dbc_path, "w", encoding="utf-8") as f:
            f.write(DBC_TEXT)
        cls.parser = DbcParser()
        assert cls.parser.load(cls.dbc_path)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.dbc_path)

    def test_messages_loaded(self):
        self.assertEqual(len(self.parser.messages), 2)
        self.assertEqual(self.parser.signal_count, 4)

    def test_little_endian_scale(self):
        # EngineSpeed = raw*0.25, raw=0x0BB8(3000) -> 750 rpm
        data = bytes([0xB8, 0x0B, 0, 0, 0, 0, 0, 0])
        result = self.parser.decode_frame(100, data)
        self.assertAlmostEqual(result["EngineSpeed"][0], 750.0)
        self.assertEqual(result["EngineSpeed"][1], "rpm")

    def test_offset(self):
        # CoolantTemp = raw-40, raw=100 -> 60 degC
        data = bytes([0, 0, 100, 0, 0, 0, 0, 0])
        result = self.parser.decode_frame(100, data)
        self.assertAlmostEqual(result["CoolantTemp"][0], 60.0)

    def test_signed_twos_complement(self):
        # TorqueSigned raw=0xFE -> -2
        data = bytes([0, 0, 0, 0xFE, 0, 0, 0, 0])
        result = self.parser.decode_frame(100, data)
        self.assertAlmostEqual(result["TorqueSigned"][0], -2.0)

    def test_big_endian(self):
        # Voltage: 31|8@0+ MSB在byte3位7, raw=0x64(100) -> 10.0V
        data = bytes([0, 0, 0, 0x64, 0, 0, 0, 0])
        result = self.parser.decode_frame(200, data)
        self.assertAlmostEqual(result["Voltage"][0], 10.0)

    def test_unknown_id(self):
        self.assertEqual(self.parser.decode_frame(0x7FF, b"\x00" * 8), {})


if __name__ == "__main__":
    unittest.main()
