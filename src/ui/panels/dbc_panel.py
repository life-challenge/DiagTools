"""诊断数据库面板（DBC）

报文分析 → 数据库 页签:
  加载 .dbc 文件 → 左侧报文列表 → 右侧信号表 + 数据解码预览。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QTreeWidget, QTreeWidgetItem, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt

from src.business.dbc_parser import DbcParser


class DbcPanel(QWidget):
    """DBC数据库浏览与信号解码"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parser = DbcParser()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ---- 工具行 ----
        bar = QHBoxLayout()
        btn_load = QPushButton("加载DBC")
        btn_load.clicked.connect(self._load_dbc)
        bar.addWidget(btn_load)
        self._lbl_info = QLabel("未加载数据库")
        self._lbl_info.setStyleSheet("color: #888;")
        bar.addWidget(self._lbl_info, 1)
        layout.addLayout(bar)

        # ---- 报文列表 / 信号表 ----
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._msg_tree = QTreeWidget()
        self._msg_tree.setHeaderLabels(["ID", "报文", "DLC", "发送节点"])
        self._msg_tree.setColumnWidth(0, 70)
        self._msg_tree.itemSelectionChanged.connect(self._on_msg_selected)
        splitter.addWidget(self._msg_tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._sig_table = QTableWidget()
        self._sig_table.setColumnCount(6)
        self._sig_table.setHorizontalHeaderLabels(
            ["信号", "起始位", "长度", "字节序", "因子/偏移", "单位"])
        self._sig_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._sig_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sig_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        right_layout.addWidget(self._sig_table, 1)

        self._lbl_decode = QLabel("选择报文查看信号定义")
        self._lbl_decode.setWordWrap(True)
        self._lbl_decode.setStyleSheet("color: #888;")
        right_layout.addWidget(self._lbl_decode)
        splitter.addWidget(right)
        splitter.setSizes([360, 480])
        layout.addWidget(splitter, 1)

    # ---------------- 加载 ----------------

    @property
    def parser(self) -> DbcParser:
        return self._parser

    def _load_dbc(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择DBC数据库", "", "DBC文件 (*.dbc);;所有文件 (*)")
        if not filepath:
            return
        self.load_file(filepath)

    def load_file(self, filepath: str) -> bool:
        """加载并刷新界面; 成功返回True"""
        if not self._parser.load(filepath):
            self._lbl_info.setText("DBC加载失败")
            self._lbl_info.setStyleSheet("color: #F44336;")
            return False
        self._refresh_tree()
        n_msg = len(self._parser.messages)
        n_sig = self._parser.signal_count
        self._lbl_info.setText(
            f"{self._parser.name}: {n_msg} 条报文 / {n_sig} 个信号")
        self._lbl_info.setStyleSheet("color: #4CAF50;")
        return True

    def _refresh_tree(self):
        self._msg_tree.clear()
        for can_id in sorted(self._parser.messages):
            msg = self._parser.messages[can_id]
            item = QTreeWidgetItem([
                f"0x{can_id:03X}", msg.name, str(msg.dlc), msg.sender])
            item.setData(0, Qt.ItemDataRole.UserRole, can_id)
            self._msg_tree.addTopLevelItem(item)

    # ---------------- 信号/解码 ----------------

    def _on_msg_selected(self):
        items = self._msg_tree.selectedItems()
        if not items:
            return
        msg = self._parser.get_message(
            items[0].data(0, Qt.ItemDataRole.UserRole))
        if msg is None:
            return
        self._sig_table.setRowCount(len(msg.signals))
        for row, sig in enumerate(msg.signals):
            self._sig_table.setItem(row, 0, QTableWidgetItem(sig.name))
            self._sig_table.setItem(
                row, 1, QTableWidgetItem(str(sig.start_bit)))
            self._sig_table.setItem(
                row, 2, QTableWidgetItem(str(sig.bit_length)))
            self._sig_table.setItem(
                row, 3, QTableWidgetItem(
                    "Intel(小端)" if sig.little_endian else "Motorola(大端)"))
            self._sig_table.setItem(
                row, 4, QTableWidgetItem(f"{sig.factor:g} / {sig.offset:g}"))
            self._sig_table.setItem(row, 5, QTableWidgetItem(sig.unit))
        # 全0数据解码预览
        preview = msg.decode(bytes(msg.dlc))
        if preview:
            text = "  |  ".join(
                f"{name}={val:g}{(' ' + unit) if unit else ''}"
                for name, (val, unit) in preview.items())
            self._lbl_decode.setText(f"全0帧解码: {text}")
        else:
            self._lbl_decode.setText("该报文无可解码信号")

    def decode_frame(self, can_id: int, data: bytes) -> str:
        """供外部调用: 解码一帧数据为文本描述; 无匹配返回空串"""
        result = self._parser.decode_frame(can_id, data)
        return " ".join(
            f"{name}={val:g} {unit}".strip()
            for name, (val, unit) in result.items())
