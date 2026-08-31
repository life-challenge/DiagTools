"""数据流面板（V2.1 Sprint 2）

功能:
  - 信号源管理: 手动添加DID / 从DID定义文件导入 / 移除
  - 周期采集: 后台线程轮询，可配置周期，启动/停止
  - 实时表格: DID/名称/当前值/原始数据/单位/更新时间
  - 趋势曲线: 选中DID的数值趋势（TrendChart）
  - 数据录制: 按周期写入 data_recordings/ 下的CSV文件
"""

import os
import time
import csv
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QSpinBox, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QSplitter, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal
from src.business.did_manager import DidManager, DidValue
from src.ui.widgets.trend_chart import TrendChart

# 数据录制输出目录: <项目根>/data_recordings
_RECORD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))), "data_recordings")


class DataStreamPanel(QWidget):
    """数据流面板"""

    # 后台线程读取完成 -> GUI线程刷新 (did_id, DidValue)
    value_updated = pyqtSignal(int, object)

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        self._did_manager = DidManager()
        self._stream_ids: list = []        # 采集中的DID列表（保持顺序）
        self._running = False
        self._recorder = None              # 录制CSV句柄
        self._record_path = ""
        self.value_updated.connect(self._on_value_updated)
        self._init_ui()

    def set_uds_client(self, client):
        if client is None and self._running:
            self._stop_stream()
        self._uds_client = client

    # ---------------- UI ----------------

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # ---- 控制栏 ----
        bar = QHBoxLayout()
        bar.addWidget(QLabel("DID:"))
        self._did_edit = QLineEdit()
        self._did_edit.setPlaceholderText("如 F190 或 22F190")
        self._did_edit.setFixedWidth(110)
        bar.addWidget(self._did_edit)

        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._add_did)
        bar.addWidget(add_btn)

        import_btn = QPushButton("从定义导入")
        import_btn.setToolTip("从DID定义JSON导入全部信号")
        import_btn.clicked.connect(self._import_definitions)
        bar.addWidget(import_btn)

        remove_btn = QPushButton("移除选中")
        remove_btn.clicked.connect(self._remove_selected)
        bar.addWidget(remove_btn)

        bar.addSpacing(12)
        bar.addWidget(QLabel("周期:"))
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(100, 60000)
        self._interval_spin.setValue(1000)
        self._interval_spin.setSuffix(" ms")
        bar.addWidget(self._interval_spin)

        self._start_btn = QPushButton("开始")
        self._start_btn.setStyleSheet(
            "QPushButton { background: #4CAF50; color: white; font-weight: bold; }")
        self._start_btn.clicked.connect(self._start_stream)
        bar.addWidget(self._start_btn)

        self._stop_btn = QPushButton("停止")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_stream)
        bar.addWidget(self._stop_btn)

        self._record_check = QCheckBox("录制CSV")
        bar.addWidget(self._record_check)

        bar.addStretch()
        self._status_label = QLabel("停止")
        self._status_label.setStyleSheet("color: #888;")
        bar.addWidget(self._status_label)
        layout.addLayout(bar)

        # ---- 中部: 表格 + 趋势曲线 ----
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["DID", "名称", "当前值", "原始数据", "单位", "更新时间"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self._table)

        self._chart = TrendChart(max_points=120)
        self._chart.set_title("趋势曲线")
        splitter.addWidget(self._chart)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

    # ---------------- 信号源管理 ----------------

    def _parse_did_text(self, text: str):
        """解析 'F190' / '0xF190' / '22 F1 90' 等输入"""
        text = text.strip().replace(" ", "").replace("0x", "").lower()
        if not text:
            return None
        try:
            # 支持带22前缀的完整请求形式
            if len(text) == 6 and text.startswith("22"):
                text = text[2:]
            return int(text, 16)
        except ValueError:
            return None

    def _add_did(self):
        did_id = self._parse_did_text(self._did_edit.text())
        if did_id is None:
            self._status_label.setText("DID格式错误")
            return
        self._add_source(did_id)
        self._did_edit.clear()

    def _import_definitions(self):
        """从DID定义JSON导入全部信号"""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        start_dir = os.path.join(project_root, "resources", "did_definitions")
        filepath, _ = QFileDialog.getOpenFileName(
            self, "加载DID定义", start_dir, "JSON Files (*.json)")
        if not filepath:
            return
        count = self._did_manager.load_definitions_from_json(filepath)
        if count <= 0:
            self._status_label.setText("导入失败: 未解析到有效DID定义")
            self._status_label.setStyleSheet("color: #F44336;")
            return
        for did_id in sorted(self._did_manager.definitions):
            self._add_source(did_id)
        self._status_label.setText(f"已导入 {count} 个定义")
        self._status_label.setStyleSheet("color: #4CAF50;")

    def _add_source(self, did_id: int):
        if did_id in self._stream_ids:
            return
        if self._running:
            # 运行中添加: 重启轮询以包含新DID
            self._did_manager.stop_polling()
        self._stream_ids.append(did_id)

        defn = self._did_manager.definitions.get(did_id)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(f"0x{did_id:04X}"))
        self._table.setItem(row, 1, QTableWidgetItem(defn.name if defn else "--"))
        self._table.setItem(row, 2, QTableWidgetItem("--"))
        self._table.setItem(row, 3, QTableWidgetItem("--"))
        self._table.setItem(row, 4, QTableWidgetItem(defn.unit if defn else ""))
        self._table.setItem(row, 5, QTableWidgetItem("--"))

        if self._running:
            self._start_polling()

    def _remove_selected(self):
        row = self._table.currentRow()
        if row < 0:
            return
        did_id = self._row_did(row)
        if self._running:
            self._did_manager.stop_polling()
        if did_id in self._stream_ids:
            self._stream_ids.remove(did_id)
        self._table.removeRow(row)
        if self._running and self._stream_ids:
            self._start_polling()

    def _row_did(self, row: int) -> int:
        item = self._table.item(row, 0)
        return int(item.text(), 16) if item else -1

    # ---------------- 采集控制 ----------------

    def _start_stream(self):
        if not self._stream_ids:
            self._status_label.setText("请先添加信号")
            return
        if not self._uds_client:
            self._status_label.setText("未连接")
            return
        self._running = True
        if self._record_check.isChecked():
            self._open_recorder()
        self._start_polling()
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._interval_spin.setEnabled(False)
        self._status_label.setText("采集中")
        self._status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")

    def _start_polling(self):
        interval = self._interval_spin.value() / 1000.0
        self._did_manager.start_polling(
            self._stream_ids, self._uds_client, interval, self._on_polled)

    def _stop_stream(self):
        self._running = False
        self._did_manager.stop_polling()
        self._close_recorder()
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._interval_spin.setEnabled(True)
        self._status_label.setText("停止")
        self._status_label.setStyleSheet("color: #888;")

    def _on_polled(self, did_id: int, val: DidValue):
        """后台轮询线程回调 -> 信号转GUI线程"""
        self.value_updated.emit(did_id, val)

    def _on_value_updated(self, did_id: int, val: DidValue):
        """GUI线程: 刷新表格/曲线/录制"""
        for row in range(self._table.rowCount()):
            if self._row_did(row) != did_id:
                continue
            self._table.item(row, 2).setText(val.display_value)
            self._table.item(row, 3).setText(val.raw_data.hex(" ").upper())
            self._table.item(row, 5).setText(time.strftime("%H:%M:%S"))

            # 趋势曲线: 仅选中DID
            sel = self._table.currentRow()
            if sel == row:
                self._chart.add_point(val.physical_value)

            # 录制
            if self._recorder:
                self._recorder.writerow([
                    time.strftime("%H:%M:%S"), f"0x{did_id:04X}",
                    val.display_value, val.raw_data.hex(" ").upper()])
                self._recorder_file.flush()
            break

    def _on_selection_changed(self):
        """切换选中DID时重置趋势曲线"""
        row = self._table.currentRow()
        if row < 0:
            return
        did_id = self._row_did(row)
        name_item = self._table.item(row, 1)
        unit_item = self._table.item(row, 4)
        self._chart.clear()
        self._chart.set_title(
            f"0x{did_id:04X} {name_item.text() if name_item else ''}",
            unit_item.text() if unit_item else "")

    # ---------------- CSV录制 ----------------

    def _open_recorder(self):
        try:
            os.makedirs(_RECORD_DIR, exist_ok=True)
            self._record_path = os.path.join(
                _RECORD_DIR,
                f"stream_{time.strftime('%Y%m%d_%H%M%S')}.csv")
            self._recorder_file = open(
                self._record_path, "w", newline="", encoding="utf-8-sig")
            self._recorder = csv.writer(self._recorder_file)
            self._recorder.writerow(["时间", "DID", "当前值", "原始数据"])
        except OSError:
            self._recorder = None

    def _close_recorder(self):
        if self._recorder:
            try:
                self._recorder_file.close()
            except Exception:
                pass
            self._recorder = None

    def closeEvent(self, event):
        self._stop_stream()
        super().closeEvent(event)
