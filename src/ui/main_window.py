"""主窗口（V2.1布局）

布局原则（V2.1）:
1. 菜单栏=系统级功能; 工具栏=全局操作(连接/断开/全车扫描/日志); 业务导航=一级业务模块
2. 左侧项目树: 当前项目/车辆/ECU(在线状态图标)/网络/配置（配置驱动）
3. 中央: 一级导航 + 各业务工作区
4. 底部: 可折叠通信日志面板（业务日志/UDS Trace/CAN Trace, §7）
5. 状态栏: CAN/UDS/VCI/总线/ECU/Session/Security/Power/Logging（§8）
"""

import os
import subprocess
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QSplitter,
    QTabWidget, QToolBar, QStatusBar, QLabel, QMenu, QTreeWidget,
    QTreeWidgetItem, QInputDialog, QMessageBox, QFileDialog, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QFont

from src.ui.styles import get_theme, COLORS
from src.ui.widgets.log_widget import LogWidget
from src.ui.widgets.log_dock import LogDock
from src.utils.config_manager import get_config_manager
from src.config.ecu_definition import EcuDefinition, load_ecu_definitions
from src.ui.panels.connection_panel import ConnectionPanel
from src.ui.panels.flash_panel import FlashPanel
from src.ui.views.diagnostic_view import DiagnosticView
from src.ui.views.trace_view import TraceView
from src.ui.views.tools_view import ToolsView
from src.ui.views.vehicle_overview_view import VehicleOverviewView
from src.ui.views.report_center_view import ReportCenterView, export_scan_report_csv
from src.ui.views.calibration_view import CalibrationView
from src.ui.views.test_center_view import TestCenterView
from src.ui.dialogs.a2l_import_dialog import A2lImportDialog
from src.ui.dialogs.odx_import_dialog import OdxImportDialog
from src.ui.dialogs.settings_dialog import SettingsDialog
from src.protocol.uds_client import UdsClient
from src.protocol.uds_services import UdsService, SERVICE_NAMES
from src.ui.async_uds import UdsWorker
from src.business.ecu_scanner import EcuScanner
from src.business.report_generator import ReportGenerator

# 会话类型 -> 状态栏显示名
_SESSION_NAMES = {0x01: "默认", 0x02: "编程", 0x03: "扩展", 0x04: "安全"}

# 一级导航索引（V2.2 §1: 8个一级功能）
(NAV_OVERVIEW, NAV_DIAG, NAV_FLASH, NAV_CAL, NAV_TEST,
 NAV_TRACE, NAV_REPORT, NAV_TOOLS) = range(8)

_ICON_ON, _ICON_OFF = "🟢", "⚪"


