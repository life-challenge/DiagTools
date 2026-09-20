"""报文日志窗口控件"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QLineEdit, QComboBox, QLabel, QCheckBox,
    QFileDialog, QMenu, QApplication, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QMutex, QTimer
from PyQt6.QtGui import QColor, QFont
from datetime import datetime
import struct
import time


class LogEntry:
    """日志条目"""

    # 数据列显示截断阈值: 超长报文（如36 TransferData整块1026字节）
    # 只显示前若干字节，全量数据保留在data（复制/导出仍为完整内容）
    DISPLAY_MAX_BYTES = 16

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
    def display_hex(self) -> str:
        """数据列显示文本: 超长报文截断（前N字节 + 总长提示）"""
        if len(self.data) > self.DISPLAY_MAX_BYTES:
            shown = " ".join(f"{b:02X}" for b in
                             self.data[:self.DISPLAY_MAX_BYTES])
            return f"{shown} ... ({len(self.data)}B)"
        return self.data_hex

    @property
    def time_str(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%H:%M:%S.%f")[:-4]


class LogWidget(QWidget):
    """报文日志窗口
    
    支持颜色编码、过滤、搜索、导出等功能。
    """

    message_received = pyqtSignal(LogEntry)
    flushed = pyqtSignal()  # 批量渲染完成后发射（供外部刷新计数等）

    # 行渲染资源缓存（每帧×5列创建QFont/QColor在总线洪泛时开销显著）
    _MONO_FONT = QFont("Consolas", 10)
    _COLOR_ERROR = QColor("#F44336")
    _COLOR_TX = QColor("#64B5F6")
    _COLOR_RX = QColor("#4CAF50")

    # 批量渲染: 高频总线报文（洪泛时数千帧/秒）逐条insertRow+scrollToBottom
    # 会占满GUI事件队列导致按钮点击明显滞后，改为定时批量插入
    _FLUSH_INTERVAL_MS = 100   # 渲染批次周期
    _MAX_PENDING = 500         # 待渲染积压上限，超过丢弃最旧（保最新）

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
        self._pending: list[LogEntry] = []  # 待批量渲染条目（GUI线程）
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(self._FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush_pending)
        # DoIP会话逻辑地址上下文: (tester_addr, ecu_addr)，
        # 供pcap导出重建以太网帧；CAN会话为None。
        # _doip_frame_view: True=传输层视图拼完整DoIP帧（DoIP Trace），
        # False=会话层视图保持纯UDS（UDS Trace）
        self._doip_context = None
        self._doip_frame_view = True

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

    def set_doip_context(self, tester_addr: int, ecu_addr: int,
                          frame_view: bool = True):
        """设置DoIP会话逻辑地址（pcap导出时重建以太网帧用）；
        ID列同时改为"逻辑地址"——DoIP下该列显示的是逻辑地址而非CAN ID。

        frame_view: True=传输层视图（DoIP Trace），数据列拼完整
        DoIP帧（02 FD 80 01...）；False=会话层视图（UDS Trace），
        数据列保持纯UDS数据（14229诊断内容，不带传输层头）
        """
        self._doip_context = (tester_addr, ecu_addr)
        self._doip_frame_view = frame_view
        self._table.setHorizontalHeaderItem(2, QTableWidgetItem("逻辑地址"))
        self._filter_id.setToolTip("逻辑地址过滤: 单个(1000/0x1000)或范围(1000-0E80)")

    def clear_doip_context(self):
        """清除DoIP上下文（断开连接时调用），ID列恢复CAN语义"""
        self._doip_context = None
        self._doip_frame_view = True
        self._table.setHorizontalHeaderItem(2, QTableWidgetItem("CAN ID"))
        self._filter_id.setToolTip("CAN ID过滤: 单个(714/0x714)或范围(714-794)")

    def _on_entry_received(self, entry: LogEntry):
        """GUI线程中接收新条目: 仅入队，等待批量渲染"""
        if self._first_timestamp == 0.0:
            self._first_timestamp = entry.timestamp
        self._last_timestamp = entry.timestamp

        # 背压保护: 待渲染积压超限时丢弃最旧条目（显示层保最新）。
        # 洪泛的周期应用报文丢失显示不影响诊断，GUI流畅性优先
        if len(self._pending) >= self._MAX_PENDING:
            del self._pending[:len(self._pending) - self._MAX_PENDING + 1]

        self._pending.append(entry)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending(self):
        """定时批量渲染待处理条目（GUI线程）"""
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        if self._paused:
            self._paused_entries.extend(batch)
            return
        self._insert_batch(batch)

    def _insert_batch(self, batch: list):
        """批量插入条目: 关闭重绘→逐行填充→一次滚动/计数→恢复重绘"""
        self._table.setUpdatesEnabled(False)
        for entry in batch:
            self._add_entry(entry)
        # 批量滚动一次（逐条scrollToBottom会触发整表重布局）
        if self._auto_scroll and self._table.rowCount() > 0:
            self._table.scrollToBottom()
        self._table.setUpdatesEnabled(True)
        self._update_status()
        self.flushed.emit()

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

        # 添加到表格（批量插入时由外层统一滚动/计数）
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._fill_row(row, entry)

    def _fill_row(self, row: int, entry: LogEntry):
        """填充一行数据"""
        # 时间
        time_str = self._format_time(entry)
        time_item = QTableWidgetItem(time_str)
        time_item.setFont(self._MONO_FONT)

        # 方向
        dir_item = QTableWidgetItem(entry.direction)
        dir_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        # CAN ID
        id_item = QTableWidgetItem(f"0x{entry.can_id:03X}")
        id_item.setFont(self._MONO_FONT)
        id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        # 数据（超长报文截断显示，全量保留在entry.data供导出/复制）。
        # DoIP传输层视图显示完整DoIP诊断消息帧（含头+源/目标逻辑
        # 地址）；UDS会话层视图（frame_view=False）保持纯UDS——
        # 存储层始终为裸UDS（pcap导出重建以太网帧依赖此语义）
        if self._doip_context is not None and self._doip_frame_view:
            data_item = QTableWidgetItem(self._doip_frame_hex(entry))
        else:
            data_item = QTableWidgetItem(entry.display_hex)
        data_item.setFont(self._MONO_FONT)
        if len(entry.data) > LogEntry.DISPLAY_MAX_BYTES:
            data_item.setToolTip(entry.data_hex[:512])

        # 描述
        desc_item = QTableWidgetItem(entry.description)

        # 颜色编码
        if entry.is_error or (entry.direction == "RX" and entry.data and entry.data[0] == 0x7F):
            color = self._COLOR_ERROR  # 红色 - 错误/负响应
            desc_item.setForeground(color)
            data_item.setForeground(color)
        elif entry.direction == "TX":
            dir_item.setForeground(self._COLOR_TX)  # 蓝色 - TX
        elif entry.direction == "RX":
            dir_item.setForeground(self._COLOR_RX)  # 绿色 - RX正响应

        self._table.setItem(row, 0, time_item)
        self._table.setItem(row, 1, dir_item)
        self._table.setItem(row, 2, id_item)
        self._table.setItem(row, 3, data_item)
        self._table.setItem(row, 4, desc_item)

    def _doip_frame_hex(self, entry: LogEntry) -> str:
        """DoIP会话数据列: 显示完整DoIP诊断消息帧（ISO 13400-2 0x8001）

        02 FD 80 01 <长度4> <源2> <目标2> <UDS数据>——与trace_exporter
        重建pcap的帧格式一致（版本0x02）。TX源=Tester目标=ECU，
        RX反向。仅显示层拼帧，entry.data仍为裸UDS
        """
        tester, ecu = self._doip_context
        if entry.direction == "TX":
            src, dst = tester, ecu
        else:
            src, dst = ecu, tester
        payload = struct.pack("!HH", src, dst) + entry.data
        frame = (bytes([0x02, 0xFD, 0x80, 0x01])
                 + struct.pack("!I", len(payload)) + payload)
        # 超长截断: 帧头12字节全显示，UDS数据按显示阈值截断
        if len(frame) > 12 + LogEntry.DISPLAY_MAX_BYTES:
            shown = " ".join(f"{b:02X}" for b in
                             frame[:12 + LogEntry.DISPLAY_MAX_BYTES])
            return f"{shown} ... ({len(frame)}B)"
        return " ".join(f"{b:02X}" for b in frame)

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
            batch, self._paused_entries = self._paused_entries, []
            self._insert_batch(batch)

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
        self._pending.clear()
        self._flush_timer.stop()
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
        """导出日志（按扩展名选择格式: ASC/BLF/pcap/CSV/文本）"""
        from src.utils.trace_exporter import export_entries
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出日志", "diag_log.asc",
            "CANoe ASC日志 (*.asc);;Vector BLF日志 (*.blf);;"
            "DoIP pcap抓包 (*.pcap);;CSV文件 (*.csv);;文本文件 (*.txt)"
        )
        if not filepath:
            return

        entries = self._entries + self._paused_entries
        try:
            fmt = export_entries(entries, filepath, self._doip_context)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"导出失败:\n{e}")
            return
        if not fmt:
            QMessageBox.warning(
                self, "导出失败", "不支持的文件格式，请使用 asc/blf/pcap/csv/txt 扩展名")
            return
        QMessageBox.information(
            self, "导出完成",
            f"已导出 {len(entries)} 条记录为 {fmt} 格式:\n{filepath}")

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
