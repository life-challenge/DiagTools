"""自定义序列执行引擎

用户自定义UDS执行序列，将多个UDS步骤编排为可重复执行的自动化流程。
支持步骤级发送次数和步骤间延时配置。
步骤校验四种模式（协议一致性测试基础，参ISO 14229-1 / Diva）:
  1. expected_response  - 期望响应前缀匹配（正响应+数据前缀）
  2. expected_nrc       - 期望负响应 7F <SID> <NRC∈列表>
  3. expect_no_response - 期望无响应（如 3E 80 抑制正响应）
  4. check_positive     - 默认，要求正响应（首字节=SID+0x40）
"""

import json
import time
import threading
from typing import Optional, Callable
from src.log.log_manager import get_log_manager


class SequenceStep:
    """序列步骤"""

    def __init__(self, step_name: str = "", request_data: bytes = b"",
                 send_count: int = 1, expected_response: bytes = None,
                 check_positive: bool = True, delay_before_ms: int = 0,
                 delay_after_ms: int = 0, enabled: bool = True,
                 expected_nrc: list = None, expect_no_response: bool = False):
        self.step_name = step_name
        self.request_data = request_data
        self.send_count = send_count
        self.expected_response = expected_response
        self.check_positive = check_positive
        self.delay_before_ms = delay_before_ms
        self.delay_after_ms = delay_after_ms
        self.enabled = enabled
        # 协议一致性扩展: 期望负响应NRC列表 / 期望无响应（优先级高于check_positive）
        self.expected_nrc = expected_nrc          # 如 [0x12, 0x13]，任一命中即通过
        self.expect_no_response = expect_no_response

    def to_dict(self) -> dict:
        return {
            "name": self.step_name,
            "request": self.request_data.hex(" ") if self.request_data else "",
            "send_count": self.send_count,
            "expected_response": self.expected_response.hex(" ") if self.expected_response else None,
            "check_positive": self.check_positive,
            "delay_before_ms": self.delay_before_ms,
            "delay_after_ms": self.delay_after_ms,
            "enabled": self.enabled,
            "expected_nrc": self.expected_nrc,
            "expect_no_response": self.expect_no_response,
        }

    @staticmethod
    def from_dict(d: dict) -> 'SequenceStep':
        req_hex = d.get("request", "")
        req_data = bytes.fromhex(req_hex.replace(" ", "")) if req_hex else b""
        exp_hex = d.get("expected_response")
        exp_data = bytes.fromhex(exp_hex.replace(" ", "")) if exp_hex else None
        return SequenceStep(
            step_name=d.get("name", ""),
            request_data=req_data,
            send_count=d.get("send_count", 1),
            expected_response=exp_data,
            check_positive=d.get("check_positive", True),
            delay_before_ms=d.get("delay_before_ms", 0),
            delay_after_ms=d.get("delay_after_ms", 0),
            enabled=d.get("enabled", True),
            expected_nrc=d.get("expected_nrc"),
            expect_no_response=d.get("expect_no_response", False),
        )


class UdsSequence:
    """UDS序列定义"""

    def __init__(self, name: str = "", description: str = ""):
        self.name = name
        self.description = description
        self.steps: list[SequenceStep] = []
        # 前置条件步骤（用例前执行，任一失败则用例判失败并跳过主步骤）
        # 与清理步骤（用例后总执行，如会话还原，失败仅警告不影响判定）——
        # 对应 Diva TestModule 的 setup/teardown 结构
        self.pre_steps: list[SequenceStep] = []
        self.post_steps: list[SequenceStep] = []
        self.loop_count: int = 1
        self.loop_delay_ms: int = 0
        self.stop_on_error: bool = True
        self.auto_tester_present: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "loop_count": self.loop_count,
            "loop_delay_ms": self.loop_delay_ms,
            "stop_on_error": self.stop_on_error,
            "auto_tester_present": self.auto_tester_present,
            "pre_steps": [s.to_dict() for s in self.pre_steps],
            "steps": [s.to_dict() for s in self.steps],
            "post_steps": [s.to_dict() for s in self.post_steps],
        }

    @staticmethod
    def from_dict(d: dict) -> 'UdsSequence':
        seq = UdsSequence(
            name=d.get("name", ""),
            description=d.get("description", ""),
        )
        seq.loop_count = d.get("loop_count", 1)
        seq.loop_delay_ms = d.get("loop_delay_ms", 0)
        seq.stop_on_error = d.get("stop_on_error", True)
        seq.auto_tester_present = d.get("auto_tester_present", False)
        seq.pre_steps = [SequenceStep.from_dict(s) for s in d.get("pre_steps", [])]
        seq.steps = [SequenceStep.from_dict(s) for s in d.get("steps", [])]
        seq.post_steps = [SequenceStep.from_dict(s) for s in d.get("post_steps", [])]
        return seq


