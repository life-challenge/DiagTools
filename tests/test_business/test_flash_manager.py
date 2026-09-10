# -*- coding: utf-8 -*-
"""FlashManager 刷写流程测试（mock UDS客户端 + 文件解析，无需硬件）"""

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

from src.business.flash_manager import (
    FlashManager, FlashFileInfo, FlashConfig, FlashState)


class MockFlashClient:
    """刷写mock客户端: 仅实现send_raw + tx_id，按SID分派响应"""

    def __init__(self):
        self.tx_id = 0x7E0
        self.sent = []          # [(tx_id, data), ...]
        self.fail_sids = set()  # 指定这些SID一律回NRC 0x31
        self._on_send = None    # 每次send_raw后的钩子（取消测试用）

    def send_raw(self, data, **kwargs):
        data = bytes(data)
        self.sent.append((self.tx_id, data))
        if self._on_send:
            self._on_send(data)
        sid = data[0]
        if sid in self.fail_sids:
            return bytes([0x7F, sid, 0x31])
        if sid == 0x10:
            return bytes([0x50, data[1], 0x00, 0x32, 0x01, 0xF4])
        if sid == 0x11:
            return bytes([0x51, data[1]])
        if sid == 0x14:
            return bytes([0x54])
        if sid == 0x28:
            return bytes([0x68, data[1]])
        if sid == 0x2E:
            return bytes([0x6E]) + data[1:3]
        if sid == 0x31:
            return bytes([0x71]) + data[1:]   # 回显请求（含CRC选项）
        if sid == 0x34:
            return bytes([0x74, 0x20, 0x10, 0x00])  # maxBlockLength=0x1000
        if sid == 0x36:
            return bytes([0x76, data[1]])
        if sid == 0x37:
            return bytes([0x77])
        if sid == 0x85:
            return bytes([0xC5, data[1]])
        if sid == 0x27:
            if data[1] % 2 == 1:
                return bytes([0x67, data[1]]) + b"\x11\x22"
            return bytes([0x67, data[1]])     # 接受任意密钥
        return bytes([0x7F, sid, 0x11])

    # --- 高级UDS服务接口（镜像UdsClient，内部均走send_raw） ---

    def diagnostic_session_control(self, session_type):
        return self.send_raw(bytes([0x10, session_type]))

    def ecu_reset(self, reset_type):
        return self.send_raw(bytes([0x11, reset_type]))

    def security_access_request_seed(self, level):
        return self.send_raw(bytes([0x27, level]))

    def security_access_send_key(self, level, key):
        # 镜像UdsClient: 发送密钥用偶数子功能（level+1）
        return self.send_raw(bytes([0x27, level + 1]) + bytes(key))

    def write_data_by_identifier(self, did_id, value):
        return self.send_raw(
            bytes([0x2E]) + did_id.to_bytes(2, "big") + bytes(value))

    def routine_control(self, sub_func, routine_id, data=b""):
        return self.send_raw(
            bytes([0x31, sub_func]) + routine_id.to_bytes(2, "big")
            + bytes(data))

    def request_download(self, addr, size, data_format=0x00):
        return self.send_raw(
            bytes([0x34, data_format, 0x44])
            + addr.to_bytes(4, "big") + size.to_bytes(4, "big"))

    def transfer_data(self, block_seq, data):
        return self.send_raw(bytes([0x36, block_seq & 0xFF]) + bytes(data))

    def request_transfer_exit(self):
        return self.send_raw(bytes([0x37]))

    def clear_dtc(self, group=0xFFFFFF):
        return self.send_raw(bytes([0x14]) + group.to_bytes(3, "big"))


class TestFlashFileInfo(unittest.TestCase):

    def _write(self, tmp, name, content: bytes):
        path = os.path.join(tmp, name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def test_parse_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "app.bin", b"\x01\x02\x03\x04")
            info = FlashFileInfo(path)
            self.assertEqual(info.data, b"\x01\x02\x03\x04")
            self.assertEqual(info.file_size, 4)
            self.assertEqual(info.format, ".bin")
            self.assertIsNone(info.start_address)
            self.assertIsNone(info.address_span)

    def test_parse_s19(self):
        """S1记录: 数据排序拼接 + 地址跨度"""
        with tempfile.TemporaryDirectory() as tmp:
            # 两条S1记录各8字节: 0x0800-0x0808 / 0x0808-0x0810
            # （第二条地址更高却写在文件前面，验证按地址排序拼接）
            path = self._write(tmp, "app.s19", b"""S10B0808AABBCCDDEEFF00119A
S10B080011223344556677889A
S5030004F9
""")
            info = FlashFileInfo(path)
            self.assertEqual(info.start_address, 0x0800)
            self.assertEqual(info.end_address, 0x0810)
            self.assertEqual(info.address_span, 0x10)
            # 两个记录按地址排序拼接（第二个地址更高却写在前面）
            self.assertEqual(info.data[:4], bytes([0x11, 0x22, 0x33, 0x44]))
            self.assertEqual(info.data[8:12], bytes([0xAA, 0xBB, 0xCC, 0xDD]))
            self.assertEqual(info.file_size, 16)

    def test_parse_hex_with_extended_linear_address(self):
        """Intel HEX: 类型04扩展线性地址 + 类型00数据记录"""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "app.hex", b""":020000041000EA
:04080000DEADBEEFC3
:0408040011223344C5
:00000001FF
""")
            info = FlashFileInfo(path)
            self.assertEqual(info.data, bytes([0xDE, 0xAD, 0xBE, 0xEF,
                                                0x11, 0x22, 0x33, 0x44]))
            self.assertEqual(info.start_address, 0x10000800)
            self.assertEqual(info.end_address, 0x10000808)
            self.assertEqual(info.address_span, 8)

    def test_unsupported_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "app.elf", b"\x00")
            with self.assertRaises(ValueError):
                FlashFileInfo(path)


