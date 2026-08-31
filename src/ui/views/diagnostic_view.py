"""ECU诊断工作区视图（V2.1 §3/§4/§5）

布局:
  顶部: ECU头部（名称/描述 + 在线状态徽章）
  快捷入口: 图标式入口条（故障码/数据流/DID/IO控制/例程/特殊功能/高级诊断）
  ECU信息卡（§5）: 三状态区（ECU状态/通信状态/诊断状态）
                    + ECU信息（字段双列，[读取ECU信息]右上角）
                    + 通信信息（VCI/通道/波特率/TX RX/协议 竖排列表）
  高级诊断子Tab: UDS服务 / 会话控制 / 安全访问 / Sequence
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                              QGroupBox, QLabel, QPushButton, QTabWidget,
                              QStackedWidget, QScrollArea, QFrame)
from PyQt6.QtCore import Qt, QThread
from src.ui.async_uds import UdsWorker
from src.ui.views.uds_service_view import UdsServiceView
from src.ui.panels.dtc_panel import DtcPanel
from src.ui.panels.did_panel import DidPanel
from src.ui.panels.datastream_panel import DataStreamPanel
from src.ui.panels.io_panel import IoPanel
from src.ui.panels.routine_panel import RoutinePanel
from src.ui.panels.special_panel import SpecialPanel
from src.ui.panels.session_panel import SessionPanel
from src.ui.panels.security_panel import SecurityPanel
from src.ui.panels.sequence_panel import SequencePanel
from src.config.ecu_definition import EcuDefinition


class DiagnosticView(QWidget):
    """ECU诊断工作区"""

    # 快捷入口条定义: 图标 + 名称（§6 功能页签的快捷入口形式）
    _QUICK_ENTRIES = [
        ("📋", "ECU信息"), ("⚠", "故障码 DTC"), ("📈", "数据流"),
        ("🔍", "DID"), ("🎛", "IO控制"), ("⚙", "例程 Routine"),
        ("⭐", "特殊功能"), ("🧰", "高级诊断"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._uds_client = None
        self._ecu: EcuDefinition = None
        self._worker = None          # 保持引用防GC（信息DID读取）
        self._worker_thread = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- ECU头部: 名称 + 描述 + 在线状态徽章 ----
        header = QHBoxLayout()
        header.setSpacing(10)
        self._title_label = QLabel("ECU")
        self._title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        header.addWidget(self._title_label)
        self._desc_label = QLabel("")
        self._desc_label.setStyleSheet("color: #888; font-size: 12px;")
        header.addWidget(self._desc_label)
        header.addStretch()
        self._online_label = QLabel("● Offline")
        self._online_label.setObjectName("offline_badge")
        header.addWidget(self._online_label)
        layout.addLayout(header)

        # ---- 快捷入口条（图标+名称，横向滚动） ----
        bar_widget = QWidget()
        bar_widget.setObjectName("quick_bar")
        bar_layout = QHBoxLayout(bar_widget)
        bar_layout.setContentsMargins(6, 4, 6, 4)
        bar_layout.setSpacing(2)
        self._quick_btns: list = []
        for icon, name in self._QUICK_ENTRIES:
            btn = QPushButton(f"{icon} {name}")
            btn.setObjectName("quick_btn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _c, n=name: self._select_quick(n))
            bar_layout.addWidget(btn)
            self._quick_btns.append(btn)
        bar_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(bar_widget)
        scroll.setFixedHeight(40)
        layout.addWidget(scroll)

        # ---- 功能面板堆栈（入口条切换） ----
        self._stack = QStackedWidget()

        self._info_card = self._build_info_card()
        self._dtc_panel = DtcPanel()
        self._did_panel = DidPanel()
        self._datastream_panel = DataStreamPanel()
        self._io_panel = IoPanel()
        self._routine_panel = RoutinePanel()
        self._special_view = SpecialPanel()

        # 高级诊断: 工程师入口（§4）
        self._uds_service_view = UdsServiceView()
        self._session_panel = SessionPanel()
        self._security_panel = SecurityPanel()
        self._sequence_panel = SequencePanel()
        self._adv_tabs = QTabWidget()
        adv_tabs = self._adv_tabs
        adv_tabs.addTab(self._uds_service_view, "UDS服务")
        adv_tabs.addTab(self._session_panel, "Session 会话控制")
        adv_tabs.addTab(self._security_panel, "Security Access")
        adv_tabs.addTab(self._sequence_panel, "Sequence")

        # 顺序与_QUICK_ENTRIES一致（信息卡由ECU信息入口对应）
        for w in (self._info_card, self._dtc_panel, self._datastream_panel,
                  self._did_panel, self._io_panel, self._routine_panel,
                  self._special_view, adv_tabs):
            self._stack.addWidget(w)
        layout.addWidget(self._stack, 1)

        self._select_quick("ECU信息")

    # ---------------- ECU信息卡（§5） ----------------

    @staticmethod
    def _status_card(title: str) -> tuple:
        """顶部状态区小卡: 标题 + 两行值，返回(卡片, 主值, 副值)"""
        card = QWidget()
        card.setObjectName("status_card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("color: #888; font-size: 11px;")
        lay.addWidget(t)
        v1 = QLabel("--")
        v1.setStyleSheet("font-size: 15px; font-weight: bold;")
        lay.addWidget(v1)
        v2 = QLabel("")
        v2.setStyleSheet("color: #888;")
        lay.addWidget(v2)
        return card, v1, v2

    def _build_info_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # ---- 顶部三个状态区（§5: ECU状态/通信状态/诊断状态） ----
        st_row = QHBoxLayout()
        st_row.setSpacing(10)
        self._st_ecu_card, self._st_ecu_v1, self._st_ecu_v2 = \
            self._status_card("ECU状态")
        self._st_comm_card, self._st_comm_v1, self._st_comm_v2 = \
            self._status_card("通信状态")
        self._st_comm_v1.setText("--")
        self._st_diag_card, self._st_diag_v1, self._st_diag_v2 = \
            self._status_card("诊断状态")
        self._st_diag_v1.setText("默认")
        self._st_diag_v2.setText("Locked")
        self._st_dtc_card, self._st_dtc_v1, self._st_dtc_v2 = \
            self._status_card("DTC")
        self._st_dtc_v1.setText("0")
        self._st_dtc_v2.setText("无故障")
        for c in (self._st_ecu_card, self._st_comm_card,
                  self._st_diag_card, self._st_dtc_card):
            c.setMinimumWidth(150)
            st_row.addWidget(c)
        st_row.addStretch(1)
        layout.addLayout(st_row)

        # 分隔线（减少无意义边框: 仅用细线分区）
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #313244; border: none;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # ---- ECU信息（§5: 字段竖排双列，[读取ECU信息]右上角） ----
        info_box = QGroupBox("ECU信息")
        info_layout = QVBoxLayout(info_box)
        info_layout.setSpacing(8)
        head_row = QHBoxLayout()
        head_row.addStretch()
        self._read_info_btn = QPushButton("读取 ECU 信息")
        self._read_info_btn.setObjectName("btn_primary")
        self._read_info_btn.setFixedWidth(130)
        self._read_info_btn.clicked.connect(self._read_info)
        head_row.addWidget(self._read_info_btn)
        info_layout.addLayout(head_row)
        self._info_grid = QGridLayout()
        self._info_grid.setHorizontalSpacing(8)
        self._info_grid.setVerticalSpacing(6)
        info_layout.addLayout(self._info_grid)
        self._info_labels: dict = {}   # 显示名 -> 值QLabel
        layout.addWidget(info_box)

        # ---- 通信信息（§5: VCI/Channel/Baudrate/TX RX/Protocol 竖排） ----
        comm_box = QGroupBox("通信信息")
        comm_grid = QGridLayout(comm_box)
        comm_grid.setHorizontalSpacing(8)
        comm_grid.setVerticalSpacing(6)
        self._lbl_vci = QLabel("--")
        self._lbl_bus = QLabel("--")
        self._lbl_bitrate = QLabel("--")
        self._lbl_addr = QLabel("--")
        self._lbl_proto = QLabel("CAN + ISO-TP + UDS")
        for r, (k, v) in enumerate(
                [("VCI", self._lbl_vci), ("通道", self._lbl_bus),
                 ("波特率", self._lbl_bitrate), ("TX / RX", self._lbl_addr),
                 ("协议", self._lbl_proto)]):
            key = QLabel(f"{k}:")
            key.setStyleSheet("color: #888;")
            key.setFixedWidth(80)
            comm_grid.addWidget(key, r, 0)
            comm_grid.addWidget(v, r, 1)
        comm_grid.setColumnStretch(1, 1)
        layout.addWidget(comm_box)

        layout.addStretch()
        return card

    def _rebuild_identity_fields(self):
        """根据当前ECU定义重建身份字段（配置驱动）"""
        while self._info_grid.count():
            item = self._info_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._info_labels.clear()

        ecu = self._ecu
        info_dids = ecu.info_dids if ecu else {}
        # 竖排双列: 每行两对 键: 值，键定宽对齐（§5 列表形式）
        row, col, max_cols = 0, 0, 2
        for name in info_dids:
            key = QLabel(f"{name}:")
            key.setStyleSheet("color: #888;")
            key.setFixedWidth(90)
            val = QLabel("--")
            val.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            self._info_grid.addWidget(key, row, col * 2)
            self._info_grid.addWidget(val, row, col * 2 + 1)
            self._info_labels[name] = val
            col += 1
            if col >= max_cols:
                col, row = 0, row + 1
        self._info_grid.setColumnStretch(1, 1)
        self._info_grid.setColumnStretch(3, 1)

    # ---------------- 对外接口 ----------------

    def switch_panel(self, name: str):
        """外部入口: 按名称切换功能面板（快捷入口条同名项）"""
        self._select_quick(name)

    def read_ecu_info(self):
        """外部入口: 触发信息DID批量读取（工具-批量读取DID）"""
        self._read_info()

    def open_security(self):
        """外部入口: 打开高级诊断-Security Access（工具-配置工具）"""
        self._select_quick("高级诊断")
        self._adv_tabs.setCurrentIndex(2)

    @property
    def panels(self) -> list:
        """所有需要注入UDS客户端的子面板"""
        return [self._dtc_panel, self._did_panel, self._datastream_panel,
                self._io_panel, self._routine_panel, self._special_view,
                self._session_panel, self._security_panel,
                self._sequence_panel, self._uds_service_view]

    @property
    def session_panel(self) -> SessionPanel:
        return self._session_panel

    @property
    def security_panel(self) -> SecurityPanel:
        return self._security_panel

    @property
    def dtc_panel(self) -> DtcPanel:
        return self._dtc_panel

    @property
    def did_panel(self) -> DidPanel:
        return self._did_panel

    def set_uds_client(self, client):
        self._uds_client = client
        for panel in self.panels:
            panel.set_uds_client(client)

    def set_ecu(self, ecu: EcuDefinition):
        """切换当前ECU: 更新头部/状态卡字段（配置驱动）"""
        self._ecu = ecu
        self._title_label.setText(ecu.name)
        self._desc_label.setText(ecu.description or "")
        self._rebuild_identity_fields()
        # 切换ECU后旧的状态不再有效（诊断状态区）
        self._st_diag_v1.setText("--")
        self._st_diag_v2.setText("Locked")
        self._st_diag_v2.setStyleSheet("color: #888;")
        self._st_ecu_v1.setText(ecu.name)
        self._special_view.set_ecu(ecu)

    def set_online(self, online: bool):
        self._online_label.setText("● Online" if online else "● Offline")
        self._online_label.setObjectName(
            "online_badge" if online else "offline_badge")
        # 切换objectName后重新应用QSS
        style = self._online_label.style()
        style.unpolish(self._online_label)
        style.polish(self._online_label)
        # ECU状态区同步（§9: 状态一眼可识别）
        self._st_ecu_v2.setText("Online" if online else "Offline")
        self._st_ecu_v2.setStyleSheet(
            "color: #4CAF50; font-weight: bold;" if online
            else "color: #888;")

    def set_connection_info(self, interface_name: str, channel_info: str,
                            bitrate: int, tx_id: int, rx_id: int):
        """连接成功后更新通信信息区与通信状态区"""
        self._lbl_vci.setText(interface_name or "--")
        self._lbl_bus.setText(channel_info or "--")
        self._lbl_bitrate.setText(f"{bitrate // 1000} kbps" if bitrate else "--")
        self._lbl_addr.setText(f"0x{tx_id:03X} / 0x{rx_id:03X}")
        # 通信状态区: VCI + 通道/波特率（§5）
        self._st_comm_v1.setText(interface_name or "--")
        self._st_comm_v2.setText(
            f"{channel_info} | {bitrate // 1000}k"
            if bitrate else (channel_info or "--"))

    def update_live(self, session_text: str = None, security_text: str = None,
                    dtc_count=None, ecu_name: str = None):
        """主窗口周期性推送的实时状态（诊断状态区，带颜色区分）"""
        if ecu_name is not None:
            self._st_ecu_v1.setText(ecu_name)
        if session_text is not None:
            self._st_diag_v1.setText(session_text)
        if security_text is not None:
            self._st_diag_v2.setText(security_text)
            locked = "Unlocked" not in security_text
            self._st_diag_v2.setStyleSheet(
                "color: #FF9800; font-weight: bold;" if locked
                else "color: #4CAF50; font-weight: bold;")
        if dtc_count is not None:
            n = int(dtc_count)
            self._st_dtc_v1.setText(str(n))
            self._st_dtc_v2.setText("当前故障" if n > 0 else "无故障")
            if n > 0:
                self._st_dtc_v1.setStyleSheet(
                    "color: #F44336; font-weight: bold; font-size: 15px;")
            else:
                self._st_dtc_v1.setStyleSheet(
                    "color: #4CAF50; font-weight: bold; font-size: 15px;")

    def _select_quick(self, name: str):
        """快捷入口切换: 高亮当前入口按钮，切换面板堆栈"""
        for i, (_icon, n) in enumerate(self._QUICK_ENTRIES):
            if n == name:
                self._stack.setCurrentIndex(i)
        for (_icon, n), btn in zip(self._QUICK_ENTRIES, self._quick_btns):
            selected = (n == name)
            btn.setObjectName("quick_btn_sel" if selected else "quick_btn")
            style = btn.style()
            style.unpolish(btn)
            style.polish(btn)

    # ---------------- 信息DID读取 ----------------

    def _read_info(self):
        """后台批量读取信息DID（F190 VIN等）"""
        if self._worker_thread is not None:
            return
        if not self._uds_client:
            return
        if not self._ecu or not self._ecu.info_dids:
            return

        dids = {}
        for name, hexstr in self._ecu.info_dids.items():
            try:
                dids[name] = int(str(hexstr), 16)
            except (ValueError, TypeError):
                continue
        if not dids:
            return

        client = self._uds_client

        def _read_all():
            results = {}
            for name, did in dids.items():
                resp = client.read_data_by_identifier(did)
                if resp and len(resp) >= 3 and resp[0] == 0x62:
                    payload = resp[3:]
                    try:
                        text = payload.decode("ascii").strip("\x00 ")
                        if not text or not all(32 <= ord(c) < 127 for c in text):
                            text = payload.hex(" ").upper()
                    except UnicodeDecodeError:
                        text = payload.hex(" ").upper()
                    results[name] = text
                else:
                    results[name] = "读取失败"
            return results

        worker = UdsWorker(_read_all)
        thread = QThread(self)
        worker.moveToThread(thread)

        def _cleanup():
            thread.quit()
            thread.wait()
            self._worker = None
            self._worker_thread = None
            self._read_info_btn.setEnabled(True)

        def _on_done(results):
            try:
                for name, text in results.items():
                    if name in self._info_labels:
                        self._info_labels[name].setText(text)
            finally:
                _cleanup()

        def _on_error(msg):
            for name in dids:
                if name in self._info_labels:
                    self._info_labels[name].setText("异常")
            _cleanup()

        worker.finished.connect(_on_done)
        worker.error.connect(_on_error)
        thread.started.connect(worker.run)
        self._worker = worker
        self._worker_thread = thread
        self._read_info_btn.setEnabled(False)
        thread.start()

    def closeEvent(self, event):
        """确保信息读取线程安全退出"""
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        super().closeEvent(event)
