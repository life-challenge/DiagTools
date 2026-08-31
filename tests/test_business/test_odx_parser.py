# -*- coding: utf-8 -*-
"""ODX/PDX/CDD 解析测试：J2012 DTC编码、容器、非法文件拒绝"""

import os
import sys
import unittest
import zipfile
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

from src.business.odx_parser import OdxParser, _parse_dtc_id

ODX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ODX MODEL-VERSION="2.2.0" xmlns="http://www.2002.2.20/ODX">
 <SHORT-NAME>UNIT_ODX</SHORT-NAME>
 <DIAG-COMMS>
  <DIAG-SERVICE ID="svc_1">
    <SHORT-NAME>ReadVIN</SHORT-NAME>
    <DESC><P>Read VIN</P></DESC>
    <POS-COMPARAM-SEQ>
      <PARAM><SHORT-NAME>SID</SHORT-NAME><CODED-VALUE>34</CODED-VALUE></PARAM>
      <PARAM><SHORT-NAME>DID</SHORT-NAME><CODED-VALUE>61840</CODED-VALUE></PARAM>
    </POS-COMPARAM-SEQ>
  </DIAG-SERVICE>
 </DIAG-COMMS>
 <DTCS>
  <DTC ID="d1"><SHORT-NAME>P0123</SHORT-NAME><DESC><P>TPS high</P></DESC></DTC>
  <DTC ID="d2"><SHORT-NAME>U0100</SHORT-NAME><DESC><P>Lost comm</P></DESC></DTC>
 </DTCS>
</ODX>
"""


class TestJ2012Encoding(unittest.TestCase):
    """J2012 DTC 编码: 类前缀占bit19-16, 后4位HEX占bit15-0"""

    def test_powertrain(self):
        self.assertEqual(_parse_dtc_id("P0123"), 0x000123)

    def test_network(self):
        self.assertEqual(_parse_dtc_id("U0100"), 0xC0100)

    def test_chassis_body(self):
        self.assertEqual(_parse_dtc_id("C1234"), 0x41234)
        self.assertEqual(_parse_dtc_id("B2345"), 0x82345)

    def test_plain_hex(self):
        self.assertEqual(_parse_dtc_id("00C0FF"), 0x00C0FF)

    def test_invalid(self):
        self.assertIsNone(_parse_dtc_id("XYZ"))


class TestOdxParser(unittest.TestCase):

    def _write_tmp(self, suffix: str, data, mode="w"):
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(path, mode, encoding=None if "b" in mode else "utf-8") as f:
            f.write(data)
        return path

    def test_parse_xml(self):
        path = self._write_tmp(".odx", ODX_XML)
        try:
            db = OdxParser().parse_file(path)
            self.assertIsNotNone(db)
            self.assertEqual(db.project, "UNIT_ODX")
            self.assertEqual(len(db.comms), 1)
            # CODED-VALUE 61840(0xF190) 展开为大端两字节
            self.assertEqual(db.comms[0].request,
                             bytes([0x22, 0xF1, 0x90]))
            self.assertEqual(len(db.dtcs), 2)
            self.assertEqual(db.dtcs[0].dtc_id, 0x000123)
            self.assertEqual(db.dtcs[1].dtc_id, 0xC0100)
        finally:
            os.unlink(path)

    def test_parse_pdx_container(self):
        fd, path = tempfile.mkstemp(suffix=".pdx")
        os.close(fd)
        try:
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("diagnostics/layer.odx", ODX_XML)
            db = OdxParser().parse_file(path)
            self.assertIsNotNone(db)
            self.assertEqual(len(db.dtcs), 2)
        finally:
            os.unlink(path)

    def test_reject_binary_cdd(self):
        path = self._write_tmp(".cdd", b"\xC4\xDD\x00binary", "wb")
        try:
            self.assertIsNone(OdxParser().parse_file(path))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
