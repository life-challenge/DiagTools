# -*- coding: utf-8 -*-
"""报文重放面板文件解析测试（ASC/BLF/CSV/HEX，静态方法无需Qt应用实例）

ASC/BLF用python-can读写器生成——与trace_exporter导出同源，
验证"导出的文件可直接回放"的闭环。
"""

import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

import can

from src.ui.panels.replay_panel import ReplayPanel


def _sample_frames():
    """三帧样例: 间隔0.5s/0.7s，含标准ID与扩展ID"""
    t0 = 1000.0
    return [
        can.Message(timestamp=t0, arbitration_id=0x714,
                    data=b"\x02\x10\x03", is_extended_id=False),
        can.Message(timestamp=t0 + 0.5, arbitration_id=0x7E8,
                    data=bytes(range(8)), is_extended_id=False),
        can.Message(timestamp=t0 + 1.2, arbitration_id=0x18FF10E0,
                    data=b"\x03\x22\xf1\x90", is_extended_id=True),
    ]


class TestParseStandardFormats(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _path(self, name):
        return os.path.join(self.tmp.name, name)

    # ---------------- ASC/BLF（python-can读写器） ----------------

    def test_parse_asc_roundtrip(self):
        """ASC: 写入→读回帧ID/数据/相对时序一致"""
        path = self._path("trace.asc")
        with can.ASCWriter(path) as writer:
            for msg in _sample_frames():
                writer.on_message_received(msg)
        frames = ReplayPanel._parse_asc(path)
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0][0], 0x714)
        self.assertEqual(frames[0][1], b"\x02\x10\x03")
        self.assertEqual(frames[2][0], 0x18FF10E0)
        self.assertEqual(frames[2][1], b"\x03\x22\xf1\x90")
        # 相对时序按首帧归零
        self.assertAlmostEqual(frames[0][2], 0.0, places=3)
        self.assertAlmostEqual(frames[1][2], 0.5, places=3)
        self.assertAlmostEqual(frames[2][2], 1.2, places=3)

    def test_parse_blf_roundtrip(self):
        """BLF二进制: 写入→读回帧ID/数据/相对时序一致"""
        path = self._path("trace.blf")
        with can.BLFWriter(path) as writer:
            for msg in _sample_frames():
                writer.on_message_received(msg)
        frames = ReplayPanel._parse_blf(path)
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0][0], 0x714)
        self.assertEqual(frames[2][1], b"\x03\x22\xf1\x90")
        self.assertAlmostEqual(frames[1][2], 0.5, places=3)
        self.assertAlmostEqual(frames[2][2], 1.2, places=3)

    # ---------------- CSV/HEX（原有格式回归） ----------------

    def test_parse_csv(self):
        """CSV: LogWidget导出格式 时间,方向,CAN ID,数据,描述"""
        path = self._path("trace.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["时间", "方向", "CAN ID", "数据", "描述"])
            writer.writerow(["10:00:00.000", "TX", "0x714",
                             "02 10 03", "请求默认会话"])
            writer.writerow(["10:00:00.500", "RX", "0x7E8",
                             "50 03 00 32 01 F4", "正响应"])
        frames = ReplayPanel._parse_csv(path)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0], (0x714, b"\x02\x10\x03", 0.0))
        self.assertEqual(frames[1][0], 0x7E8)
        self.assertAlmostEqual(frames[1][2], 0.5, places=3)

    def test_parse_hex_lines(self):
        """HEX行: '714 02 10 03' 与 '0x714: 02 10 03' 两种写法"""
        path = self._path("frames.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("714 02 10 03\n")
            f.write("0x7E8: 50 03 00 32 01 F4\n")
            f.write("\n")          # 空行跳过
            f.write("not a frame\n")   # 非法行跳过
        frames = ReplayPanel._parse_hex_lines(path)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0], (0x714, b"\x02\x10\x03", 0.0))
        self.assertEqual(frames[1][0], 0x7E8)

    def test_parse_asc_garbage_yields_no_frames(self):
        """非法ASC内容: python-can静默跳过→返回空列表（_load_file提示无有效帧）"""
        path = self._path("bad.asc")
        with open(path, "w", encoding="utf-8") as f:
            f.write("this is not an asc file\n")
        self.assertEqual(ReplayPanel._parse_asc(path), [])


if __name__ == "__main__":
    unittest.main()
