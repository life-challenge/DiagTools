"""报文日志窗口控件"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QLineEdit, QComboBox, QLabel, QCheckBox,
    QFileDialog, QMenu, QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QMutex
from PyQt6.QtGui import QColor, QFont, QAction
from datetime import datetime
from typing import Optional
import time


class LogEntry:
    """日志条目"""
    def __init__(self, timestamp: float, direction: str, can_id: int,
                 data: bytes, description: str = "", is_error: bool = False):
        self.timestamp = timestamp
        self.direction = direction  # "TX" or "RX"
        self.can_id = can_id
        self.data = data
        self.description = description
        self.is_error = is_error

    @property
    def data_hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)

    @property
    def time_str(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%H:%M:%S.%f")[:-4]


class LogWidget(QWidget):
    """报文日志窗口
    
    支持颜色编码、过滤、搜索、导出等功能。
    """

    message_received = pyqtSignal(LogEntry)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[LogEntry] = []
        self._max_entries = 10000
        self._auto_scroll = True
        self._paused = False
        self._paused_entries: list[LogEntry] = []
        self._first_timestamp = 0.0
        self._last_timestamp = 0.0
        self._tx_count = 0
        self._rx_count = 0
        self._error_count = 0
        self._mutex = QMutex()

        self._init_ui()
        self._connect_signals()
        # 通过信号接收条目，保证表格更新始终在GUI线程执行（add_message可能从后台线程调用）
        self.message_received.connect(self._on_entry_received)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 工具栏（包裹在带底边框的容器中，与下方表格形成视觉分隔）
        toolbar_widget = QWidget()
        toolbar_widget.setObjectName("log_toolbar")
        toolbar = QHBoxLayout(toolbar_widget)
        toolbar.setContentsMargins(6, 3, 6, 3)
        toolbar.setSpacing(4)

        # 过滤
        self._filter_direction = QComboBox()
        self._filter_direction.addItems(["全部", "TX", "RX"])
        self._filter_direction.setFixedWidth(70)
        toolbar.addWidget(QLabel("方向:"))
        toolbar.addWidget(self._filter_direction)

        self._filter_sid = QLineEdit()
        self._filter_sid.setPlaceholderText("SID过滤")
        self._filter_sid.setFixedWidth(80)
        toolbar.addWidget(self._filter_sid)

        # CAN ID过滤: 单个(714/0x714)或范围(714-794)（§13）
        self._filter_id = QLineEdit()
        self._filter_id.setPlaceholderText("ID过滤 如714或714-794")
        self._filter_id.setFixedWidth(130)
        toolbar.addWidget(self._filter_id)

        toolbar.addSpacing(8)

        # 时间戳模式
        self._time_mode = QComboBox()
        self._time_mode.addItems(["绝对时间", "相对时间", "增量时间"])
        self._time_mode.setFixedWidth(90)
        toolbar.addWidget(QLabel("时间:"))
        toolbar.addWidget(self._time_mode)

        # 自动滚动开关（新报文到达时滚动到底部）
        self._chk_autoscroll = QCheckBox("自动滚动")
        self._chk_autoscroll.setChecked(True)
        self._chk_autoscroll.toggled.connect(self._toggle_autoscroll)
        toolbar.addWidget(self._chk_autoscroll)

        toolbar.addStretch()

        # 搜索
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索HEX...")
        self._search_input.setFixedWidth(150)
        self._search_input.setVisible(False)
        toolbar.addWidget(self._search_input)

        # 按钮
        self._btn_search = QPushButton("搜索")
        self._btn_search.setCheckable(True)
        self._btn_search.setFixedWidth(50)
        toolbar.addWidget(self._btn_search)

        self._btn_pause = QPushButton("暂停")
        self._btn_pause.setCheckable(True)
        self._btn_pause.setFixedWidth(50)
        toolbar.addWidget(self._btn_pause)

        self._btn_export = QPushButton("导出")
        self._btn_export.setFixedWidth(50)
        toolbar.addWidget(self._btn_export)

        self._btn_clear = QPushButton("清除")
        self._btn_clear.setFixedWidth(50)
        toolbar.addWidget(self._btn_clear)

        layout.addWidget(toolbar_widget)

        # 日志表格
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["时间", "方向", "CAN ID", "数据", "描述"])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self._table, 1)

        # 状态栏
        status_bar = QHBoxLayout()
        status_bar.setContentsMargins(6, 2, 6, 2)
        status_bar.setSpacing(12)
        self._lbl_total = QLabel("总数: 0")
        self._lbl_tx = QLabel("TX: 0")
        self._lbl_rx = QLabel("RX: 0")
        self._lbl_errors = QLabel("错误: 0")
        for lbl in (self._lbl_total, self._lbl_tx,
                    self._lbl_rx, self._lbl_errors):
            lbl.setStyleSheet("color: #888;")
            status_bar.addWidget(lbl)
        status_bar.addStretch()
        layout.addLayout(status_bar)

    def _connect_signals(self):
        self._btn_clear.clicked.connect(self.clear)
        self._btn_export.clicked.connect(self._export_log)
        self._btn_pause.toggled.connect(self._toggle_pause)
        self._btn_search.toggled.connect(self._toggle_search)
        self._filter_direction.currentTextChanged.connect(self._apply_filter)
        self._filter_sid.textChanged.connect(self._apply_filter)
        self._filter_id.textChanged.connect(self._apply_filter)
        self._time_mode.currentTextChanged.connect(self._refresh_display)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

    def add_message(self, direction: str, can_id: int, data: bytes,
                    description: str = "", is_error: bool = False):
        """添加一条报文记录（线程安全，可从任意线程调用）"""
        entry = LogEntry(time.time(), direction, can_id, data, description, is_error)
        self.message_received.emit(entry)

    def _on_entry_received(self, entry: LogEntry):
        """GUI线程中处理新条目"""
        if self._first_timestamp == 0.0:
            self._first_timestamp = entry.timestamp
        self._last_timestamp = entry.timestamp

        if self._paused:
            self._paused_entries.append(entry)
            return

        self._add_entry(entry)

    @property
    def tx_count(self) -> int:
        return self._tx_count

    @property
    def rx_count(self) -> int:
        return self._rx_count

    def _add_entry(self, entry: LogEntry):
        """添加条目到表格"""
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

        # 更新计数
        if entry.direction == "TX":
            self._tx_count += 1
        else:
            self._rx_count += 1
        if entry.is_error:
            self._error_count += 1

        # 检查过滤
        if not self._matches_filter(entry):
            self._update_status()
            return

        # 添加到表格
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._fill_row(row, entry)

        # 自动滚动
        if self._auto_scroll:
            self._table.scrollToBottom()

        self._update_status()

    def _fill_row(self, row: int, entry: LogEntry):
        """填充一行数据"""
        # 时间
        time_str = self._format_time(entry)
        time_item = QTableWidgetItem(time_str)
        time_item.setFont(QFont("Consolas", 10))

        # 方向
        dir_item = QTableWidgetItem(entry.direction)
        dir_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        # CAN ID
        id_item = QTableWidgetItem(f"0x{entry.can_id:03X}")
        id_item.setFont(QFont("Consolas", 10))
        id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        # 数据
        data_item = QTableWidgetItem(entry.data_hex)
        data_item.setFont(QFont("Consolas", 10))

        # 描述
        desc_item = QTableWidgetItem(entry.description)

        # 颜色编码
        if entry.is_error or (entry.direction == "RX" and entry.data and entry.data[0] == 0x7F):
            color = QColor("#F44336")  # 红色 - 错误/负响应
            desc_item.setForeground(color)
            data_item.setForeground(color)
        elif entry.direction == "TX":
            color = QColor("#64B5F6")  # 蓝色 - TX
            dir_item.setForeground(color)
        elif entry.direction == "RX":
            color = QColor("#4CAF50")  # 绿色 - RX正响应
            dir_item.setForeground(color)

        self._table.setItem(row, 0, time_item)
        self._table.setItem(row, 1, dir_item)
        self._table.setItem(row, 2, id_item)
        self._table.setItem(row, 3, data_item)
        self._table.setItem(row, 4, desc_item)

    def _format_time(self, entry: LogEntry) -> str:
        mode = self._time_mode.currentText()
        if mode == "绝对时间":
            return entry.time_str
        elif mode == "相对时间":
            delta = entry.timestamp - self._first_timestamp
            return f"+{delta:.4f}"
        elif mode == "增量时间":
            delta = entry.timestamp - self._last_timestamp if self._last_timestamp > 0 else 0
            return f"Δ{delta:.4f}"
        return entry.time_str

    def _matches_filter(self, entry: LogEntry) -> bool:
        """检查条目是否匹配过滤条件"""
        dir_filter = self._filter_direction.currentText()
        if dir_filter != "全部" and entry.direction != dir_filter:
            return False

        sid_filter = self._filter_sid.text().strip()
        if sid_filter:
            try:
                sid = int(sid_filter.replace("0x", ""), 16)
                if entry.data and entry.data[0] != sid:
                    return False
            except ValueError:
                pass

        # CAN ID过滤: 支持单个或 a-b 范围（十六进制）
        id_filter = self._filter_id.text().strip()
        if id_filter:
            try:
                if "-" in id_filter:
                    lo_s, hi_s = id_filter.split("-", 1)
                    lo = int(lo_s.replace("0x", "").strip(), 16)
                    hi = int(hi_s.replace("0x", "").strip(), 16)
                    if not (lo <= entry.can_id <= hi):
                        return False
                else:
                    target = int(id_filter.replace("0x", ""), 16)
                    if entry.can_id != target:
                        return False
            except ValueError:
                pass

        return True

    def _apply_filter(self):
        """应用过滤"""
        self._refresh_display()

    def _refresh_display(self):
        """刷新显示"""
        self._table.setRowCount(0)
        for entry in self._entries:
            if self._matches_filter(entry):
                row = self._table.rowCount()
                self._table.insertRow(row)
                self._fill_row(row, entry)
        if self._auto_scroll:
            self._table.scrollToBottom()

    def _toggle_pause(self, paused: bool):
        self._paused = paused
        self._btn_pause.setText("继续" if paused else "暂停")
        if not paused and self._paused_entries:
            for entry in self._paused_entries:
                self._add_entry(entry)
            self._paused_entries.clear()

    def _toggle_autoscroll(self, enabled: bool):
        self._auto_scroll = enabled
        if enabled and self._table.rowCount() > 0:
            self._table.scrollToBottom()

    def _toggle_search(self, visible: bool):
        self._search_input.setVisible(visible)
        if visible:
            self._search_input.setFocus()

    def clear(self):
        """清除日志"""
        self._entries.clear()
        self._paused_entries.clear()
        self._table.setRowCount(0)
        self._tx_count = 0
        self._rx_count = 0
        self._error_count = 0
        self._first_timestamp = 0.0
        self._last_timestamp = 0.0
        self._update_status()

    def _update_status(self):
        total = self._tx_count + self._rx_count
        self._lbl_total.setText(f"总数: {total}")
        self._lbl_tx.setText(f"TX: {self._tx_count}")
        self._lbl_rx.setText(f"RX: {self._rx_count}")
        self._lbl_errors.setText(f"错误: {self._error_count}")

    def _export_log(self):
        """导出日志"""
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出日志", "diag_log.csv", "CSV文件 (*.csv);;文本文件 (*.txt)"
        )
        if not filepath:
            return

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("时间,方向,CAN ID,数据,描述\n")
            for entry in self._entries:
                f.write(f"{entry.time_str},{entry.direction},0x{entry.can_id:03X},{entry.data_hex},{entry.description}\n")

    def _show_context_menu(self, pos):
        """右键菜单"""
        menu = QMenu(self)
        copy_action = menu.addAction("复制报文")
        copy_hex_action = menu.addAction("复制为HEX")
        menu.addSeparator()
        clear_action = menu.addAction("清除日志")

        action = menu.exec(self._table.mapToGlobal(pos))
        if action == copy_action:
            row = self._table.currentRow()
            if row >= 0 and row < len(self._entries):
                entry = self._entries[row]
                text = f"{entry.time_str} [{entry.direction}] 0x{entry.can_id:03X} {entry.data_hex}"
                QApplication.clipboard().setText(text)
        elif action == copy_hex_action:
            row = self._table.currentRow()
            if row >= 0 and row < len(self._entries):
                QApplication.clipboard().setText(self._entries[row].data_hex)
        elif action == clear_action:
            self.clear()
