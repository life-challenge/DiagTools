"""测试中心视图（V2.2 一级页）

自动化诊断测试:
  内置标准测试用例（会话切换/TesterPresent/读DTC/读DID）+
  ISO 14229-1 / Vector Diva 风格协议一致性用例库（正响应/负响应NRC/无响应）+
  可加载 resources/sequences 下的自定义序列。
执行在后台线程完成（期间自动暂停会话保活避免干扰），
结果表显示通过/失败，可导出测试报告CSV。
"""

import os
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QTextEdit, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from src.ui.async_uds import UdsWorker
from src.business.sequence_manager import (
    SequenceManager, SequenceStep, UdsSequence)
from src.utils.paths import get_project_root, get_resource_path

# 常用NRC名（失败详情可读化，ISO 14229-1 Table A.1）
_NRC_NAMES = {
    0x10: "generalReject", 0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported", 0x13: "incorrectMessageLength",
    0x14: "responseTooLong", 0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect", 0x24: "requestSequenceError",
    0x31: "requestOutOfRange", 0x33: "securityAccessDenied",
    0x35: "invalidKey", 0x36: "exceededNumberOfAttempts",
    0x70: "uploadDownloadNotAccepted", 0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure", 0x73: "wrongBlockSequenceCounter",
    0x78: "responsePending", 0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}


def _step(name: str, req_hex: str, exp_hex: str = None,
          nrc: list = None, no_resp: bool = False,
          delay_before: int = 0, delay_after: int = 0,
          tolerated: list = None) -> SequenceStep:
    """用例步骤构造快捷函数

    tolerated: 容忍NRC——期望正响应但ECU回声明的NRC也判通过
    （如10 02预编程条件未满足回7F 10 22属ECU合法行为）
    """
    step = SequenceStep(
        name, bytes.fromhex(req_hex.replace(" ", "")),
        expected_response=(bytes.fromhex(exp_hex.replace(" ", ""))
                           if exp_hex else None),
        expected_nrc=nrc, expect_no_response=no_resp,
        check_positive=True, tolerated_nrc=tolerated,
        delay_before_ms=delay_before, delay_after_ms=delay_after)
    if step.request_data[:1] in (b"\x10", b"\x22") and exp_hex:
        # 会话切换(10 xx)与读DID(22 xx)均幂等无副作用，ECU切会话/落盘
        # 偶有~2s无响应窗口: 超时自动重试最多5次，收到任何答复即停
        # （与生成器 _make_step/_read_step 同规则；写/例程类不重试防重复执行）
        step.retry_on_timeout = True
        step.send_count = 5
    return step


def _seq(name: str, desc: str, stop_on_error: bool = True) -> UdsSequence:
    s = UdsSequence(name, desc)
    s.stop_on_error = stop_on_error
    return s


def _pre_ext_session() -> SequenceStep:
    """前置条件: 进入扩展会话（部分服务在非默认会话才支持，如27/2E/85）"""
    return _step("进入扩展会话", "10 03", exp_hex="50 03")


def _pre_default_session() -> SequenceStep:
    """前置条件: 进入默认会话（负向用例“默认会话安全受限”等必须显式
    声明起点状态——否则受上一用例残留会话污染: 如 SC-01 编程切换
    失败停止时 ECU 停在扩展会话，默认会话判定就成了误判）
    """
    return _step("进入默认会话", "10 01", exp_hex="50 01")


def _post_default_session() -> SequenceStep:
    """清理: 还原默认会话（用例后总执行，失败仅警告）"""
    return _step("还原默认会话", "10 01", exp_hex="50 01")


def _builtin_cases() -> list:
    """内置标准测试用例（基础冒烟，均为标准服务）"""
    cases = []

    seq = UdsSequence("会话切换测试", "扩展会话→默认会话")
    seq.steps.append(SequenceStep(
        "进入扩展会话", bytes.fromhex("1003"),
        expected_response=bytes.fromhex("5003")))
    seq.steps.append(SequenceStep(
        "返回默认会话", bytes.fromhex("1001"),
        expected_response=bytes.fromhex("5001"),
        delay_before_ms=200))
    cases.append(seq)

    seq = UdsSequence("TesterPresent测试", "3E 00 要求正响应")
    seq.steps.append(SequenceStep(
        "TesterPresent", bytes.fromhex("3E00"),
        expected_response=bytes.fromhex("7E00")))
    cases.append(seq)

    seq = UdsSequence("读故障码测试", "19 02 FF 按状态掩码读DTC")
    seq.steps.append(SequenceStep(
        "ReadDTCInformation", bytes.fromhex("1902FF"),
        expected_response=bytes([0x59])))
    cases.append(seq)

    seq = UdsSequence("读识别DID测试", "22 F190 VIN")
    seq.steps.append(SequenceStep(
        "ReadDataByIdentifier", bytes.fromhex("22F190"),
        check_positive=True))
    cases.append(seq)
    return cases


