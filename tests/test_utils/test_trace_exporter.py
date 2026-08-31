"""Trace标准格式导出单元测试

覆盖: ASC文本结构、BLF回读一致性、pcap以太网帧重建（含DoIP头/IPv4校验和）、
export_entries按扩展名分发。
"""

import os
import sys
import struct
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.utils.trace_exporter import (
    write_asc, write_blf, write_pcap, export_entries, _ipv4_checksum,
)


class _Entry:
    """测试用LogEntry替身"""

    def __init__(self, timestamp, direction, can_id, data, description=""):
        self.timestamp = timestamp
        self.direction = direction
        self.can_id = can_id
        self.data = data
        self.description = description

    @property
    def time_str(self):
        return "00:00:00.000"


ENTRIES = [
    _Entry(1725000000.0, "TX", 0x7E0, bytes([0x02, 0x10, 0x03]), "会话控制"),
    _Entry(1725000000.1, "RX", 0x7E8, bytes([0x06, 0x50, 0x03, 0x00, 0x32, 0x01, 0xF4]), ""),
    _Entry(1725000000.2, "TX", 0x7E0, bytes([0x03, 0x22, 0xF1, 0x90]), "读VIN"),
]


class TestAscExport(unittest.TestCase):
    """ASC格式导出"""

    def test_asc_structure(self):
        with tempfile.NamedTemporaryFile(suffix=".asc", delete=False) as f:
            path = f.name
        try:
            write_asc(ENTRIES, path)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # ASC头部与帧行关键要素（ID为大写hex，头部关键字间隔为双空格）
            self.assertIn("base hex", content)
            self.assertIn("timestamps absolute", content)
            self.assertIn("7E0", content)
            self.assertIn("Tx", content)       # 方向
            self.assertIn("d 3 02 10 03", content)  # DLC + 数据
        finally:
            os.unlink(path)


class TestBlfExport(unittest.TestCase):
    """BLF格式导出与回读"""

    def test_blf_roundtrip(self):
        with tempfile.NamedTemporaryFile(suffix=".blf", delete=False) as f:
            path = f.name
        try:
            write_blf(ENTRIES, path)
            # 用python-can BLFReader回读，验证文件合法性
            from can.io import BLFReader
            msgs = list(BLFReader(path))
            self.assertEqual(len(msgs), 3)
            self.assertEqual(msgs[0].arbitration_id, 0x7E0)
            self.assertEqual(bytes(msgs[0].data), bytes([0x02, 0x10, 0x03]))
            self.assertFalse(msgs[0].is_rx)   # TX
            self.assertTrue(msgs[1].is_rx)    # RX
            self.assertEqual(msgs[2].arbitration_id, 0x7E0)
        finally:
            os.unlink(path)


class TestPcapExport(unittest.TestCase):
    """pcap格式导出（DoIP以太网帧重建）"""

    def _parse_pcap(self, path):
        with open(path, "rb") as f:
            data = f.read()
        # 全局头
        magic, ver, _, _, _, snaplen, linktype = struct.unpack(
            "<IHHiIII", data[:24])
        self.assertEqual(magic, 0xA1B2C3D4)
        self.assertEqual(linktype, 1)  # LINKTYPE_ETHERNET
        packets = []
        off = 24
        while off < len(data):
            sec, usec, incl, orig = struct.unpack("<IIII", data[off:off + 16])
            off += 16
            packets.append((sec, usec, data[off:off + incl]))
            off += incl
        return packets

    def test_pcap_doip_frames(self):
        with tempfile.NamedTemporaryFile(suffix=".pcap", delete=False) as f:
            path = f.name
        try:
            write_pcap(ENTRIES, path, tester_addr=0x0E80, ecu_addr=0x1000)
            packets = self._parse_pcap(path)
            self.assertEqual(len(packets), 3)

            sec, usec, pkt = packets[0]
            self.assertEqual(sec, 1725000000)
            # 以太网: 目的MAC(6) + 源MAC(6) + EtherType(2)
            self.assertEqual(pkt[12:14], b"\x08\x00")  # IPv4
            # IPv4头
            ip = pkt[14:34]
            self.assertEqual(ip[0] >> 4, 4)
            self.assertEqual(ip[9], 17)  # UDP
            total_len = struct.unpack("!H", ip[2:4])[0]
            self.assertEqual(total_len, len(pkt) - 14)
            # IPv4校验和正确性
            self.assertEqual(_ipv4_checksum(ip), 0)
            # UDP
            udp = pkt[34:42]
            sport, dport, ulen, _ = struct.unpack("!HHHH", udp)
            self.assertEqual(sport, 13400)
            self.assertEqual(dport, 13400)
            self.assertEqual(ulen, len(pkt) - 34)
            # DoIP: TX方向 0x8001, Tester->ECU
            doip = pkt[42:50]
            ver, inv, ptype, dlen = struct.unpack("!BBHI", doip)
            self.assertEqual(ver, 0x02)
            self.assertEqual(inv, 0xFD)
            self.assertEqual(ptype, 0x8001)
            src, dst = struct.unpack("!HH", pkt[50:54])
            self.assertEqual(src, 0x0E80)
            self.assertEqual(dst, 0x1000)
            self.assertEqual(pkt[54:], bytes([0x02, 0x10, 0x03]))

            # RX方向: 0x8002, ECU->Tester
            _, _, pkt2 = packets[1]
            ptype2 = struct.unpack("!H", pkt2[44:46])[0]
            self.assertEqual(ptype2, 0x8002)
            src2, dst2 = struct.unpack("!HH", pkt2[50:54])
            self.assertEqual(src2, 0x1000)
            self.assertEqual(dst2, 0x0E80)
        finally:
            os.unlink(path)

    def test_ipv4_checksum_known_vector(self):
        """校验和函数自洽: 计算结果再算一次为0"""
        header = bytes.fromhex("4500001c0001400040110000") + b"\x7f\x00\x00\x01" + b"\x7f\x00\x00\x01"
        cs = _ipv4_checksum(header)
        filled = header[:10] + struct.pack("!H", cs) + header[12:]
        self.assertEqual(_ipv4_checksum(filled), 0)


class TestExportEntries(unittest.TestCase):
    """按扩展名分发"""

    def test_dispatch(self):
        cases = [(".asc", "ASC"), (".blf", "BLF"), (".pcap", "pcap"),
                 (".csv", "CSV"), (".txt", "文本")]
        for ext, kw in cases:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                path = f.name
            try:
                fmt = export_entries(ENTRIES, path, (0x0E80, 0x1000))
                self.assertIn(kw, fmt)
                self.assertGreater(os.path.getsize(path), 0)
            finally:
                os.unlink(path)

    def test_unknown_ext(self):
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            path = f.name
        try:
            self.assertEqual(export_entries(ENTRIES, path), "")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