class TestStepsGeneration(unittest.TestCase):

    def test_default_full_sequence(self):
        """默认配置: 量产完整流程顺序"""
        keys = [k for k, _, _ in FlashManager.steps(FlashConfig())]
        self.assertEqual(keys, [
            "precheck",
            "prep_ext_session", "preprog_check", "dtc_off", "comm_disable",
            "session", "security", "fingerprint",
            "erase", "download", "transfer", "transfer_exit",
            "check_integrity", "check_dependency", "reset",
            "post_ext_session", "post_comm_enable", "post_dtc_on",
            "post_default_session", "post_clear_dtc",
            "result",
        ])

    def test_minimal_sequence(self):
        """全关可选步骤: 仅保留主体刷写"""
        cfg = FlashConfig()
        cfg.wake_enabled = False
        cfg.prep_ext_session = False
        cfg.preprog_check = False
        cfg.dtc_off = False
        cfg.comm_disable = False
        cfg.enter_programming = False
        cfg.security_level = 0
        cfg.write_fingerprint = False
        cfg.check_integrity = False
        cfg.check_dependency = False
        cfg.reset_after_flash = False
        cfg.post_ext_session = False
        cfg.post_comm_enable = False
        cfg.post_dtc_on = False
        cfg.post_default_session = False
        cfg.post_clear_dtc = False
        keys = [k for k, _, _ in FlashManager.steps(cfg)]
        self.assertEqual(keys, [
            "precheck", "erase", "download", "transfer", "transfer_exit",
            "result"])

    def test_wake_inserted_after_precheck(self):
        cfg = FlashConfig()
        cfg.wake_enabled = True
        keys = [k for k, _, _ in FlashManager.steps(cfg)]
        self.assertEqual(keys[:3], ["precheck", "wake", "prep_ext_session"])


