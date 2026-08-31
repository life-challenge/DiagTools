"""测试中心视图（V2.2 一级页）

自动化诊断测试:
  内置标准测试用例（会话切换/TesterPresent/读DTC/读DID）+
  可加载 resources/sequences 下的自定义序列。
执行在后台线程完成，结果表显示通过/失败，可导出测试报告CSV。
"""

import os
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog, QTextEdit
)
from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QColor
from src.ui.async_uds import UdsWorker
from src.business.sequence_manager import (
    SequenceManager, SequenceStep, UdsSequence)


def _builtin_cases() -> list:
    """内置标准测试用例（不硬编码ECU名，均为标准服务）"""
    cases = []

    seq = UdsSequence("会话切换测试", "扩展会话→默认会话")
    seq.steps.append(SequenceStep(
        "进入扩展会话", bytes.fromhex("10 03".replace(" ", "")),
        expected_response=bytes.fromhex("50 03".replace(" ", ""))))
    seq.steps.append(SequenceStep(
        "返回默认会话", bytes.fromhex("10 01".replace(" ", "")),
        expected_response=bytes.fromhex("50 01".replace(" ", "")),
        delay_before_ms=200))
    cases.append(seq)

    seq = UdsSequence("TesterPresent测试", "3E 00 抑制正响应除外")
    seq.steps.append(SequenceStep(
        "TesterPresent", bytes.fromhex("3E00"),
        expected_response=bytes.fromhex("7E00")))
    cases.append(seq)

    seq = UdsSequence("读故障码测试", "19 02 FF 按状态掩码读DTC")
    seq.steps.append(SequenceStep(
        "ReadDTCInformation", bytes.fromhex("1902FF"),
        expected_response=bytes.fromhex("59"),
        check_positive=True))
    seq.steps[0].expected_response = bytes([0x59])  # 仅校验正响应首字节由框架处理
    cases.append(seq)

    seq = UdsSequence("读识别DID测试", "22 F190 VIN")
    seq.steps.append(SequenceStep(
        "ReadDataByIdentifier", bytes.fromhex("22F190"),
        check_positive=True))
    cases.append(seq)
    return cases


class TestCenterView(QWidget):
    """测试中心"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._uds_client = None
        self._seq_manager = SequenceManager()
        self._cases: list = _builtin_cases()
        self._worker = None
        self._worker_thread = None
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
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        start_dir = os.path.join(project_root, "resources", "sequences")
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

        def _on_done(results):
            try:
                passed = 0
                for row, seq, result, elapsed in results:
                    ok = result.success
                    passed += int(ok)
                    self._table.item(row, 3).setText("通过" if ok else "失败")
                    self._table.item(row, 3).setForeground(
                        QColor("#4CAF50") if ok else QColor("#F44336"))
                    self._table.item(row, 4).setText(f"{elapsed*1000:.0f} ms")
                    detail = ""
                    if not ok and result.failed_step_name:
                        detail = f"步骤{result.failed_step}: {result.failed_step_name}"
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
        self._log(f"开始执行 {len(rows)} 个测试用例...")
        thread.start()

    # ---------------- 报告 ----------------

    def _export_report(self):
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        report_dir = os.path.join(project_root, "reports")
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
