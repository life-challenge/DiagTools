"""故障码诊断面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                              QHeaderView, QComboBox, QTextEdit, QFileDialog,
                              QCheckBox, QProgressBar)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from src.business.dtc_manager import DtcManager


class DtcPanel(QWidget):
    """故障码诊断面板"""

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        self._dtc_manager = DtcManager()
        self._dtc_count = None  # 最近一次读取的DTC总数（未读取时为None）
        self._init_ui()

    def set_uds_client(self, client):
        self._uds_client = client

    def dtc_count(self):
        """最近一次读取的DTC总数，供状态卡显示；未读取返回None"""
        return self._dtc_count

    @property
    def dtc_records(self) -> list:
        """当前已解析的DTC记录（供报告导出）"""
        return list(self._dtc_manager.current_dtcs.values())

    def read_from_ecu(self):
        """外部工具入口（工具-批量读取DTC）: 触发一次读取并刷新表格"""
        self._read_dtcs()

    def import_definitions(self, filepath: str) -> int:
        """程序化导入DTC定义（菜单/项目加载入口），返回导入数量"""
        return self._dtc_manager.load_definitions(filepath)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("读取模式:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems([
            "0x02 - 按状态掩码读取",
            "0x01 - 读取DTC数量",
            "0x04 - 读取快照数据",
            "0x06 - 读取扩展数据",
            "0x0A - 读取所有DTC",
        ])
        toolbar.addWidget(self._mode_combo)

        self._read_btn = QPushButton("读取DTC")
        self._read_btn.clicked.connect(self._read_dtcs)
        toolbar.addWidget(self._read_btn)

        self._clear_btn = QPushButton("清除DTC")
        self._clear_btn.clicked.connect(self._clear_dtcs)
        toolbar.addWidget(self._clear_btn)

        self._export_btn = QPushButton("导出CSV")
        self._export_btn.clicked.connect(self._export_csv)
        toolbar.addWidget(self._export_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 汇总信息
        summary_layout = QHBoxLayout()
        self._total_label = QLabel("总计: 0")
        self._confirmed_label = QLabel("已确认: 0")
        self._confirmed_label.setStyleSheet("color: #F44336;")
        self._pending_label = QLabel("待确认: 0")
        self._pending_label.setStyleSheet("color: #FF9800;")
        self._history_label = QLabel("历史: 0")
        self._history_label.setStyleSheet("color: #9E9E9E;")
        summary_layout.addWidget(self._total_label)
        summary_layout.addWidget(self._confirmed_label)
        summary_layout.addWidget(self._pending_label)
        summary_layout.addWidget(self._history_label)
        summary_layout.addStretch()
        layout.addLayout(summary_layout)

        # DTC列表表格
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["DTC ID", "名称", "状态", "状态描述", "严重度", "出现次数"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

        # DTC状态位可视化
        status_group = QGroupBox("DTC状态位 (选中DTC)")
        status_layout = QHBoxLayout()
        self._status_bits = []
        for i in range(8):
            cb = QCheckBox(f"Bit{i}")
            cb.setEnabled(False)
            self._status_bits.append(cb)
            status_layout.addWidget(cb)
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        # 日志
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(120)
        layout.addWidget(self._log_text)

    def _read_dtcs(self):
        if not self._uds_client:
            self._log("未连接UDS客户端")
            return
        mode_idx = self._mode_combo.currentIndex()
        sub_funcs = [0x02, 0x01, 0x04, 0x06, 0x0A]
        sub_func = sub_funcs[mode_idx] if mode_idx < len(sub_funcs) else 0x02

        if sub_func == 0x02:
            resp = self._uds_client.send_raw(bytes([0x19, 0x02, 0xFF]))
        elif sub_func == 0x0A:
            resp = self._uds_client.send_raw(bytes([0x19, 0x0A, 0xFF]))
        else:
            resp = self._uds_client.send_raw(bytes([0x19, sub_func]))

        if resp and resp[0] == 0x59:
            records = self._dtc_manager.parse_read_dtc_response(resp)
            self._refresh_table(records)
            self._log(f"读取到 {len(records)} 个DTC")
        else:
            self._log("读取DTC失败")

    def _refresh_table(self, records):
        self._table.setRowCount(len(records))
        for row, r in enumerate(records):
            self._table.setItem(row, 0, QTableWidgetItem(r.dtc_id_hex))
            self._table.setItem(row, 1, QTableWidgetItem(r.definition))
            self._table.setItem(row, 2, QTableWidgetItem(f"0x{r.status:02X}"))
            self._table.setItem(row, 3, QTableWidgetItem(r.status_description))

            sev_item = QTableWidgetItem(r.severity_level)
            if r.severity_level == "confirmed":
                sev_item.setForeground(QColor("#F44336"))
            elif r.severity_level == "pending":
                sev_item.setForeground(QColor("#FF9800"))
            else:
                sev_item.setForeground(QColor("#9E9E9E"))
            self._table.setItem(row, 4, sev_item)
            self._table.setItem(row, 5, QTableWidgetItem(str(r.occurrence)))

        # 更新汇总
        summary = self._dtc_manager.get_dtc_summary()
        self._dtc_count = summary['total']
        self._total_label.setText(f"总计: {summary['total']}")
        self._confirmed_label.setText(f"已确认: {summary['confirmed']}")
        self._pending_label.setText(f"待确认: {summary['pending']}")
        self._history_label.setText(f"历史: {summary['history']}")

    def _on_selection_changed(self):
        row = self._table.currentRow()
        if row < 0:
            return
        status_text = self._table.item(row, 2).text()
        status = int(status_text, 16)
        for i in range(8):
            self._status_bits[i].setChecked(bool(status & (1 << i)))

    def _clear_dtcs(self):
        if not self._uds_client:
            return
        resp = self._uds_client.clear_dtc()
        if resp and resp[0] == 0x54:
            self._dtc_manager.clear()
            self._table.setRowCount(0)
            self._dtc_count = 0
            self._log("DTC已清除")
        else:
            self._log("清除DTC失败")

    def _export_csv(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出DTC", "dtc_report.csv", "CSV Files (*.csv)")
        if filepath:
            self._dtc_manager.export_to_csv(filepath)
            self._log(f"DTC已导出到 {filepath}")

    def _log(self, msg: str):
        import time
        ts = time.strftime("%H:%M:%S")
        self._log_text.append(f"[{ts}] {msg}")
