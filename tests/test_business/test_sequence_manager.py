# -*- coding: utf-8 -*-
"""SequenceManager 序列执行引擎测试（mock UDS客户端，无需硬件）"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..")))

from src.business.sequence_manager import (
    SequenceManager, SequenceStep, UdsSequence)


class MockUdsClient:
    """脚本化mock客户端: 按发送顺序弹出预置响应"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.sent = []

    def send_raw(self, data, **kwargs):
        self.sent.append(bytes(data))
        if self.responses:
            return self.responses.pop(0)
        return None


class SecurityMockClient:
    """安全访问mock: 27 09发种子(1122), 27 0A按XOR 0xA55A校验密钥"""

    def __init__(self, seed=b"\x11\x22", accept_key=True):
        self.sent = []
        self.seed = seed
        self.accept_key = accept_key

    def send_raw(self, data, **kwargs):
        data = bytes(data)
        self.sent.append(data)
        if len(data) == 2 and data[0] == 0x27 and data[1] % 2 == 1:
            return bytes([0x67, data[1]]) + self.seed
        if len(data) >= 2 and data[0] == 0x27 and data[1] % 2 == 0:
            expected = (int.from_bytes(self.seed, "big") ^ 0xA55A).to_bytes(
                len(self.seed), "big")
            if self.accept_key and data[2:] == expected:
                return bytes([0x67, data[1]])
            return bytes([0x7F, 0x27, 0x35])  # invalidKey
        return bytes([0x7F, data[0], 0x11])


def xor_key_generator(level, seed):
    """与SecurityMockClient配对的密钥算法"""
    return (int.from_bytes(seed, "big") ^ 0xA55A).to_bytes(len(seed), "big")


class TestSequenceStepSerialization(unittest.TestCase):

    def test_step_dict_roundtrip_all_fields(self):
        """全字段 to_dict/from_dict 往返一致"""
        step = SequenceStep(
            step_name="写DID", request_data=bytes([0x2E, 0xF1, 0x90]),
            send_count=3, expected_response=bytes([0x6E, 0xF1, 0x90]),
            check_positive=False, delay_before_ms=100, delay_after_ms=50,
            enabled=False, expected_nrc=[0x12, 0x13],
            expect_no_response=False, security_access=True, write_back=True,
            retry_on_timeout=True, tolerated_nrc=[0x22])
        d = step.to_dict()
        self.assertEqual(d["request"], "2e f1 90")
        self.assertEqual(d["expected_response"], "6e f1 90")
        self.assertEqual(d["expected_nrc"], [0x12, 0x13])
        restored = SequenceStep.from_dict(d)
        self.assertEqual(restored.step_name, "写DID")
        self.assertEqual(restored.request_data, step.request_data)
        self.assertEqual(restored.expected_response, step.expected_response)
        self.assertEqual(restored.expected_nrc, [0x12, 0x13])
        self.assertEqual(restored.send_count, 3)
        self.assertFalse(restored.check_positive)
        self.assertFalse(restored.enabled)
        self.assertTrue(restored.security_access)
        self.assertTrue(restored.write_back)
        self.assertTrue(restored.retry_on_timeout)
        self.assertEqual(restored.tolerated_nrc, [0x22])
        self.assertEqual(restored.delay_before_ms, 100)
        self.assertEqual(restored.delay_after_ms, 50)

    def test_step_defaults_from_dict(self):
        """最小字典加载使用默认值"""
        step = SequenceStep.from_dict({"name": "s", "request": "10 03"})
        self.assertEqual(step.request_data, bytes([0x10, 0x03]))
        self.assertTrue(step.check_positive)
        self.assertIsNone(step.expected_nrc)
        self.assertFalse(step.expect_no_response)
        self.assertEqual(step.tolerated_nrc, [])

    def test_sequence_dict_roundtrip(self):
        """序列级 to_dict/from_dict（含前置/清理步骤）"""
        seq = UdsSequence("流程", "描述")
        seq.loop_count = 2
        seq.loop_delay_ms = 10
        seq.stop_on_error = False
        seq.pre_steps = [SequenceStep("前置", bytes([0x10, 0x03]))]
        seq.steps = [SequenceStep("读DID", bytes([0x22, 0xF1, 0x90]))]
        seq.post_steps = [SequenceStep("还原", bytes([0x10, 0x01]))]

        restored = UdsSequence.from_dict(json.loads(json.dumps(seq.to_dict())))
        self.assertEqual(restored.name, "流程")
        self.assertEqual(restored.loop_count, 2)
        self.assertFalse(restored.stop_on_error)
        self.assertEqual(len(restored.pre_steps), 1)
        self.assertEqual(len(restored.steps), 1)
        self.assertEqual(len(restored.post_steps), 1)
        self.assertEqual(restored.pre_steps[0].request_data, bytes([0x10, 0x03]))
        self.assertEqual(restored.post_steps[0].request_data, bytes([0x10, 0x01]))


