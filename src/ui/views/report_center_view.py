"""报告中心视图（V2.2 Phase 18）

统一报告入口:
  - 全车扫描报告
  - ECU诊断报告（DTC）
  - 刷写报告（预留接口）
格式: HTML / CSV / JSON（PDF后续）
输出: <项目根>/reports/
"""

import os
import csv
import json
import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog
)
from PyQt6.QtCore import Qt

from src.utils.paths import get_project_root


class ReportCenterView(QWidget):
    """报告中心工作区"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project_root = get_project_root()
        self._report_dir = os.path.join(self._project_root, "reports")
        self._init_ui()
        self.refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # ---- 标题 + 操作 ----
        head = QHBoxLayout()
        title = QLabel("报告中心")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch(1)
        self._btn_open_dir = QPushButton("打开报告目录")
        self._btn_open_dir.clicked.connect(self._open_report_dir)
        head.addWidget(self._btn_open_dir)
        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.clicked.connect(self.refresh)
        head.addWidget(self._btn_refresh)
        layout.addLayout(head)

        hint = QLabel(
            "报告由 文件→导出→诊断报告 / 全车扫描 / 刷写流程 自动生成；"
            "支持 HTML / CSV / JSON，PDF 后续增加。")
        hint.setStyleSheet("color: #888;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ---- 报告列表 ----
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(
            ["类型", "文件名", "大小", "生成时间"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self._table, 1)

        # ---- 底部操作 ----
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self._btn_open = QPushButton("打开报告")
        self._btn_open.setObjectName("btn_primary")
        self._btn_open.clicked.connect(self._open_selected)
        bottom.addWidget(self._btn_open)
        layout.addLayout(bottom)

    # ---------------- 对外接口 ----------------

    def refresh(self):
        """重新扫描报告目录"""
        rows = []
        if os.path.isdir(self._report_dir):
            for name in os.listdir(self._report_dir):
                path = os.path.join(self._report_dir, name)
                if not os.path.isfile(path):
                    continue
                st = os.stat(path)
                rows.append((
                    self._classify(name), name,
                    f"{st.st_size / 1024:.1f} KB",
                    time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(st.st_mtime))))
        rows.sort(key=lambda r: r[3], reverse=True)
        self._table.setRowCount(len(rows))
        for row, (rtype, name, size, mtime) in enumerate(rows):
            self._table.setItem(row, 0, QTableWidgetItem(rtype))
            self._table.setItem(row, 1, QTableWidgetItem(name))
            self._table.setItem(row, 2, QTableWidgetItem(size))
            self._table.setItem(row, 3, QTableWidgetItem(mtime))

    @staticmethod
    def _classify(name: str) -> str:
        low = name.lower()
        if "scan" in low:
            return "全车扫描报告"
        if "dtc" in low:
            return "DTC诊断报告"
        if "did" in low:
            return "DID快照报告"
        if "flash" in low:
            return "刷写报告"
        if "test" in low:
            return "测试报告"
        return "其他"

    # ---------------- 内部 ----------------

    def _selected_path(self) -> str:
        row = self._table.currentRow()
        if row < 0:
            return ""
        item = self._table.item(row, 1)
        return os.path.join(self._report_dir, item.text()) if item else ""

    def _open_selected(self):
        path = self._selected_path()
        if not path:
            return
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])

    def _open_report_dir(self):
        os.makedirs(self._report_dir, exist_ok=True)
        if os.name == "nt":
            os.startfile(self._report_dir)  # noqa: S606
        else:
            import subprocess
            subprocess.Popen(["xdg-open", self._report_dir])


def export_scan_report_csv(results: list, ecu_defs: list, report_dir: str) -> str:
    """全车扫描报告导出（CSV）: 供主窗口扫描完成后调用"""
    os.makedirs(report_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(report_dir, f"scan_report_{ts}.csv")
    found = {(e.tx_id, e.rx_id) for e in results}
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ECU", "状态", "TX", "RX", "描述"])
        for d in ecu_defs:
            online = (d.tx_id, d.rx_id) in found
            writer.writerow([
                d.name, "Online" if online else "Offline",
                f"0x{d.tx_id:03X}", f"0x{d.rx_id:03X}", d.description or ""])
    return filepath