def _conformance_cases() -> list:
    """ISO 14229-1 / Vector Diva 风格协议一致性用例库

    按 Diva TestModule 分组（名称前缀）:
      GB=GeneralBehavior  SC=SessionControl  SA=SecurityAccess
      DT=DTC服务  DD=DataIdentifier  RC=RoutineControl
    会话切换类用例末步还原默认会话（stop_on_error关闭保证还原步必执行）。
    """
    cases = []

    # ---- GB: General Behavior（通用行为） ----
    seq = _seq("GB-01 未支持服务", "发送未定义SID应回NRC 0x11 serviceNotSupported")
    seq.steps.append(_step("未支持SID", "AA", nrc=[0x11]))
    cases.append(seq)

    seq = _seq("GB-02 请求长度错误", "仅发SID无子参数应回NRC 0x13 incorrectMessageLength")
    seq.steps.append(_step("10缺子功能字节", "10", nrc=[0x13]))
    cases.append(seq)

    seq = _seq("GB-03 TesterPresent正响应", "3E 00 → 7E 00")
    seq.steps.append(_step("TesterPresent", "3E 00", exp_hex="7E 00"))
    cases.append(seq)

    seq = _seq("GB-04 TesterPresent抑制响应", "3E 80 抑制正响应: ECU不得回复")
    seq.steps.append(_step("3E 80应无响应", "3E 80", no_resp=True))
    cases.append(seq)

    seq = _seq("GB-05 TesterPresent非法子功能", "3E 10 应回NRC 0x12（实现差异时容忍0x13）")
    seq.steps.append(_step("非法子功能", "3E 10", nrc=[0x12, 0x13]))
    cases.append(seq)

    # ---- SC: DiagnosticSessionControl (0x10) ----
    # stop_on_error关闭: 任一会话切换失败也要执行末步还原默认会话，
    # 否则失败残留的非默认会话会污染后续用例（如SA-01误在扩展会话下测）
    seq = _seq("SC-01 会话切换循环", "默认→扩展→编程→还原默认 正响应前缀校验",
               stop_on_error=False)
    # 顺序依据: 多数ECU不允许从默认会话直接进编程会话（回 7F 10 7E
    # subFunctionNotSupportedInActiveSession），须经扩展会话中转；
    # 10 02 还可能因预编程条件未满足回 7F 10 22（SVC-10-02带完整
    # 预编程链覆盖正路径，此处容忍0x22——如实区分“须条件检查”与故障）
    seq.steps.append(_step("默认会话", "10 01", exp_hex="50 01"))
    seq.steps.append(_step("扩展会话", "10 03", exp_hex="50 03", delay_before=200))
    seq.steps.append(_step("编程会话", "10 02", exp_hex="50 02", delay_before=200,
                           tolerated=[0x22]))
    seq.steps.append(_step("还原默认会话", "10 01", exp_hex="50 01", delay_before=200,
                           tolerated=[0x7F]))
    cases.append(seq)

    seq = _seq("SC-02 会话非法子功能", "10 00 应回NRC 0x12 subFunctionNotSupported")
    seq.steps.append(_step("子功能0x00", "10 00", nrc=[0x12]))
    cases.append(seq)

    seq = _seq("SC-03 会话未支持子功能", "10 7E 应回NRC 0x12")
    seq.steps.append(_step("子功能0x7E", "10 7E", nrc=[0x12]))
    cases.append(seq)

    seq = _seq("SC-04 会话长度错误", "10 03 00 超长应回NRC 0x13")
    seq.steps.append(_step("超长请求", "10 03 00", nrc=[0x13]))
    cases.append(seq)

    # ---- SA: SecurityAccess (0x27) ----
    seq = _seq("SA-01 默认会话安全受限",
               "前置默认会话，27 01应回NRC 0x7F/0x7E（实现允许时可能正响应，属实现差异）")
    # 前置显式进默认会话: 用例必须自声明起点状态，不能假设初始状态
    # （上一用例失败残留的扩展会话会让27 01得到正响应、误判失败）
    seq.pre_steps.append(_pre_default_session())
    seq.steps.append(_step("默认会话请种子", "27 01", nrc=[0x7F, 0x7E], delay_before=200))
    cases.append(seq)

    seq = _seq("SA-02 扩展会话种子请求", "前置扩展会话，27 01应回67 01+种子")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.append(_step("请求种子", "27 01", exp_hex="67 01", delay_before=200))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    seq = _seq("SA-03 非法安全等级", "27 10 应回NRC 0x12（实现差异时容忍0x31）")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.append(_step("非法等级", "27 10", nrc=[0x12, 0x31], delay_before=200))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    seq = _seq("SA-04 安全长度错误",
               "前置扩展会话（27默认会话回NRC 0x7F，会话检查先于长度检查）, 仅发27应回NRC 0x13")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.append(_step("27缺子功能", "27", nrc=[0x13], delay_before=200))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    # ---- DT: DTC服务 ----
    # 前置先读支持掩码: 部分ECU对含不支持位的状态掩码FF回NRC 0x31，
    # 先19 01读出ECU实际支持的掩码便于区分“掩码不支持”与“服务不可用”
    seq = _seq("DT-01 按状态掩码读DTC", "19 01 FF 读支持掩码, 19 02 FF 应回正响应59")
    seq.steps.append(_step("读支持状态掩码", "19 01 FF", exp_hex="59 01"))
    seq.steps.append(_step("ReadDTCInformation", "19 02 FF", exp_hex="59", delay_before=200))
    cases.append(seq)

    seq = _seq("DT-02 19非法子功能", "19 00 应回NRC 0x12")
    seq.steps.append(_step("子功能0x00", "19 00", nrc=[0x12]))
    cases.append(seq)

    seq = _seq("DT-03 19长度错误", "19 02 缺掩码应回NRC 0x13")
    seq.steps.append(_step("缺状态掩码", "19 02", nrc=[0x13]))
    cases.append(seq)

    seq = _seq("DT-04 DTC设置控制", "前置扩展会话（部分ECU默认会话回NRC 0x7F）: 85 02关→C5 02；85 01开→C5 01")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.append(_step("关闭DTC设置", "85 02", exp_hex="C5 02", delay_before=200))
    seq.steps.append(_step("开启DTC设置", "85 01", exp_hex="C5 01", delay_before=200))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    seq = _seq("DT-05 清除全部DTC", "⚠破坏性: 前置扩展会话, 14 FF FF FF 清除所有故障码，应回54")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.append(_step("ClearDTC", "14 FF FF FF", exp_hex="54", delay_before=200))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    # ---- DD: ReadDataByIdentifier / WriteDataByIdentifier ----
    seq = _seq("DD-01 读有效DID", "22 F1 90 应回 62 F1 90+数据")
    seq.steps.append(_step("读VIN", "22 F1 90", exp_hex="62 F1 90"))
    cases.append(seq)

    seq = _seq("DD-02 读无效DID", "22 00 00 应回NRC 0x31（实现差异时容忍0x22）")
    seq.steps.append(_step("无效DID", "22 00 00", nrc=[0x31, 0x22]))
    cases.append(seq)

    seq = _seq("DD-03 22长度错误", "22 F1 单字节DID应回NRC 0x13")
    seq.steps.append(_step("DID不完整", "22 F1", nrc=[0x13]))
    cases.append(seq)

    seq = _seq("DD-04 写无效DID", "前置扩展会话（部分ECU默认会话不支持27/2E）, 写入无效DID应回NRC（不污染ECU数据）")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.append(_step("写无效DID", "2E 00 00 AA", nrc=[0x31, 0x22, 0x33], delay_before=200))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    seq = _seq("DD-05 2E长度错误",
               "前置扩展会话（2E默认会话回NRC 0x7F，会话检查先于长度检查）, "
               "2E 00 缺数据应回NRC 0x13（未解锁时安全检查可能先回0x33）")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.append(_step("写请求不完整", "2E 00", nrc=[0x13, 0x33], delay_before=200))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    # ---- RC: RoutineControl (0x31) ----
    # 前置扩展会话: 31默认会话回NRC 0x7F（会话检查先于子功能/长度/ID检查），
    # 负向NRC判定必须在非默认会话下才有意义
    seq = _seq("RC-01 无效例程ID", "前置扩展会话, 31 01 FF FF 应回NRC 0x31 requestOutOfRange")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.append(_step("无效例程", "31 01 FF FF", nrc=[0x31, 0x22], delay_before=200))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    seq = _seq("RC-02 31非法子功能", "前置扩展会话, 31 00 应回NRC 0x12")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.append(_step("子功能0x00", "31 00 FF FF", nrc=[0x12], delay_before=200))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    seq = _seq("RC-03 31长度错误", "前置扩展会话, 31 01 缺例程ID应回NRC 0x13")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.append(_step("缺例程ID", "31 01", nrc=[0x13], delay_before=200))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    # ---- SAL: SecurityAccess 锁定/延时/复位交互 ----
    # 行为模型（14229-1 + 安全加固规范，已实测验证）:
    #   · 错误密钥计数: 每次错误key +1，达到3 → 0x36 并启动10s延时
    #   · 连续种子计数: 种子未消费时重复请求 +1，连续第4次 → 0x36
    #   · 延时期间 27 01/27 02 → 0x37（到期间发key可能回0x24顺序错误）
    #   · 自然到期(~10s): 计数器与计时器皆清零
    #   · 成功解锁(67 02): 计数器重置为0（跨会话后重新累计）
    #   · 11 01 复位不应清除闭锁（防暴力破解绕过）——SAL-04按规范期望
    # 每条用例末步均为"等到期后取种子"自清场，锁定状态不污染后续用例;
    # 长延时步(5~11s)期间无报文，休眠快的ECU需先在连接面板开启
    # "连接时自动唤醒"或手动唤醒。SAL-05需先加载正确安全算法DLL。

    def _sal_lock_steps():
        """三次“取种子→错误key”累积到锁定，返回步骤列表
        （第3次错误key期望0x36，与0x35不同以便如实区分触发轮次）"""
        steps = []
        for i in (1, 2, 3):
            steps.append(_step(f"取种子#{i}", "27 01", exp_hex="67 01"))
            steps.append(_step(
                f"错误密钥#{i}", "27 02 00 00 00 00",
                nrc=[0x36] if i == 3 else [0x35]))
        return steps

    seq = _seq("SAL-01 错误密钥计数锁定",
               "3次错误密钥→0x36启动10s延时，种子请求回0x37；"
               "末步等到期自清场验证计数器/计时器归零")
    seq.pre_steps.append(_pre_ext_session())
    seq.steps.extend(_sal_lock_steps())
    seq.steps.append(_step("锁定后请种子", "27 01", nrc=[0x37]))
    seq.steps.append(_step("等到期后取种子(自清场)", "27 01",
                           exp_hex="67 01", delay_before=11000))
    cases.append(seq)

    seq = _seq("SAL-02 连续种子请求锁定",
               "种子未消费时重复请求计数: 连续第4次请种子→0x36，"
               "随后种子请求回0x37。不设末步取种子: 连续种子路径锁定期间"
               "种子未消费，到期后的种子请求会被计为重复请求+1残留"
               "（实测），自然到期由下一条用例前置的11s延时承担")
    seq.pre_steps.append(_step("进入扩展会话", "10 03", exp_hex="50 03",
                               delay_before=11000))  # 等SAL-01锁定到期
    for i in (1, 2, 3):
        seq.steps.append(_step(f"连续请种子#{i}", "27 01", exp_hex="67 01"))
    seq.steps.append(_step("连续请种子#4(触发锁定)", "27 01", nrc=[0x36]))
    seq.steps.append(_step("锁定后请种子", "27 01", nrc=[0x37]))
    cases.append(seq)

    seq = _seq("SAL-03 延时计时器时长",
               "锁定后5s仍回0x37(计时中)，~10s到期后恢复种子——"
               "验证10s延时窗口而非立即解锁")
    seq.pre_steps.append(_step("进入扩展会话", "10 03", exp_hex="50 03",
                               delay_before=11000))  # 等SAL-02锁定到期
    seq.steps.extend(_sal_lock_steps())
    seq.steps.append(_step("零点后5s请种子(应仍闭锁)", "27 01",
                           nrc=[0x37], delay_before=5000))
    seq.steps.append(_step("零点后~11s请种子(到期恢复)", "27 01",
                           exp_hex="67 01", delay_before=6000))
    cases.append(seq)

    seq = _seq("SAL-04 复位保持闭锁",
               "⚠含ECU复位: 锁定后11 01，重启后种子请求仍应回0x37"
               "（市面安全加固规范: 软复位不得清除闭锁，否则可绕过"
               "防暴力破解；复位后ECU需处于唤醒状态）")
    seq.pre_steps.append(_step("进入扩展会话", "10 03", exp_hex="50 03",
                               delay_before=11000))  # 等SAL-03锁定到期
    seq.steps.extend(_sal_lock_steps())
    seq.steps.append(_step("锁定确认", "27 01", nrc=[0x37]))
    seq.steps.append(_step("ECU Reset", "11 01", exp_hex="51 01",
                           delay_before=500))
    seq.steps.append(_step("重启后进扩展会话", "10 03", exp_hex="50 03",
                           delay_before=3500))  # 等重启完成(Boot跳转~2.5s)
    # 复位后会话回落默认，27在默认会话回0x7F属会话限制非闭锁判定:
    # 必须先回扩展会话，此步才真正判"闭锁是否被复位清除"
    seq.steps.append(_step("重启后请种子(规范期望仍闭锁)", "27 01",
                           nrc=[0x37]))
    seq.steps.append(_step("等到期后取种子(自清场)", "27 01",
                           exp_hex="67 01", delay_before=11000))
    seq.post_steps.append(_post_default_session())
    cases.append(seq)

    seq = _seq("SAL-05 成功解锁重置计数器",
               "⚠需已加载正确安全算法: 2次错误key后算法解锁(67 02)，"
               "跨会话后需重新累计3次错误key才再锁定——验证解锁后"
               "计数器归零；末步等到期自清场")
    seq.pre_steps.append(_step("进入扩展会话", "10 03", exp_hex="50 03",
                               delay_before=11000))  # 等SAL-04复位/锁定恢复
    seq.steps.append(_step("取种子#1", "27 01", exp_hex="67 01"))
    seq.steps.append(_step("错误密钥#1", "27 02 00 00 00 00", nrc=[0x35]))
    seq.steps.append(_step("取种子#2", "27 01", exp_hex="67 01"))
    seq.steps.append(_step("错误密钥#2", "27 02 00 00 00 00", nrc=[0x35]))
    # 裸发27 01(无校验设置) → 引擎自动调用已加载算法完成取种子→算钥→
    # 发密钥全流程，成功回 67 02 即解锁（计数器应重置为0）
    seq.steps.append(_step("算法解锁(自动全流程)", "27 01", delay_before=200))
    seq.steps.append(_step("退出会话(解锁状态随会话失效)", "10 01",
                           exp_hex="50 01", delay_before=1000))
    seq.steps.append(_step("重进扩展会话", "10 03", exp_hex="50 03",
                           delay_before=3000))
    # 计数器已重置: 需要3次错误key才再锁定（若第1次即0x36=残留未清）
    seq.steps.extend(_sal_lock_steps())
    seq.steps.append(_step("等到期后取种子(自清场)", "27 01",
                           exp_hex="67 01", delay_before=11000))
    cases.append(seq)

    seq = _seq("SAL-06 到期后尝试语义", 
               "⚠规格期望（DEM实测为到期全清模型，本用例预期FAIL暴露偏离）: "
               "10s到期后计数器减一，仅允许一次尝试解锁；若仍为错误key，"
               "则重新触发10s闭锁，第二轮到期后再次允许一次尝试")
    seq.pre_steps.append(_step("进入扩展会话", "10 03", exp_hex="50 03",
                               delay_before=11000))  # 等SAL-05锁定到期
    seq.steps.extend(_sal_lock_steps())
    # 到期取种子（=规格中“允许的一次尝试”的种子）
    seq.steps.append(_step("等到期取种子(允许一次尝试)", "27 01",
                           exp_hex="67 01", delay_before=11000))
    # 该次尝试发错误key: 0x35=普通失败(尝试被允许)；0x36=减一后直接再锁
    # （两种均为“减一+失败再锁”模型的合规实现，多值NRC任一即过）
    seq.steps.append(_step("到期后的一次尝试(错误key)", "27 02 00 00 00 00",
                           nrc=[0x35, 0x36]))
    # 核心判定: 失败尝试后应重新闭锁（计数器回到3，重新计时10s）
    seq.steps.append(_step("失败后请种子(规格期望重新闭锁)", "27 01",
                           nrc=[0x37]))
    # 第二轮到期后再次允许一次尝试（兼作自清场）
    seq.steps.append(_step("第二轮到期后取种子(自清场)", "27 01",
                           exp_hex="67 01", delay_before=11000))
    cases.append(seq)

    return cases


