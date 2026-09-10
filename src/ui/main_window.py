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
import sys
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QSplitter,
    QTabWidget, QToolBar, QStatusBar, QLabel, QMenu, QTreeWidget,
    QTreeWidgetItem, QInputDialog, QMessageBox, QFileDialog, QSizePolicy,
    QApplication, QScrollArea, QFrame, QPushButton
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QFont

from src.ui.styles import get_theme, COLORS
from src.ui.widgets.log_widget import LogWidget
from src.ui.widgets.log_dock import LogDock
from src.utils.config_manager import get_config_manager
from src.utils.paths import get_project_root
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
import src  # __version__ 统一版本号（关于/检查更新）

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
        # 自检验证在线的ECU地址对集合(tx, rx): 树图标/徽章的真实依据，
        # 区别于"总线已连接"——当前ECU不响应时不应显示在线
        self._ecu_online: set = set()
        # 会话级VIN（连接后自动读/手动编辑，断开即失效回"--"）:
        # VIN是车辆实时信息而非工具配置，不持久化到配置文件——
        # 否则上次连接读到的VIN会在未连接时残留显示
        self._session_vin: str = ""
        # UDS Trace重组器: TX/RX各自独立，从分段帧重组完整UDS报文（14229会话层）
        self._tx_reasm = _TpReassembler()
        self._rx_reasm = _TpReassembler()

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
            f"DiagTools v{src.__version__} 启动, 当前ECU: "
            f"{self._current_ecu.name if self._current_ecu else '--'}")

    # ---------------- UI骨架 ----------------

    def _init_ui(self):
        self.setWindowTitle("DiagTools - CAN/DoIP UDS 诊断仪")
        # 最小尺寸需适配小屏幕（笔记本可用区可低至~1366x688），
        # 过大的硬性最小值会导致窗口无法缩小、底部内容永久在屏幕外
        self.setMinimumSize(900, 600)
        self.resize(1440, 900)
        # 启动时收缩到当前屏幕可用区内（如小尺寸笔记本屏）
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            self.resize(min(1440, avail.width()), min(900, avail.height()))

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
        # 工作区包滚动区: 小屏（笔记本150%缩放下逻辑可用区可低至1280x752）
        # 工作区被压缩时以滚动条代替裁切，保证被压缩区域的内容可达可读
        self._nav_scroll = QScrollArea()
        self._nav_scroll.setWidget(self._nav)
        self._nav_scroll.setWidgetResizable(True)
        self._nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        h_splitter.addWidget(self._nav_scroll)

        h_splitter.setStretchFactor(0, 1)
        h_splitter.setStretchFactor(1, 6)
        # 注: 工作区不再设固定最小高度——其自然最小尺寸(约620px)由滚动区
        # 接管，空间不足时出滚动条而非裁切内容

        # 刷写安全钩子: 复用安全面板已加载的算法插件 (level, seed) -> key
        _sm = self._diag_view.security_panel.security_manager
        self._flash_panel.set_key_generator(
            lambda level, seed: _sm.generate_key(level, seed))
        # 测试中心同样注入安全钩子: 用例中的27 xx自动全流程
        # （如WR组解锁前置）需要算法算钥，漏注入时收种子后无法发密钥
        self._test_view.set_key_generator(
            lambda level, seed: _sm.generate_key(level, seed))
        # 刷写中心直达安全算法页（算法加载/管理与刷写安全访问共用）
        self._flash_panel.open_security_requested.connect(
            self._on_security_config)

        # ---- 底部: 可折叠日志面板（§7）----
        self._log_dock = LogDock()
        # ECU诊断子面板日志统一汇入底部业务日志（面板已不内置日志窗口）
        self._diag_view.business_log.connect(self._log_dock.log_business)
        # 测试中心执行期间暂停会话保活: 避免3E帧与协议一致性用例收发交错干扰
        self._test_view.tests_started.connect(
            self._diag_view.session_panel.pause_keepalive)
        self._test_view.tests_finished.connect(
            self._diag_view.session_panel.resume_keepalive)
        # 日志面板最小高度由LogDock自管（展开170防挤压/折叠收缩到头部一行）
        self._v_splitter.addWidget(self._log_dock)
        self._v_splitter.setStretchFactor(0, 1)
        self._v_splitter.setStretchFactor(1, 0)
        self._v_splitter.setChildrenCollapsible(False)
        # 日志面板高度: 优先恢复用户上次拖拽的位置，否则默认约屏高4成
        saved_dock_h = int(self._config.get("ui.log_dock_height", 0) or 0)
        # 上限同时受窗口高度约束（工作区至少保前340px最小高度）
        max_dock_h = min(780, max(170, self.height() - 340))
        if 170 <= saved_dock_h <= max_dock_h:
            dock_h = saved_dock_h
        else:
            dock_h = min(380, max(170, self.height() // 2))
        self._v_splitter.setSizes([max(340, self.height() - dock_h), dock_h])

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
        vin = self._session_vin or "--"
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
        """网络节点文本: 实时连接状态（接口 + 通道 + 波特率）

        未连接时显示"未连接"而非配置值，避免误导用户以为已连通。
        """
        try:
            if (self._uds_client is None
                    or not self._connection_panel.is_connected):
                return "未连接"
            iface = self._connection_panel.can_interface
            name = iface.interface_name if iface is not None else "CAN"
            ch = self._connection_panel.channel or "--"
            br = self._connection_panel.bitrate
            return f"{name} | {ch} | {br // 1000}k"
        except Exception:
            return "未连接"

    def _refresh_ecu_icons(self):
        """刷新ECU在线状态图标并高亮当前ECU

        在线判定: 连通性自检通过过的地址对(_ecu_online)，
        或最近一次全车扫描中响应过（存入_scan_online）。
        仅"总线已连接"不再直接点亮——避免切到未响应ECU时误示在线。
        """
        scan_online = getattr(self, "_scan_online", None) or set()
        norm = QFont()
        bold = QFont()
        bold.setBold(True)
        for defn in self._ecu_defs:
            item = self._ecu_items.get(defn.name)
            if item is None:
                continue
            is_current = (self._current_ecu is not None
                          and defn.name == self._current_ecu.name)
            verified = (defn.tx_id, defn.rx_id) in self._ecu_online
            scanned = (defn.tx_id, defn.rx_id) in scan_online
            icon = _ICON_ON if (verified or scanned) else _ICON_OFF
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
        elif data == "__cfg_flash__":
            # Flash配置已实现（文件→导入→Flash配置），双击直达刷写中心
            self._nav.setCurrentIndex(NAV_FLASH)
            self._statusbar.showMessage(
                "刷写配置可经 文件→导入→Flash配置 导入（JSON）", 4000)
        elif data == "__cfg_a2l__":
            # A2L导入已实现: 打开解析对话框（导出后记录到项目配置）
            self._on_import_a2l()

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
                if is_model:
                    text, ok = QInputDialog.getText(
                        self, "编辑车辆信息", "车型:",
                        text=self._config.get("project.vehicle_model", ""))
                    if ok:
                        self._config.set("project.vehicle_model",
                                         text.strip() or "--")
                else:
                    # VIN为会话级: 手动填的值仅本次连接有效，不持久化
                    text, ok = QInputDialog.getText(
                        self, "编辑车辆信息", "VIN:",
                        text=self._session_vin)
                    if ok:
                        self._session_vin = text.strip()
                self._build_project_tree()
                self._refresh_overview_info()

    def _open_ecu_definition_dir(self, defn: EcuDefinition = None):
        """在资源管理器中打开ECU定义目录"""
        target = defn or self._current_ecu
        if target is None or not getattr(target, "def_dir", ""):
            return
        if os.path.exists(target.def_dir):
            subprocess.Popen(f'explorer "{target.def_dir}"')

    # ---------------- ECU选择 ----------------

    def _select_ecu(self, defn: EcuDefinition):
        """选中ECU: 应用其地址到连接配置，并更新诊断工作区上下文

        多ECU在线切换: 若总线已连接（CAN），热切换UDS寻址到新ECU地址对
        并重新自检——无需断开重连，点击项目树即可在多个在线ECU间切换。
        """
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
        # 已连接且地址变化 → 热切换寻址（总线不断开）
        if self._uds_client is not None:
            if self._connection_panel.is_doip:
                self._log_dock.log_business(
                    f"切换到 ECU {defn.name}: DoIP连接需重新建立（断开后重连）",
                    "WARNING")
            elif (self._uds_client.tx_id, self._uds_client.rx_id) \
                    != (defn.tx_id, defn.rx_id):
                self._hot_switch_ecu(defn)
        self._log_dock.log_business(
            f"选择 ECU: {defn.name} (TX 0x{defn.tx_id:03X} / RX 0x{defn.rx_id:03X})")
        self._statusbar.showMessage(
            f"已选择 ECU: {defn.name} (0x{defn.tx_id:03X}/0x{defn.rx_id:03X})", 3000)

    def _hot_switch_ecu(self, defn: EcuDefinition):
        """已连接状态下切换ECU: 重定向UDS寻址并重新自检（总线保持打开）

        停保活→改地址→恢复保活→自检，避免保活线程与新地址的请求交错。
        """
        sp = self._diag_view.session_panel
        sp.pause_keepalive()
        try:
            self._uds_client.tx_id = defn.tx_id
            self._uds_client.rx_id = defn.rx_id
        finally:
            sp.resume_keepalive()
        # 同步UI地址信息（通信信息区TX/RX）
        can_iface = self._connection_panel.can_interface
        if can_iface is not None:
            self._diag_view.set_connection_info(
                can_iface.interface_name, can_iface.channel_info,
                self._connection_panel.bitrate, defn.tx_id, defn.rx_id)
        self._log_dock.log_business(
            f"已切换寻址到 ECU {defn.name} "
            f"(0x{defn.tx_id:03X}/0x{defn.rx_id:03X})，正在自检...")
        self._link_check()

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
            self._session_vin or "--", network)

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
        """全局工具栏（§2.1）: 连接 | 断开 | 全车扫描 | 日志（主题移入设置）

        连接/断开为互斥操作对：用红绿对比色实体按钮呈现，
        未连接时"断开"禁用变灰、已连接时"连接"禁用变灰——
        任何一个时刻只有可执行的那个是彩色醒目的。
        其余按钮用Unicode字形图标+文字，保持轻量（无需图标资源文件）。
        """
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._btn_connect = QPushButton("▶ 连接")
        self._btn_connect.setObjectName("btn_connect")
        self._btn_connect.setShortcut("F5")
        self._btn_connect.setToolTip("建立诊断连接 (F5)")
        self._btn_connect.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_connect.clicked.connect(self._on_connect)
        toolbar.addWidget(self._btn_connect)

        self._btn_disconnect = QPushButton("■ 断开")
        self._btn_disconnect.setObjectName("btn_disconnect")
        self._btn_disconnect.setShortcut("F6")
        self._btn_disconnect.setToolTip("断开诊断连接 (F6)")
        self._btn_disconnect.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_disconnect.clicked.connect(self._on_disconnect)
        toolbar.addWidget(self._btn_disconnect)

        toolbar.addSeparator()

        self._btn_scan = QAction("🔍 全车扫描", self)
        self._btn_scan.setShortcut("F7")
        self._btn_scan.setToolTip("对已定义地址逐个探测在线ECU (F7)")
        self._btn_scan.triggered.connect(self._on_scan)
        toolbar.addAction(self._btn_scan)

        toolbar.addSeparator()

        self._btn_log = QAction("📋 日志", self)
        self._btn_log.setShortcut("Ctrl+L")
        self._btn_log.setToolTip("折叠/展开底部日志面板 (Ctrl+L)")
        self._btn_log.triggered.connect(self._toggle_log_dock)
        toolbar.addAction(self._btn_log)

        # 右上角连接状态标签: 全局常驻反馈（ECU诊断页外的页面无Online指示）
        self._conn_label = QLabel("未连接")
        self._conn_label.setStyleSheet("color: #888;")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addWidget(self._conn_label)

        # 初始未连接态: 绿色"连接"可点、"断开"/"扫描"灰色禁用
        # （后续由 _set_toolbar_connected 随连接状态切换）
        self._set_toolbar_connected(False)

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
            txt = ", ".join(f"L{lvl}" for lvl in levels)
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

            if self._connection_panel.is_doip:
                # DoIP: 注入DoIP传输层，用逻辑地址替代CAN ID展示
                self._uds_client = UdsClient(transport_layer=can_iface)
                bus_text = (f"{self._connection_panel.doip_ip}:"
                            f"{self._connection_panel.doip_port}")
                bitrate = 0
                addr_tx = can_iface.ecu_address
                addr_rx = can_iface.tester_address
                # DoIP会话上下文: pcap导出时重建以太网帧用
                self._log_dock.can_trace.set_doip_context(
                    can_iface.tester_address, can_iface.ecu_address)
                self._trace_view.can_trace.set_doip_context(
                    can_iface.tester_address, can_iface.ecu_address)
            else:
                self._uds_client = UdsClient(can_iface, tx_id=tx_id, rx_id=rx_id)
                bus_text = (f"{self._connection_panel.channel} | "
                            f"{self._connection_panel.bitrate // 1000}k")
                bitrate = self._connection_panel.bitrate
                addr_tx, addr_rx = tx_id, rx_id
                self._log_dock.can_trace.clear_doip_context()
                self._trace_view.can_trace.clear_doip_context()
                # 连接成功后按实际通信地址识别ECU: 若连接面板配置的地址对
                # 与当前选中的ECU不一致（如手动填了DASH地址但树选的是BCM），
                # 自动切换ECU上下文，避免"显示BCM实际在跟DASH说话"的错位
                matched = next((d for d in self._ecu_defs
                                if (d.tx_id, d.rx_id) == (tx_id, rx_id)), None)
                if matched is not None and matched is not self._current_ecu:
                    self._log_dock.log_business(
                        f"连接地址 0x{tx_id:03X}/0x{rx_id:03X} 匹配 ECU "
                        f"{matched.name}，自动切换ECU上下文", "INFO")
                    self._select_ecu(matched)
                elif matched is None and self._current_ecu is not None \
                        and (self._current_ecu.tx_id, self._current_ecu.rx_id) \
                        != (tx_id, rx_id):
                    self._log_dock.log_business(
                        f"注意: 连接地址 0x{tx_id:03X}/0x{rx_id:03X} 与当前ECU "
                        f"{self._current_ecu.name}(0x{self._current_ecu.tx_id:03X}/"
                        f"0x{self._current_ecu.rx_id:03X})不一致", "WARNING")

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
            self._ecu_online.clear()  # 旧连接的在线记录作废，等自检重新验证
            self._diag_view.set_connection_info(
                can_iface.interface_name, can_iface.channel_info,
                bitrate, addr_tx, addr_rx)
            self._diag_view.update_live(session_text="默认",
                                        ecu_name=(self._current_ecu.name
                                                  if self._current_ecu else None))

            # 状态栏（§8）
            self._set_dot(self._lbl_can, "CAN", True)
            self._set_dot(self._lbl_uds, "UDS", True)
            self._lbl_vci.setText(f"VCI: {can_iface.interface_name}")
            self._lbl_bus.setText(bus_text)
            self._set_toolbar_connected(True)
            self._conn_label.setText(
                f"已连接 {can_iface.interface_name} | {bus_text}")
            self._conn_label.setStyleSheet("color: #4CAF50;")
            ecu_name = self._current_ecu.name if self._current_ecu else "--"
            self._lbl_ecu.setText(f"ECU: {ecu_name} | Online")
            self._lbl_session.setText("Session: 默认")
            self._log_dock.log_business(
                f"已连接 {can_iface.interface_name} | {bus_text}")
            self._refresh_overview_info()
            # 连接后连通性自检: 发 TesterPresent 验证ECU真实可达，
            # 区分"总线已打开"与"ECU有响应"，避免误示Online
            self._link_check()
        else:
            # 断开前先等后台UDS线程（自检/扫描等）退出，避免与Uninitialize
            # 并发读写使驱动进入异常状态——这是快速连断时概率性重连失败的根因
            for attr in ("_link_thread", "_scan_thread", "_cleardtc_thread"):
                th = getattr(self, attr, None)
                if th is not None:
                    th.wait(1500)
                    setattr(self, attr, None)
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
            self._ecu_online.clear()
            self._log_dock.can_trace.clear_doip_context()
            self._trace_view.can_trace.clear_doip_context()

            self._set_dot(self._lbl_can, "CAN", False)
            self._set_dot(self._lbl_uds, "UDS", False)
            self._lbl_vci.setText("VCI: --")
            self._lbl_bus.setText("--")
            self._set_toolbar_connected(False)
            self._conn_label.setText("未连接")
            self._conn_label.setStyleSheet("color: #888;")
            # VIN是会话级车辆信息: 断开后失效回"--"，不残留本次读到的值
            self._session_vin = ""
            self._build_project_tree()
            self._refresh_overview_info()
            ecu_name = self._current_ecu.name if self._current_ecu else "--"
            self._lbl_ecu.setText(f"ECU: {ecu_name} | Offline")
            self._log_dock.log_business("已断开连接")

        self._refresh_ecu_icons()

    def _link_check(self):
        """连接后连通性自检（后台发 3E 00 验证ECU响应）

        总线打开成功≠ECU可达: 无响应时明确告警（波特率/接线/供电），
        避免用户误以为已连通而扫描全离线。
        """
        if self._uds_client is None:
            return
        client = self._uds_client
        ecu = self._current_ecu  # 捕获发起时的ECU，避免自检在递时切换上下文错记

        def _check():
            # 非抑制 TesterPresent: ECU 应回 7E 00
            return client.tester_present(suppress_response=False)

        def _on_ok(resp):
            if resp and resp[0] == 0x7E:
                self._log_dock.log_business(
                    f"连通性自检通过: {ecu.name if ecu else 'ECU'}响应正常",
                    "SUCCESS")
                self._diag_view.set_ecu_reachability(True)
                if ecu is not None:
                    self._ecu_online.add((ecu.tx_id, ecu.rx_id))
                    self._refresh_ecu_icons()
                # 自检通过后自动补全车辆信息（VIN）
                self._auto_fill_vin()
            else:
                self._link_check_fail()

        def _on_err(_msg):
            self._link_check_fail()

        self._start_worker(_check, (), _on_ok, _on_err,
                           "_link_worker", "_link_thread")

    def _auto_fill_vin(self):
        """自检通过后自动读VIN（22 F190）回填项目树"车辆信息"

        仅当前VIN为空/"--"时回填，不覆盖用户手动编辑的值；
        读取失败静默忽略（不影响主流程）。车型无标准DID，
        仍需右键"编辑车型"手动设置。
        """
        cur = (self._session_vin or "").strip()
        if cur:
            return
        if self._uds_client is None:
            return
        client = self._uds_client

        def _read():
            # VIN为多帧响应，真实ECU可能超过P2，用2s超时
            return client.read_data_by_identifier(0xF190, timeout=2.0)

        def _on_done(resp):
            if not resp or len(resp) < 4 or resp[0] != 0x62:
                return
            vin = resp[3:].decode("ascii", errors="ignore").strip("\x00 \t\r\n")
            if not vin:
                return
            # 会话级缓存，不写入持久配置（断开即失效）
            self._session_vin = vin
            self._build_project_tree()
            self._refresh_overview_info()
            self._log_dock.log_business(f"自动读取车辆VIN: {vin}", "SUCCESS")

        def _on_err(_msg):
            pass  # VIN读取失败静默，不打扰用户

        self._start_worker(_read, (), _on_done, _on_err,
                           "_vin_worker", "_vin_thread")

    def _link_check_fail(self):
        ecu_name = self._current_ecu.name if self._current_ecu else "--"
        self._lbl_ecu.setText(f"ECU: {ecu_name} | 无响应")
        self._diag_view.set_ecu_reachability(False)
        # 该ECU未通过自检: 从在线集合移除，树图标不再点亮
        if self._current_ecu is not None:
            self._ecu_online.discard(
                (self._current_ecu.tx_id, self._current_ecu.rx_id))
            self._refresh_ecu_icons()
        # 附上总线级收发计数，直接暴露"只发不收"证据
        stats = ""
        rx_zero = False
        try:
            iface = self._connection_panel.can_interface
            if iface is not None:
                st = iface.get_stats()
                rx_zero = int(st.get("rx_count", 0)) == 0
                stats = f"（总线计数 TX:{st.get('tx_count', '?')} " \
                        f"RX:{st.get('rx_count', '?')}）"
        except Exception:
            pass
        if rx_zero:
            # RX=0: 总线无任何响应帧，首要怀疑ECU休眠（市场需求场景）
            self._log_dock.log_business(
                f"连通性自检失败: ECU无响应{stats}，已发 3E 00 无 7E 00 回复。"
                "RX为0说明ECU可能休眠或未上电。若ECU休眠，请在连接面板勾选"
                "\"连接时自动唤醒\"后重新连接，或手动启动\"ECU唤醒报文\"发送；"
                "若仍无响应，请检查: ECU供电、波特率（两端是否一致）、"
                "终端电阻、CAN高/低线接线、PCAN设备指示灯", "WARNING")
        else:
            # 有收帧但无诊断回复: 通信物理层正常，多为地址/诊断层问题
            self._log_dock.log_business(
                f"连通性自检失败: 诊断无响应{stats}。总线有帧但ECU未回复"
                "3E 00，请检查诊断请求/响应ID（0x{self._connection_panel.tx_id:03X}/"
                "0x{self._connection_panel.rx_id:03X}）是否与ECU实际地址一致、"
                "ECU是否处于休眠（可勾选\"连接时自动唤醒\"后重连）", "WARNING")

    def _on_can_message(self, direction: str, msg):
        """报文监听回调（可能在后台线程），转发到底部日志面板与报文分析工作区

        分层记录策略:
        - CAN Trace: 全部原始帧（含ISO-TP分段帧FF/CF/流控帧FC、DoIP传输层报文）
        - UDS Trace: 仅重组完成的完整UDS报文（14229会话层诊断内容），
          TX/RX各自独立重组状态机；FC帧不进UDS Trace
        """
        uds_msg = None
        uds_desc = ""
        if self._connection_panel.is_doip:
            # DoIP诊断消息携带裸UDS数据（无ISO-TP头），即完整报文
            uds_msg = msg.data
            uds_desc = self._describe_uds(msg.data)
            desc = uds_desc
        else:
            desc = self._describe_frame(msg.data)
            # 诊断地址帧才进重组器: RX=本ECU响应地址，TX=请求/功能地址
            func_id = getattr(self._current_ecu, "functional_tx_id",
                              None) or 0x7DF
            if direction == "RX":
                is_diag = (msg.can_id == self._connection_panel.rx_id)
                reasm = self._rx_reasm
            else:
                is_diag = msg.can_id in (self._connection_panel.tx_id,
                                         func_id)
                reasm = self._tx_reasm
            if is_diag:
                uds_msg = reasm.feed(msg.data)
                if uds_msg is not None:
                    uds_desc = self._describe_uds(uds_msg)
        self._log_dock.add_frame(direction, msg.can_id, msg.data, desc,
                                 uds_msg, uds_desc)
        self._trace_view.add_frame(direction, msg.can_id, msg.data, desc,
                                   uds_msg, uds_desc)

    @staticmethod
    def _describe_sid(sid: int) -> str:
        """UDS SID描述（正/负响应与服务名）"""
        if sid == 0x7F:
            return "负响应"
        if sid > 0x40:
            return f"{UdsService.get_service_name(sid - 0x40)} - 正响应"
        return UdsService.get_service_name(sid)

    @staticmethod
    def _describe_frame(data: bytes) -> str:
        """CAN帧描述: 根据ISO-TP帧类型和UDS SID生成"""
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
        return MainWindow._describe_sid(sid)

    @staticmethod
    def _describe_uds(data: bytes) -> str:
        """DoIP帧描述: 数据为裸UDS（无ISO-TP头），首字节即SID"""
        if not data:
            return ""
        return MainWindow._describe_sid(data[0])

    def _set_toolbar_connected(self, connected: bool):
        """工具栏启动/停止互补状态

        未连接: "连接"黄色醒目可点；"断开"灰色禁用。
        已连接: "连接"置灰不可再点；"断开"红色可点。
        任何时刻只有一个按钮可操作，当前可执行的动作颜色醒目。
        """
        self._btn_connect.setEnabled(not connected)
        self._btn_disconnect.setEnabled(connected)
        self._btn_scan.setEnabled(connected)

    def _on_connect(self):
        """工具栏连接按钮：委托给连接面板执行真实连接流程

        按钮仅在未连接时可点（已连接时置灰），无需防重复处理
        """
        self._connection_panel._on_connect()

    def _on_disconnect(self):
        """工具栏断开按钮：委托给连接面板"""
        self._connection_panel._on_disconnect()

    def _on_scan(self):
        """ECU全扫描（§10）: 对已定义地址逐个发 0x10 01 识别在线状态"""
        if self._uds_client is None or self._connection_panel.can_interface is None:
            QMessageBox.information(
                self, "ECU全扫描",
                "请先建立诊断连接（连接 CAN 总线或 DoIP）再执行全车扫描")
            return
        if getattr(self, "_scan_thread", None) is not None:
            self._statusbar.showMessage("扫描进行中...", 2000)
            return
        # 扫描期间暂停会话保持，避免 3E 保活帧与扫描请求交错干扰ECU响应
        self._diag_view.session_panel.pause_keepalive()
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
        self._diag_view.session_panel.resume_keepalive()  # 恢复会话保持
        found = {(e.tx_id, e.rx_id) for e in results}
        self._scan_online = found  # 供项目树图标显示扫描发现在线状态
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
        self._diag_view.session_panel.resume_keepalive()  # 恢复会话保持
        self._log_dock.log_business(f"ECU扫描异常: {msg}", "ERROR")
        # 车辆总览拓扑黄色警示（区别于"扫描完成但离线"的灰色态）
        self._overview_view.set_scan_failed(msg)

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
        """项目根目录（源码运行=仓库根；打包后=exe所在目录）

        注: 历史实现回溯2层得到的是src目录而非项目根，已修正。
        """
        return get_project_root()

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
            self, "导入DID/DTC配置", start_dir,
            "DID/DTC/调查表 (*.json *.xlsx *.xlsm);;JSON Files (*.json);;"
            "调查表Excel (*.xlsx *.xlsm)")
        if not filepath:
            return
        self._apply_did_dtc_import(filepath, persist=True)

    def _apply_did_dtc_import(self, filepath: str, persist: bool = False):
        """解析文件内容并注入DID面板和/或DTC面板

        支持: JSON（dids/dtcs键或列表）、OEM调查表xlsx
        （自动解析服务矩阵/DID/DTC sheet; dids与dtcs同时存在时两者均导入）
        """
        import json
        import tempfile

        ext = os.path.splitext(filepath)[1].lower()
        if ext in (".xlsx", ".xlsm"):
            # OEM调查表Excel → 解析为调查表JSON → 递归走统一导入逻辑
            try:
                from src.business.survey_xlsx_parser import parse_survey_xlsx
                data = parse_survey_xlsx(filepath)
            except Exception as e:
                QMessageBox.warning(self, "导入配置", f"调查表解析失败: {e}")
                return
            if not (data.get("dids") or data.get("dtcs")):
                QMessageBox.warning(
                    self, "导入配置", "调查表内未解析到DID/DTC定义")
                return
            with tempfile.NamedTemporaryFile(
                    "w", suffix=".json", delete=False,
                    encoding="utf-8") as tmp:
                json.dump(data, tmp, ensure_ascii=False)
                tmp_path = tmp.name
            try:
                # persist在递归内关闭，由外层记录原始xlsx路径
                self._apply_did_dtc_import(tmp_path)
            finally:
                os.remove(tmp_path)
            if persist:
                self._config.set("imports.did_dtc", filepath)
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "导入配置", f"文件解析失败: {e}")
            return

        # 内容识别: dids→DID定义, dtcs→DTC定义（两者同时存在时均导入）
        did_count = dtc_count = 0
        imported = False
        if isinstance(data, dict) and "dids" in data:
            did_count = self._diag_view.did_panel.import_definitions(filepath)
            imported = True
        if isinstance(data, dict) and "dtcs" in data:
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
            imported = True
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            if "dtc_id" in data[0]:
                dtc_count = self._diag_view.dtc_panel.import_definitions(
                    filepath)
                imported = True
            else:
                did_count = self._diag_view.did_panel.import_definitions(
                    filepath)
                imported = True

        if not imported:
            QMessageBox.warning(
                self, "导入配置", "未识别的配置格式（需含 dids 或 dtcs 键）")
            return

        parts = []
        if did_count:
            parts.append(f"DID定义 {did_count} 项")
        if dtc_count:
            parts.append(f"DTC定义 {dtc_count} 项")
        msg = "导入" + "、".join(parts) if parts else "导入内容为空"
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
            QMessageBox.information(
                self, "清除DTC", "请先建立诊断连接再执行清除DTC")
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
        """工具-Security配置/刷写中心跳转: 直达ECU诊断-安全算法页"""
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
            "<li><b>车辆总览</b>: 项目信息卡 + ECU拓扑（在线状态由扫描/自检驱动）</li>"
            "<li><b>ECU诊断</b>: 信息卡 / 故障码 / 数据流 / DID / IO控制 / "
            "例程 / 特殊功能 / 高级诊断(UDS服务、Session、Security、Sequence)</li>"
            "<li><b>刷写中心</b>: 安全访问→擦除→下载→传输→校验→复位六步刷写</li>"
            "<li><b>标定中心</b>: DID标定表（批量读取→编辑→写入→回读校验）</li>"
            "<li><b>测试中心</b>: 内置诊断用例+自定义序列，后台执行导出报告</li>"
            "<li><b>报文分析</b>: CAN/UDS Trace、原始报文、报文重放、DBC数据库解码</li>"
            "<li><b>报告中心</b>: 扫描/诊断/刷写报告统一管理（HTML/CSV/JSON）</li>"
            "<li><b>工具中心</b>: VCI/CAN通信配置</li>"
            "</ul><p>导入: 文件→导入 支持 DID/DTC配置、Flash配置、A2L、ODX/CDD。</p>"
            "<p>诊断项目: 文件→保存诊断项目(Ctrl+S) 保存连接参数与"
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
            "<tr><td>Ctrl+1 ~ Ctrl+8</td><td>切换一级导航</td></tr>"
            "<tr><td>Space</td><td>暂停/恢复 CAN Trace</td></tr>"
            "<tr><td>F11</td><td>全屏模式</td></tr>"
            "<tr><td>Alt+F4</td><td>退出</td></tr>"
            "</table>")

    def _on_check_update(self):
        QMessageBox.information(
            self, "检查更新",
            f"DiagTools v{src.__version__} 已是最新版本")

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
            self, "关于 DiagTools",
            "<h2>DiagTools</h2>"
            f"<p>CAN/DoIP UDS 诊断仪 · 版本: {src.__version__}</p>"
            "<p>配置驱动的ECU工程诊断平台: 诊断 / 刷写 / 标定 / 测试 / Trace</p>"
            "<p>技术栈: Python + PyQt6 + python-can</p>"
        )

    def _on_settings(self):
        """选项设置（§2.1: 主题等系统设置）"""
        current_days = int(self._config.get("log.retention_days", 30))
        dlg = SettingsDialog(self._current_theme, current_days, self)
        if dlg.exec():
            new_theme = dlg.selected_theme
            if new_theme != self._current_theme:
                self._current_theme = new_theme
                self._apply_theme()
                self._config.set("ui.theme", new_theme)
                self._log_dock.log_business(f"主题切换: {new_theme}")
            new_days = dlg.retention_days
            if new_days != current_days:
                self._config.set("log.retention_days", new_days)
                self._config.save_config()
                self._log_dock.log_business(
                    f"日志保留天数: {new_days} 天（下次启动时生效）")

    # ---------------- 屏幕适配 ----------------

    def _clamp_to_screen(self):
        """窗口适配: 收缩到当前屏幕可用区内，并确保标题栏可见可拖动"""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        w = min(self.width(), avail.width())
        h = min(self.height(), avail.height())
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)
        # 超出可视区（如从大显示器拖到小笔记本屏）时收回屏内
        if (self.x() + w > avail.right() or self.y() + h > avail.bottom()
                or self.x() < avail.left() or self.y() < avail.top()):
            self.move(max(avail.left(), min(self.x(), avail.right() - w)),
                      max(avail.top(), min(self.y(), avail.bottom() - h)))

    def showEvent(self, event):
        """首次显示时适配屏幕，并监听后续跨屏拖动"""
        super().showEvent(event)
        self._clamp_to_screen()
        handle = self.windowHandle()
        if handle is not None:
            try:
                handle.screenChanged.connect(self._on_screen_changed)
            except (TypeError, RuntimeError):
                pass

    def _on_screen_changed(self, _screen):
        """窗口被拖到另一块屏幕: 按新屏可用区收缩并收回屏内"""
        self._clamp_to_screen()
        # DPI不同的屏之间拖动时（如100%外接屏→150%笔记本屏），
        # 几何信息在切换瞬间尚未稳定，延迟再校正一次
        QTimer.singleShot(150, self._clamp_to_screen)

    def closeEvent(self, event):
        """退出时自动保存连接参数与当前ECU，关闭桥接子进程"""
        try:
            self._connection_panel.save_to_config()
            if self._current_ecu is not None:
                self._config.set("project.current_ecu", self._current_ecu.name)
            # 记住日志面板拖拽后的高度（折叠状态除外）
            if not self._log_dock.collapsed:
                self._config.set("ui.log_dock_height", self._log_dock.height())
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


