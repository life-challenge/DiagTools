"""故障码诊断面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                              QHeaderView, QComboBox, QFileDialog,
                              QCheckBox, QLineEdit, QInputDialog,
                              QGridLayout)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from src.business.dtc_manager import DtcManager, DTC_STATUS_BITS
from src.ui.widgets.click_combo import make_combo_popup_on_click

_MONO = QFont("Consolas", 10)

# ISO 14229 DTC状态位中文含义（状态位区逐位解释）
_DTC_BIT_LABELS = {
    0: "当前失败",          # testFailed
    1: "本周期失败",        # testFailedThisOperationCycle
    2: "待定",              # pendingDtc
    3: "已确认",            # confirmedDtc
    4: "清除后未完成",      # testNotCompletedSinceLastClear
    5: "清除后失败",        # testFailedSinceLastClear
    6: "本周期未完成",      # testNotCompletedThisOperationCycle
    7: "警告灯请求",        # warningIndicatorRequested
}

# 0x19子功能 → (是否需要状态掩码, 是否需要DTC号+记录号)
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
        self._refresh_dtc_combo()   # 旧ECU读到的“（读取到）”项从下拉移除

    @property
    def dtc_records(self) -> list:
        """当前已解析的DTC记录（供报告导出）"""
        return list(self._dtc_manager.current_dtcs.values())

    def read_from_ecu(self):
        """外部工具入口（工具-批量读取DTC）: 触发一次读取并刷新表格"""
        self._read_dtcs()

    def import_definitions(self, filepath: str) -> int:
        """程序化导入DTC定义（菜单/项目加载入口），返回导入数量

        支持DTC定义JSON列表、调查表JSON（含dtcs键）、OEM调查表xlsx；
        导入后回填已读取记录的名称并刷新表格
        """
        import os
        ext = os.path.splitext(filepath)[1].lower()
        if ext in (".xlsx", ".xlsm"):
            try:
                from src.business.survey_xlsx_parser import parse_survey_xlsx
                items = parse_survey_xlsx(filepath).get("dtcs", [])
            except Exception as e:
                self._log(f"调查表解析失败: {e}", "ERROR")
                return 0
            count = self._dtc_manager.load_definitions_from_list(items)
        else:
            count = self._dtc_manager.load_definitions(filepath)
        if count:
            self._dtc_manager.refresh_current_definitions()
            self._refresh_dtc_combo()   # DTC号下拉同步新定义
            records = list(self._dtc_manager.current_dtcs.values())
            if records:
                self._refresh_table(records)
            self._log(f"加载了 {count} 个DTC定义: "
                      f"{os.path.basename(filepath)}")
        return count

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 工具栏（定义导入统一由主窗口工具栏"定义库"入口提供）
        toolbar = QHBoxLayout()

        toolbar.addWidget(QLabel("读取模式:"))
        self._mode_combo = QComboBox()
        for sub, label, _, _ in _MODES:
            self._mode_combo.addItem(label, sub)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        toolbar.addWidget(self._mode_combo)

        # 状态掩码（0x01/0x02子功能用，其余模式隐藏）
        self._mask_label = QLabel("状态掩码:")
        toolbar.addWidget(self._mask_label)
        self._mask_combo = QComboBox()
        for value, label in _MASK_PRESETS:
            self._mask_combo.addItem(label, value)
        self._mask_combo.addItem("自定义...", -1)
        self._mask_combo.setToolTip(
            "ISO 14229-1 DTC状态位掩码，仅 0x01/0x02 模式使用:\n"
            "Bit0 当前失败 Bit1 本周期失败 Bit2 待定 Bit3 已确认\n"
            "Bit4 已不再失败 Bit5 已完成周期 Bit6 警告灯请求 Bit7 警告灯点亮")
        toolbar.addWidget(self._mask_combo)

        # DTC号（0x04/0x06子功能用，其余模式隐藏）
        self._dtc_label = QLabel("DTC号:")
        toolbar.addWidget(self._dtc_label)
        self._dtc_edit = QComboBox()
        self._dtc_edit.setEditable(True)
        self._dtc_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # 输入框适中，但弹窗列表加宽显示完整描述（不随输入框宽度截断）
        self._dtc_edit.setFixedWidth(280)
        self._dtc_edit.view().setMinimumWidth(560)
        self._dtc_edit.setFont(_MONO)
        self._dtc_edit.lineEdit().setPlaceholderText("如 F00913")
        self._dtc_edit.setToolTip(
            "0x04/0x06 模式的DTC编号（3字节hex）；FF FF FF 表示全部。\n"
            "已加载定义时可直接下拉选择DTC")
        self._dtc_edit.currentIndexChanged.connect(self._on_dtc_combo_pick)
        # 点击输入框即弹出候选（不依赖右侧箭头命中）
        make_combo_popup_on_click(self._dtc_edit)
        toolbar.addWidget(self._dtc_edit)

        # 记录号（0x04/0x06子功能的第2个参数，1字节hex；FF=全部记录）
        self._record_label = QLabel("记录号:")
        toolbar.addWidget(self._record_label)
        self._record_edit = QLineEdit("FF")
        self._record_edit.setFixedWidth(60)
        self._record_edit.setFont(_MONO)
        self._record_edit.setPlaceholderText("FF")
        self._record_edit.setToolTip(
            "快照记录号(0x04)/扩展数据记录号(0x06)，1字节hex；\n"
            "FF 表示读取该DTC的全部记录；\n"
            "DTC号填 FF FF FF + 记录号 FF 可读取全部快照")
        toolbar.addWidget(self._record_edit)

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
        # 名称/状态描述两列共享弹性空间；其余列随内容自适应，
        # 避免长定义挤压状态/严重度显示
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 4, 5):
            header.setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        # 超长定义以省略号截断（完整内容见tooltip）
        self._table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._table.setWordWrap(False)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

        # DTC状态位可视化（逐位中文含义，勾选状态即解释）
        status_group = QGroupBox("DTC状态位 (选中DTC)")
        status_layout = QGridLayout()
        status_layout.setContentsMargins(8, 4, 8, 4)
        status_layout.setHorizontalSpacing(16)
        self._status_bits = []
        for i in range(8):
            cb = QCheckBox(f"Bit{i} {_DTC_BIT_LABELS[i]}")
            cb.setToolTip(DTC_STATUS_BITS.get(i, ""))
            cb.setEnabled(False)
            self._status_bits.append(cb)
            # 2行×4列布局，避免中文标签挤不下
            status_layout.addWidget(cb, i // 4, i % 4)
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        layout.addStretch()

    def _on_mode_changed(self):
        """模式切换: 按子功能需求显隐掩码/DTC号/记录号控件

        掩码仅 0x01/0x02；DTC号+记录号仅 0x04/0x06（隐藏而非禁用，
        避免无关参数干扰当前模式的理解）
        """
        idx = self._mode_combo.currentIndex()
        need_mask = _MODES[idx][2] if 0 <= idx < len(_MODES) else True
        need_dtc = _MODES[idx][3] if 0 <= idx < len(_MODES) else False
        for w in (self._mask_label, self._mask_combo):
            w.setVisible(need_mask)
        for w in (self._dtc_label, self._dtc_edit,
                  self._record_label, self._record_edit):
            w.setVisible(need_dtc)

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

    def _refresh_dtc_combo(self):
        """填充DTC号下拉: 已加载定义 + 当前读取到的DTC（合并去重）

        快照(0x04)/扩展数据(0x06)最常查的是刚读出的DTC，
        未加载定义时也要能直接下拉选中它们
        """
        combo = self._dtc_edit
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("手动输入DTC号...", "")
        # 来源1: 当前读到的DTC（优先，带读取状态提示）
        for dtc_id in sorted(self._dtc_manager.current_dtcs):
            desc = self._dtc_manager.get_definition(dtc_id) or ""
            label = f"{dtc_id:06X} - {desc}" if desc else f"{dtc_id:06X} - （读取到）"
            combo.addItem(label, f"{dtc_id:06X}")
        # 来源2: 已加载定义（跳过已在来源1出现的）
        current = set(self._dtc_manager.current_dtcs)
        for dtc_id, desc in sorted(self._dtc_manager.definitions.items()):
            if dtc_id in current:
                continue
            combo.addItem(f"{dtc_id:06X} - {desc}", f"{dtc_id:06X}")
        combo.lineEdit().clear()
        combo.blockSignals(False)
        # 选择后保留下拉光标位置（避免lineEdit清空导致闪烁）
        combo.setCurrentIndex(-1)

    def _on_dtc_combo_pick(self):
        """下拉选择定义后仅保留DTC号到输入框（描述丢弃）"""
        data = self._dtc_edit.currentData()
        if data:
            self._dtc_edit.lineEdit().setText(data)

    def _current_record(self) -> int:
        """当前记录号（1字节），非法输入默认 0xFF（全部记录）"""
        text = self._record_edit.text().strip().replace("0x", "")
        if not text:
            return 0xFF
        try:
            return int(text, 16) & 0xFF
        except ValueError:
            self._log("记录号格式错误（应为1~2位hex），使用 FF")
            return 0xFF

    def _current_dtc(self) -> bytes:
        """当前输入的DTC号（3字节），空或非法时默认 FF FF FF

        支持下拉选择后的 "D10587 - 描述" 格式（提取前6位hex）
        """
        import re
        text = self._dtc_edit.currentText().strip()
        if not text:
            return b"\xFF\xFF\xFF"
        m = re.match(r"(?:0x)?([0-9A-Fa-f]{6})\b", text.replace(" ", ""))
        if not m:
            self._log("DTC号格式错误，使用 FF FF FF")
            return b"\xFF\xFF\xFF"
        return bytes.fromhex(m.group(1).upper())

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
            # 快照/扩展数据按DTC号+记录号读取（ISO 14229-1:19请求为
            # 19 <子功能> <DTC号3字节> <记录号1字节>），响应为非标准
            # 记录格式，原样记录日志
            req = bytes([0x19, sub_func]) + self._current_dtc() \
                + bytes([self._current_record()])
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
            if sub_func == 0x01:
                # 数量模式: 汇总区至少同步ECU上报的总数；部分ECU在计数后
                # 附带DTC明细（非标准但实际存在），有则直接解析显示
                count = self._dtc_manager.parse_dtc_count(resp)
                if count is None:
                    self._log(f"数量响应解析失败: {resp.hex(' ').upper()}",
                              "ERROR")
                    return
                fmt = resp[3] if len(resp) > 3 else 0
                fmt_name = {
                    0x01: "ISO 15031-6 (P/C/B/U)",
                    0x02: "SAE J2012",
                    0x03: "ISO 14229 DTC",
                    0x04: "SAE J1939-73",
                }.get(fmt, f"0x{fmt:02X} 未知格式")
                avail = resp[2] if len(resp) > 2 else 0
                records = self._dtc_manager.parse_read_dtc_response(resp)
                if records:
                    # 响应附带明细: 填充表格与统计卡（与0x02同路径）
                    self._refresh_table(records)
                    self._refresh_dtc_combo()
                    self._log(
                        f"ECU报告 {count} 个DTC（格式: {fmt_name}，"
                        f"可用状态位掩码 0x{avail:02X}）——"
                        f"响应附带 {len(records)} 条明细，已显示", "SUCCESS")
                else:
                    # 纯数量: 无明细记录，总计同步ECU上报数，统计项置--
                    self._dtc_count = count
                    self._total_label.setText(f"总计: {count}")
                    if not self._dtc_manager.current_dtcs:
                        # 之前无明细数据时，其它统计项无从计算，置为--
                        self._confirmed_label.setText("已确认: --")
                        self._pending_label.setText("待确认: --")
                        self._history_label.setText("历史: --")
                    self._log(
                        f"ECU报告 {count} 个DTC（格式: {fmt_name}，"
                        f"可用状态位掩码 0x{avail:02X}）——数量模式无明细，"
                        f"切换 0x02/0x0A 可查看明细列表", "SUCCESS")
                return
            records = self._dtc_manager.parse_read_dtc_response(resp)
            self._refresh_table(records)
            self._refresh_dtc_combo()   # 读出的DTC立即可下拉选择（查快照/扩展数据）
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
            name_item = QTableWidgetItem(r.definition or "--")
            name_item.setToolTip(
                (r.definition or "未加载定义（可用\"加载定义\"导入调查表）"))
            self._table.setItem(row, 1, name_item)
            self._table.setItem(row, 2, QTableWidgetItem(f"0x{r.status:02X}"))
            # 状态描述精简为激活位号，详细含义由下方状态位区逐位解释
            bits_item = QTableWidgetItem(r.status_bits_label)
            bits_item.setToolTip(r.status_description)
            bits_item.setFont(_MONO)
            self._table.setItem(row, 3, bits_item)

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
            self._refresh_dtc_combo()   # 已清除的DTC从下拉移除
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