class TestCenterView(QWidget):
    """测试中心"""

    # 测试执行生命周期: 主窗口据此暂停/恢复会话保活，避免3E帧干扰用例收发
    tests_started = pyqtSignal()
    tests_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._uds_client = None
        self._seq_manager = SequenceManager()
        self._cases: list = _builtin_cases() + _conformance_cases()
        self._worker = None
        self._worker_thread = None
        # 最近一次执行结果 {表格行: (seq, SequenceResult, 耗时秒)}
        # 导出报告时据此输出每步 TX/RX 报文明细（而非仅步骤数）
        self._last_results: dict = {}
        self._init_ui()
        self._refresh_table()

    def set_uds_client(self, client):
        self._uds_client = client

    def set_key_generator(self, fn):
        """注入密钥计算钩子（与序列/刷写面板共用同一算法管理器），
        供用例中的安全访问步骤（27 xx自动全流程）计算密钥"""
        self._seq_manager.set_key_generator(fn)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ---- 工具栏 ----
        bar = QHBoxLayout()
        btn_load = QPushButton("加载测试序列")
        btn_load.setToolTip("加载 resources/sequences 下的JSON序列文件作为测试用例")
        btn_load.clicked.connect(self._load_sequence)
        bar.addWidget(btn_load)
        btn_did = QPushButton("调查表/ODX生成用例")
        btn_did.setToolTip(
            "从诊断调查表（JSON/XLSX）、ODX/PDX/CDD 解析服务、子功能、DID，\n"
            "自动生成全功能用例: 每个声明的服务前缀/DID一条独立用例\n"
            "XLSX调查表需含服务矩阵/DID列表/DTC列表等标准sheet（按表头识别）\n"
            "高危服务（ECU复位/清DTC）默认禁用，工具栏“启用禁用项”或右键可批量启用")
        btn_did.clicked.connect(self._generate_cases)
        bar.addWidget(btn_did)
        btn_run_all = QPushButton("运行全部")
        btn_run_all.setObjectName("btn_primary")
        btn_run_all.clicked.connect(self._run_all)
        bar.addWidget(btn_run_all)
        btn_run_sel = QPushButton("运行选中")
        btn_run_sel.clicked.connect(self._run_selected)
        bar.addWidget(btn_run_sel)
        btn_rerun = QPushButton("重跑失败")
        btn_rerun.setToolTip("重新运行结果为“失败”的用例（上次运行过的；\n"
                             "适合修复环境/加载算法后只重跑失败项）")
        btn_rerun.clicked.connect(self._rerun_failed)
        bar.addWidget(btn_rerun)
        btn_export = QPushButton("导出测试报告")
        btn_export.clicked.connect(self._export_report)
        bar.addWidget(btn_export)
        btn_del = QPushButton("删除选中")
        btn_del.setToolTip("从用例列表删除选中的用例（可多选；内置用例也可删，可随时恢复）")
        btn_del.clicked.connect(self._delete_selected)
        bar.addWidget(btn_del)
        btn_reset = QPushButton("恢复内置")
        btn_reset.setToolTip("恢复内置标准+协议一致性用例，并保留当前加载/生成的用例")
        btn_reset.clicked.connect(self._restore_builtin)
        bar.addWidget(btn_reset)
        btn_enable_all = QPushButton("启用禁用项")
        btn_enable_all.setToolTip(
            "一键启用所有被禁用的用例（高危ECU复位/清DTC等默认禁用项）\n"
            "⚠ 高危用例会真实操作ECU，确认风险后使用；\n"
            "部分步骤禁用的用例（如WR组写回步）请右键单条启用")
        btn_enable_all.clicked.connect(self._enable_all_disabled)
        bar.addWidget(btn_enable_all)
        bar.addStretch()
        self._lbl_summary = QLabel("4 个内置用例")
        self._lbl_summary.setStyleSheet("color: #888;")
        bar.addWidget(self._lbl_summary)
        layout.addLayout(bar)

        # ---- 结果表 ----
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["用例", "描述", "步骤数", "结果", "耗时", "失败详情"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        # 右键菜单: 启用/禁用用例（高危生成用例默认禁用）
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._case_context_menu)
        layout.addWidget(self._table, 1)

        # ---- 日志 ----
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFixedHeight(110)
        layout.addWidget(self._log_text)

    # ---------------- 用例管理 ----------------

    def _refresh_table(self):
        self._table.setRowCount(len(self._cases))
        for row, seq in enumerate(self._cases):
            name = seq.name
            if self._case_disabled(seq):
                name += "  [已禁用]"
            for col, text in ((0, name), (1, seq.description),
                              (2, str(len(seq.steps)))):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 0 and self._case_disabled(seq):
                    it.setForeground(QColor("#888888"))
                self._table.setItem(row, col, it)
            for col in (3, 4, 5):
                it = QTableWidgetItem("--" if col == 3 else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, it)
        self._lbl_summary.setText(f"{len(self._cases)} 个测试用例")

    @staticmethod
    def _case_disabled(seq) -> bool:
        """用例是否禁用（主步骤全部enabled=False，如高危用例默认态）"""
        return bool(seq.steps) and not any(s.enabled for s in seq.steps)

    def _case_context_menu(self, pos):
        """右键菜单: 启用/禁用（多选批量）、删除用例（高危生成用例默认禁用）"""
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._cases):
            return
        selected = {i.row() for i in self._table.selectedIndexes()}
        rows = sorted(selected) if row in selected else [row]
        seq = self._cases[row]
        batch = len(rows) > 1
        menu = QMenu(self)
        act_enable = menu.addAction(
            f"启用选中用例（{len(rows)}条）" if batch else "启用用例")
        act_disable = menu.addAction(
            f"禁用选中用例（{len(rows)}条）" if batch else "禁用用例")
        act_parts = None
        if not batch and not self._case_disabled(seq) and any(
                not s.enabled for s in seq.steps):
            # 部分步骤禁用（如WR组写回步默认禁用），单独提供启用入口
            act_parts = menu.addAction("启用其中的禁用步骤（含写回）")
        disabled_rows = [i for i, s in enumerate(self._cases)
                         if self._case_disabled(s)]
        act_all = None
        if disabled_rows:
            act_all = menu.addAction(
                f"启用全部已禁用用例（{len(disabled_rows)}条）")
        menu.addSeparator()
        act_del = menu.addAction("删除用例")
        act = menu.exec(self._table.viewport().mapToGlobal(pos))
        if act == act_del:
            self._delete_rows(set(rows))
            return
        if act == act_all:
            for i in disabled_rows:
                for s in self._cases[i].steps:
                    s.enabled = True
            self._refresh_table()
            self._log(f"已启用 {len(disabled_rows)} 条禁用用例")
            return
        if act == act_parts:
            for s in seq.steps:
                s.enabled = True
            self._refresh_table()
            self._log(f"已启用用例的全部步骤: {seq.name}")
            return
        if act == act_enable:
            for r in rows:
                for s in self._cases[r].steps:
                    s.enabled = True
            self._refresh_table()
            self._log(f"已启用 {len(rows)} 条用例的全部步骤")
        elif act == act_disable:
            for r in rows:
                for s in self._cases[r].steps:
                    s.enabled = False
            self._refresh_table()
            self._log(f"已禁用 {len(rows)} 条用例")

    def _enable_all_disabled(self):
        """工具栏一键启用: 所有被禁用的用例（主步骤全部enabled=False的高危用例）

        部分步骤禁用的用例（如WR组写回步、NEG-SES写变体）不在此列——
        那些步骤禁用是防误写保护，需右键单条确认后启用。
        """
        targets = [s for s in self._cases if self._case_disabled(s)]
        if not targets:
            self._log("没有被禁用的用例")
            return
        for seq in targets:
            for s in seq.steps:
                s.enabled = True
        self._refresh_table()
        names = "、".join(s.name for s in targets[:3])
        more = f" 等{len(targets)}条" if len(targets) > 3 else ""
        self._log(f"已启用 {len(targets)} 条禁用用例: {names}{more}")

    def _delete_selected(self):
        """工具栏删除: 移除选中的用例（可多选）"""
        rows = {i.row() for i in self._table.selectedIndexes()}
        if not rows:
            self._log("请先选中要删除的用例")
            return
        self._delete_rows(rows)

    def _delete_rows(self, rows: set):
        """按行号集合删除用例并刷新（保留执行结果的行号重映射）"""
        if self._worker_thread is not None:
            self._log("测试执行中，无法删除用例")
            return
        rows = {r for r in rows if 0 <= r < len(self._cases)}
        if not rows:
            return
        removed = [self._cases[r].name for r in sorted(rows)]
        # 行号重映射: 删除后旧行号→新行号（保住已执行结果的报告导出）
        old_to_new = {}
        new_idx = 0
        for old_idx in range(len(self._cases)):
            if old_idx not in rows:
                old_to_new[old_idx] = new_idx
                new_idx += 1
        self._cases = [c for i, c in enumerate(self._cases) if i not in rows]
        self._last_results = {
            old_to_new[r]: v for r, v in self._last_results.items()
            if r in old_to_new}
        self._refresh_table()
        self._log(f"已删除 {len(removed)} 个用例: "
                  + ("、".join(removed[:5]) + "..." if len(removed) > 5
                     else "、".join(removed)))

    def _restore_builtin(self):
        """恢复内置用例（去重后追加，不清除加载/生成的用例）"""
        if self._worker_thread is not None:
            self._log("测试执行中，无法恢复内置用例")
            return
        existing = {c.name.lstrip("⚠") for c in self._cases}
        added = [c for c in _builtin_cases() + _conformance_cases()
                 if c.name not in existing]
        self._cases.extend(added)
        self._refresh_table()
        self._log(f"已恢复 {len(added)} 个内置用例" if added
                  else "内置用例均已在列表中")

    def _load_sequence(self):
        start_dir = get_resource_path("sequences")
        filepath, _ = QFileDialog.getOpenFileName(
            self, "加载测试序列", start_dir, "JSON Files (*.json)")
        if not filepath:
            return
        seq = self._seq_manager.load_sequence(filepath)
        if seq is None:
            self._log(f"序列加载失败: {filepath}")
            return
        self._cases.append(seq)
        self._refresh_table()
        self._log(f"已加载测试序列: {seq.name}")

    def _generate_cases(self):
        """从调查表/ODX生成全功能用例（配置驱动）

        支持: 调查表XLSX（服务矩阵/DID/DTC/例程sheet，自动解析）、
        调查表JSON（dids+services）、ODX导出JSON（comms）、
        ODX/PDX/CDD原始文件。生成 SVC-组（服务/子功能）、DID-组（读）、
        WR-组（写，默认禁用）、NEG-组（负向全排列）、SES-组（会话/安全
        状态维度），重复生成时整组替换避免堆积。
        """
        start_dir = get_resource_path("did_definitions")
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择诊断调查表 / ODX 文件", start_dir,
            "调查表/ODX (*.xlsx *.xlsm *.json *.odx *.pdx *.cdd);;所有文件 (*)")
        if not filepath:
            return
        from src.business.conformance_generator import generate_from_file
        report = generate_from_file(filepath)
        if not report.sequences:
            self._log(f"未生成任何用例: {os.path.basename(filepath)}"
                      + (f"（跳过 {len(report.skipped)} 条）" if report.skipped else ""))
            return
        # 替换旧的生成组（SVC-/DID-/WR-/NEG-/SES-前缀），保留内置与手动加载的用例
        self._cases = [c for c in self._cases
                       if not c.name.lstrip("⚠").startswith(
                           ("SVC-", "DID-", "WR-", "NEG-", "SES-"))]
        self._cases.extend(report.sequences)
        self._refresh_table()
        self._log(f"[{os.path.basename(filepath)}] {report.summary()}")
        for name, reason in report.skipped[:5]:
            self._log(f"  跳过 {name}: {reason}")
        if len(report.skipped) > 5:
            self._log(f"  ...共跳过 {len(report.skipped)} 条")
        if report.dangerous:
            self._log(f"  ⚠ {report.dangerous} 条高危用例（ECU复位/清DTC）默认禁用，"
                      "工具栏“启用禁用项”可一键启用")

    # ---------------- 执行 ----------------

    def _run_all(self):
        self._run_cases(list(range(len(self._cases))))

    def _run_selected(self):
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        if not rows:
            self._log("请先选中要运行的用例")
            return
        self._run_cases(rows)

    def _rerun_failed(self):
        """重跑上次运行结果为失败的用例（环境修复后只复跑失败项）"""
        failed_rows = [row for row, (_, result, _t) in self._last_results.items()
                       if not result.success]
        if not failed_rows:
            self._log("没有可重跑的失败用例（尚未运行或全部通过）")
            return
        self._log(f"重跑 {len(failed_rows)} 条失败用例")
        self._run_cases(sorted(failed_rows))

    def _run_cases(self, rows: list):
        if self._worker_thread is not None:
            self._log("上一次测试仍在执行")
            return
        if not self._uds_client:
            self._log("未连接UDS，无法执行测试")
            return
        # 过滤禁用用例（高危默认禁用，工具栏“启用禁用项”启用后才参与执行）
        disabled_rows = [r for r in rows if self._case_disabled(self._cases[r])]
        for r in disabled_rows:
            self._table.item(r, 3).setText("已禁用")
            self._table.item(r, 3).setForeground(QColor("#888888"))
        rows = [r for r in rows if r not in disabled_rows]
        if not rows:
            self._log("所选用例均已禁用（工具栏“启用禁用项”或右键可启用）")
            return
        if disabled_rows:
            self._log(f"跳过 {len(disabled_rows)} 条禁用用例")
        client = self._uds_client
        manager = self._seq_manager
        cases = [(r, self._cases[r]) for r in rows]

        def _execute():
            results = []
            for row, seq in cases:
                t0 = time.time()
                result = manager.execute_sequence(seq, client)
                results.append((row, seq, result, time.time() - t0))
            return results

        worker = UdsWorker(_execute)
        thread = QThread(self)
        worker.moveToThread(thread)

        def _cleanup():
            thread.quit()
            thread.wait()
            self._worker = None
            self._worker_thread = None
            self.tests_finished.emit()  # 恢复会话保活

        def _on_done(results):
            try:
                passed = 0
                for row, seq, result, elapsed in results:
                    self._last_results[row] = (seq, result, elapsed)
                    ok = result.success
                    passed += int(ok)
                    self._table.item(row, 3).setText("通过" if ok else "失败")
                    self._table.item(row, 3).setForeground(
                        QColor("#4CAF50") if ok else QColor("#F44336"))
                    self._table.item(row, 4).setText(f"{elapsed*1000:.0f} ms")
                    detail = ""
                    if not ok and result.failed_step_name:
                        detail = f"{result.failed_step_name}"
                        # 附带失败步骤的校验错误（含NRC可读名）:
                        # 按阶段定位首个失败步骤（前置步骤存在时索引不对位）
                        failed_sr = next(
                            (sr for sr in result.step_results
                             if not sr.is_positive
                             and sr.phase in ("前置条件", "主步骤")), None)
                        if failed_sr is not None and failed_sr.error_message:
                            err = failed_sr.error_message
                            nrc_txt = ""
                            if err.startswith("NRC: "):
                                try:
                                    code = int(err[5:], 16)
                                    nrc_txt = f" {_NRC_NAMES.get(code, '')}"
                                except ValueError:
                                    pass
                            detail += f" | {err}{nrc_txt}"
                    self._table.item(row, 5).setText(detail)
                    self._log(f"[{seq.name}] {'通过' if ok else '失败'} ({elapsed*1000:.0f} ms)")
                self._log(f"测试完成: {passed}/{len(results)} 通过")
            finally:
                _cleanup()

        def _on_error(msg):
            self._log(f"测试执行异常: {msg}")
            _cleanup()

        worker.finished.connect(_on_done)
        worker.error.connect(_on_error)
        thread.started.connect(worker.run)
        self._worker = worker
        self._worker_thread = thread
        self.tests_started.emit()  # 主窗口据此暂停会话保活，避免3E帧干扰用例收发
        self._log(f"开始执行 {len(rows)} 个测试用例...")
        thread.start()

    # ---------------- 报告 ----------------

    def _export_report(self):
        """导出测试报告CSV: 有执行结果时输出每步TX/RX报文明细，
        未运行过则退化为表格摘要导出"""
        report_dir = os.path.join(get_project_root(), "reports")
        os.makedirs(report_dir, exist_ok=True)
        default_name = f"test_report_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出测试报告",
            os.path.join(report_dir, default_name), "CSV Files (*.csv)")
        if not filepath:
            return
        import csv
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if self._last_results:
                # 步骤级明细报告: 每行一步，含请求/响应报文
                writer.writerow([
                    "用例", "用例描述", "用例结果", "用例耗时(ms)",
                    "步骤阶段", "步骤序号", "步骤名称",
                    "请求报文(TX)", "响应报文(RX)",
                    "步骤结果", "步骤耗时(ms)", "错误信息"])
                for row in sorted(self._last_results):
                    seq, result, elapsed = self._last_results[row]
                    case_ok = "通过" if result.success else "失败"
                    for sr in result.step_results:
                        writer.writerow([
                            seq.name, seq.description, case_ok,
                            f"{elapsed*1000:.0f}",
                            sr.phase, sr.step_index + 1, sr.step_name,
                            sr.request_sent.hex(" ").upper(),
                            (sr.response_received.hex(" ").upper()
                             if sr.response_received else "无响应"),
                            "通过" if sr.is_positive else "失败",
                            f"{sr.elapsed_ms:.0f}",
                            sr.error_message])
            else:
                # 未运行过: 退化为当前表格摘要导出
                writer.writerow(["用例", "描述", "步骤数", "结果", "耗时", "失败详情"])
                for row in range(self._table.rowCount()):
                    writer.writerow([
                        self._table.item(row, c).text()
                        if self._table.item(row, c) else "" for c in range(6)])
        self._log(f"测试报告已导出: {filepath}")

    def _log(self, msg: str):
        self._log_text.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def closeEvent(self, event):
        """确保测试线程安全退出"""
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        super().closeEvent(event)
