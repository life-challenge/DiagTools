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
    QFileDialog, QTextEdit
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
          delay_before: int = 0, delay_after: int = 0) -> SequenceStep:
    """用例步骤构造快捷函数"""
    return SequenceStep(
        name, bytes.fromhex(req_hex.replace(" ", "")),
        expected_response=(bytes.fromhex(exp_hex.replace(" ", ""))
                           if exp_hex else None),
        expected_nrc=nrc, expect_no_response=no_resp,
        check_positive=True,
        delay_before_ms=delay_before, delay_after_ms=delay_after)


def _seq(name: str, desc: str, stop_on_error: bool = True) -> UdsSequence:
    s = UdsSequence(name, desc)
    s.stop_on_error = stop_on_error
    return s


def _pre_ext_session() -> SequenceStep:
    """前置条件: 进入扩展会话（部分服务在非默认会话才支持，如27/2E/85）"""
    return _step("进入扩展会话", "10 03", exp_hex="50 03")


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
    seq = _seq("SC-01 会话切换循环", "默认→编程→扩展→默认 正响应前缀校验")
    seq.steps.append(_step("默认会话", "10 01", exp_hex="50 01"))
    seq.steps.append(_step("编程会话", "10 02", exp_hex="50 02", delay_before=200))
    seq.steps.append(_step("扩展会话", "10 03", exp_hex="50 03", delay_before=200))
    seq.steps.append(_step("还原默认会话", "10 01", exp_hex="50 01", delay_before=200))
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
               "默认会话下发27应回NRC 0x7F（实现允许时可能正响应，属实现差异）")
    seq.steps.append(_step("默认会话请种子", "27 01", nrc=[0x7F]))
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

    seq = _seq("SA-04 安全长度错误", "仅发27应回NRC 0x13")
    seq.steps.append(_step("27缺子功能", "27", nrc=[0x13]))
    cases.append(seq)

    # ---- DT: DTC服务 ----
    seq = _seq("DT-01 按状态掩码读DTC", "19 02 FF 应回正响应59")
    seq.steps.append(_step("ReadDTCInformation", "19 02 FF", exp_hex="59"))
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

    seq = _seq("DD-05 2E长度错误", "2E 00 缺数据应回NRC 0x13")
    seq.steps.append(_step("写请求不完整", "2E 00", nrc=[0x13]))
    cases.append(seq)

    # ---- RC: RoutineControl (0x31) ----
    seq = _seq("RC-01 无效例程ID", "31 01 FF FF 应回NRC 0x31 requestOutOfRange")
    seq.steps.append(_step("无效例程", "31 01 FF FF", nrc=[0x31, 0x22]))
    cases.append(seq)

    seq = _seq("RC-02 31非法子功能", "31 00 应回NRC 0x12")
    seq.steps.append(_step("子功能0x00", "31 00 FF FF", nrc=[0x12]))
    cases.append(seq)

    seq = _seq("RC-03 31长度错误", "31 01 缺例程ID应回NRC 0x13")
    seq.steps.append(_step("缺例程ID", "31 01", nrc=[0x13]))
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
        btn_did = QPushButton("生成DID用例")
        btn_did.setToolTip(
            "从DID定义表（诊断调查表JSON）生成用例: 表中每个DID一条独立读取用例\n"
            "(22 <DID> → 期望正响应 62 <DID> 前缀)，重复生成时替换旧的DID-组用例")
        btn_did.clicked.connect(self._generate_did_cases)
        bar.addWidget(btn_did)
        btn_run_all = QPushButton("运行全部")
        btn_run_all.setObjectName("btn_primary")
        btn_run_all.clicked.connect(self._run_all)
        bar.addWidget(btn_run_all)
        btn_run_sel = QPushButton("运行选中")
        btn_run_sel.clicked.connect(self._run_selected)
        bar.addWidget(btn_run_sel)
        btn_export = QPushButton("导出测试报告")
        btn_export.clicked.connect(self._export_report)
        bar.addWidget(btn_export)
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
            for col, text in ((0, seq.name), (1, seq.description),
                              (2, str(len(seq.steps)))):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, it)
            for col in (3, 4, 5):
                it = QTableWidgetItem("--" if col == 3 else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, it)
        self._lbl_summary.setText(f"{len(self._cases)} 个测试用例")

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

    def _generate_did_cases(self):
        """从DID定义表（诊断调查表）生成读取用例: 每个DID一条独立用例

        用例结构: 22 <DID> → 期望响应前缀 62 <DID>（正响应+DID回显，
        数据内容不做断言——运行时值随时变化）。重复生成时替换旧的
        DID-组用例，避免重复堆积。
        """
        start_dir = get_resource_path("did_definitions")
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择DID定义表（诊断调查表）", start_dir,
            "JSON Files (*.json)")
        if not filepath:
            return
        from src.business.did_manager import DidManager
        mgr = DidManager()
        count = mgr.load_definitions_from_json(filepath)
        if not count:
            self._log(f"DID定义表加载失败或为空: {filepath}")
            return
        # 移除旧的DID-组用例（重新生成替换）
        self._cases = [c for c in self._cases if not c.name.startswith("DID-")]
        for did_id, defn in sorted(mgr.definitions.items()):
            desc = defn.description or defn.name
            seq = UdsSequence(
                f"DID-{did_id:04X} {defn.name}",
                f"读取 {desc}（22 {did_id:04X} → 62 {did_id:04X}+数据）")
            did_bytes = did_id.to_bytes(2, "big")
            seq.steps.append(SequenceStep(
                f"读取 {defn.name}",
                bytes([0x22]) + did_bytes,
                expected_response=bytes([0x62]) + did_bytes))
            self._cases.append(seq)
        self._refresh_table()
        self._log(f"已按定义表生成 {count} 条DID读取用例（DID-组）: "
                  f"{os.path.basename(filepath)}")

    # ---------------- 执行 ----------------

    def _run_all(self):
        self._run_cases(list(range(len(self._cases))))

    def _run_selected(self):
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        if not rows:
            self._log("请先选中要运行的用例")
            return
        self._run_cases(rows)

    def _run_cases(self, rows: list):
        if self._worker_thread is not None:
            self._log("上一次测试仍在执行")
            return
        if not self._uds_client:
            self._log("未连接UDS，无法执行测试")
            return
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
