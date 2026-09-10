"""ECU设备树控件"""

from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem, QMenu
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QColor, QBrush


class EcuTreeWidget(QTreeWidget):
    """ECU设备树控件
    
    树形结构展示所有已配置ECU: ECU名称 -> 连接信息/当前会话/安全状态
    """

    ecu_selected = pyqtSignal(int, int)  # tx_id, rx_id
    ecu_connect_requested = pyqtSignal(int, int)
    ecu_disconnect_requested = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["ECU设备", "地址", "状态"])
        self.setColumnCount(3)
        self._ecu_items = {}

    def add_ecu(self, name: str, tx_id: int, rx_id: int, connected: bool = False):
        """添加ECU节点"""
        item = QTreeWidgetItem()
        item.setText(0, name)
        item.setText(1, f"TX:0x{tx_id:03X} RX:0x{rx_id:03X}")
        item.setText(2, "已连接" if connected else "未连接")

        color = QColor("#4CAF50") if connected else QColor("#999")
        item.setForeground(2, QBrush(color))

        item.setData(0, Qt.ItemDataRole.UserRole, (tx_id, rx_id))
        self.addTopLevelItem(item)
        self._ecu_items[(tx_id, rx_id)] = item

        # 子节点
        session_item = QTreeWidgetItem(item)
        session_item.setText(0, "当前会话")
        session_item.setText(1, "默认 (0x01)")

        security_item = QTreeWidgetItem(item)
        security_item.setText(0, "安全状态")
        security_item.setText(1, "未解锁")

        item.setExpanded(True)

    def remove_ecu(self, tx_id: int, rx_id: int):
        """移除ECU节点"""
        key = (tx_id, rx_id)
        if key in self._ecu_items:
            item = self._ecu_items.pop(key)
            index = self.indexOfTopLevelItem(item)
            if index >= 0:
                self.takeTopLevelItem(index)

    def update_ecu_status(self, tx_id: int, rx_id: int, connected: bool = False,
                          session: str = "", security: str = ""):
        """更新ECU状态"""
        key = (tx_id, rx_id)
        if key not in self._ecu_items:
            return
        item = self._ecu_items[key]
        item.setText(2, "已连接" if connected else "未连接")
        color = QColor("#4CAF50") if connected else QColor("#999")
        item.setForeground(2, QBrush(color))

        if session and item.childCount() > 0:
            item.child(0).setText(1, session)
        if security and item.childCount() > 1:
            item.child(1).setText(1, security)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if not item or item.parent():
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        tx_id, rx_id = data
        menu = QMenu(self)
        connect_action = menu.addAction("连接")
        disconnect_action = menu.addAction("断开")
        menu.addSeparator()
        menu.addAction("扫描DTC")
        menu.addAction("快速读取DID")

        action = menu.exec(event.globalPos())
        if action == connect_action:
            self.ecu_connect_requested.emit(tx_id, rx_id)
        elif action == disconnect_action:
            self.ecu_disconnect_requested.emit(tx_id, rx_id)
