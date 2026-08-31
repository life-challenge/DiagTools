"""诊断会话控制面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QComboBox, QPushButton, QFormLayout,
                              QTextEdit)
from PyQt6.QtCore import pyqtSignal, QThread
from src.ui.async_uds import UdsWorker as _UdsWorker


class SessionPanel(QWidget):
    """诊断会话控制面板"""

    session_changed = pyqtSignal(int)  # 会话类型变化

    SESSION_TYPES = {
        "默认会话 (0x01)": 0x01,
        "编程会话 (0x02)": 0x02,
        "扩展会话 (0x03)": 0x03,
        "安全系统会话 (0x04)": 0x04,
    }

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        self._current_session = 0x01
        self._worker_thread = None
        self._worker = None  # 保持worker引用，防止被GC回收导致信号丢失
        self._init_ui()

    def set_uds_client(self, client):
        self._uds_client = client

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 快捷会话按钮组
        quick_group = QGroupBox("快捷操作")
        quick_layout = QHBoxLayout()
        self._quick_btns = {}
        for name, stype in self.SESSION_TYPES.items():
            short = name.split(" ")[0]  # "默认会话"
            btn = QPushButton(short)
            btn.setToolTip(f"切换到 {name}")
            btn.setFixedHeight(32)
            btn.clicked.connect(lambda checked, t=stype, n=name: self._quick_switch(t, n))
            quick_layout.addWidget(btn)
            self._quick_btns[stype] = btn
        quick_group.setLayout(quick_layout)
        layout.addWidget(quick_group)

        # 会话控制组
        session_group = QGroupBox("诊断会话控制")
        form = QFormLayout()

        self._session_combo = QComboBox()
        for name in self.SESSION_TYPES:
            self._session_combo.addItem(name)
        form.addRow("会话类型:", self._session_combo)

        session_group.setLayout(form)
        layout.addWidget(session_group)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self._switch_btn = QPushButton("切换会话")
        self._switch_btn.clicked.connect(self._on_switch_session)
        btn_layout.addWidget(self._switch_btn)

        self._tp_btn = QPushButton("TesterPresent")
        self._tp_btn.clicked.connect(self._on_tester_present)
        btn_layout.addWidget(self._tp_btn)
        layout.addLayout(btn_layout)

        # 当前状态
        status_group = QGroupBox("当前状态")
        status_layout = QFormLayout()
        self._session_label = QLabel("默认会话 (0x01)")
        status_layout.addRow("当前会话:", self._session_label)
        self._p2_label = QLabel("--")
        status_layout.addRow("P2超时:", self._p2_label)
        self._p2_star_label = QLabel("--")
        status_layout.addRow("P2*超时:", self._p2_star_label)
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        # 日志
        log_group = QGroupBox("操作日志")
        log_layout = QVBoxLayout()
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(200)
        log_layout.addWidget(self._log_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        layout.addStretch()

    def _run_uds_async(self, func, *args, on_success=None, on_error=None):
        """在后台线程执行UDS调用，完成后回到GUI线程回调"""
        if self._worker_thread is not None:
            self._log("上一次操作尚未完成，请稍后再试")
            return
        worker = _UdsWorker(func, *args)
        thread = QThread(self)
        worker.moveToThread(thread)

        def _cleanup():
            thread.quit()
            thread.wait()
            self._worker_thread = None
            self._worker = None  # 释放worker引用

        def _on_finished(result):
            try:
                if on_success:
                    on_success(result)
            except Exception as e:
                self._log(f"响应处理异常: {e}")
            finally:
                _cleanup()

        def _on_error(msg):
            try:
                if on_error:
                    on_error(msg)
                else:
                    self._log(f"操作异常: {msg}")
            except Exception:
                pass
            finally:
                _cleanup()

        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        thread.started.connect(worker.run)
        self._worker = worker       # 关键: 保持引用防止GC回收
        self._worker_thread = thread
        thread.start()

    def _quick_switch(self, session_type: int, session_name: str):
        """快捷按钮切换会话"""
        if not self._uds_client:
            self._log("未连接UDS客户端")
            return

        client = self._uds_client
        self._set_buttons_enabled(False)
        self._log(f"正在切换 {session_name}...")

        def _handle_response(resp):
            self._set_buttons_enabled(True)
            try:
                if resp and len(resp) >= 2 and resp[0] == 0x50:
                    self._current_session = session_type
                    self._session_label.setText(session_name)
                    # 同步下拉框
                    for i in range(self._session_combo.count()):
                        if self.SESSION_TYPES.get(self._session_combo.itemText(i)) == session_type:
                            self._session_combo.setCurrentIndex(i)
                            break
                    if len(resp) >= 6:
                        p2 = (resp[2] << 8 | resp[3]) * 0.001
                        p2_star = (resp[4] << 8 | resp[5]) * 0.01
                        self._p2_label.setText(f"{p2:.3f}s")
                        self._p2_star_label.setText(f"{p2_star:.3f}s")
                    self._log(f"切换到 {session_name} 成功")
                    self.session_changed.emit(session_type)
                else:
                    self._log(f"切换失败: 负响应或无响应")
            except Exception as e:
                self._log(f"响应解析异常: {e}")

        def _handle_error(msg):
            self._set_buttons_enabled(True)
            self._log(f"切换异常: {msg}")

        self._run_uds_async(
            client.diagnostic_session_control, session_type,
            on_success=_handle_response, on_error=_handle_error
        )

    def _set_buttons_enabled(self, enabled: bool):
        """统一启用/禁用所有操作按钮"""
        self._switch_btn.setEnabled(enabled)
        self._tp_btn.setEnabled(enabled)
        for btn in self._quick_btns.values():
            btn.setEnabled(enabled)

    def _on_switch_session(self):
        session_name = self._session_combo.currentText()
        session_type = self.SESSION_TYPES.get(session_name, 0x01)

        if not self._uds_client:
            self._log("未连接UDS客户端")
            return

        client = self._uds_client
        self._switch_btn.setEnabled(False)
        self._log(f"正在切换 {session_name}...")

        def _handle_response(resp):
            self._switch_btn.setEnabled(True)
            if resp and len(resp) >= 2 and resp[0] == 0x50:
                self._current_session = session_type
                self._session_label.setText(f"{session_name}")
                if len(resp) >= 6:
                    p2 = (resp[2] << 8 | resp[3]) * 0.001
                    p2_star = (resp[4] << 8 | resp[5]) * 0.01
                    self._p2_label.setText(f"{p2:.3f}s")
                    self._p2_star_label.setText(f"{p2_star:.3f}s")
                self._log(f"切换到 {session_name} 成功")
                self.session_changed.emit(session_type)
            else:
                self._log(f"切换失败: 负响应或无响应")

        def _handle_error(msg):
            self._switch_btn.setEnabled(True)
            self._log(f"切换异常: {msg}")

        self._run_uds_async(
            client.diagnostic_session_control, session_type,
            on_success=_handle_response, on_error=_handle_error
        )

    def _on_tester_present(self):
        if not self._uds_client:
            self._log("未连接UDS客户端")
            return

        client = self._uds_client
        self._tp_btn.setEnabled(False)

        def _handle_response(resp):
            self._tp_btn.setEnabled(True)
            if resp:
                self._log("TesterPresent 发送成功")
            else:
                self._log("TesterPresent 发送失败")

        def _handle_error(msg):
            self._tp_btn.setEnabled(True)
            self._log(f"TesterPresent 异常: {msg}")

        self._run_uds_async(
            client.tester_present,
            on_success=_handle_response, on_error=_handle_error
        )

    def closeEvent(self, event):
        """窗口关闭时确保工作线程安全退出"""
        if self._worker_thread and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        super().closeEvent(event)

    def _log(self, msg: str):
        import time
        ts = time.strftime("%H:%M:%S")
        self._log_text.append(f"[{ts}] {msg}")

    @property
    def current_session(self) -> int:
        return self._current_session