class _TpReassembler:
    """轻量ISO-TP重组器（仅UI显示用）

    从诊断地址的原始CAN帧重组出完整UDS报文，供UDS Trace显示
    14229会话层的诊断内容；SF直接返回，FF+CF链重组，FC忽略。
    与协议层transport_layer的重组逻辑独立，互不影响。
    """

    def __init__(self):
        self._reset()

    def _reset(self):
        self._buf = bytearray()
        self._total = 0

    def feed(self, data: bytes):
        """输入一帧诊断地址报文，返回重组完成的完整报文（未完成返回None）"""
        if not data:
            return None
        pci = data[0] & 0xF0
        if pci == 0x00:  # 单帧: 首字节低半字节为长度
            self._reset()
            length = data[0] & 0x0F
            if 0 < length <= len(data) - 1:
                return bytes(data[1:1 + length])
        elif pci == 0x10:  # 首帧: 开始新重组（丢弃未完成的旧链）
            self._total = ((data[0] & 0x0F) << 8) | data[1]
            self._buf = bytearray(data[2:])
        elif pci == 0x20:  # 连续帧: 追加，收满返回
            if self._total:
                self._buf += data[1:]
                if len(self._buf) >= self._total:
                    result = bytes(self._buf[:self._total])
                    self._reset()
                    return result
        # 流控帧(0x30)与其他帧不产出UDS报文
        return None