class TestCheckStepResponse(unittest.TestCase):
    """四种校验模式（协议一致性基础）"""

    def setUp(self):
        self.mgr = SequenceManager()

    def test_default_positive(self):
        step = SequenceStep(request_data=bytes([0x22, 0xF1, 0x90]))
        ok, _ = self.mgr._check_step_response(step, bytes([0x62, 0xF1, 0x90, 0x41]))
        self.assertTrue(ok)
        ok, _ = self.mgr._check_step_response(step, None)
        self.assertFalse(ok)

    def test_default_negative(self):
        step = SequenceStep(request_data=bytes([0x22, 0xF1, 0x90]))
        ok, err = self.mgr._check_step_response(
            step, bytes([0x7F, 0x22, 0x31]))
        self.assertFalse(ok)
        self.assertIn("0x31", err)

    def test_tolerated_nrc(self):
        """容忍NRC: 期望正响应但收到声明的NRC也判通过"""
        step = SequenceStep(request_data=bytes([0x10, 0x02]),
                            tolerated_nrc=[0x22])
        ok, _ = self.mgr._check_step_response(
            step, bytes([0x7F, 0x10, 0x22]))
        self.assertTrue(ok)
        ok, _ = self.mgr._check_step_response(
            step, bytes([0x7F, 0x10, 0x12]))
        self.assertFalse(ok)

    def test_expected_response_prefix(self):
        step = SequenceStep(request_data=bytes([0x22, 0xF1, 0x90]),
                            expected_response=bytes([0x62, 0xF1, 0x90]))
        ok, _ = self.mgr._check_step_response(
            step, bytes([0x62, 0xF1, 0x90, 0x41, 0x42, 0x43]))
        self.assertTrue(ok)  # 前缀命中，多余数据不管
        ok, err = self.mgr._check_step_response(
            step, bytes([0x62, 0xF1, 0x91]))
        self.assertFalse(ok)
        self.assertIn("前缀不符", err)

    def test_expected_nrc(self):
        step = SequenceStep(request_data=bytes([0x22, 0xF1, 0x90]),
                            expected_nrc=[0x12, 0x13])
        ok, _ = self.mgr._check_step_response(
            step, bytes([0x7F, 0x22, 0x13]))
        self.assertTrue(ok)  # 命中列表任一即通过
        ok, _ = self.mgr._check_step_response(
            step, bytes([0x7F, 0x22, 0x31]))
        self.assertFalse(ok)
        ok, _ = self.mgr._check_step_response(step, None)
        self.assertFalse(ok)  # 期望负响应却无响应
        # 正响应也不算通过
        ok, _ = self.mgr._check_step_response(
            step, bytes([0x62, 0xF1, 0x90]))
        self.assertFalse(ok)

    def test_expect_no_response(self):
        step = SequenceStep(request_data=bytes([0x3E, 0x80]),
                            expect_no_response=True)
        ok, _ = self.mgr._check_step_response(step, None)
        self.assertTrue(ok)
        # 抑制位只抑制正响应，负响应仍合规
        ok, _ = self.mgr._check_step_response(
            step, bytes([0x7F, 0x3E, 0x12]))
        self.assertTrue(ok)
        ok, _ = self.mgr._check_step_response(
            step, bytes([0x7E, 0x80]))
        self.assertFalse(ok)  # 收到正响应反而判失败


