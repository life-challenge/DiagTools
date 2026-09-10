"""A2L导入对话框（文件→导入→A2L）

解析 .a2l 文件中的 MEASUREMENT / CHARACTERISTIC 块并列表展示，
可将解析结果导出为JSON到 resources/a2l/ 供标定参考。
"""

import os
import json
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView
)
from src.business.a2l_parser import A2lParser


class A2lImportDialog(QDialog):
    """A2L解析与导出"""

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入 A2L")
        self.resize(760, 480)
        self._project_root = project_root
        self._db = None
        self._source_file = ""
        self.exported_path = ""        # 导出成功后由外部读取
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 文件行
        file_row = QHBoxLayout()
        self._file_edit = QLineEdit()
        self._file_edit.setReadOnly(True)
        self._file_edit.setPlaceholderText("选择A2L文件 (*.a2l)...")
        file_row.addWidget(self._file_edit, 1)
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(self._browse)
        file_row.addWidget(btn_browse)
        btn_parse = QPushButton("解析")
        btn_parse.setObjectName("btn_primary")
        btn_parse.clicked.connect(self._parse)
        file_row.addWidget(btn_parse)
        layout.addLayout(file_row)

        self._lbl_info = QLabel("未解析")
        self._lbl_info.setStyleSheet("color: #888;")
        layout.addWidget(self._lbl_info)

        # 结果表
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["类型", "名称", "地址", "数据类型", "描述"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self._table, 1)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_export = QPushButton("导出到 resources/a2l")
        btn_export.clicked.connect(self._export)
        btn_row.addWidget(btn_export)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    # ---------------- 操作 ----------------

    def _browse(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择A2L文件", "", "A2L文件 (*.a2l);;所有文件 (*)")
        if filepath:
            self._file_edit.setText(filepath)
            self._source_file = filepath
            self._parse()

    def _parse(self):
        filepath = self._file_edit.text().strip()
        if not filepath:
            return
        self._db = A2lParser().parse_file(filepath)
        if self._db is None:
            self._lbl_info.setText("解析失败")
            self._lbl_info.setStyleSheet("color: #F44336;")
            return
        self._refresh_table()
        self._lbl_info.setText(
            f"项目: {self._db.project} / 模块: {self._db.module} | "
            f"测量 {len(self._db.measurements)} | 标定 {len(self._db.characteristics)}")
        self._lbl_info.setStyleSheet("color: #4CAF50;")

    def _refresh_table(self):
        entries = self._db.entries
        self._table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            kind_text = "测量" if e.kind == "MEASUREMENT" else "标定"
            self._table.setItem(row, 0, QTableWidgetItem(kind_text))
            self._table.setItem(row, 1, QTableWidgetItem(e.name))
            self._table.setItem(row, 2, QTableWidgetItem(f"0x{e.address:X}"))
            self._table.setItem(row, 3, QTableWidgetItem(e.data_type))
            self._table.setItem(row, 4, QTableWidgetItem(e.description))

    def _export(self):
        if not self._db or not self._db.entries:
            self._lbl_info.setText("无解析结果可导出")
            self._lbl_info.setStyleSheet("color: #FF9800;")
            return
        out_dir = os.path.join(self._project_root, "resources", "a2l")
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(self._source_file))[0]
        out_path = os.path.join(out_dir, f"{base}.json")
        data = {
            "project": self._db.project,
            "module": self._db.module,
            "source": self._source_file,
            "entries": [e.to_dict() for e in self._db.entries],
        }
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            self._lbl_info.setText(f"导出失败: {e}")
            self._lbl_info.setStyleSheet("color: #F44336;")
            return
        self.exported_path = out_path
        self._lbl_info.setText(f"已导出: {out_path}")
        self._lbl_info.setStyleSheet("color: #4CAF50;")
