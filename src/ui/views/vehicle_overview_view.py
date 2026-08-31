"""车辆总览视图（V2.2 Phase 4）

显示:
  - 车辆信息: 车型 / VIN / 网络
  - 统计: ECU总数 / Online / Offline / DTC数量
  - ECU拓扑: 配置式逻辑拓扑（Gateway→子节点），状态色
    绿色=Online无DTC / 红色=有DTC / 黄色=扫描异常 / 灰色=Offline
  - 最近一次全车扫描结果表

拓扑为配置式逻辑拓扑（§4 第一版），不硬编码具体ECU名。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTreeWidget,
    QTreeWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QFrame, QAbstractItemView
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

# 状态色规范（§6: 统一Badge/状态颜色）
_COLOR_ONLINE = "#4CAF50"
_COLOR_DTC = "#F44336"
_COLOR_WARNING = "#FF9800"
_COLOR_OFFLINE = "#888888"


class VehicleOverviewView(QWidget):
    """车辆总览工作区"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ecu_defs = []           # EcuDefinition列表
        self._online_set = set()      # 最近扫描在线的ECU名
        self._dtc_map = {}            # ECU名 -> DTC数量（已知时）
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # ---- 标题 ----
        title = QLabel("车辆总览")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        # ---- 车辆信息行 ----
        info_row = QHBoxLayout()
        info_row.setSpacing(24)
        self._lbl_model = QLabel("车型: --")
        self._lbl_vin = QLabel("VIN: --")
        self._lbl_network = QLabel("网络: --")
        for lbl in (self._lbl_model, self._lbl_vin, self._lbl_network):
            lbl.setStyleSheet("color: #CDD6F4; font-size: 13px;")
            info_row.addWidget(lbl)
        info_row.addStretch(1)
        self._btn_rescan = QPushButton("全车扫描")
        self._btn_rescan.setObjectName("btn_primary")
        self._btn_rescan.setFixedWidth(120)
        info_row.addWidget(self._btn_rescan)
        layout.addLayout(info_row)

        # ---- 统计卡行 ----
        stat_row = QHBoxLayout()
        stat_row.setSpacing(10)
        self._st_total = self._stat_card("ECU总数", "--", "#CDD6F4")
        self._st_online = self._stat_card("Online", "--", _COLOR_ONLINE)
        self._st_offline = self._stat_card("Offline", "--", _COLOR_OFFLINE)
        self._st_dtc = self._stat_card("DTC数量", "--", _COLOR_DTC)
        self._st_comm = self._stat_card("通信异常", "--", _COLOR_WARNING)
        for card, _ in (self._st_total, self._st_online, self._st_offline,
                        self._st_dtc, self._st_comm):
            card.setMinimumWidth(140)
            stat_row.addWidget(card)
        stat_row.addStretch(1)
        layout.addLayout(stat_row)

        # ---- 中部: 拓扑 + 扫描结果表 ----
        mid_row = QHBoxLayout()
        mid_row.setSpacing(10)

        # ECU拓扑（配置式逻辑拓扑）
        topo_box = QVBoxLayout()
        topo_title = QLabel("ECU拓扑")
        topo_title.setStyleSheet("font-weight: bold;")
        topo_box.addWidget(topo_title)
        self._topo_tree = QTreeWidget()
        self._topo_tree.setHeaderHidden(True)
        self._topo_tree.setUniformRowHeights(True)
        self._topo_tree.setMinimumWidth(240)
        self._topo_tree.setMaximumWidth(320)
        self._topo_tree.itemDoubleClicked.connect(self._on_topo_double_clicked)
        topo_box.addWidget(self._topo_tree, 1)
        legend = QLabel(
            "● Online无DTC  ● 有DTC  ● 扫描异常  ● Offline（双击节点切换ECU）")
        legend.setStyleSheet("color: #888; font-size: 11px;")
        topo_box.addWidget(legend)
        mid_row.addLayout(topo_box, 2)

        # 最近扫描结果表
        scan_box = QVBoxLayout()
        scan_title = QLabel("最近一次全车扫描")
        scan_title.setStyleSheet("font-weight: bold;")
        scan_box.addWidget(scan_title)
        self._scan_table = QTableWidget()
        self._scan_table.setColumnCount(4)
        self._scan_table.setHorizontalHeaderLabels(
            ["ECU", "状态", "地址", "DTC"])
        header = self._scan_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._scan_table.verticalHeader().setVisible(False)
        self._scan_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._scan_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        scan_box.addWidget(self._scan_table, 1)
        self._lbl_scan_time = QLabel("尚未执行扫描")
        self._lbl_scan_time.setStyleSheet("color: #888; font-size: 11px;")
        scan_box.addWidget(self._lbl_scan_time)
        mid_row.addLayout(scan_box, 5)

        layout.addLayout(mid_row, 1)

    @staticmethod
    def _stat_card(title: str, value: str, color: str) -> tuple:
        """统计卡: (card, value_label)"""
        card = QFrame()
        card.setObjectName("status_card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("color: #888; font-size: 12px;")
        val = QLabel(value)
        val.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: bold;")
        v.addWidget(t)
        v.addWidget(val)
        return card, val

    # ---------------- 对外接口 ----------------

    def set_rescan_handler(self, handler):
        """主窗口注入全车扫描入口（工具栏同源）"""
        self._btn_rescan.clicked.connect(handler)

    def set_ecu_select_handler(self, handler):
        """主窗口注入ECU选择回调: handler(EcuDefinition)"""
        self._ecu_select_handler = handler

    def set_vehicle_info(self, model: str, vin: str, network: str):
        self._lbl_model.setText(f"车型: {model or '--'}")
        self._lbl_vin.setText(f"VIN: {vin or '--'}")
        self._lbl_network.setText(f"网络: {network or '--'}")

    def set_ecus(self, ecu_defs: list):
        """配置式逻辑拓扑: Gateway → 全部已定义ECU"""
        self._ecu_defs = list(ecu_defs)
        self._rebuild_topo()
        self._update_stats()

    def set_online_ecus(self, online_names):
        """扫描结果: 在线ECU名集合"""
        self._online_set = set(online_names)
        self._rebuild_topo()
        self._update_stats()

    def set_dtc_count(self, ecu_name: str, count: int):
        self._dtc_map[ecu_name] = count
        self._rebuild_topo()
        self._update_stats()

    def set_scan_results(self, results: list, ecu_defs: list, scan_time: str):
        """扫描结果表（§5 结果格式）: results为在线EcuDefinition列表"""
        found = {(e.tx_id, e.rx_id) for e in results}
        self._online_set = {d.name for d in ecu_defs
                            if (d.tx_id, d.rx_id) in found}
        self._scan_table.setRowCount(len(ecu_defs))
        for row, d in enumerate(ecu_defs):
            online = (d.tx_id, d.rx_id) in found
            self._scan_table.setItem(row, 0, QTableWidgetItem(d.name))
            st_item = QTableWidgetItem("Online" if online else "Offline")
            st_item.setForeground(QColor(
                _COLOR_ONLINE if online else _COLOR_OFFLINE))
            self._scan_table.setItem(row, 1, st_item)
            self._scan_table.setItem(
                row, 2, QTableWidgetItem(f"0x{d.tx_id:03X} / 0x{d.rx_id:03X}"))
            dtc = self._dtc_map.get(d.name)
            dtc_item = QTableWidgetItem("--" if dtc is None else str(dtc))
            if dtc:
                dtc_item.setForeground(QColor(_COLOR_DTC))
            self._scan_table.setItem(row, 3, dtc_item)
        self._lbl_scan_time.setText(
            f"扫描时间: {scan_time} | 在线 {len(results)}/{len(ecu_defs)}")
        self._rebuild_topo()
        self._update_stats()

    # ---------------- 内部 ----------------

    def _rebuild_topo(self):
        self._topo_tree.clear()
        gw = QTreeWidgetItem(self._topo_tree, ["🌐 Gateway"])
        gw.setExpanded(True)
        for d in self._ecu_defs:
            online = d.name in self._online_set
            dtc = self._dtc_map.get(d.name)
            if online and dtc:
                dot, color = "●", _COLOR_DTC
            elif online:
                dot, color = "●", _COLOR_ONLINE
            elif self._online_set:
                # 已扫描过但不在线 -> 灰色离线；扫描异常预留黄色
                dot, color = "●", _COLOR_OFFLINE
            else:
                dot, color = "○", _COLOR_OFFLINE
            item = QTreeWidgetItem(
                gw, [f"{dot} {d.name}  0x{d.tx_id:03X}/0x{d.rx_id:03X}"])
            item.setForeground(0, QColor(color))
            item.setData(0, Qt.ItemDataRole.UserRole, d)
        self._topo_tree.expandAll()

    def _update_stats(self):
        total = len(self._ecu_defs)
        online = len(self._online_set)
        dtc_total = sum(c for c in self._dtc_map.values() if c)
        _, v_total = self._st_total
        _, v_online = self._st_online
        _, v_offline = self._st_offline
        _, v_dtc = self._st_dtc
        _, v_comm = self._st_comm
        v_total.setText(str(total))
        v_online.setText(str(online) if self._online_set else "--")
        v_offline.setText(str(total - online) if self._online_set else "--")
        v_dtc.setText(str(dtc_total) if self._dtc_map else "--")
        v_comm.setText("0" if self._online_set else "--")

    def _on_topo_double_clicked(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is not None and hasattr(self, "_ecu_select_handler"):
            self._ecu_select_handler(data)
