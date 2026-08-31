"""报文重放面板（V2.2 Phase 13 重放项）

从导出的CAN Trace CSV（时间,方向,CAN ID,数据,描述）或纯HEX行文件加载帧序列，
经当前CAN接口按固定间隔/原始时序重放。后台线程执行，不阻塞UI。
"""

import csv
import time
import threading
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QSpinBox, QCheckBox, QFileDialog, QProgressBar
)
from PyQt6.QtCore import pyqtSignal

from src.models.can_message import CanMessage


class ReplayPanel(QWidget):
    """报文重放"""

    # 后台线程 -> GUI线程
    progress = pyqtSignal(int, int)          # 当前, 总数
    finished = pyqtSignal(str)               # 完成/取消信息

    def __init__(self, can_interface=None, parent=None):
        super().__init__(parent)
        self._can_interface = can_interface
        self._frames: list = []              # [(can_id, data, ts_offset)]
        self._source_file = ""
        self._running = False
        self._cancel = threading.Event()
        self._thread = None
        self.progress.connect(self._on_progress)
        self.finished.connect(self._on_finished)
        self._init_ui()

    def set_can_interface(self, iface):
        self._can_interface = iface

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # ---- 文件行 ----
        file_row = QHBoxLayout()
        self._file_edit = QLineEdit()
        self._file_edit.setReadOnly(True)
        self._file_edit.setPlaceholderText("选择CAN Trace导出的CSV或HEX文本...")
        file_row.addWidget(self._file_edit, 1)
        btn_load = QPushButton("加载文件")
        btn_load.clicked.connect(self._load_file)
        file_row.addWidget(btn_load)
        layout.addLayout(file_row)

        # ---- 参数行 ----
        param_row = QHBoxLayout()
        param_row.addWidget(QLabel("帧间隔:"))
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(1, 60000)
        self._interval_spin.setValue(50)
        self._interval_spin.setSuffix(" ms")
        param_row.addWidget(self._interval_spin)
        self._chk_timing = QCheckBox("按原始时序")
        self._chk_timing.toggled.connect(
            lambda on: self._interval_spin.setEnabled(not on))
        param_row.addWidget(self._chk_timing)
        param_row.addSpacing(12)
        param_row.addWidget(QLabel("循环:"))
        self._loop_spin = QSpinBox()
        self._loop_spin.setRange(1, 999)
        self._loop_spin.setValue(1)
        self._loop_spin.setSuffix(" 次")
        param_row.addWidget(self._loop_spin)
        param_row.addStretch(1)
        self._lbl_count = QLabel("0 帧")
        self._lbl_count.setStyleSheet("color: #888;")
        param_row.addWidget(self._lbl_count)
        layout.addLayout(param_row)

        # ---- 进度 + 控制 ----
        ctrl_row = QHBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(0)
        ctrl_row.addWidget(self._progress_bar, 1)
        self._btn_start = QPushButton("开始重放")
        self._btn_start.setObjectName("btn_primary")
        self._btn_start.clicked.connect(self._start_replay)
        ctrl_row.addWidget(self._btn_start)
        self._btn_stop = QPushButton("停止")
        self._btn_stop.setObjectName("btn_danger")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_replay)
        ctrl_row.addWidget(self._btn_stop)
        layout.addLayout(ctrl_row)

        self._lbl_status = QLabel("未加载帧序列")
        self._lbl_status.setStyleSheet("color: #888;")
        layout.addWidget(self._lbl_status)
        layout.addStretch(1)

    # ---------------- 文件加载 ----------------

    def _load_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择重放文件", "",
            "CAN Trace (*.csv);;文本文件 (*.txt *.log);;所有文件 (*)")
        if not filepath:
            return
        frames = []
        try:
            if filepath.lower().endswith(".csv"):
                frames = self._parse_csv(filepath)
            else:
                frames = self._parse_hex_lines(filepath)
        except Exception as e:
            self._lbl_status.setText(f"解析失败: {e}")
            self._lbl_status.setStyleSheet("color: #F44336;")
            return
        if not frames:
            self._lbl_status.setText("未解析到有效帧")
            self._lbl_status.setStyleSheet("color: #F44336;")
            return
        self._frames = frames
        self._source_file = filepath
        self._file_edit.setText(filepath)
        self._lbl_count.setText(f"{len(frames)} 帧")
        self._lbl_status.setText("帧序列已加载，可开始重放")
        self._lbl_status.setStyleSheet("color: #4CAF50;")
        self._progress_bar.setRange(0, len(frames))
        self._progress_bar.setValue(0)

    @staticmethod
    def _parse_csv(filepath: str) -> list:
        """解析LogWidget导出格式: 时间,方向,CAN ID,数据,描述"""
        frames = []
        t0 = None
        with open(filepath, "r", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) < 4:
                    continue
                if row[0].startswith("时间"):
                    continue
                try:
                    can_id = int(row[2].replace("0x", ""), 16)
                    data = bytes(int(b, 16) for b in row[3].split())
                except ValueError:
                    continue
                ts = ReplayPanel._parse_time(row[0])
                if t0 is None and ts is not None:
                    t0 = ts
                offset = (ts - t0).total_seconds() if (ts is not None and t0 is not None) else 0.0
                frames.append((can_id, data, offset))
        return frames

    @staticmethod
    def _parse_hex_lines(filepath: str) -> list:
        """解析纯HEX行: 每行 '714 02 10 03' 或 '0x714: 02 10 03'"""
        frames = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                toks = line.replace(":", " ").split()
                if len(toks) < 2:
                    continue
                try:
                    can_id = int(toks[0].replace("0x", ""), 16)
                    data = bytes(int(t, 16) for t in toks[1:])
                except ValueError:
                    continue
                frames.append((can_id, data, 0.0))
        return frames

    @staticmethod
    def _parse_time(text: str):
        """尽力解析 HH:MM:SS.ff 时间戳"""
        from datetime import datetime
        for fmt in ("%H:%M:%S.%f", "%H:%M:%S"):
            try:
                return datetime.strptime(text.strip(), fmt)
            except ValueError:
                continue
        return None

    # ---------------- 重放控制 ----------------

    def _start_replay(self):
        if not self._frames:
            self._lbl_status.setText("请先加载帧序列")
            return
        if self._can_interface is None:
            self._lbl_status.setText("未连接CAN，请先在工具中心连接")
            self._lbl_status.setStyleSheet("color: #F44336;")
            return
        self._running = True
        self._cancel.clear()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._lbl_status.setText("重放中...")
        self._lbl_status.setStyleSheet("color: #4CAF50;")
        self._thread = threading.Thread(target=self._replay_loop, daemon=True)
        self._thread.start()

    def _stop_replay(self):
        self._cancel.set()

    def _replay_loop(self):
        use_timing = self._chk_timing.isChecked()
        interval = self._interval_spin.value() / 1000.0
        loops = self._loop_spin.value()
        total = len(self._frames)
        sent = 0
        try:
            for _ in range(loops):
                prev_offset = 0.0
                for idx, (can_id, data, offset) in enumerate(self._frames):
                    if self._cancel.is_set():
                        self.finished.emit(f"已停止: 发送 {sent} 帧")
                        return
                    if use_timing and idx > 0:
                        delay = max(0.0, offset - prev_offset)
                        if delay > 0:
                            time.sleep(min(delay, 5.0))
                    elif interval > 0:
                        time.sleep(interval)
                    prev_offset = offset
                    msg = CanMessage(can_id=can_id, data=data)
                    self._can_interface.send(msg)
                    sent += 1
                    self.progress.emit(sent % total or total, total)
        except Exception as e:
            self.finished.emit(f"重放异常: {e}")
            return
        self.finished.emit(f"重放完成: 共发送 {sent} 帧")

    # ---------------- GUI回调 ----------------

    def _on_progress(self, cur: int, total: int):
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(cur)

    def _on_finished(self, info: str):
        self._running = False
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._lbl_status.setText(info)
        self._lbl_status.setStyleSheet(
            "color: #4CAF50;" if "完成" in info else "color: #FF9800;")
