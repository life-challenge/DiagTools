# -*- coding: utf-8 -*-
"""特殊功能面板测试：配置驱动解析 + 虚拟CAN端到端执行"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

from src.config.ecu_definition import EcuDefinition, load_ecu_definitions
from src.ui.panels.special_panel import _build_request


class TestBuildRequest(unittest.TestCase):
    """特殊功能定义 -> UDS请求字节"""

    def test_routine(self):
        req = _build_request({"type": "routine", "id": "0x0203"})
        self.assertEqual(req, bytes([0x31, 0x01, 0x02, 0x03]))

    def test_did(self):
        req = _build_request({"type": "did", "id": "F195"})
        self.assertEqual(req, bytes([0x22, 0xF1, 0x95]))

    def test_reset(self):
        req = _build_request({"type": "reset", "reset_type": "01"})
        self.assertEqual(req, bytes([0x11, 0x01]))

    def test_raw(self):
        req = _build_request({"type": "raw", "data": "10 03"})
        self.assertEqual(req, bytes([0x10, 0x03]))

    def test_invalid(self):
        self.assertIsNone(_build_request({"type": "did"}))
        self.assertIsNone(_build_request({"type": "routine"}))
        self.assertIsNone(_build_request({"type": "raw"}))
        self.assertIsNone(_build_request({"type": "raw", "data": ""}))
        self.assertIsNone(_build_request({"type": "unknown"}))
        self.assertIsNone(_build_request({}))
        self.assertIsNone(
            _build_request({"type": "raw", "data": "ZZ"}))


class TestEcuDefinition(unittest.TestCase):
    """配置加载: special_functions 字段"""

    def test_dem_has_special_functions(self):
        defs = load_ecu_definitions()
        dem = [d for d in defs if d.name == "DEM"]
        self.assertTrue(dem, "DEM定义应存在")
        funcs = dem[0].special_functions
        self.assertEqual(len(funcs), 4)
        types = {f["type"] for f in funcs}
        self.assertEqual(types, {"routine", "did", "reset", "raw"})
        # 每条配置都能翻译为合法请求
        for f in funcs:
            self.assertIsNotNone(_build_request(f), f"无效配置: {f}")

    def test_default_empty(self):
        d = EcuDefinition(name="X")
        self.assertEqual(d.special_functions, [])


class TestSpecialPanelGui(unittest.TestCase):
    """GUI: 面板构造/注入/虚拟ECU端到端执行"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_panel_and_execution(self):
        from src.ui.panels.special_panel import SpecialPanel
        from src.can_layer.virtual_interface import VirtualCanInterface
        from src.protocol.uds_client import UdsClient

        iface = VirtualCanInterface()
        self.assertTrue(iface.connect({}))
        client = UdsClient(iface, tx_id=0x7E0, rx_id=0x7E8,
                            p2_timeout=2.0)

        panel = SpecialPanel()
        panel.set_uds_client(client)
        dem = [d for d in load_ecu_definitions() if d.name == "DEM"][0]
        panel.set_ecu(dem)

        self.assertEqual(panel._table.rowCount(), 4)
        # 行2 = 软复位 (0x11 01)，虚拟ECU必回正响应 51 01
        panel._table.selectRow(2)
        panel._execute()
        # pump事件循环直到后台线程完成并回调GUI槽
        deadline = time.time() + 5
        while panel._thread is not None and time.time() < deadline:
            TestSpecialPanelGui.app.processEvents()
            time.sleep(0.05)
        TestSpecialPanelGui.app.processEvents()
        html = panel._result_view.toHtml()
        self.assertIn("正响应", html)
        self.assertIn("51 01", html)
        iface.disconnect()

    def test_no_connection_prompt(self):
        from src.ui.panels.special_panel import SpecialPanel
        panel = SpecialPanel()
        panel.set_uds_client(None)
        dem = [d for d in load_ecu_definitions() if d.name == "DEM"][0]
        panel.set_ecu(dem)
        panel._table.selectRow(0)
        panel._execute()
        self.assertIn("未连接", panel._result_view.toHtml())


if __name__ == "__main__":
    unittest.main()