class TestFlashManagerFlow(unittest.TestCase):
    """端到端刷写流程（mock客户端，无延时）"""

    def setUp(self):
        self.client = MockFlashClient()
        self.mgr = FlashManager(self.client)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app_data = bytes(range(256)) * 4   # 1024字节，8块×128
        self.config = self._make_config()

    def _make_config(self, name="app.bin"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(self.app_data)
        cfg = FlashConfig()
        cfg.file_path = path
        cfg.block_size = 128
        cfg.erase_size = 0            # 用文件大小
        cfg.security_level = 9
        cfg.key_generator = lambda level, seed: b"\x01\x02"
        cfg.prog_session_delay = 0    # 测试不等跳转稳定
        cfg.reset_delay = 0
        cfg.fingerprint_data = b"20260909"
        return cfg

    def _run(self, config=None):
        self.mgr.start_flash(config or self.config)
        self.mgr._thread.join(timeout=30)
        self.assertFalse(self.mgr.is_running)
        return self.mgr.state

    def _requests(self):
        return [data for _, data in self.client.sent]

    def test_full_flash_success(self):
        state = self._run()
        self.assertEqual(state, FlashState.COMPLETED)
        self.assertEqual(self.mgr.progress.total_bytes, len(self.app_data))
        self.assertEqual(self.mgr.progress.transferred_bytes, len(self.app_data))
        self.assertEqual(self.mgr.progress.total_blocks, 8)

        reqs = self._requests()
        # 刷前准备（功能寻址: tx_id切换到0x7DF）
        functional_reqs = [d for tx, d in self.client.sent if tx == 0x7DF]
        self.assertIn(bytes([0x10, 0x03]), functional_reqs)
        self.assertIn(bytes([0x85, 0x02]), functional_reqs)
        self.assertIn(bytes([0x28, 0x03, 0x03]), functional_reqs)
        # 功能寻址后tx_id恢复物理地址
        self.assertEqual(self.client.sent[-1][0], 0x7E0)
        # 预编程条件检查 + 编程会话 + 安全访问 + 指纹
        self.assertIn(bytes([0x31, 0x01, 0x02, 0x03]), reqs)
        self.assertIn(bytes([0x10, 0x02]), reqs)
        self.assertIn(bytes([0x27, 0x09]), reqs)
        self.assertIn(bytes([0x27, 0x0A, 0x01, 0x02]), reqs)
        self.assertIn(bytes([0x2E, 0xF1, 0x5A]) + b"20260909", reqs)
        # 擦除: 31 01 FF00 44 + 地址4B + 大小4B（1024向上取整到0x40000）
        erase_req = bytes([0x31, 0x01, 0xFF, 0x00, 0x44]) \
            + (0x08000000).to_bytes(4, "big") + (0x40000).to_bytes(4, "big")
        self.assertIn(erase_req, reqs)
        # 数据传输: 块序号1~8
        self.assertIn(bytes([0x36, 0x01]) + self.app_data[:128], reqs)
        self.assertIn(bytes([0x36, 0x08]) + self.app_data[128 * 7:], reqs)
        # 传输退出 + 校验 + 复位 + 刷后恢复
        self.assertIn(bytes([0x37]), reqs)
        self.assertIn(bytes([0x11, 0x01]), reqs)
        self.assertIn(bytes([0x14, 0xFF, 0xFF, 0xFF]), reqs)

    def test_integrity_check_carries_crc32(self):
        """完整性校验例程选项携带本地CRC32，且响应回带一致判通过"""
        from src.utils.crc32 import crc32
        state = self._run()
        self.assertEqual(state, FlashState.COMPLETED)
        local_crc = crc32(self.app_data).to_bytes(4, "big")
        self.assertIn(bytes([0x31, 0x01, 0x02, 0x02]) + local_crc,
                      self._requests())

    def test_step_failure_sets_failed_state(self):
        """擦除例程被拒 → FAILED，后续传输不再执行"""
        self.client.fail_sids = {0x36}   # 传输数据失败
        state = self._run()
        self.assertEqual(state, FlashState.FAILED)
        self.assertIn("失败", self.mgr.progress.error_message)
        self.assertEqual(self.mgr.progress.transferred_bytes, 0)

    def test_cancel_during_transfer(self):
        """取消: 块传输检查点退出并置CANCELLED终态"""
        got_download = threading.Event()
        release = threading.Event()

        def on_send(data):
            if data[0] == 0x34:
                got_download.set()
                # 阻塞在请求下载处，期间主线程请求取消
                release.wait(timeout=10)

        self.client._on_send = on_send
        self.mgr.start_flash(self.config)
        self.assertTrue(got_download.wait(timeout=10),
                        "未到达请求下载步骤")
        self.mgr.stop_flash()
        release.set()
        self.mgr._thread.join(timeout=30)
        self.assertEqual(self.mgr.state, FlashState.CANCELLED)

    def test_security_retry_on_session_nrc(self):
        """请求种子被拒(可重试NRC)后重试成功"""
        retry_state = {"rejected": False}

        orig = self.client.send_raw

        def send_raw(data, **kwargs):
            data = bytes(data)
            if data[:2] == bytes([0x27, 0x09]) and not retry_state["rejected"]:
                retry_state["rejected"] = True
                self.client.sent.append((self.client.tx_id, data))
                return bytes([0x7F, 0x27, 0x22])   # conditionsNotCorrect
            return orig(data, **kwargs)

        self.client.send_raw = send_raw
        state = self._run()
        self.assertEqual(state, FlashState.COMPLETED)

    def test_security_without_key_generator_fails(self):
        self.config.key_generator = None
        state = self._run()
        self.assertEqual(state, FlashState.FAILED)

    def test_fingerprint_missing_data_fails(self):
        self.config.fingerprint_data = b""
        state = self._run()
        self.assertEqual(state, FlashState.FAILED)
        self.assertIn("指纹", self.mgr.progress.error_message)

    def test_precheck_empty_file_fails(self):
        cfg = self._make_config("empty.bin")
        with open(cfg.file_path, "wb") as f:
            f.write(b"")
        state = self._run(cfg)
        self.assertEqual(state, FlashState.FAILED)

    def test_block_size_respects_ecu_limit(self):
        """ECU声明maxBlockLength=0x1000时，数据块= min(配置, 0x1000-2)"""
        state = self._run()
        self.assertEqual(state, FlashState.COMPLETED)
        # 配置块128 < 4094，仍按128传输
        transfer_reqs = [d for d in self._requests() if d[0] == 0x36]
        self.assertEqual(len(transfer_reqs), 8)
        for d in transfer_reqs:
            self.assertLessEqual(len(d), 0x1000)


if __name__ == "__main__":
    unittest.main()
