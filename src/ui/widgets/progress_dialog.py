"""刷写进度对话框"""

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QProgressBar, QPushButton, QTextEdit)


class ProgressDialog(QDialog):
    """刷写进度对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("刷写进度")
        self.setMinimumWidth(400)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        self._status_label = QLabel("准备中...")
        layout.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        layout.addWidget(self._progress_bar)

        info_layout = QHBoxLayout()
        self._rate_label = QLabel("速率: --")
        self._time_label = QLabel("剩余: --")
        self._block_label = QLabel("块: --")
        info_layout.addWidget(self._rate_label)
        info_layout.addWidget(self._time_label)
        info_layout.addWidget(self._block_label)
        info_layout.addStretch()
        layout.addLayout(info_layout)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(100)
        layout.addWidget(self._log_text)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._cancel_btn)
        layout.addLayout(btn_layout)

    def update_progress(self, percentage: float, rate: float = 0,
                        remaining: float = 0, block_info: str = ""):
        self._progress_bar.setValue(int(percentage))
        if rate > 0:
            self._rate_label.setText(f"速率: {rate:.2f} KB/s")
        if remaining > 0:
            self._time_label.setText(f"剩余: {remaining:.0f}s")
        if block_info:
            self._block_label.setText(f"块: {block_info}")

    def set_status(self, text: str):
        self._status_label.setText(text)

    def add_log(self, text: str):
        self._log_text.append(text)