class StepResult:
    """步骤执行结果"""

    def __init__(self, step_index: int, step_name: str,
                 request_sent: bytes, response_received: bytes = None,
                 is_positive: bool = False, error_message: str = "",
                 elapsed_ms: float = 0, phase: str = "主步骤"):
        self.step_index = step_index
        self.step_name = step_name
        self.request_sent = request_sent
        self.response_received = response_received
        self.is_positive = is_positive
        self.error_message = error_message
        self.elapsed_ms = elapsed_ms
        self.phase = phase  # 前置条件/主步骤/清理（报告与失败详情区分用）


class SequenceResult:
    """序列执行结果"""

    def __init__(self):
        self.success = False
        self.step_results: list[StepResult] = []
        self.failed_step: Optional[int] = None
        self.failed_step_name: str = ""
        self.total_elapsed_ms: float = 0
        self.error_message: str = ""


class SequenceManager:
    """序列管理器"""

    def __init__(self, sequences_dir: str = "resources/sequences"):
        self._sequences_dir = sequences_dir
        self._sequences: dict[str, UdsSequence] = {}
        self._running = False
        self._stop_event = threading.Event()
        self._logger = get_log_manager().get_sequence_logger()

    @property
    def is_running(self) -> bool:
        return self._running

    def load_sequence(self, filepath: str) -> Optional[UdsSequence]:
        """从JSON文件加载序列"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            seq = UdsSequence.from_dict(data)
            self._sequences[seq.name] = seq
            return seq
        except Exception as e:
            self._logger.error(f"加载序列失败 [{filepath}]: {e}")
            return None

    def save_sequence(self, seq: UdsSequence, filepath: str):
        """保存序列到JSON文件"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(seq.to_dict(), f, indent=2, ensure_ascii=False)
            self._sequences[seq.name] = seq
        except Exception as e:
            self._logger.error(f"保存序列失败: {e}")

    def list_sequences(self) -> list[str]:
        """列出已加载的序列名称"""
        return list(self._sequences.keys())

    def get_sequence(self, name: str) -> Optional[UdsSequence]:
        return self._sequences.get(name)

    @staticmethod
    def _check_step_response(step: SequenceStep, response: Optional[bytes]):
        """步骤响应校验（四种模式，优先级: 无响应 > 期望NRC > 期望前缀 > 正响应）

        Returns:
            (ok, error_message)
        """
        sid = step.request_data[0] if step.request_data else 0
        if step.expect_no_response:
            if response is None:
                return True, ""
            return False, f"期望无响应, 实际RX: {response.hex(' ')}"
        if step.expected_nrc:
            exp = " ".join(f"0x{n:02X}" for n in step.expected_nrc)
            if response is None:
                return False, f"无响应（期望负响应 7F {sid:02X} {exp}）"
            if len(response) >= 3 and response[0] == 0x7F \
                    and response[1] == sid and response[2] in step.expected_nrc:
                return True, ""
            if response[0] == 0x7F:
                got = response[2] if len(response) > 2 else 0
                return False, f"NRC不符: 实际0x{got:02X}, 期望{exp}"
            return False, f"期望负响应, 实际收到 {response.hex(' ')}"
        if step.expected_response:
            if response is None:
                return False, "无响应（超时）"
            if response[:len(step.expected_response)] == step.expected_response:
                return True, ""
            return False, \
                f"响应前缀不符: 期望{step.expected_response.hex(' ')}, 实际{response.hex(' ')}"
        # 默认: 要求正响应（首字节=SID+0x40）
        if response is None:
            return False, "无响应（超时）"
        if response[0] == sid + 0x40:
            return True, ""
        if response[0] == 0x7F:
            nrc = response[2] if len(response) > 2 else 0
            return False, f"NRC: 0x{nrc:02X}"
        return False, f"意外响应 {response.hex(' ')}"

    def _execute_step(self, seq: UdsSequence, step: SequenceStep,
                      step_idx: int, total: int, phase: str,
                      uds_client, result: SequenceResult,
                      step_callback: Callable = None) -> bool:
        """执行单步并追加步骤结果，返回校验是否通过"""
        if not step.enabled:
            return True

        # 执行前延时
        if step.delay_before_ms > 0:
            time.sleep(step.delay_before_ms / 1000.0)

        step_start = time.time()
        last_response = None
        last_ok = False
        last_error = ""

        # 按发送次数执行（每次发送后用同一校验规则判定）
        for send_idx in range(step.send_count):
            if self._stop_event.is_set():
                break

            response = uds_client.send_raw(step.request_data)
            last_response = response
            last_ok, last_error = self._check_step_response(step, response)

            if step.check_positive and not last_ok:
                break

        elapsed_ms = (time.time() - step_start) * 1000

        # 构造步骤结果（is_positive含义扩展为"步骤校验通过"）
        step_result = StepResult(
            step_index=step_idx,
            step_name=step.step_name,
            request_sent=step.request_data,
            response_received=last_response,
            is_positive=last_ok,
            elapsed_ms=elapsed_ms,
            phase=phase,
        )

        if not last_ok:
            step_result.error_message = last_error or "校验失败"
            # 非预期负响应时附带NRC提示（校验失败且收到7F帧）
            if not step.expected_nrc and last_response \
                    and len(last_response) >= 3 \
                    and last_response[0] == 0x7F:
                nrc = last_response[2]
                step_result.error_message = f"NRC: 0x{nrc:02X}"

        result.step_results.append(step_result)

        # 日志（带阶段标记，便于区分前置/主/清理）
        resp_hex = last_response.hex(" ") if last_response else "None"
        status = "OK" if last_ok else "FAIL"
        self._logger.info(
            f"[{phase}] Step {step_idx + 1}/{total}: {step.step_name} | "
            f"TX: {step.request_data.hex(' ')} | RX: {resp_hex} | "
            f"{status} | 耗时: {elapsed_ms:.0f}ms")

        if step_callback:
            step_callback(step_result)

        # 执行后延时
        if step.delay_after_ms > 0:
            time.sleep(step.delay_after_ms / 1000.0)

        return last_ok

    def execute_sequence(self, seq: UdsSequence, uds_client,
                         step_callback: Callable = None,
                         finish_callback: Callable = None) -> SequenceResult:
        """执行序列（同步，建议在子线程中调用）

        执行顺序（Diva TestModule 结构）:
          1. 前置条件步骤 pre_steps —— 任一失败则用例判失败并跳过主步骤（如前置扩展会话失败）
          2. 主步骤 steps —— stop_on_error=True 时遇错中止主步骤（清理仍执行）
          3. 清理步骤 post_steps —— 总执行（会话还原等），失败仅警告不影响判定；
             失败记录首个失败步骤（即使stop_on_error=False，也用于最终判定）
        """
        result = SequenceResult()
        self._running = True
        self._stop_event.clear()
        start_time = time.time()

        self._logger.info(f"=== 序列开始: \"{seq.name}\" ===")

        try:
            for loop_idx in range(seq.loop_count):
                if self._stop_event.is_set():
                    result.error_message = "用户取消"
                    break

                if seq.loop_count > 1:
                    self._logger.info(f"--- 循环 {loop_idx + 1}/{seq.loop_count} ---")

                # 首轮失败记录（前置/主步骤共用，仅记首个）
                failed_info = None  # (step_idx, phase+step_name, error_message)

                # ---- 1. 前置条件步骤 ----
                for step_idx, step in enumerate(seq.pre_steps):
                    if self._stop_event.is_set():
                        result.error_message = "用户取消"
                        break
                    ok = self._execute_step(
                        seq, step, step_idx, len(seq.pre_steps), "前置条件",
                        uds_client, result, step_callback)
                    if not ok and failed_info is None:
                        sr = result.step_results[-1]
                        failed_info = (step_idx, f"前置条件: {step.step_name}",
                                       sr.error_message)
                        self._logger.warning(
                            f"前置条件未满足，跳过主步骤: {step.step_name}")
                        break

                # ---- 2. 主步骤（前置失败时跳过） ----
                if failed_info is None:
                    for step_idx, step in enumerate(seq.steps):
                        if self._stop_event.is_set():
                            result.error_message = "用户取消"
                            break
                        ok = self._execute_step(
                            seq, step, step_idx, len(seq.steps), "主步骤",
                            uds_client, result, step_callback)
                        if not ok and failed_info is None:
                            sr = result.step_results[-1]
                            failed_info = (step_idx, step.step_name,
                                           sr.error_message)
                        if not ok and step.check_positive and seq.stop_on_error:
                            self._logger.warning(
                                f"序列在步骤 {step_idx + 1} 停止: {step.step_name}")
                            break

                # ---- 3. 清理步骤（总执行，失败仅警告不影响判定） ----
                for step_idx, step in enumerate(seq.post_steps):
                    if self._stop_event.is_set():
                        result.error_message = "用户取消"
                        break
                    ok = self._execute_step(
                        seq, step, step_idx, len(seq.post_steps), "清理",
                        uds_client, result, step_callback)
                    if not ok:
                        self._logger.warning(
                            f"清理步骤失败（不影响用例判定）: {step.step_name}")

                # ---- 4. 失败记录写入结果 ----
                if failed_info is not None:
                    result.failed_step, result.failed_step_name, \
                        result.error_message = failed_info
                    if seq.stop_on_error:
                        # 中止后续循环，立即收尾（清理已执行）
                        break

                # 循环间隔
                if loop_idx < seq.loop_count - 1 and seq.loop_delay_ms > 0:
                    time.sleep(seq.loop_delay_ms / 1000.0)

            result.success = result.failed_step is None
            result.total_elapsed_ms = (time.time() - start_time) * 1000
            total_steps = len(result.step_results)
            success_steps = sum(1 for r in result.step_results if r.is_positive)
            self._logger.info(
                f"=== 序列完成: {success_steps}/{total_steps}步骤成功, "
                f"总耗时: {result.total_elapsed_ms:.0f}ms ===")

        except Exception as e:
            result.error_message = str(e)
            self._logger.error(f"序列执行异常: {e}")
        finally:
            self._running = False
            if finish_callback:
                finish_callback(result)

        return result

    def stop_execution(self):
        """中止执行"""
        self._stop_event.set()
        self._running = False
