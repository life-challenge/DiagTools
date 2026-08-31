"""报文分析视图（V2.2 Phase 13）

一级页面结构:
  报文分析
  ├── CAN Trace  全部报文（ID过滤/范围/暂停/导出/搜索）
  ├── UDS Trace  仅可解析为UDS服务的报文
  ├── 原始报文   Raw UDS 发送与重放工具（工程师能力保留）
  ├── 报文重放   加载导出CSV/HEX序列按间隔或原始时序重放
  └── 数据库     DBC诊断数据库：报文/信号定义与信号级解码

底部「通信日志」面板仍提供轻量级全局Trace；本工作区为会话级分析入口，
两者数据同源（主窗口 add_frame 同步转发）。
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTabWidget
from src.ui.panels.raw_panel import RawPanel
from src.ui.panels.replay_panel import ReplayPanel
from src.ui.panels.dbc_panel import DbcPanel
from src.ui.widgets.log_widget import LogWidget


class TraceView(QWidget):
    """报文分析工作区"""

    TAB_CAN, TAB_UDS, TAB_RAW, TAB_REPLAY, TAB_DBC = range(5)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw_panel = RawPanel()
        self._can_trace = LogWidget()
        self._uds_trace = LogWidget()
        self._replay_panel = ReplayPanel()
        self._dbc_panel = DbcPanel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._can_trace, "CAN Trace")
        self._tabs.addTab(self._uds_trace, "UDS Trace")
        self._tabs.addTab(self._raw_panel, "原始报文")
        self._tabs.addTab(self._replay_panel, "报文重放")
        self._tabs.addTab(self._dbc_panel, "数据库")
        layout.addWidget(self._tabs, 1)

    # ---------------- 对外接口 ----------------

    @property
    def raw_panel(self) -> RawPanel:
        return self._raw_panel

    @property
    def can_trace(self) -> LogWidget:
        return self._can_trace

    @property
    def uds_trace(self) -> LogWidget:
        return self._uds_trace

    @property
    def replay_panel(self) -> ReplayPanel:
        return self._replay_panel

    @property
    def dbc_panel(self) -> DbcPanel:
        return self._dbc_panel

    def add_frame(self, direction: str, can_id: int, data: bytes, desc: str):
        """报文入口（与底部日志Dock同源）: CAN全量, UDS仅可解析帧"""
        if desc:
            self._uds_trace.add_message(direction, can_id, data, desc)
        # DBC信号解码追加到CAN Trace描述（已加载数据库时），不进入UDS Trace
        dbc_desc = self._dbc_panel.decode_frame(can_id, data)
        if dbc_desc:
            desc = f"{desc} | {dbc_desc}" if desc else dbc_desc
        self._can_trace.add_message(direction, can_id, data, desc)

    def show_trace(self, uds: bool = False):
        """外部入口: 切换到CAN/UDS Trace页签"""
        self._tabs.setCurrentIndex(self.TAB_UDS if uds else self.TAB_CAN)

    def show_replay(self):
        """外部入口: 切换到报文重放页签"""
        self._tabs.setCurrentIndex(self.TAB_REPLAY)

    def show_dbc(self):
        """外部入口: 切换到数据库页签"""
        self._tabs.setCurrentIndex(self.TAB_DBC)

    def set_can_interface(self, iface):
        self._replay_panel.set_can_interface(iface)

    def set_uds_client(self, client):
        self._raw_panel.set_uds_client(client)
