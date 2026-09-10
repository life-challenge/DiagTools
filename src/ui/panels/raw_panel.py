"""原始报文发送面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QPushButton, QLineEdit, QTextEdit,
                              QCheckBox, QSpinBox)
from PyQt6.QtCore import QThread
from datetime import datetime
from src.ui.async_uds import UdsWorker


class RawPanel(QWidget):
    """原始报文发送面板"""

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        self._worker = None        # 保持worker引用，防止被GC回收导致信号丢失
        self._worker_thread = None
        self._init_ui()

    def set_uds_client(self, client):
        self._uds_client = client

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 发送区
        send_group = QGroupBox("发送原始UDS报文")
        send_layout = QVBoxLayout()

        hex_layout = QHBoxLayout()
        hex_layout.addWidget(QLabel("请求数据(HEX):"))
        self._hex_edit = QLineEdit()
        self._hex_edit.setPlaceholderText("如: 10 03 或 22 F1 90")
        self._hex_edit.setStyleSheet("font-family: 'Consolas', monospace;")
        self._hex_edit.returnPressed.connect(self._send_raw)
        hex_layout.addWidget(self._hex_edit)
        send_layout.addLayout(hex_layout)

        btn_layout = QHBoxLayout()
        self._send_btn = QPushButton("发送")
        self._send_btn.setStyleSheet("QPushButton { padding: 8px 20px; font-weight: bold; }")
        self._send_btn.clicked.connect(self._send_raw)
        btn_layout.addWidget(self._send_btn)

        self._send_repeat_check = QCheckBox("重复发送")
        btn_layout.addWidget(self._send_repeat_check)

        btn_layout.addWidget(QLabel("间隔(ms):"))
        self._repeat_interval = QSpinBox()
        self._repeat_interval.setRange(10, 60000)
        self._repeat_interval.setValue(100)
        btn_layout.addWidget(self._repeat_interval)

        btn_layout.addStretch()
        send_layout.addLayout(btn_layout)
        send_group.setLayout(send_layout)
        layout.addWidget(send_group)

        # 快捷发送
        quick_group = QGroupBox("快捷发送")
        quick_layout = QVBoxLayout()
        quick_cmds = [
            ("10 01", "默认会话"), ("10 02", "编程会话"), ("10 03", "扩展会话"),
            ("27 01", "请求种子L1"), ("27 03", "请求种子L3"),
            ("3E 00", "TesterPresent"), ("11 01", "ECU硬复位"),
            ("85 01", "DTC设置开"), ("85 02", "DTC设置关"),
        ]
        quick_btn_layout = QHBoxLayout()
        for hex_data, name in quick_cmds:
            btn = QPushButton(name)
            btn.clicked.connect(lambda checked, h=hex_data: self._quick_send(h))
            quick_btn_layout.addWidget(btn)
        quick_layout.addLayout(quick_btn_layout)
        quick_group.setLayout(quick_layout)
        layout.addWidget(quick_group)

        # 响应区
        resp_group = QGroupBox("响应")
        resp_layout = QVBoxLayout()
        self._response_text = QTextEdit()
        self._response_text.setReadOnly(True)
        self._response_text.setStyleSheet("font-family: 'Consolas', monospace;")
        resp_layout.addWidget(self._response_text)
        resp_group.setLayout(resp_layout)
        layout.addWidget(resp_group)

        # 历史
        self._history = []

    def _send_raw(self):
        if not self._uds_client:
            self._append_response("错误: 未连接UDS客户端", "#F44336")
            return

        if self._worker_thread is not None:
            self._append_response("上一次请求尚未完成，请稍后再试", "#FF9800")
            return

        hex_str = self._hex_edit.text().strip()
        if not hex_str:
            return

        try:
            data = bytes.fromhex(hex_str.replace(" ", ""))
        except ValueError:
            self._append_response("错误: HEX格式无效", "#F44336")
            return

        self._history.append(hex_str)
        self._append_response(f"TX >> {data.hex(' ').upper()}", "#2196F3")
        self._send_btn.setEnabled(False)

        client = self._uds_client

        def _handle_response(resp):
            self._send_btn.setEnabled(True)
            if resp:
                hex_resp = resp.hex(" ").upper()
                if resp[0] == 0x7F:
                    self._append_response(f"RX << {hex_resp}  [负响应]", "#F44336")
                else:
                    self._append_response(f"RX << {hex_resp}  [正响应]", "#4CAF50")
            else:
                self._append_response("RX << 超时/无响应", "#FF9800")

        def _handle_error(msg):
            self._send_btn.setEnabled(True)
            self._append_response(f"发送异常: {msg}", "#F44336")

        # 后台线程执行阻塞式UDS调用，避免阻塞GUI
        worker = UdsWorker(client.send_raw, data)
        thread = QThread(self)
        worker.moveToThread(thread)

        def _cleanup():
            thread.quit()
            thread.wait()
            self._worker_thread = None
            self._worker = None

        def _on_finished(result):
            try:
                _handle_response(result)
            except Exception as e:
                self._append_response(f"响应处理异常: {e}", "#F44336")
            finally:
                _cleanup()

        def _on_error(msg):
            try:
                _handle_error(msg)
            finally:
                _cleanup()

        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        thread.started.connect(worker.run)
        self._worker = worker       # 关键: 保持引用防止GC回收
        self._worker_thread = thread
        thread.start()

    def _quick_send(self, hex_str: str):
        self._hex_edit.setText(hex_str)
        self._send_raw()

    def closeEvent(self, event):
        """窗口关闭时确保工作线程安全退出"""
        if self._worker_thread and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        super().closeEvent(event)

    def _append_response(self, text: str, color: str = ""):
        import html
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if color:
            # HTML转义: 防止 "<<"/">>" 等被当作标签解析导致内容丢失
            self._response_text.append(
                f'<span style="color:{color}">[{ts}] {html.escape(text)}</span>')
        else:
            self._response_text.append(f"[{ts}] {text}")
