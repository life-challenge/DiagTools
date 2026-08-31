"""自定义序列执行引擎

用户自定义UDS执行序列，将多个UDS步骤编排为可重复执行的自动化流程。
支持步骤级发送次数和步骤间延时配置。
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
                 delay_after_ms: int = 0, enabled: bool = True):
        self.step_name = step_name
        self.request_data = request_data
        self.send_count = send_count
        self.expected_response = expected_response
        self.check_positive = check_positive
        self.delay_before_ms = delay_before_ms
        self.delay_after_ms = delay_after_ms
        self.enabled = enabled

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
        )


class UdsSequence:
    """UDS序列定义"""

    def __init__(self, name: str = "", description: str = ""):
        self.name = name
        self.description = description
        self.steps: list[SequenceStep] = []
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
            "steps": [s.to_dict() for s in self.steps],
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
        seq.steps = [SequenceStep.from_dict(s) for s in d.get("steps", [])]
        return seq


class StepResult:
    """步骤执行结果"""

    def __init__(self, step_index: int, step_name: str,
                 request_sent: bytes, response_received: bytes = None,
                 is_positive: bool = False, error_message: str = "",
                 elapsed_ms: float = 0):
        self.step_index = step_index
        self.step_name = step_name
        self.request_sent = request_sent
        self.response_received = response_received
        self.is_positive = is_positive
        self.error_message = error_message
        self.elapsed_ms = elapsed_ms


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

    def execute_sequence(self, seq: UdsSequence, uds_client,
                         step_callback: Callable = None,
                         finish_callback: Callable = None) -> SequenceResult:
        """执行序列（同步，建议在子线程中调用）

        Args:
            seq: 序列定义
            uds_client: UDS客户端
            step_callback: 每步完成回调 (StepResult) -> None
            finish_callback: 完成回调 (SequenceResult) -> None
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

                for step_idx, step in enumerate(seq.steps):
                    if self._stop_event.is_set():
                        result.error_message = "用户取消"
                        break

                    if not step.enabled:
                        continue

                    # 执行前延时
                    if step.delay_before_ms > 0:
                        time.sleep(step.delay_before_ms / 1000.0)

                    step_start = time.time()
                    last_response = None
                    last_is_positive = False

                    # 按发送次数执行
                    for send_idx in range(step.send_count):
                        if self._stop_event.is_set():
                            break

                        response = uds_client.send_raw(step.request_data)
                        last_response = response
                        last_is_positive = (
                            response is not None and
                            len(response) > 0 and
                            0x40 <= response[0] < 0x7F
                        )

                        # 检查期望响应
                        if step.expected_response and response:
                            last_is_positive = (
                                response[:len(step.expected_response)] ==
                                step.expected_response
                            )

                        if step.check_positive and not last_is_positive:
                            break

                    elapsed_ms = (time.time() - step_start) * 1000

                    # 构造步骤结果
                    step_result = StepResult(
                        step_index=step_idx,
                        step_name=step.step_name,
                        request_sent=step.request_data,
                        response_received=last_response,
                        is_positive=last_is_positive,
                        elapsed_ms=elapsed_ms,
                    )

                    if not last_is_positive and step.check_positive:
                        if last_response and len(last_response) >= 3 and last_response[0] == 0x7F:
                            nrc = last_response[2]
                            step_result.error_message = f"NRC: 0x{nrc:02X}"
                        else:
                            step_result.error_message = "无响应或负响应"

                    result.step_results.append(step_result)

                    # 日志
                    resp_hex = last_response.hex(" ") if last_response else "None"
                    status = "OK" if last_is_positive else "FAIL"
                    self._logger.info(
                        f"Step {step_idx + 1}/{len(seq.steps)}: {step.step_name} | "
                        f"TX: {step.request_data.hex(' ')} | RX: {resp_hex} | "
                        f"{status} | 耗时: {elapsed_ms:.0f}ms")

                    if step_callback:
                        step_callback(step_result)

                    # 错误处理
                    if not last_is_positive and step.check_positive and seq.stop_on_error:
                        result.failed_step = step_idx
                        result.failed_step_name = step.step_name
                        result.error_message = step_result.error_message
                        self._running = False
                        result.total_elapsed_ms = (time.time() - start_time) * 1000
                        self._logger.warning(
                            f"序列在步骤 {step_idx + 1} 停止: {step.step_name}")
                        if finish_callback:
                            finish_callback(result)
                        return result

                    # 执行后延时
                    if step.delay_after_ms > 0:
                        time.sleep(step.delay_after_ms / 1000.0)

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