class TestExecuteSequence(unittest.TestCase):

    def setUp(self):
        self.mgr = SequenceManager()

    def test_success_flow_with_callbacks(self):
        """正向流程: 步骤/完成回调触发，阶段标记为主步骤"""
        client = MockUdsClient([
            bytes([0x50, 0x03, 0x00, 0x32]),
            bytes([0x62, 0xF1, 0x90, 0x41]),
        ])
        seq = UdsSequence("正向")
        seq.steps = [
            SequenceStep("切会话", bytes([0x10, 0x03])),
            SequenceStep("读DID", bytes([0x22, 0xF1, 0x90])),
        ]
        step_cb_results = []
        finish_results = []
        result = self.mgr.execute_sequence(
            seq, client,
            step_callback=step_cb_results.append,
            finish_callback=finish_results.append)
        self.assertTrue(result.success)
        self.assertEqual(len(step_cb_results), 2)
        self.assertEqual(len(finish_results), 1)
        self.assertTrue(all(r.phase == "主步骤" for r in step_cb_results))
        self.assertEqual(client.sent, [bytes([0x10, 0x03]),
                                        bytes([0x22, 0xF1, 0x90])])

    def test_pre_step_failure_skips_main_runs_post(self):
        """前置失败: 跳过主步骤但清理仍执行（Diva setup/teardown语义）"""
        client = MockUdsClient([
            bytes([0x7F, 0x10, 0x7F]),   # 前置扩展会话被拒
            bytes([0x50, 0x01]),          # 清理: 还原默认会话
        ])
        seq = UdsSequence("前置失败")
        seq.pre_steps = [SequenceStep("前置扩展会话", bytes([0x10, 0x03]))]
        seq.steps = [SequenceStep("主步骤", bytes([0x22, 0xF1, 0x90]))]
        seq.post_steps = [SequenceStep("还原", bytes([0x10, 0x01]))]
        result = self.mgr.execute_sequence(seq, client)
        self.assertFalse(result.success)
        self.assertTrue(result.failed_step_name.startswith("前置条件:"))
        # 主步骤未发送，只发了前置+清理
        self.assertEqual(client.sent,
                         [bytes([0x10, 0x03]), bytes([0x10, 0x01])])
        phases = [r.phase for r in result.step_results]
        self.assertEqual(phases, ["前置条件", "清理"])

    def test_post_step_failure_does_not_fail_case(self):
        """清理步骤失败仅警告，不影响用例判定"""
        client = MockUdsClient([
            bytes([0x62, 0xF1, 0x90, 0x41]),   # 主步骤OK
            None,                              # 清理无响应
        ])
        seq = UdsSequence("清理失败")
        seq.steps = [SequenceStep("读DID", bytes([0x22, 0xF1, 0x90]))]
        seq.post_steps = [SequenceStep("还原", bytes([0x10, 0x01]))]
        result = self.mgr.execute_sequence(seq, client)
        self.assertTrue(result.success)

    def test_stop_on_error(self):
        """stop_on_error=True: 主步骤失败即中止后续主步骤"""
        client = MockUdsClient([
            bytes([0x7F, 0x22, 0x31]),   # 第一步失败
            bytes([0x62, 0xF1, 0x90]),   # 第二步不应发出
        ])
        seq = UdsSequence("中止")
        seq.steps = [
            SequenceStep("第一步", bytes([0x22, 0xF1, 0x90])),
            SequenceStep("第二步", bytes([0x22, 0xF1, 0x91])),
        ]
        result = self.mgr.execute_sequence(seq, client)
        self.assertFalse(result.success)
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(result.failed_step, 0)

    def test_no_stop_on_error(self):
        """stop_on_error=False: 失败后继续执行剩余主步骤"""
        client = MockUdsClient([
            bytes([0x7F, 0x22, 0x31]),
            bytes([0x62, 0xF1, 0x90, 0x41]),
        ])
        seq = UdsSequence("继续")
        seq.stop_on_error = False
        seq.steps = [
            SequenceStep("第一步", bytes([0x22, 0xF1, 0x90])),
            SequenceStep("第二步", bytes([0x22, 0xF1, 0x91])),
        ]
        result = self.mgr.execute_sequence(seq, client)
        self.assertFalse(result.success)
        self.assertEqual(len(client.sent), 2)
        self.assertEqual(result.failed_step, 0)

    def test_disabled_step_not_sent(self):
        step = SequenceStep("禁用", bytes([0x10, 0x03]), enabled=False)
        seq = UdsSequence("跳过")
        seq.steps = [step]
        client = MockUdsClient([])
        result = self.mgr.execute_sequence(seq, client)
        self.assertTrue(result.success)
        self.assertEqual(client.sent, [])

    def test_send_count(self):
        """连发模式: 发送次数内每轮都发（即使已成功）"""
        step = SequenceStep("保活", bytes([0x3E, 0x00]), send_count=3)
        seq = UdsSequence("连发")
        seq.steps = [step]
        client = MockUdsClient([bytes([0x7E, 0x00])] * 3)
        result = self.mgr.execute_sequence(seq, client)
        self.assertTrue(result.success)
        self.assertEqual(len(client.sent), 3)

    def test_retry_on_timeout(self):
        """超时重试: 无响应时用剩余次数重发，收到答复即停"""
        step = SequenceStep("切会话", bytes([0x10, 0x03]),
                            send_count=3, retry_on_timeout=True)
        seq = UdsSequence("重试")
        seq.steps = [step]
        client = MockUdsClient([None, bytes([0x50, 0x03])])
        result = self.mgr.execute_sequence(seq, client)
        self.assertTrue(result.success)
        self.assertEqual(len(client.sent), 2)

    def test_retry_on_timeout_stops_on_nrc(self):
        """超时重试: 收到明确NRC后不再重试"""
        step = SequenceStep("切会话", bytes([0x10, 0x03]),
                            send_count=3, retry_on_timeout=True)
        seq = UdsSequence("重试NRC")
        seq.steps = [step]
        client = MockUdsClient([bytes([0x7F, 0x10, 0x22])])
        result = self.mgr.execute_sequence(seq, client)
        self.assertFalse(result.success)
        self.assertEqual(len(client.sent), 1)

    def test_loop_count(self):
        seq = UdsSequence("循环")
        seq.loop_count = 2
        seq.steps = [SequenceStep("读DID", bytes([0x22, 0xF1, 0x90]))]
        client = MockUdsClient([bytes([0x62, 0xF1, 0x90])] * 2)
        result = self.mgr.execute_sequence(seq, client)
        self.assertTrue(result.success)
        self.assertEqual(len(client.sent), 2)

    def test_write_back_mode(self):
        """写回模式: 先读后写回相同数据（请求拼接上一次读取载荷）"""
        payload = b"\xAA\xBB\xCC"
        client = MockUdsClient([
            bytes([0x62, 0xF1, 0x90]) + payload,   # 读取响应
            bytes([0x6E, 0xF1, 0x90]),              # 写回正响应
        ])
        seq = UdsSequence("写回")
        seq.steps = [
            SequenceStep("读DID", bytes([0x22, 0xF1, 0x90])),
            SequenceStep("写回", bytes([0x2E, 0xF1, 0x90]), write_back=True),
        ]
        result = self.mgr.execute_sequence(seq, client)
        self.assertTrue(result.success)
        self.assertEqual(client.sent[1],
                         bytes([0x2E, 0xF1, 0x90]) + payload)

    def test_write_back_without_read_fails(self):
        """写回模式无读取数据: 不发送直接判失败"""
        client = MockUdsClient([])
        seq = UdsSequence("写回失败")
        seq.steps = [SequenceStep("写回", bytes([0x2E, 0xF1, 0x90]),
                                  write_back=True)]
        result = self.mgr.execute_sequence(seq, client)
        self.assertFalse(result.success)
        self.assertEqual(client.sent, [])
        self.assertIn("写回失败", result.step_results[0].error_message)


