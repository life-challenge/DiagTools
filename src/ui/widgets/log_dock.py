"""可折叠底部日志面板（V2.1 §7）

三层日志:
  业务日志  - 应用级业务事件（连接/会话/安全/扫描等）
  UDS Trace - 仅记录可解析为UDS服务的报文
  CAN Trace - 全部CAN报文

支持: 折叠/展开、清空、导出、打开日志文件夹、拖拽调整高度
（由主窗口splitter提供）。日志落盘路径见头部路径标签，单击复制、双击打开。
"""

import os
import sys
import time
import subprocess
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTabWidget, QPlainTextEdit, QFileDialog, QSplitter
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from src.ui.widgets.log_widget import LogWidget
from src.log.log_manager import get_log_manager


# 页签 -> 日志子目录映射（0业务/1 UDS/2 CAN）
_TAB_SUBDIRS = {0: "app", 1: "diag", 2: "comm"}


class LogDock(QWidget):
    """底部可折叠日志面板"""

    TAB_BUSINESS, TAB_UDS, TAB_CAN = range(3)

    # 展开态最小高度（防挤压到无法阅读）；折叠态收缩到头部一行
    _MIN_HEIGHT = 170

    # 日志等级颜色（§7: INFO/SUCCESS/WARNING/ERROR/DEBUG）
    _LEVEL_COLORS = {
        "INFO": "#CDD6F4", "SUCCESS": "#4CAF50", "WARNING": "#FF9800",
        "ERROR": "#F44336", "DEBUG": "#888888",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self._expanded_height = 0  # 折叠前的高度，展开时恢复
        self.setMinimumHeight(self._MIN_HEIGHT)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- 头部: 折叠开关 + 计数 + 操作（带底边框，与下方页签分隔） ----
        self._header_widget = QWidget()
        self._header_widget.setObjectName("dock_header")
        header = QHBoxLayout(self._header_widget)
        header.setContentsMargins(6, 3, 6, 3)
        header.setSpacing(6)

        self._toggle_btn = QPushButton("▼")
        self._toggle_btn.setFixedWidth(26)
        self._toggle_btn.setToolTip("折叠/展开日志面板")
        self._toggle_btn.clicked.connect(self.toggle_collapse)
        header.addWidget(self._toggle_btn)

        self._title_label = QLabel("通信日志")
        self._title_label.setStyleSheet("font-weight: bold;")
        header.addWidget(self._title_label)

        self._count_label = QLabel("0 Events")
        self._count_label.setStyleSheet("color: #888;")
        header.addWidget(self._count_label)

        # ---- 日志落盘路径标签（显眼提示，双击打开文件夹） ----
        self._path_label = QLabel()
        self._path_label.setStyleSheet(
            "color: #4FC3F7; text-decoration: underline;")
        self._path_label.setToolTip(
            "点击打开日志文件夹（路径已展示，可手动复制）\n"
            "日志按模块分类: app/diag/comm/flash/sequence")
        self._path_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._path_label.mousePressEvent = lambda _e: self._open_log_folder()
        header.addWidget(self._path_label, 1)

        header.addStretch()

        clear_btn = QPushButton("清空")
        clear_btn.setFixedWidth(50)
        clear_btn.clicked.connect(self._clear_active)
        header.addWidget(clear_btn)
        self._clear_btn = clear_btn

        export_btn = QPushButton("导出")
        export_btn.setFixedWidth(50)
        export_btn.clicked.connect(self._export_active)
        header.addWidget(export_btn)
        self._export_btn = export_btn

        folder_btn = QPushButton("📁 打开日志文件夹")
        folder_btn.setToolTip("在资源管理器中打开当前页签对应的日志目录")
        folder_btn.clicked.connect(self._open_log_folder)
        header.addWidget(folder_btn)
        self._folder_btn = folder_btn

        layout.addWidget(self._header_widget)

        # ---- 三层日志 ----
        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.South)

        self._business_log = QPlainTextEdit()
        self._business_log.setReadOnly(True)
        self._business_log.setMaximumBlockCount(5000)

        self._uds_trace = LogWidget()
        self._can_trace = LogWidget()

        self._tabs.addTab(self._business_log, "业务日志")
        self._tabs.addTab(self._uds_trace, "UDS Trace")
        self._tabs.addTab(self._can_trace, "CAN Trace")
        layout.addWidget(self._tabs, 1)

        # CAN Trace批量渲染完成 -> 更新事件计数（每批一次，避免洪泛时逐帧刷新QLabel）
        self._can_trace.flushed.connect(self._update_count)

        # 页签切换 -> 路径标签跟随（初始也刷新一次）
        self._tabs.currentChanged.connect(lambda _i: self._refresh_log_path())
        self._refresh_log_path()

    # ---------------- 对外接口 ----------------

    @property
    def can_trace(self) -> LogWidget:
        """全部报文日志（状态栏计数数据源）"""
        return self._can_trace

    def add_frame(self, direction: str, can_id: int, data: bytes, desc: str,
                  uds_msg: bytes = None, uds_desc: str = ""):
        """CAN报文入口: CAN Trace记录全部原始帧（含TP分段帧/FC）；
        uds_msg非None时UDS Trace记录重组后的完整UDS报文（14229会话层）"""
        self._can_trace.add_message(direction, can_id, data, desc)
        if uds_msg is not None:
            self._uds_trace.add_message(direction, can_id, uds_msg, uds_desc)

    def log_business(self, msg: str, level: str = "INFO"):
        """追加业务日志条目（§7: 时间 + 等级 + 内容，按等级着色）"""
        ts = time.strftime("%H:%M:%S")
        line = f"{ts}  {level:<8}{msg}\n"
        cursor = self._business_log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._LEVEL_COLORS.get(level, "#CDD6F4")))
        cursor.insertText(line, fmt)
        # 超出上限时删除最早的行（insertText不受maximumBlockCount约束）
        if self._business_log.blockCount() > 5500:
            self._trim_business_log(1000)

    def _trim_business_log(self, n: int):
        cursor = self._business_log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.Down,
                            QTextCursor.MoveMode.KeepAnchor, n)
        cursor.removeSelectedText()
        cursor.deleteChar()

    def show_tab(self, idx: int):
        """展开并切换到指定日志页签（工具-报文工具入口）"""
        self.set_collapsed(False)
        self._tabs.setCurrentIndex(idx)

    def clear_all(self):
        self._business_log.clear()
        self._uds_trace.clear()
        self._can_trace.clear()
        self._update_count(None)

    def toggle_collapse(self):
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool):
        """折叠/展开

        折叠态: 收缩为一条紧凑头部细条——路径/操作按钮隐藏
        （对折叠面板无意义），高度收到头部一行，splitter 空间让给主区。
        展开态: 恢复完整头部与最小阅读高度，并还原折叠前的高度。
        """
        if collapsed == self._collapsed:
            self._toggle_btn.setText("▲" if collapsed else "▼")
            return
        self._collapsed = collapsed
        self._toggle_btn.setText("▲" if collapsed else "▼")
        self._tabs.setVisible(not collapsed)
        if collapsed:
            # 记录当前高度供展开时还原
            self._expanded_height = max(self.height(), self._expanded_height)
            for w in (self._path_label, self._clear_btn,
                      self._export_btn, self._folder_btn):
                w.hide()
            self.setMinimumHeight(0)
            self.setMaximumHeight(self._header_widget.sizeHint().height())
        else:
            for w in (self._path_label, self._clear_btn,
                      self._export_btn, self._folder_btn):
                w.show()
            self.setMinimumHeight(self._MIN_HEIGHT)
            self.setMaximumHeight(16777215)  # 恢复默认无上限
            # 还原折叠前的高度（splitter 两侧重新分配）
            sp = self.parent()
            if isinstance(sp, QSplitter) and self._expanded_height > 0:
                total = sum(sp.sizes())
                if total > 0:
                    sp.setSizes([max(total - self._expanded_height, 0),
                                 self._expanded_height])

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    # ---------------- 内部 ----------------

    def _current_log_dir(self) -> str:
        """当前页签对应的日志子目录路径"""
        subdir = _TAB_SUBDIRS.get(self._tabs.currentIndex(), "app")
        return os.path.join(get_log_manager().log_base_dir, subdir)

    def _refresh_log_path(self):
        """刷新头部路径标签（跟随当前页签）"""
        self._path_label.setText(
            "📄 " + self._current_log_dir().replace("/", "\\"))

    def _open_log_folder(self):
        """在资源管理器中打开当前页签对应的日志目录"""
        path = self._current_log_dir()
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
        if os.name == "nt":
            subprocess.Popen(f'explorer "{path}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _update_count(self, _entry=None):
        total = self._can_trace.tx_count + self._can_trace.rx_count
        self._count_label.setText(f"{total} Events")

    def _clear_active(self):
        idx = self._tabs.currentIndex()
        if idx == self.TAB_BUSINESS:
            self._business_log.clear()
        elif idx == self.TAB_UDS:
            self._uds_trace.clear()
        else:
            self._can_trace.clear()

    def _export_active(self):
        idx = self._tabs.currentIndex()
        if idx == self.TAB_BUSINESS:
            filepath, _ = QFileDialog.getSaveFileName(
                self, "导出业务日志", "business_log.txt", "文本文件 (*.txt)")
            if filepath:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(self._business_log.toPlainText())
        elif idx == self.TAB_UDS:
            self._uds_trace._export_log()
        else:
            self._can_trace._export_log()