class MainWindow(QMainWindow):
    """诊断仪主窗口"""

    # 信号
    connect_requested = pyqtSignal()
    disconnect_requested = pyqtSignal()
    scan_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._config = get_config_manager()
        self._current_theme = self._config.get("ui.theme", "dark")
        self._uds_client: UdsClient = None
        self._ecu_defs: list = []
        self._current_ecu: EcuDefinition = None
        self._ecu_items: dict = {}     # ECU名 -> 项目树item

        self._init_ui()
        self._init_menu()
        self._init_toolbar()
        self._init_statusbar()
        self._init_shortcuts()
        self._init_timer()
        self._apply_theme()

        # 启动时恢复上次选择的ECU
        self._restore_current_ecu()
        self._log_dock.log_business(
            f"ECU Diagnostic Studio V2.1 启动, 当前ECU: "
            f"{self._current_ecu.name if self._current_ecu else '--'}")

    # ---------------- UI骨架 ----------------

    def _init_ui(self):
        self.setWindowTitle("ECU Diagnostic Studio - CAN UDS 诊断仪")
        self.setMinimumSize(1200, 800)
        self.resize(1440, 900)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(2)

        # 垂直分割: 工作区 | 底部日志Dock（§7, 可拖拽调整高度）
        self._v_splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(self._v_splitter)

        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._h_splitter = h_splitter
        self._v_splitter.addWidget(h_splitter)

        # ---- 左侧: 项目树（§4: 诊断项目） ----
        self._project_tree = QTreeWidget()
        self._project_tree.setHeaderLabel("诊断项目")
        self._project_tree.setMinimumWidth(210)
        self._project_tree.setMaximumWidth(340)
        self._project_tree.setUniformRowHeights(True)
        self._project_tree.setIndentation(14)
        self._project_tree.itemClicked.connect(self._on_tree_clicked)
        self._project_tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        self._project_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._project_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        h_splitter.addWidget(self._project_tree)

        # ---- 中央: 一级导航 + 工作区 ----
        self._nav = QTabWidget()
        self._nav.setMovable(False)

        # 共享部件（跨视图）
        self._connection_panel = ConnectionPanel()
        self._connection_panel.connection_changed.connect(self._on_connection_changed)

        # 各工作区视图（V2.2: 车辆总览/报告中心新增）
        self._overview_view = VehicleOverviewView()
        self._diag_view = DiagnosticView()
        self._flash_panel = FlashPanel()
        self._cal_view = CalibrationView()
        self._test_view = TestCenterView()
        self._trace_view = TraceView()
        self._report_view = ReportCenterView()
        self._tools_view = ToolsView(self._connection_panel)

        self._nav.addTab(self._overview_view, "车辆总览")
        self._nav.addTab(self._diag_view, "ECU诊断")
        self._nav.addTab(self._flash_panel, "刷写中心")
        self._nav.addTab(self._cal_view, "标定中心")
        self._nav.addTab(self._test_view, "测试中心")
        self._nav.addTab(self._trace_view, "报文分析")
        self._nav.addTab(self._report_view, "报告中心")
        self._nav.addTab(self._tools_view, "工具中心")
        h_splitter.addWidget(self._nav)

        h_splitter.setStretchFactor(0, 1)
        h_splitter.setStretchFactor(1, 6)

        # 刷写安全钩子: 复用安全面板已加载的算法插件 (level, seed) -> key
        _sm = self._diag_view.security_panel.security_manager
        self._flash_panel.set_key_generator(
            lambda level, seed: _sm.generate_key(level, seed))

        # ---- 底部: 可折叠日志面板（§7）----
        self._log_dock = LogDock()
        self._v_splitter.addWidget(self._log_dock)
        self._v_splitter.setStretchFactor(0, 1)
        self._v_splitter.setStretchFactor(1, 0)
        # 日志面板默认高度: 保证能显示约8行报文（§7）
        self._v_splitter.setSizes([640, 260])

        # 加载ECU定义并构建项目树
        self._ecu_defs = load_ecu_definitions()
        self._build_project_tree()

        # 车辆总览: 拓扑数据源 + 扫描/选择入口（§4）
        self._overview_view.set_ecus(self._ecu_defs)
        self._overview_view.set_rescan_handler(self._on_scan)
        self._overview_view.set_ecu_select_handler(self._select_ecu_from_overview)
        self._refresh_overview_info()

        # 会话切换 -> 状态栏/状态卡
        self._diag_view.session_panel.session_changed.connect(self._on_session_changed)

        # 项目级导入记录恢复（上次保存的DID/Flash/A2L配置）
        self._restore_imports()

    # ---------------- 项目树（§P0） ----------------

    def _build_project_tree(self):
        """构建左侧项目树: 项目/车辆/ECU/网络/配置"""
        self._project_tree.clear()
        self._ecu_items.clear()
        hidden = self._config.get("project.hidden_ecus", []) or []

        # 项目
        name = self._config.get("project.name", "默认项目")
        proj_item = QTreeWidgetItem(self._project_tree, [f"项目: {name}"])
        proj_item.setData(0, Qt.ItemDataRole.UserRole, "__project__")
        proj_item.setFlags(proj_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)

        # 车辆
        veh_root = QTreeWidgetItem(self._project_tree, ["车辆"])
        veh_root.setExpanded(True)
        model = self._config.get("project.vehicle_model", "--")
        vin = self._config.get("project.vin", "--")
        m_item = QTreeWidgetItem(veh_root, [f"车型: {model}"])
        m_item.setData(0, Qt.ItemDataRole.UserRole, "__vehicle_model__")
        v_item = QTreeWidgetItem(veh_root, [f"VIN: {vin}"])
        v_item.setData(0, Qt.ItemDataRole.UserRole, "__vin__")

        # ECU（状态图标, 在线=当前选中且已连接）
        ecu_root = QTreeWidgetItem(self._project_tree, ["ECU"])
        ecu_root.setExpanded(True)
        ecu_root.setData(0, Qt.ItemDataRole.UserRole, "__ecu_root__")
        online = self._uds_client is not None
        for defn in self._ecu_defs:
            if defn.name in hidden:
                continue
            is_current = (self._current_ecu is not None
                          and defn.name == self._current_ecu.name)
            icon = _ICON_ON if (is_current and online) else _ICON_OFF
            item = QTreeWidgetItem(ecu_root, [f"{icon} {defn.name}"])
            item.setToolTip(0, defn.description or defn.name)
            item.setData(0, Qt.ItemDataRole.UserRole, defn)
            self._ecu_items[defn.name] = item

        # 网络
        net_root = QTreeWidgetItem(self._project_tree, ["网络"])
        net_root.setExpanded(True)
        self._net_item = QTreeWidgetItem(net_root, [self._network_text()])
        self._net_item.setData(0, Qt.ItemDataRole.UserRole, "__network__")

        # 资源（§4: ECU Definition / Flash / A2L）
        cfg_root = QTreeWidgetItem(self._project_tree, ["资源"])
        cfg_root.setExpanded(True)
        for label, data in [("ECU Definition", "__cfg_ecu__"),
                            ("Flash", "__cfg_flash__"),
                            ("A2L", "__cfg_a2l__")]:
            item = QTreeWidgetItem(cfg_root, [label])
            item.setData(0, Qt.ItemDataRole.UserRole, data)

        # 一级分类节点加粗，层次统一
        bold = QFont()
        bold.setBold(True)
        for root_item in (proj_item, veh_root, ecu_root, net_root, cfg_root):
            root_item.setFont(0, bold)

        self._project_tree.expandAll()

    def _network_text(self) -> str:
        """网络节点文本: 通道 + 波特率"""
        try:
            ch = self._connection_panel.channel or "--"
            br = self._connection_panel.bitrate
            return f"CAN | {ch} | {br // 1000}k"
        except Exception:
            return "CAN"

    def _refresh_ecu_icons(self):
        """刷新ECU在线状态图标并高亮当前ECU"""
        online = self._uds_client is not None
        norm = QFont()
        bold = QFont()
        bold.setBold(True)
        for defn in self._ecu_defs:
            item = self._ecu_items.get(defn.name)
            if item is None:
                continue
            is_current = (self._current_ecu is not None
                          and defn.name == self._current_ecu.name)
            icon = _ICON_ON if (is_current and online) else _ICON_OFF
            item.setText(0, f"{icon} {defn.name}")
            # 当前ECU加粗高亮，一眼可识别当前上下文（§9）
            item.setFont(0, bold if is_current else norm)
            if is_current:
                self._project_tree.setCurrentItem(item)
        if hasattr(self, "_net_item"):
            self._net_item.setText(0, self._network_text())

    def _on_tree_clicked(self, item: QTreeWidgetItem, column: int):
        """单击: 切换上下文"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, EcuDefinition):
            self._select_ecu(data)
            self._nav.setCurrentIndex(NAV_DIAG)
        elif data == "__network__":
            self._nav.setCurrentIndex(NAV_TOOLS)

    def _on_tree_double_clicked(self, item: QTreeWidgetItem, column: int):
        """双击: ECU=选择并连接; 配置项=打开对应入口"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, EcuDefinition):
            self._select_ecu(data)
            self._nav.setCurrentIndex(NAV_DIAG)
            if self._uds_client is None:
                self._on_connect()
        elif data == "__network__":
            self._nav.setCurrentIndex(NAV_TOOLS)
        elif data == "__cfg_ecu__":
            self._open_ecu_definition_dir()
        elif data in ("__cfg_flash__", "__cfg_a2l__"):
            self._statusbar.showMessage(
                "Flash / A2L 配置为 P1/P2 预留，将随标定中心实现", 3000)

    def _on_tree_context_menu(self, pos):
        item = self._project_tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)

        if isinstance(data, EcuDefinition):
            act_select = menu.addAction("切换到该ECU")
            act_edit = menu.addAction("打开Definition文件")
            act_remove = menu.addAction("移除(隐藏)")
            chosen = menu.exec(self._project_tree.mapToGlobal(pos))
            if chosen == act_select:
                self._select_ecu(data)
                self._nav.setCurrentIndex(NAV_DIAG)
            elif chosen == act_edit:
                self._open_ecu_definition_dir(data)
            elif chosen == act_remove:
                hidden = self._config.get("project.hidden_ecus", []) or []
                if data.name not in hidden:
                    hidden.append(data.name)
                self._config.set("project.hidden_ecus", hidden)
                self._build_project_tree()
                self._log_dock.log_business(f"已从项目树移除 ECU: {data.name}")
        elif data == "__ecu_root__":
            act_restore = menu.addAction("恢复已移除的ECU")
            chosen = menu.exec(self._project_tree.mapToGlobal(pos))
            if chosen == act_restore:
                self._config.set("project.hidden_ecus", [])
                self._build_project_tree()
        elif data == "__project__":
            act_rename = menu.addAction("重命名项目")
            chosen = menu.exec(self._project_tree.mapToGlobal(pos))
            if chosen == act_rename:
                name, ok = QInputDialog.getText(
                    self, "重命名项目", "项目名称:",
                    text=self._config.get("project.name", "默认项目"))
                if ok and name.strip():
                    self._config.set("project.name", name.strip())
                    self._build_project_tree()
        elif data in ("__vehicle_model__", "__vin__"):
            is_model = data == "__vehicle_model__"
            act_edit = menu.addAction("编辑车型" if is_model else "编辑VIN")
            chosen = menu.exec(self._project_tree.mapToGlobal(pos))
            if chosen == act_edit:
                key = "project.vehicle_model" if is_model else "project.vin"
                text, ok = QInputDialog.getText(
                    self, "编辑车辆信息",
                    "车型:" if is_model else "VIN:",
                    text=self._config.get(key, ""))
                if ok:
                    self._config.set(key, text.strip() or "--")
                    self._build_project_tree()

    def _open_ecu_definition_dir(self, defn: EcuDefinition = None):
        """在资源管理器中打开ECU定义目录"""
        target = defn or self._current_ecu
        if target is None or not getattr(target, "def_dir", ""):
            return
        if os.path.exists(target.def_dir):
            subprocess.Popen(f'explorer "{target.def_dir}"')

    # ---------------- ECU选择 ----------------

    def _select_ecu(self, defn: EcuDefinition):
        """选中ECU: 应用其地址到连接配置，并更新诊断工作区上下文"""
        self._current_ecu = defn
        self._connection_panel.set_uds_ids(defn.tx_id, defn.rx_id)
        self._diag_view.set_ecu(defn)
        self._flash_panel.set_ecu_name(defn.name, defn.functional_tx_id)
        ecu_text = f"ECU: {defn.name}"
        if self._uds_client is None:
            ecu_text += " | Offline"
        self._lbl_ecu.setText(ecu_text)
        self._config.set("project.current_ecu", defn.name)
        self._refresh_ecu_icons()
        self._log_dock.log_business(
            f"选择 ECU: {defn.name} (TX 0x{defn.tx_id:03X} / RX 0x{defn.rx_id:03X})")
        self._statusbar.showMessage(
            f"已选择 ECU: {defn.name} (0x{defn.tx_id:03X}/0x{defn.rx_id:03X})", 3000)

    def _restore_current_ecu(self):
        """启动时恢复上次选中的ECU；首次启动优先选地址与CAN配置匹配的ECU"""
        if not self._ecu_defs:
            return
        name = self._config.get("project.current_ecu", "")
        target = next((d for d in self._ecu_defs if d.name == name), None)
        if target is None:
            # 未保存过: 优先选tx/rx与已保存CAN配置一致的ECU，避免覆盖用户地址
            req = ConnectionPanel._parse_id(self._config.get("can.req_id"), None)
            resp = ConnectionPanel._parse_id(self._config.get("can.resp_id"), None)
            target = next(
                (d for d in self._ecu_defs if d.tx_id == req and d.rx_id == resp),
                self._ecu_defs[0])
        self._current_ecu = target
        self._connection_panel.set_uds_ids(target.tx_id, target.rx_id)
        self._diag_view.set_ecu(target)
        self._flash_panel.set_ecu_name(target.name, target.functional_tx_id)
        self._lbl_ecu.setText(f"ECU: {target.name} | Offline")
        self._refresh_ecu_icons()

    # ---------------- 车辆总览（V2.2 §4） ----------------

    def _refresh_overview_info(self):
        """刷新车辆总览的车辆信息与网络行"""
        try:
            network = self._network_text()
        except Exception:
            network = "--"
        self._overview_view.set_vehicle_info(
            self._config.get("project.vehicle_model", "--"),
            self._config.get("project.vin", "--"), network)

    def _select_ecu_from_overview(self, defn):
        """总览拓扑双击: 选择ECU并跳转ECU诊断"""
        self._select_ecu(defn)
        self._nav.setCurrentIndex(NAV_DIAG)

    # ---------------- 菜单/工具栏/状态栏 ----------------

    def _init_menu(self):
        """菜单（§3）: 文件=诊断项目; 视图; 工具=辅助工具; 帮助"""
        menubar = self.menuBar()

        # 文件(F): 统一"诊断项目"概念（§3/§14 P0）
        file_menu = menubar.addMenu("文件(&F)")
        self._add_action(file_menu, "新建诊断项目", self._on_new_project, "Ctrl+N")
        self._add_action(file_menu, "打开诊断项目(&O)", self._on_open_project, "Ctrl+O")
        self._add_action(file_menu, "保存诊断项目(&S)", self._on_save_project, "Ctrl+S")
        self._add_action(file_menu, "另存为...", self._on_save_project_as)
        self._recent_menu = file_menu.addMenu("最近使用")
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        import_menu = file_menu.addMenu("导入")
        self._add_action(import_menu, "ECU Definition", self._on_import_ecu_def)
        self._add_action(import_menu, "DID/DTC 配置", self._on_import_did_dtc)
        self._add_action(import_menu, "Flash 配置", self._on_import_flash_config)
        self._add_action(import_menu, "A2L", self._on_import_a2l)
        self._add_action(import_menu, "ODX / CDD", self._on_import_odx)
        export_menu = file_menu.addMenu("导出")
        self._add_action(export_menu, "诊断报告", self._on_export_report)
        self._add_action(export_menu, "日志", self._on_export_log)
        file_menu.addSeparator()
        self._add_action(file_menu, "退出(&Q)", self.close, "Alt+F4")

        # 视图(V)
        view_menu = menubar.addMenu("视图(&V)")
        self._add_action(view_menu, "全屏模式", self._toggle_fullscreen, "F11")
        self._add_action(view_menu, "日志面板", self._toggle_log_dock, "Ctrl+L")
        self._act_tree = QAction("左侧项目面板", self)
        self._act_tree.setCheckable(True)
        self._act_tree.setChecked(True)
        self._act_tree.toggled.connect(self._toggle_project_panel)
        view_menu.addAction(self._act_tree)
        self._act_statusbar = QAction("状态栏", self)
        self._act_statusbar.setCheckable(True)
        self._act_statusbar.setChecked(True)
        self._act_statusbar.toggled.connect(self._toggle_statusbar_visible)
        view_menu.addAction(self._act_statusbar)
        view_menu.addSeparator()
        self._add_action(view_menu, "恢复默认布局", self._restore_layout)

        # 工具(T): 正式业务功能不进工具菜单（§3）
        tools_menu = menubar.addMenu("工具(&T)")
        self._add_action(tools_menu, "ECU全扫描", self._on_scan, "F7")
        diag_menu = tools_menu.addMenu("诊断工具")
        self._add_action(diag_menu, "批量读取DID", self._on_batch_read_did)
        self._add_action(diag_menu, "批量读取DTC", self._on_batch_read_dtc)
        self._add_action(diag_menu, "清除所有DTC", self._on_clear_dtc)
        msg_menu = tools_menu.addMenu("报文工具")
        self._add_action(msg_menu, "CAN Trace", self._show_can_trace)
        self._add_action(msg_menu, "UDS Trace", self._show_uds_trace)
        self._add_action(msg_menu, "报文重放", self._show_replay)
        self._add_action(msg_menu, "诊断数据库", self._show_dbc)
        cfg_menu = tools_menu.addMenu("配置工具")
        self._add_action(cfg_menu, "ECU Definition管理", self._on_ecu_def_manage)
        self._add_action(cfg_menu, "CAN连接管理", self._on_can_manage)
        self._add_action(cfg_menu, "Security配置", self._on_security_config)
        log_menu = tools_menu.addMenu("日志工具")
        self._add_action(log_menu, "查看日志", self._on_view_log)
        self._add_action(log_menu, "打开日志文件夹", self._on_open_log_dir)
        self._add_action(log_menu, "导出日志", self._on_export_log)
        tools_menu.addSeparator()
        self._add_action(tools_menu, "选项设置", self._on_settings)

        # 帮助(H)
        help_menu = menubar.addMenu("帮助(&H)")
        self._add_action(help_menu, "快速入门", self._on_quick_start)
        self._add_action(help_menu, "用户手册", self._on_user_manual)
        self._add_action(help_menu, "UDS服务参考", self._on_uds_reference)
        self._add_action(help_menu, "快捷键", self._on_shortcuts_help)
        help_menu.addSeparator()
        self._add_action(help_menu, "检查更新", self._on_check_update)
        self._add_action(help_menu, "问题反馈", self._on_feedback)
        self._add_action(help_menu, "关于(&A)", self._on_about)

    def _add_action(self, menu: QMenu, text: str, callback, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def _init_toolbar(self):
        """全局工具栏（§2.1）: 连接 | 断开 | 全车扫描 | 日志（主题移入设置）"""
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._btn_connect = QAction("连接", self)
        self._btn_connect.setShortcut("F5")
        self._btn_connect.triggered.connect(self._on_connect)
        toolbar.addAction(self._btn_connect)

        self._btn_disconnect = QAction("断开", self)
        self._btn_disconnect.setShortcut("F6")
        self._btn_disconnect.triggered.connect(self._on_disconnect)
        toolbar.addAction(self._btn_disconnect)

        toolbar.addSeparator()

        self._btn_scan = QAction("全车扫描", self)
        self._btn_scan.setShortcut("F7")
        self._btn_scan.triggered.connect(self._on_scan)
        toolbar.addAction(self._btn_scan)

        toolbar.addSeparator()

        self._btn_log = QAction("日志", self)
        self._btn_log.setShortcut("Ctrl+L")
        self._btn_log.triggered.connect(self._toggle_log_dock)
        toolbar.addAction(self._btn_log)

        # 连接状态行（§2: 通道 | 波特率 | 连接状态）
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self._conn_label = QLabel("未连接")
        self._conn_label.setStyleSheet("color: #888;")
        toolbar.addWidget(self._conn_label)

    def _init_statusbar(self):
        """状态栏（§8）: 独立状态块，块间有边框分隔"""
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._lbl_can = self._sb_block("CAN ○")
        self._lbl_uds = self._sb_block("UDS ○")
        self._lbl_vci = self._sb_block("VCI: --")
        self._lbl_bus = self._sb_block("--")
        self._lbl_ecu = self._sb_block("ECU: --")
        self._lbl_session = self._sb_block("Session: 默认")
        self._lbl_security = self._sb_block("Security: Locked")
        self._lbl_power = self._sb_block("Power: --")
        self._lbl_logging = self._sb_block("Logging ●")
        # Logging块可点击: 打开日志根目录（显眼入口）
        self._lbl_logging.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lbl_logging.setToolTip(
            "点击打开日志文件夹（logs/：app/diag/comm/flash/sequence）")
        self._lbl_logging.mousePressEvent = \
            lambda _e: self._on_open_log_dir()

        self._lbl_counter = self._sb_block("TX:0 RX:0", permanent=True)
        self._lbl_time = self._sb_block("", permanent=True)

        self._set_dot(self._lbl_can, "CAN", False)
        self._set_dot(self._lbl_uds, "UDS", False)

    def _sb_block(self, text: str, permanent: bool = False) -> QLabel:
        """创建状态栏独立状态块标签"""
        label = QLabel(text)
        label.setObjectName("sb_block")
        if permanent:
            self._statusbar.addPermanentWidget(label)
        else:
            self._statusbar.addWidget(label)
        return label

    @staticmethod
    def _set_dot(label: QLabel, name: str, on: bool):
        """状态指示点: 绿●=正常 / 灰○=断开"""
        if on:
            label.setText(f"{name} ●")
            label.setStyleSheet(f"color: {COLORS['positive']}; font-weight: bold;")
        else:
            label.setText(f"{name} ○")
            label.setStyleSheet("color: #888;")

    def _init_shortcuts(self):
        """Ctrl+1~8 切换一级导航"""
        for i in range(8):
            action = QAction(self)
            action.setShortcut(f"Ctrl+{i + 1}")
            action.triggered.connect(lambda checked, idx=i: self._nav.setCurrentIndex(idx))
            self.addAction(action)

        # Space 暂停CAN Trace
        action = QAction(self)
        action.setShortcut("Space")
        action.triggered.connect(self._toggle_log_pause)
        self.addAction(action)

    def _init_timer(self):
        """状态更新定时器 + 诊断项目自动保存（§2: 每5分钟）"""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_statusbar)
        self._timer.start(1000)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave_project)
        self._autosave_timer.start(300_000)

    def _autosave_project(self):
        """自动保存诊断项目（静默，异常不打断用户）"""
        try:
            self._connection_panel.save_to_config()
            self._config.save_config()
            self._statusbar.showMessage("诊断项目已自动保存", 2000)
        except Exception:
            pass

    def _update_statusbar(self):
        from datetime import datetime
        self._lbl_time.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        trace = self._log_dock.can_trace
        self._lbl_counter.setText(f"TX:{trace.tx_count} RX:{trace.rx_count}")

        # Security状态: 有任意等级处于解锁且未超时则显示（锁定=橙色警示）
        levels = self._diag_view.security_panel.unlocked_levels()
        if levels:
            txt = ", ".join(f"L{l}" for l in levels)
            sec_text = f"Unlocked ({txt})"
            self._lbl_security.setText(f"Security: {sec_text}")
            self._lbl_security.setStyleSheet(
                f"color: {COLORS['positive']}; font-weight: bold;")
        else:
            sec_text = "Locked"
            self._lbl_security.setText("Security: Locked")
            self._lbl_security.setStyleSheet(
                f"color: {COLORS['warning']}; font-weight: bold;")

        # 状态卡实时状态同步（诊断状态区）
        dtc_count = self._diag_view.dtc_panel.dtc_count()
        ecu_name = self._current_ecu.name if self._current_ecu else None
        self._diag_view.update_live(security_text=sec_text,
                                    dtc_count=dtc_count, ecu_name=ecu_name)

    def _on_session_changed(self, session_type: int):
        name = _SESSION_NAMES.get(session_type, f"0x{session_type:02X}")
        self._lbl_session.setText(f"Session: {name}")
        self._diag_view.update_live(session_text=name)
        self._log_dock.log_business(f"会话切换: {name} (0x{session_type:02X})")

    # ---------------- 主题/设置 ----------------

    def _apply_theme(self):
        theme = get_theme(self._current_theme)
        self.setStyleSheet(theme)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_log_dock(self):
        """工具栏「日志」: 折叠/展开底部日志面板"""
        self._log_dock.toggle_collapse()
        self._lbl_logging.setStyleSheet(
            "" if not self._log_dock.collapsed else "color: #888;")
        self._lbl_logging.setText(
            "Logging ●" if not self._log_dock.collapsed else "Logging ○")

    def _toggle_log_pause(self):
        self._log_dock.can_trace._btn_pause.toggle()

    # ---------------- 连接流程 ----------------

    def _on_connection_changed(self, connected: bool):
        if connected:
            can_iface = self._connection_panel.can_interface
            tx_id = self._connection_panel.tx_id
            rx_id = self._connection_panel.rx_id
            self._uds_client = UdsClient(can_iface, tx_id=tx_id, rx_id=rx_id)

            # 注入UDS客户端到所有工作区
            self._diag_view.set_uds_client(self._uds_client)
            self._flash_panel.set_uds_client(self._uds_client)
            self._cal_view.set_uds_client(self._uds_client)
            self._test_view.set_uds_client(self._uds_client)
            self._trace_view.set_uds_client(self._uds_client)
            self._trace_view.set_can_interface(can_iface)

            # 挂接真实报文到日志面板（监听器可能在后台线程被回调，LogWidget内部线程安全）
            can_iface.add_message_listener(self._on_can_message)

            # 诊断工作区在线状态与状态卡Communication块
            self._diag_view.set_online(True)
            self._diag_view.set_connection_info(
                can_iface.interface_name, can_iface.channel_info,
                self._connection_panel.bitrate, tx_id, rx_id)
            self._diag_view.update_live(session_text="默认",
                                        ecu_name=(self._current_ecu.name
                                                  if self._current_ecu else None))

            # 状态栏（§8）
            self._set_dot(self._lbl_can, "CAN", True)
            self._set_dot(self._lbl_uds, "UDS", True)
            self._lbl_vci.setText(f"VCI: {can_iface.interface_name}")
            self._lbl_bus.setText(
                f"{self._connection_panel.channel} | "
                f"{self._connection_panel.bitrate // 1000}k")
            ecu_name = self._current_ecu.name if self._current_ecu else "--"
            self._lbl_ecu.setText(f"ECU: {ecu_name} | Online")
            self._lbl_session.setText("Session: 默认")
            self._log_dock.log_business(
                f"已连接 {can_iface.interface_name} | "
                f"{self._connection_panel.channel} | "
                f"{self._connection_panel.bitrate // 1000}k")
            self._refresh_overview_info()
        else:
            can_iface = self._connection_panel.can_interface
            if can_iface is not None:
                can_iface.remove_message_listener(self._on_can_message)
            self._uds_client = None
            self._diag_view.set_uds_client(None)
            self._flash_panel.set_uds_client(None)
            self._cal_view.set_uds_client(None)
            self._test_view.set_uds_client(None)
            self._trace_view.set_uds_client(None)
            self._trace_view.set_can_interface(None)
            self._diag_view.set_online(False)

            self._set_dot(self._lbl_can, "CAN", False)
            self._set_dot(self._lbl_uds, "UDS", False)
            self._lbl_vci.setText("VCI: --")
            self._lbl_bus.setText("--")
            ecu_name = self._current_ecu.name if self._current_ecu else "--"
            self._lbl_ecu.setText(f"ECU: {ecu_name} | Offline")
            self._log_dock.log_business("已断开连接")

        self._refresh_ecu_icons()

    def _on_can_message(self, direction: str, msg):
        """CAN报文监听回调（可能在后台线程），转发到底部日志面板与报文分析工作区"""
        desc = self._describe_frame(msg.data)
        self._log_dock.add_frame(direction, msg.can_id, msg.data, desc)
        self._trace_view.add_frame(direction, msg.can_id, msg.data, desc)

    @staticmethod
    def _describe_frame(data: bytes) -> str:
        """根据ISO-TP帧类型和UDS SID生成描述"""
        if not data:
            return ""
        pci = data[0] & 0xF0
        if pci == 0x30:
            return "流控帧 (FC)"
        if pci == 0x20:
            return "连续帧 (CF)"
        # 单帧(SF): SID在第2字节；首帧(FF): SID在第3字节
        if pci == 0x00 and len(data) > 1:
            sid = data[1]
        elif pci == 0x10 and len(data) > 2:
            sid = data[2]
        else:
            return ""
        if sid == 0x7F:
            return "负响应"
        if sid > 0x40:
            return f"{UdsService.get_service_name(sid - 0x40)} - 正响应"
        return UdsService.get_service_name(sid)

    def _on_connect(self):
        """工具栏连接按钮：委托给连接面板执行真实连接流程"""
        self._connection_panel._on_connect()

    def _on_disconnect(self):
        """工具栏断开按钮：委托给连接面板"""
        self._connection_panel._on_disconnect()

    def _on_scan(self):
        """ECU全扫描（§10）: 对已定义地址逐个发 0x10 01 识别在线状态"""
        if self._uds_client is None or self._connection_panel.can_interface is None:
            QMessageBox.information(self, "ECU全扫描", "请先连接CAN再执行扫描")
            return
        if getattr(self, "_scan_thread", None) is not None:
            self._statusbar.showMessage("扫描进行中...", 2000)
            return
        pairs = [(d.tx_id, d.rx_id) for d in self._ecu_defs]
        scanner = EcuScanner(can_interface=self._connection_panel.can_interface)
        self._log_dock.log_business(
            f"开始ECU全扫描: {len(pairs)} 个已定义地址")
        self._statusbar.showMessage("ECU全扫描进行中...", 0)
        self._start_worker(
            scanner.scan, (pairs,),
            self._on_scan_done, self._on_scan_error,
            "_scan_worker", "_scan_thread")

    def _on_scan_done(self, results):
        """扫描完成: 结果表+车辆总览拓扑更新+扫描报告导出（§5）"""
        from datetime import datetime
        found = {(e.tx_id, e.rx_id) for e in results}
        lines = []
        for d in self._ecu_defs:
            online = (d.tx_id, d.rx_id) in found
            mark = _ICON_ON if online else _ICON_OFF
            state = "" if online else "  未响应"
            lines.append(f"{mark} {d.name:<6} 0x{d.tx_id:03X} / 0x{d.rx_id:03X}{state}")
        self._statusbar.clearMessage()
        self._log_dock.log_business(
            f"ECU全扫描完成: {len(results)}/{len(self._ecu_defs)} 在线", "SUCCESS")

        # 车辆总览: 拓扑状态色 + 扫描结果表（§4/§5）
        scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._overview_view.set_scan_results(results, self._ecu_defs, scan_time)
        self._refresh_ecu_icons()

        # 扫描报告自动导出到报告中心（§18）
        try:
            report_path = export_scan_report_csv(
                results, self._ecu_defs,
                os.path.join(self._project_root(), "reports"))
            self._report_view.refresh()
            self._log_dock.log_business(
                f"扫描报告已导出: {report_path}", "SUCCESS")
        except Exception as e:
            self._log_dock.log_business(f"扫描报告导出失败: {e}", "ERROR")

        QMessageBox.information(
            self, "ECU扫描结果",
            "ECU扫描结果（详见 车辆总览 / 报告中心）\n\n" + "\n".join(lines))

    def _on_scan_error(self, msg):
        self._statusbar.clearMessage()
        self._log_dock.log_business(f"ECU扫描异常: {msg}", "ERROR")

    def _start_worker(self, func, args: tuple, on_done, on_error,
                      worker_attr: str, thread_attr: str):
        """启动后台UDS调用线程（保持worker引用防止GC回收）"""
        worker = UdsWorker(func, *args)
        thread = QThread(self)
        worker.moveToThread(thread)
        worker.finished.connect(on_done)
        worker.error.connect(on_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(lambda: setattr(self, thread_attr, None))
        setattr(self, worker_attr, worker)
        setattr(self, thread_attr, thread)
        thread.start()

    # ---------------- 诊断项目（文件菜单, §3） ----------------

    def _project_root(self) -> str:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _on_new_project(self):
        """新建诊断项目: 恢复默认连接参数"""
        self._config.set("can", {
            "interface_type": "virtual",
            "channel": "Virtual_0",
            "bitrate": 500000,
            "req_id": 0x7E0,
            "resp_id": 0x7E8,
        })
        self._connection_panel.load_saved_config()
        self._log_dock.log_business("新建诊断项目（默认参数）")
        self._statusbar.showMessage("已新建诊断项目（默认参数）", 3000)

    def _on_open_project(self):
        """打开诊断项目: 从 .dproj/.json 文件加载配置并应用到界面"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "打开诊断项目", "", "诊断项目 (*.dproj *.json)")
        if not filepath:
            return
        self._load_project_file(filepath)

    def _load_project_file(self, filepath: str):
        if self._config.load_config(filepath):
            self._connection_panel.load_saved_config()
            self._add_recent(filepath)
            self._restore_imports()
            self._log_dock.log_business(
                f"已加载诊断项目: {filepath}", "SUCCESS")
            self._statusbar.showMessage(f"已加载诊断项目: {filepath}", 3000)
        else:
            QMessageBox.warning(self, "打开诊断项目", "配置文件加载失败或格式无效")

    def _on_save_project(self):
        """保存诊断项目: 先采集界面当前参数再写入磁盘"""
        self._connection_panel.save_to_config()
        self._config.save_config()
        self._add_recent(self._config.current_file)
        self._log_dock.log_business(
            f"诊断项目已保存: {self._config.current_file}", "SUCCESS")
        self._statusbar.showMessage("诊断项目已保存", 3000)

    def _on_save_project_as(self):
        """另存为: 选择目标文件保存诊断项目"""
        self._connection_panel.save_to_config()
        filepath, _ = QFileDialog.getSaveFileName(
            self, "诊断项目另存为", "diagnostic_project.dproj",
            "诊断项目 (*.dproj *.json)")
        if not filepath:
            return
        if self._config.save_config(filepath):
            self._add_recent(filepath)
            self._log_dock.log_business(
                f"诊断项目另存为: {filepath}", "SUCCESS")
        else:
            QMessageBox.warning(self, "另存为", "保存失败")

    def _add_recent(self, filepath: str):
        """维护最近使用列表（持久化）"""
        recent = self._config.get("project.recent", []) or []
        if filepath in recent:
            recent.remove(filepath)
        recent.insert(0, filepath)
        self._config.set("project.recent", recent[:8])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        """重建最近使用子菜单"""
        if not hasattr(self, "_recent_menu"):
            return
        self._recent_menu.clear()
        recent = self._config.get("project.recent", []) or []
        if not recent:
            act = self._recent_menu.addAction("（无）")
            act.setEnabled(False)
            return
        for path in recent:
            act = self._recent_menu.addAction(path)
            act.triggered.connect(
                lambda checked, p=path: self._open_recent(p))

    def _open_recent(self, filepath: str):
        if not os.path.exists(filepath):
            QMessageBox.warning(self, "最近使用", f"文件不存在: {filepath}")
            recent = [p for p in (self._config.get("project.recent", []) or [])
                      if p != filepath]
            self._config.set("project.recent", recent)
            self._rebuild_recent_menu()
            return
        self._load_project_file(filepath)

    def _on_import_ecu_def(self):
        """导入ECU Definition: 打开定义目录（新增ECU只需放置文件夹+ecu.json）"""
        ecu_dir = os.path.join(self._project_root(), "resources", "ecu")
        self._log_dock.log_business(
            "ECU Definition导入: 在 resources/ecu 下新增 ECU文件夹与ecu.json"
            "（JSON内不允许注释），重启或重建树后生效")
        if os.path.isdir(ecu_dir):
            subprocess.Popen(f'explorer "{ecu_dir}"')

    def _on_import_did_dtc(self):
        """导入DID/DTC配置: 自动识别内容并注入诊断工作区"""
        start_dir = os.path.join(
            self._project_root(), "resources", "did_definitions")
        filepath, _ = QFileDialog.getOpenFileName(
            self, "导入DID/DTC配置", start_dir, "JSON Files (*.json)")
        if not filepath:
            return
        self._apply_did_dtc_import(filepath, persist=True)

    def _apply_did_dtc_import(self, filepath: str, persist: bool = False):
        """解析文件内容并注入DID面板或DTC面板"""
        import json
        import tempfile
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "导入配置", f"文件解析失败: {e}")
            return

        # 内容识别: dids→DID定义, dtcs→DTC定义
        if isinstance(data, dict):
            if "dids" in data:
                kind = "did"
                did_count = self._diag_view.did_panel.import_definitions(
                    filepath)
                dtc_count = 0
            elif "dtcs" in data:
                # DTC定义管理器只认列表格式，归一化到临时文件加载
                with tempfile.NamedTemporaryFile(
                        "w", suffix=".json", delete=False,
                        encoding="utf-8") as tmp:
                    json.dump(data["dtcs"], tmp, ensure_ascii=False)
                    tmp_path = tmp.name
                try:
                    dtc_count = self._diag_view.dtc_panel.import_definitions(
                        tmp_path)
                finally:
                    os.remove(tmp_path)
                kind, did_count = "dtc", 0
            else:
                QMessageBox.warning(
                    self, "导入配置", "未识别的配置格式（需含 dids 或 dtcs 键）")
                return
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            if "dtc_id" in data[0]:
                dtc_count = self._diag_view.dtc_panel.import_definitions(
                    filepath)
                kind, did_count = "dtc", 0
            else:
                did_count = self._diag_view.did_panel.import_definitions(
                    filepath)
                kind, dtc_count = "did", 0
        else:
            QMessageBox.warning(self, "导入配置", "未识别的配置格式")
            return

        msg = (f"导入DID定义 {did_count} 项" if kind == "did"
               else f"导入DTC定义 {dtc_count} 项")
        self._log_dock.log_business(
            f"{msg}: {os.path.basename(filepath)}",
            "SUCCESS" if (did_count or dtc_count) else "ERROR")
        self._statusbar.showMessage(msg, 3000)
        if persist and (did_count or dtc_count):
            self._config.set("imports.did_dtc", filepath)

    def _on_import_flash_config(self):
        """导入Flash配置: 应用到刷写中心参数"""
        import json
        filepath, _ = QFileDialog.getOpenFileName(
            self, "导入Flash配置", self._project_root(),
            "JSON Files (*.json)")
        if not filepath:
            return
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "导入Flash配置", f"文件解析失败: {e}")
            return
        if not isinstance(cfg, dict):
            QMessageBox.warning(self, "导入Flash配置", "配置必须为JSON对象")
            return
        applied = self._flash_panel.import_config(cfg.get("flash", cfg))
        self._nav.setCurrentIndex(NAV_FLASH)
        self._log_dock.log_business(
            f"Flash配置已导入 {applied} 项: {os.path.basename(filepath)}",
            "SUCCESS" if applied else "ERROR")
        self._statusbar.showMessage(f"Flash配置已应用 {applied} 项", 3000)
        self._config.set("imports.flash_config", filepath)

    def _on_import_a2l(self):
        """导入A2L: 打开解析对话框，导出后记录到项目"""
        dlg = A2lImportDialog(self._project_root(), self)
        dlg.exec()
        if dlg.exported_path:
            self._config.set("imports.a2l", dlg.exported_path)
            self._log_dock.log_business(
                f"A2L已导出: {dlg.exported_path}", "SUCCESS")

    def _on_import_odx(self):
        """导入ODX/CDD: 解析诊断数据，DTC定义可注入故障码面板"""
        dlg = OdxImportDialog(self._project_root(), self)
        dlg.exec()
        if dlg.exported_path:
            self._config.set("imports.odx", dlg.exported_path)
        if dlg.exported_dtc_path:
            count = self._diag_view.dtc_panel.import_definitions(
                dlg.exported_dtc_path)
            self._config.set("imports.odx_dtcs", dlg.exported_dtc_path)
            self._log_dock.log_business(
                f"ODX DTC定义已注入 {count} 条: "
                f"{os.path.basename(dlg.exported_dtc_path)}",
                "SUCCESS" if count else "ERROR")
            self._statusbar.showMessage(f"已注入 {count} 条DTC定义", 3000)

    def _restore_imports(self):
        """项目加载/启动时恢复已记录的导入配置（文件丢失则跳过）"""
        did_dtc = self._config.get("imports.did_dtc")
        if did_dtc and os.path.isfile(did_dtc):
            self._apply_did_dtc_import(did_dtc)
        odx_dtcs = self._config.get("imports.odx_dtcs")
        if odx_dtcs and os.path.isfile(odx_dtcs):
            self._diag_view.dtc_panel.import_definitions(odx_dtcs)
        flash_cfg = self._config.get("imports.flash_config")
        if flash_cfg and os.path.isfile(flash_cfg):
            import json
            try:
                with open(flash_cfg, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict):
                    self._flash_panel.import_config(cfg.get("flash", cfg))
            except Exception:
                pass

    def _reserved(self, name: str, stage: str):
        """P1/P2预留功能提示"""
        self._statusbar.showMessage(
            f"{name}为 {stage} 预留，将随对应业务中心实现", 4000)
        self._log_dock.log_business(f"{name}: {stage} 预留", "WARNING")

    # ---------------- 视图菜单 ----------------

    def _toggle_project_panel(self, visible: bool):
        """视图-左侧项目面板开关"""
        self._project_tree.setVisible(visible)

    def _toggle_statusbar_visible(self, visible: bool):
        """视图-状态栏开关"""
        self._statusbar.setVisible(visible)

    def _restore_layout(self):
        """视图-恢复默认布局: 面板可见/日志展开/分割比例复位"""
        self._project_tree.setVisible(True)
        self._act_tree.setChecked(True)
        self._statusbar.setVisible(True)
        self._act_statusbar.setChecked(True)
        self._log_dock.set_collapsed(False)
        self._lbl_logging.setText("Logging ●")
        self._lbl_logging.setStyleSheet("")
        self._v_splitter.setSizes([640, 260])
        if self.isFullScreen():
            self.showNormal()
        self._log_dock.log_business("已恢复默认布局")
        self._statusbar.showMessage("已恢复默认布局", 2000)

    # ---------------- 工具菜单 ----------------

    def _on_batch_read_did(self):
        """工具-批量读取DID: 跳转ECU诊断触发信息DID批量读取"""
        self._nav.setCurrentIndex(NAV_DIAG)
        self._diag_view.read_ecu_info()

    def _on_batch_read_dtc(self):
        """工具-批量读取DTC: 跳转故障码页签并触发读取"""
        self._nav.setCurrentIndex(NAV_DIAG)
        self._diag_view.switch_panel("故障码 DTC")
        self._diag_view.dtc_panel.read_from_ecu()

    def _on_clear_dtc(self):
        """清除所有DTC: 发送 0x14 FF FF FF（真实UDS调用）"""
        if self._uds_client is None:
            QMessageBox.information(self, "清除DTC", "请先连接CAN再执行清除DTC")
            return
        ecu_name = self._current_ecu.name if self._current_ecu else "--"
        reply = QMessageBox.question(
            self, "清除DTC", f"确认清除 ECU [{ecu_name}] 的所有DTC?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._log_dock.log_business("清除所有DTC: 发送 14 FF FF FF")
        self._start_worker(
            self._uds_client.send_raw, (bytes([0x14, 0xFF, 0xFF, 0xFF]),),
            self._on_clear_dtc_done, self._on_clear_dtc_error,
            "_cleardtc_worker", "_cleardtc_thread")

    def _on_clear_dtc_done(self, resp):
        if resp and UdsService.is_positive_response(resp):
            self._log_dock.log_business("DTC已全部清除", "SUCCESS")
            self._statusbar.showMessage("DTC已全部清除", 3000)
        elif resp and UdsService.is_negative_response(resp):
            nrc = resp[2] if len(resp) > 2 else 0
            self._log_dock.log_business(
                f"清除DTC失败: NRC 0x{nrc:02X} "
                f"{UdsService.get_nrc_description(nrc)}", "ERROR")
        else:
            self._log_dock.log_business("清除DTC失败: 无响应", "ERROR")

    def _on_clear_dtc_error(self, msg):
        self._log_dock.log_business(f"清除DTC异常: {msg}", "ERROR")

    def _show_can_trace(self):
        """工具-报文工具: 展开日志面板并切到CAN Trace"""
        self._log_dock.show_tab(LogDock.TAB_CAN)

    def _show_uds_trace(self):
        """工具-报文工具: 展开日志面板并切到UDS Trace"""
        self._log_dock.show_tab(LogDock.TAB_UDS)

    def _show_replay(self):
        """工具-报文工具: 跳转报文分析→报文重放页签"""
        self._nav.setCurrentIndex(NAV_TRACE)
        self._trace_view.show_replay()

    def _show_dbc(self):
        """工具-报文工具: 跳转报文分析→数据库页签"""
        self._nav.setCurrentIndex(NAV_TRACE)
        self._trace_view.show_dbc()

    def _on_ecu_def_manage(self):
        """工具-ECU Definition管理: 打开定义目录"""
        ecu_dir = os.path.join(self._project_root(), "resources", "ecu")
        if os.path.isdir(ecu_dir):
            subprocess.Popen(f'explorer "{ecu_dir}"')
        self._log_dock.log_business(f"打开ECU Definition目录: {ecu_dir}")

    def _on_can_manage(self):
        """工具-CAN连接管理: 跳转工具中心"""
        self._nav.setCurrentIndex(NAV_TOOLS)
        self._statusbar.showMessage(
            "CAN连接管理: 在工具中心配置VCI与通道后使用 连接(F5)", 4000)

    def _on_security_config(self):
        """工具-Security配置: 跳转高级诊断-Security Access"""
        self._nav.setCurrentIndex(NAV_DIAG)
        self._diag_view.open_security()

    def _on_view_log(self):
        """工具-日志工具: 展开并查看业务日志"""
        self._log_dock.show_tab(LogDock.TAB_BUSINESS)

    def _on_export_log(self):
        """导出日志: 导出当前活动页签的日志"""
        self._log_dock.show_tab(self._log_dock._tabs.currentIndex())
        self._log_dock._export_active()

    # ---------------- 文件菜单（导出报告） ----------------

    def _on_export_report(self):
        """导出诊断报告: 基于当前DTC读取结果生成HTML报告"""
        records = self._diag_view.dtc_panel.dtc_records
        ecu_name = self._current_ecu.name if self._current_ecu else ""
        if not records:
            self._log_dock.log_business(
                "导出报告: 尚未读取DTC，将生成空报告", "WARNING")
        gen = ReportGenerator(os.path.join(self._project_root(), "reports"))
        filepath = gen.generate_dtc_report(records, ecu_name)
        self._log_dock.log_business(f"诊断报告已生成: {filepath}", "SUCCESS")
        self._statusbar.showMessage(f"报告已生成: {filepath}", 5000)
        reply = QMessageBox.question(
            self, "导出诊断报告",
            f"报告已生成:\n{filepath}\n\n是否打开所在文件夹?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            subprocess.Popen(f'explorer /select,"{filepath}"')

    # ---------------- 帮助菜单 ----------------

    def _on_quick_start(self):
        QMessageBox.information(
            self, "快速入门",
            "<h3>快速入门</h3><ol>"
            "<li><b>配置CAN</b>: 工具中心选择VCI类型/通道/波特率</li>"
            "<li><b>连接</b>: 工具栏 连接 或 F5</li>"
            "<li><b>选择ECU</b>: 左侧项目树双击目标ECU切换上下文</li>"
            "<li><b>诊断</b>: ECU诊断页用快捷入口条读取ECU信息、DTC、数据流等</li>"
            "<li><b>全车扫描</b>: F7 批量识别所有已定义ECU在线状态</li>"
            "<li><b>报文</b>: 底部通信日志(Ctrl+L)提供业务/UDS/CAN三层报文</li>"
            "</ol>")

    def _on_user_manual(self):
        QMessageBox.information(
            self, "用户手册",
            "<h3>模块说明</h3><ul>"
            "<li><b>ECU诊断</b>: 信息卡 / 故障码 / 数据流 / DID / IO控制 / "
            "例程 / 特殊功能 / 高级诊断(UDS服务、Session、Security、Sequence)</li>"
            "<li><b>刷写中心</b>: 安全访问→擦除→下载→传输→校验→复位六步刷写</li>"
            "<li><b>标定中心 / 测试中心</b>: P2/P1 预留</li>"
            "<li><b>报文分析</b>: CAN报文会话级分析</li>"
            "<li><b>工具中心</b>: VCI/CAN通信配置</li>"
            "</ul><p>诊断项目: 文件→保存诊断项目(Ctrl+S) 保存连接参数与"
            "ECU上下文，下次启动自动恢复。</p>")

    def _on_uds_reference(self):
        """UDS服务参考（ISO 14229-1核心服务）"""
        descs = {
            0x10: "诊断会话控制", 0x11: "ECU复位", 0x14: "清除故障码",
            0x19: "读取DTC信息", 0x22: "按标识符读取数据", 0x23: "按地址读取内存",
            0x24: "读取标度数据", 0x27: "安全访问(种子/密钥)", 0x28: "通信控制",
            0x2E: "按标识符写入数据", 0x2F: "输入输出控制", 0x31: "例程控制",
            0x34: "请求下载", 0x35: "请求上传", 0x36: "数据传输",
            0x37: "请求传输退出", 0x3E: "TesterPresent", 0x85: "DTC设置控制",
        }
        rows = []
        for sid, name in SERVICE_NAMES.items():
            rows.append(
                f"<tr><td>0x{sid:02X}</td><td>{name}</td>"
                f"<td>{descs.get(sid, '')}</td></tr>")
        QMessageBox.information(
            self, "UDS服务参考",
            "<h3>UDS核心服务 (ISO 14229-1)</h3>"
            "<table cellspacing='4'>"
            "<tr><th>SID</th><th>服务</th><th>说明</th></tr>"
            + "".join(rows) + "</table>")

    def _on_shortcuts_help(self):
        QMessageBox.information(
            self, "快捷键",
            "<table cellspacing='4'>"
            "<tr><td>F5 / F6</td><td>连接 / 断开</td></tr>"
            "<tr><td>F7</td><td>全车扫描</td></tr>"
            "<tr><td>Ctrl+N / Ctrl+O / Ctrl+S</td>"
            "<td>新建 / 打开 / 保存诊断项目</td></tr>"
            "<tr><td>Ctrl+L</td><td>显示/隐藏日志面板</td></tr>"
            "<tr><td>Ctrl+1 ~ Ctrl+6</td><td>切换一级导航</td></tr>"
            "<tr><td>Space</td><td>暂停/恢复 CAN Trace</td></tr>"
            "<tr><td>F11</td><td>全屏模式</td></tr>"
            "<tr><td>Alt+F4</td><td>退出</td></tr>"
            "</table>")

    def _on_check_update(self):
        QMessageBox.information(
            self, "检查更新", "ECU Diagnostic Studio V2.1.0 已是最新版本")

    def _on_feedback(self):
        QMessageBox.information(
            self, "问题反馈",
            "遇到问题时，请通过 文件→导出→日志 保存通信日志，"
            "并连同诊断项目文件与复现步骤一并反馈给工具维护者。")

    def _on_open_log_dir(self):
        """工具-日志工具: 在资源管理器中打开日志根目录"""
        from src.log.log_manager import get_log_manager
        log_dir = get_log_manager().log_base_dir
        os.makedirs(log_dir, exist_ok=True)
        if os.name == "nt":
            subprocess.Popen(f'explorer "{log_dir}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", log_dir])
        else:
            subprocess.Popen(["xdg-open", log_dir])
        self._statusbar.showMessage(f"已打开日志文件夹: {log_dir}", 3000)

    def _on_about(self):
        QMessageBox.about(
            self, "关于 ECU Diagnostic Studio",
            "<h2>ECU Diagnostic Studio</h2>"
            "<p>版本: 2.1.0 (V2.1 UI)</p>"
            "<p>配置驱动的ECU工程诊断平台: 诊断 / 刷写 / 测试 / Trace</p>"
            "<p>技术栈: Python + PyQt6 + python-can</p>"
        )

    def _on_settings(self):
        """选项设置（§2.1: 主题等系统设置）"""
        dlg = SettingsDialog(self._current_theme, self)
        if dlg.exec():
            new_theme = dlg.selected_theme
            if new_theme != self._current_theme:
                self._current_theme = new_theme
                self._apply_theme()
                self._config.set("ui.theme", new_theme)
                self._log_dock.log_business(f"主题切换: {new_theme}")

    def closeEvent(self, event):
        """退出时自动保存连接参数与当前ECU，关闭桥接子进程"""
        try:
            self._connection_panel.save_to_config()
            if self._current_ecu is not None:
                self._config.set("project.current_ecu", self._current_ecu.name)
            self._config.save_config()
        except Exception:
            pass
        try:
            # 关闭32位桥接子进程（若存在）
            self._diag_view.security_panel.shutdown()
        except Exception:
            pass
        super().closeEvent(event)

    @property
    def log_widget(self) -> LogWidget:
        """兼容入口: 全局CAN Trace"""
        return self._log_dock.can_trace
