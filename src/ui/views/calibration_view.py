"""标定中心视图（V2.2 一级页）

基于 UDS 22/2E 服务的 DID 级标定:
  加载DID定义 → 批量读取当前值 → 编辑目标值 → 写入 → 回读校验。
数据流图表与轮询由ECU诊断工作区承担，本页专注标定读写。
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QTextEdit, QCheckBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from src.business.did_manager import DidManager
from src.utils.paths import get_resource_path


class CalibrationView(QWidget):
    """标定中心"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._uds_client = None
        self._did_manager = DidManager()
        self._init_ui()

    def set_uds_client(self, client):
        self._uds_client = client

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ---- 工具栏 ----
        bar = QHBoxLayout()
        btn_load = QPushButton("加载DID定义")
        btn_load.clicked.connect(self._load_definitions)
        bar.addWidget(btn_load)
        btn_read = QPushButton("读取全部")
        btn_read.setObjectName("btn_primary")
        btn_read.clicked.connect(self._read_all)
        bar.addWidget(btn_read)
        btn_write = QPushButton("写入选中")
        btn_write.clicked.connect(self._write_selected)
        bar.addWidget(btn_write)
        btn_write_all = QPushButton("写入全部已编辑")
        btn_write_all.clicked.connect(self._write_all_edited)
        bar.addWidget(btn_write_all)
        self._chk_verify = QCheckBox("写入后回读校验")
        self._chk_verify.setChecked(True)
        bar.addWidget(self._chk_verify)
        bar.addStretch()
        self._lbl_info = QLabel("未加载定义")
        self._lbl_info.setStyleSheet("color: #888;")
        bar.addWidget(self._lbl_info)
        layout.addLayout(bar)

        # ---- 标定表 ----
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["DID", "名称", "类型", "长度", "当前值(原始)", "写入值(原始)", "状态"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table, 1)

        # ---- 日志 ----
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFixedHeight(110)
        layout.addWidget(self._log_text)

    # ---------------- 定义加载 ----------------

    def load_definitions_file(self, filepath: str) -> int:
        """程序化入口（项目加载时恢复）"""
        count = self._did_manager.load_definitions_from_json(filepath)
        if count:
            self._refresh_table()
            self._lbl_info.setText(f"{count} 个DID定义")
        return count

    def _load_definitions(self):
        start_dir = get_resource_path("did_definitions")
        filepath, _ = QFileDialog.getOpenFileName(
            self, "加载DID定义", start_dir, "JSON Files (*.json)")
        if not filepath:
            return
        count = self.load_definitions_file(filepath)
        self._log(f"加载 {count} 个DID定义: {os.path.basename(filepath)}"
                  if count else f"定义加载失败: {filepath}")

    def _refresh_table(self):
        defs = self._did_manager.definitions
        self._table.itemChanged.disconnect(self._on_item_changed)
        try:
            self._table.setRowCount(len(defs))
            for row, (did_id, d) in enumerate(sorted(defs.items())):
                it_did = QTableWidgetItem(f"{did_id:04X}")
                it_did.setFlags(it_did.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, 0, it_did)
                for col, text in ((1, d.name), (2, d.data_type),
                                  (3, str(d.length))):
                    it = QTableWidgetItem(text)
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self._table.setItem(row, col, it)
                self._table.setItem(row, 4, QTableWidgetItem("--"))
                self._table.setItem(row, 5, QTableWidgetItem(""))
                self._set_status(row, "")
        finally:
            self._table.itemChanged.connect(self._on_item_changed)

    # ---------------- 读取 ----------------

    def _read_all(self):
        if not self._uds_client:
            self._log("未连接UDS，无法读取")
            return
        for row in range(self._table.rowCount()):
            did_id = int(self._table.item(row, 0).text(), 16)
            resp = self._uds_client.read_data_by_identifier(did_id)
            if resp and len(resp) >= 3 and resp[0] == 0x62:
                raw = resp[3:]
                self._table.item(row, 4).setText(raw.hex(" ").upper())
                self._set_status(row, "已读")
            else:
                self._set_status(row, "读取失败", ok=False)
        self._log(f"读取完成: {self._table.rowCount()} 个DID")

    # ---------------- 写入 ----------------

    def _write_selected(self):
        row = self._table.currentRow()
        if row < 0:
            self._log("请先选中一行")
            return
        self._write_row(row)

    def _write_all_edited(self):
        written = 0
        for row in range(self._table.rowCount()):
            if self._table.item(row, 5).text().strip():
                if self._write_row(row):
                    written += 1
        self._log(f"批量写入完成: {written} 项")

    def _write_row(self, row: int) -> bool:
        if not self._uds_client:
            self._log("未连接UDS，无法写入")
            return False
        did_text = self._table.item(row, 0).text()
        write_text = self._table.item(row, 5).text().strip()
        if not write_text:
            self._set_status(row, "无写入值", ok=False)
            return False
        try:
            data = bytes.fromhex(write_text.replace(" ", ""))
        except ValueError:
            self._set_status(row, "HEX格式错误", ok=False)
            return False
        did_id = int(did_text, 16)
        resp = self._uds_client.write_data_by_identifier(did_id, data)
        if not (resp and resp[0] == 0x6E):
            nrc = f" NRC=0x{resp[2]:02X}" if resp and len(resp) > 2 else ""
            self._set_status(row, f"写入失败{nrc}", ok=False)
            return False
        # 回读校验
        if self._chk_verify.isChecked():
            reread = self._uds_client.read_data_by_identifier(did_id)
            if reread and len(reread) >= 3 and reread[0] == 0x62 \
                    and reread[3:] == data:
                self._table.item(row, 4).setText(data.hex(" ").upper())
                self._set_status(row, "写入并校验通过")
            else:
                self._set_status(row, "写入成功/回读不一致", ok=False)
        else:
            self._set_status(row, "写入成功")
        return True

    # ---------------- 辅助 ----------------

    def _set_status(self, row: int, text: str, ok: bool = True):
        item = self._table.item(row, 6)
        if item is None:
            item = QTableWidgetItem("")
            self._table.setItem(row, 6, item)
        item.setText(text)
        if text:
            item.setForeground(
                QColor("#4CAF50") if ok else QColor("#F44336"))

    def _on_item_changed(self, item: QTableWidgetItem):
        """写入值列变更时标记待写入状态"""
        if item.column() == 5:
            self._set_status(item.row(),
                             "待写入" if item.text().strip() else "")

    def _log(self, msg: str):
        import time
        self._log_text.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
