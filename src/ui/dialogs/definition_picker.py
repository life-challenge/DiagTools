"""定义选择对话框（DID/DTC/例程编号选择的模态路径）

作为可编辑下拉框的兜底/增强入口: QDialog 为普通顶层窗口，
不依赖 QComboBox 的 Qt::Popup 弹窗机制——任何环境下可靠打开。
带搜索过滤与双击选择。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton)

_MONO = QFont("Consolas", 10)


class DefinitionPickerDialog(QDialog):
    """从已加载定义中选择编号的模态对话框

    items: [(value, label, tooltip)] —— value为回填输入框的编号文本
    （如 "5390"/"F190"/"F00913"），label为显示条目，tooltip为悬停详情
    """

    def __init__(self, title: str, items: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(440, 480)
        self._items = list(items)
        self._result_value = None

        lay = QVBoxLayout(self)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索编号或名称...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._refresh)
        lay.addWidget(self._search)

        self._list = QListWidget()
        self._list.setFont(_MONO)
        self._list.itemDoubleClicked.connect(self._accept_item)
        lay.addWidget(self._list, 1)

        hint = QLabel("双击条目或选中后点确定")
        hint.setStyleSheet("color: #888;")
        lay.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        self._refresh()

    def _refresh(self):
        kw = self._search.text().strip().lower()
        self._list.clear()
        for value, label, tooltip in self._items:
            if kw and kw not in f"{value} {label}".lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, value)
            if tooltip:
                item.setToolTip(tooltip)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _accept_item(self, item):
        self._result_value = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def _on_ok(self):
        item = self._list.currentItem()
        if item is not None:
            self._accept_item(item)

    @property
    def picked_value(self):
        """选中的编号文本；取消时为None"""
        return self._result_value

    @staticmethod
    def pick(title: str, items: list, parent=None):
        """打开选择对话框，返回选中的编号文本；取消返回None"""
        dlg = DefinitionPickerDialog(title, items, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.picked_value
        return None
