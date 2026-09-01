"""故障码诊断面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                              QHeaderView, QComboBox, QFileDialog,
                              QCheckBox, QProgressBar, QLineEdit, QInputDialog)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from src.business.dtc_manager import DtcManager

_MONO = QFont("Consolas", 10)

# 0x19子功能 → (是否需要状态掩码, 是否需要DTC号)
_MODES = [
    (0x02, "0x02 - 按状态掩码读取", True, False),
    (0x01, "0x01 - 读取DTC数量", True, False),
    (0x04, "0x04 - 读取快照数据", False, True),
    (0x06, "0x06 - 读取扩展数据", False, True),
    (0x0A, "0x0A - 读取所有DTC", False, False),
]

# 常用状态掩码预设 (ISO 14229-1 DTC状态位)
_MASK_PRESETS = [
    (0xFF, "0xFF - 全部状态"),
    (0x09, "0x09 - 当前失败+已确认"),
    (0x08, "0x08 - 已确认"),
    (0x04, "0x04 - 待定"),
    (0x02, "0x02 - 本周期失败"),
    (0x01, "0x01 - 当前失败"),
]


class DtcPanel(QWidget):
    """故障码诊断面板"""

    # 业务日志转发(msg, level): 面板不再内置日志窗口，统一由主窗口业务日志呈现
    business_log = pyqtSignal(str, str)

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

    def clear_results(self):
        """清空DTC读取结果（切换ECU时旧ECU的数据不再有效）"""
        self._dtc_count = None
        self._dtc_manager.clear()
        self._table.setRowCount(0)

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
        for sub, label, _, _ in _MODES:
            self._mode_combo.addItem(label, sub)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        toolbar.addWidget(self._mode_combo)

        # 状态掩码（0x01/0x02子功能用）
        toolbar.addWidget(QLabel("状态掩码:"))
        self._mask_combo = QComboBox()
        for value, label in _MASK_PRESETS:
            self._mask_combo.addItem(label, value)
        self._mask_combo.addItem("自定义...", -1)
        self._mask_combo.setToolTip(
            "ISO 14229-1 DTC状态位掩码，仅 0x01/0x02 模式使用:\n"
            "Bit0 当前失败 Bit1 本周期失败 Bit2 待定 Bit3 已确认\n"
            "Bit4 已不再失败 Bit5 已完成周期 Bit6 警告灯请求 Bit7 警告灯点亮")
        toolbar.addWidget(self._mask_combo)

        # DTC号（0x04/0x06子功能用，3字节hex）
        toolbar.addWidget(QLabel("DTC号:"))
        self._dtc_edit = QLineEdit()
        self._dtc_edit.setPlaceholderText("如 F00913")
        self._dtc_edit.setFixedWidth(110)
        self._dtc_edit.setFont(_MONO)
        self._dtc_edit.setToolTip(
            "0x04/0x06 模式的DTC编号（3字节hex）；FF FF FF 表示全部")
        toolbar.addWidget(self._dtc_edit)

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
        self._on_mode_changed()  # 初始化控件可用状态

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

        layout.addStretch()

    def _on_mode_changed(self):
        """模式切换: 按子功能需求启停掩码/DTC号输入"""
        idx = self._mode_combo.currentIndex()
        need_mask = _MODES[idx][2] if 0 <= idx < len(_MODES) else True
        need_dtc = _MODES[idx][3] if 0 <= idx < len(_MODES) else False
        self._mask_combo.setEnabled(need_mask)
        self._dtc_edit.setEnabled(need_dtc)

    def _current_mask(self) -> int:
        """当前选中的状态掩码，自定义时弹输入框"""
        value = self._mask_combo.currentData()
        if value == -1:
            text, ok = QInputDialog.getText(
                self, "自定义状态掩码",
                "状态掩码（1字节hex，如 09 或 0x09）:")
            if not ok:
                return 0xFF
            try:
                v = int(text.strip().replace("0x", ""), 16)
                return v & 0xFF
            except ValueError:
                self._log("掩码格式错误，回退到 0xFF")
                return 0xFF
        return value & 0xFF

    def _current_dtc(self) -> bytes:
        """当前输入的DTC号（3字节），空或非法时默认 FF FF FF"""
        text = self._dtc_edit.text().strip().replace(" ", "")
        if not text:
            return b"\xFF\xFF\xFF"
        try:
            raw = bytes.fromhex(text)
            if len(raw) == 3:
                return raw
            if len(raw) < 3:
                return raw.rjust(3, b"\x00")
            return raw[:3]
        except ValueError:
            self._log("DTC号格式错误，使用 FF FF FF")
            return b"\xFF\xFF\xFF"

    def _read_dtcs(self):
        if not self._uds_client:
            self._log("未连接UDS客户端")
            return
        idx = self._mode_combo.currentIndex()
        sub_func = _MODES[idx][0] if 0 <= idx < len(_MODES) else 0x02

        if sub_func in (0x01, 0x02):
            mask = self._current_mask()
            req = bytes([0x19, sub_func, mask])
            resp = self._uds_client.send_raw(req)
        elif sub_func == 0x0A:
            # 0x0A报所有支持的DTC，不带状态掩码
            req = bytes([0x19, 0x0A])
            resp = self._uds_client.send_raw(req)
        elif sub_func in (0x04, 0x06):
            # 快照/扩展数据按DTC号读取，响应为非标准记录格式，原样记录日志
            req = bytes([0x19, sub_func]) + self._current_dtc()
            resp = self._uds_client.send_raw(req)
            self._log(f"Request: {req.hex(' ').upper()}")
            if resp:
                self._log(f"Response: {resp.hex(' ').upper()}")
            else:
                self._log("无响应")
            return
        else:
            resp = self._uds_client.send_raw(bytes([0x19, sub_func]))

        if resp and resp[0] == 0x59:
            records = self._dtc_manager.parse_read_dtc_response(resp)
            self._refresh_table(records)
            self._log(f"读取到 {len(records)} 个DTC", "SUCCESS")
        elif resp is None:
            self._log("读取DTC失败: 无响应 (超时)", "ERROR")
        elif resp[0] == 0x7F and len(resp) >= 3:
            from src.protocol.uds_services import UdsService
            nrc = resp[2]
            self._log(f"读取DTC失败: 负响应 {resp.hex(' ').upper()} "
                      f"(NRC 0x{nrc:02X} {UdsService.get_nrc_description(nrc)})",
                      "ERROR")
        else:
            self._log(f"读取DTC失败: 意外响应 {resp.hex(' ').upper()}", "ERROR")

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

    def _log(self, msg: str, level: str = "INFO"):
        self.business_log.emit(msg, level)