class TestSecurityAccess(unittest.TestCase):

    def setUp(self):
        self.mgr = SequenceManager()

    def test_auto_security_access_flow(self):
        """裸发 27 09 自动走全流程: 请求种子→算法算钥→发密钥"""
        client = SecurityMockClient()
        self.mgr.set_key_generator(xor_key_generator)
        seq = UdsSequence("安全访问")
        seq.steps = [SequenceStep("解锁", bytes([0x27, 0x09]))]
        result = self.mgr.execute_sequence(seq, client)
        self.assertTrue(result.success)
        self.assertEqual(len(client.sent), 2)
        self.assertEqual(client.sent[0], bytes([0x27, 0x09]))
        self.assertEqual(client.sent[1][:2], bytes([0x27, 0x0A]))
        expected_key = (0x1122 ^ 0xA55A).to_bytes(2, "big")
        self.assertEqual(client.sent[1][2:], expected_key)

    def test_security_access_even_level_falls_back(self):
        """偶数等级请求回退到奇数等级发种子"""
        client = SecurityMockClient()
        self.mgr.set_key_generator(xor_key_generator)
        seq = UdsSequence("偶数等级")
        seq.steps = [SequenceStep("解锁", bytes([0x27, 0x0A]),
                                  security_access=True)]
        result = self.mgr.execute_sequence(seq, client)
        self.assertTrue(result.success)
        # 种子请求应发 27 09（奇数）
        self.assertEqual(client.sent[0], bytes([0x27, 0x09]))

    def test_security_access_key_rejected(self):
        client = SecurityMockClient(accept_key=False)
        self.mgr.set_key_generator(xor_key_generator)
        seq = UdsSequence("密钥被拒")
        seq.steps = [SequenceStep("解锁", bytes([0x27, 0x09]))]
        result = self.mgr.execute_sequence(seq, client)
        self.assertFalse(result.success)
        # 步骤结果错误信息附带NRC描述（invalidKey）
        self.assertIn("0x35", result.step_results[0].error_message)

    def test_security_access_without_algorithm(self):
        client = SecurityMockClient()
        seq = UdsSequence("无算法")
        seq.steps = [SequenceStep("解锁", bytes([0x27, 0x09]))]
        result = self.mgr.execute_sequence(seq, client)
        self.assertFalse(result.success)
        self.assertIn("未加载安全算法", result.step_results[0].error_message)

    def test_security_access_with_negative_expectation(self):
        """设置了期望NRC的27请求不自动走全流程（负向用例语义）"""
        client = MockUdsClient([bytes([0x7F, 0x27, 0x7F])])
        seq = UdsSequence("SA-01")
        seq.steps = [SequenceStep("默认会话受限", bytes([0x27, 0x09]),
                                 expected_nrc=[0x7F])]
        result = self.mgr.execute_sequence(seq, client)
        self.assertTrue(result.success)
        self.assertEqual(len(client.sent), 1)  # 只裸发了27 09


class TestManagerPersistence(unittest.TestCase):

    def test_save_and_load_sequence(self):
        mgr = SequenceManager()
        seq = UdsSequence("持久化", "描述")
        seq.steps = [SequenceStep("步骤", bytes([0x10, 0x03]),
                                 expected_nrc=[0x12])]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "seq.json")
            mgr.save_sequence(seq, path)
            self.assertIn("持久化", mgr.list_sequences())

            mgr2 = SequenceManager()
            loaded = mgr2.load_sequence(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.name, "持久化")
            self.assertEqual(loaded.steps[0].request_data, bytes([0x10, 0x03]))
            self.assertEqual(loaded.steps[0].expected_nrc, [0x12])

    def test_load_invalid_file_returns_none(self):
        mgr = SequenceManager()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("not-json")
            self.assertIsNone(mgr.load_sequence(path))
        self.assertEqual(mgr.get_sequence("不存在"), None)


if __name__ == "__main__":
    unittest.main()
