"""ODX/PDX/CDD导入对话框（文件→导入→ODX/CDD）

解析诊断数据容器，展示服务与DTC列表；可导出完整JSON或
DTC定义（DTC面板可注入格式 [{"dtc_id": "...", "description": "..."}]）。
"""

import os
import json
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QSplitter
)
from PyQt6.QtCore import Qt
from src.business.odx_parser import OdxParser


class OdxImportDialog(QDialog):
    """ODX/PDX/CDD解析与导出"""

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入 ODX / CDD")
        self.resize(820, 520)
        self._project_root = project_root
        self._db = None
        self._source_file = ""
        self.exported_path = ""        # 完整JSON导出路径
        self.exported_dtc_path = ""    # DTC定义导出路径（供注入）
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 文件行
        file_row = QHBoxLayout()
        self._file_edit = QLineEdit()
        self._file_edit.setReadOnly(True)
        self._file_edit.setPlaceholderText(
            "选择诊断数据文件 (*.odx / *.pdx / *.cdd)...")
        file_row.addWidget(self._file_edit, 1)
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(self._browse)
        file_row.addWidget(btn_browse)
        btn_parse = QPushButton("解析")
        btn_parse.setObjectName("btn_primary")
        btn_parse.clicked.connect(self._parse)
        file_row.addWidget(btn_parse)
        layout.addLayout(file_row)

        self._lbl_info = QLabel(
            "支持 ODX 与 PDX 容器；CDD 仅支持ZIP封装（二进制旧版请从"
            "CANdelaStudio导出ODX）")
        self._lbl_info.setStyleSheet("color: #888;")
        self._lbl_info.setWordWrap(True)
        layout.addWidget(self._lbl_info)

        # 服务表 / DTC表
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._comm_table = self._mk_table(
            ["服务", "类型", "请求", "描述"], (1, 3))
        splitter.addWidget(self._mk_group("诊断服务 (DIAG-COMM)",
                                          self._comm_table))
        self._dtc_table = self._mk_table(
            ["故障码", "值", "描述"], (0, 2))
        splitter.addWidget(self._mk_group("DTC", self._dtc_table))
        splitter.setSizes([240, 180])
        layout.addWidget(splitter, 1)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_export = QPushButton("导出JSON")
        btn_export.setToolTip("完整解析结果导出到 resources/odx/")
        btn_export.clicked.connect(self._export)
        btn_row.addWidget(btn_export)
        btn_dtc = QPushButton("导出DTC定义")
        btn_dtc.setToolTip("导出为DTC面板可注入的定义文件并自动注入")
        btn_dtc.clicked.connect(self._export_dtcs)
        btn_row.addWidget(btn_dtc)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    @staticmethod
    def _mk_table(headers: list, stretch_cols: tuple) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        header = table.horizontalHeader()
        for col in stretch_cols:
            header.setSectionResizeMode(
                col, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        return table

    @staticmethod
    def _mk_group(title: str, table: QTableWidget):
        from PyQt6.QtWidgets import QGroupBox, QVBoxLayout
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(table)
        return box

    # ---------------- 操作 ----------------

    def _browse(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择诊断数据文件", "",
            "诊断数据 (*.odx *.pdx *.cdd);;所有文件 (*)")
        if filepath:
            self._file_edit.setText(filepath)
            self._source_file = filepath
            self._parse()

    def _parse(self):
        filepath = self._file_edit.text().strip()
        if not filepath:
            return
        self._db = OdxParser().parse_file(filepath)
        if self._db is None:
            self._lbl_info.setText(
                "解析失败（二进制CDD不受支持，请在CANdelaStudio中导出ODX/PDX）")
            self._lbl_info.setStyleSheet("color: #F44336;")
            return
        self._refresh_tables()
        self._lbl_info.setText(
            f"项目: {self._db.project} | {self._db.xml_count} 个XML | "
            f"服务 {len(self._db.comms)} | DTC {len(self._db.dtcs)} | "
            f"计算方法 {self._db.compu_count}")
        self._lbl_info.setStyleSheet("color: #4CAF50;")

    def _refresh_tables(self):
        comms = self._db.comms
        self._comm_table.setRowCount(len(comms))
        for row, c in enumerate(comms):
            self._comm_table.setItem(row, 0, QTableWidgetItem(c.name))
            self._comm_table.setItem(row, 1, QTableWidgetItem(c.kind))
            self._comm_table.setItem(
                row, 2, QTableWidgetItem(c.request.hex(" ").upper()))
            self._comm_table.setItem(row, 3, QTableWidgetItem(c.desc))

        dtcs = self._db.dtcs
        self._dtc_table.setRowCount(len(dtcs))
        for row, d in enumerate(dtcs):
            self._dtc_table.setItem(row, 0, QTableWidgetItem(d.code))
            self._dtc_table.setItem(
                row, 1,
                QTableWidgetItem(f"0x{d.dtc_id:06X}" if d.dtc_id is not None
                                 else "--"))
            self._dtc_table.setItem(row, 2, QTableWidgetItem(d.desc))

    def _base_name(self) -> str:
        return os.path.splitext(os.path.basename(self._source_file))[0]

    def _out_dir(self) -> str:
        out_dir = os.path.join(self._project_root, "resources", "odx")
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _export(self):
        if not self._db:
            return
        out_path = os.path.join(self._out_dir(), f"{self._base_name()}.json")
        data = {
            "project": self._db.project,
            "source": self._source_file,
            "comms": [{"name": c.name, "kind": c.kind,
                       "request": c.request.hex(" ").upper(),
                       "description": c.desc} for c in self._db.comms],
            "dtcs": [{"code": d.code,
                      "dtc_id": f"0x{d.dtc_id:06X}" if d.dtc_id is not None
                                else None,
                      "description": d.desc} for d in self._db.dtcs],
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

    def _export_dtcs(self):
        if not self._db or not self._db.dtcs:
            self._lbl_info.setText("无DTC数据可导出")
            self._lbl_info.setStyleSheet("color: #FF9800;")
            return
        items = []
        for d in self._db.dtcs:
            if d.dtc_id is None:
                continue
            items.append({
                "dtc_id": f"{d.dtc_id:06X}",
                "description": d.desc or d.code,
            })
        if not items:
            self._lbl_info.setText("DTC均无可识别的数值编码")
            self._lbl_info.setStyleSheet("color: #FF9800;")
            return
        out_path = os.path.join(
            self._out_dir(), f"{self._base_name()}_dtcs.json")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except OSError as e:
            self._lbl_info.setText(f"导出失败: {e}")
            self._lbl_info.setStyleSheet("color: #F44336;")
            return
        self.exported_dtc_path = out_path
        self._lbl_info.setText(
            f"已导出 {len(items)} 条DTC定义: {out_path}（关闭后自动注入）")
        self._lbl_info.setStyleSheet("color: #4CAF50;")
