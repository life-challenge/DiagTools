"""诊断会话控制面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QComboBox, QPushButton, QFormLayout,
                              QCheckBox, QSpinBox)
from PyQt6.QtCore import pyqtSignal, QThread
from src.ui.async_uds import UdsWorker as _UdsWorker
from src.utils.config_manager import get_config_manager


class SessionPanel(QWidget):
    """诊断会话控制面板"""

    session_changed = pyqtSignal(int)  # 会话类型变化

    # 业务日志转发(msg, level): 面板不再内置日志窗口，统一由主窗口业务日志呈现
    business_log = pyqtSignal(str, str)

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
        cfg = get_config_manager()
        self._keepalive_enabled = bool(cfg.get("session.auto_tester_present", False))
        self._keepalive_interval = int(cfg.get("session.tester_present_interval_ms", 2000))
        self._init_ui()
        self._update_keepalive_status()  # 初始未连接时显示"待连接"

    def set_uds_client(self, client):
        """连接/断开时切换UDS客户端，同步会话保持定时器"""
        # 先停掉旧客户端的保活定时器，避免残留线程
        if self._uds_client is not None:
            self._uds_client.stop_tester_present()
        self._uds_client = client
        self._apply_keepalive()
        self._update_keepalive_status()

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

        # 会话保持（自动TesterPresent，防S3超时退回默认会话）
        keepalive_row = QHBoxLayout()
        self._keepalive_check = QCheckBox("自动发送 TesterPresent")
        self._keepalive_check.setChecked(self._keepalive_enabled)
        self._keepalive_check.setToolTip(
            "勾选后周期发送 3E 80 保持当前非默认会话，\n"
            "防止ECU S3超时（约5秒无诊断请求）自动退回默认会话")
        self._keepalive_check.toggled.connect(self._on_keepalive_toggled)
        keepalive_row.addWidget(self._keepalive_check)
        self._keepalive_spin = QSpinBox()
        self._keepalive_spin.setRange(200, 4000)
        self._keepalive_spin.setSingleStep(100)
        self._keepalive_spin.setSuffix(" ms")
        self._keepalive_spin.setValue(self._keepalive_interval)
        self._keepalive_spin.setToolTip("发送间隔，建议不大于ECU的S3定时器(通常5000ms)的一半")
        self._keepalive_spin.valueChanged.connect(self._on_interval_changed)
        keepalive_row.addWidget(self._keepalive_spin)
        keepalive_row.addStretch()
        form.addRow("会话保持:", keepalive_row)

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
        self._keepalive_label = QLabel("未启用")
        status_layout.addRow("保活状态:", self._keepalive_label)
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        layout.addStretch()

    # ---------------- 会话保持 ----------------

    def pause_keepalive(self):
        """临时暂停保活（如ECU扫描期间避免与扫描请求交错干扰），不清开关状态"""
        if self._uds_client is not None:
            self._uds_client.stop_tester_present()
        self._update_keepalive_status()

    def resume_keepalive(self):
        """恢复保活（若开关开启）"""
        self._apply_keepalive()
        self._update_keepalive_status()

    def _on_keepalive_toggled(self, checked: bool):
        """开关切换: 启停保活并持久化到配置"""
        self._keepalive_enabled = checked
        cfg = get_config_manager()
        cfg.set("session.auto_tester_present", checked)
        cfg.save_config()
        self._apply_keepalive()
        self._update_keepalive_status()
        if checked:
            self._log(f"会话保持已开启 (间隔 {self._keepalive_interval} ms)")
        else:
            self._log("会话保持已关闭")

    def _on_interval_changed(self, value: int):
        """间隔调整: 若保活运行中则重启定时器，并持久化"""
        self._keepalive_interval = value
        cfg = get_config_manager()
        cfg.set("session.tester_present_interval_ms", value)
        cfg.save_config()
        if self._keepalive_enabled:
            self._apply_keepalive()
            self._update_keepalive_status()

    def _apply_keepalive(self):
        """按当前开关状态启停底层保活定时器"""
        if self._uds_client is None:
            return
        if self._keepalive_enabled:
            self._uds_client.start_tester_present(self._keepalive_interval)
        else:
            self._uds_client.stop_tester_present()

    def _update_keepalive_status(self):
        """刷新保活状态标签（暂停时显示暂停中）"""
        if not self._keepalive_enabled:
            self._keepalive_label.setText("未启用")
        elif self._uds_client is None:
            self._keepalive_label.setText(f"待连接 ({self._keepalive_interval} ms)")
        elif self._uds_client._tester_present_running:
            self._keepalive_label.setText(
                f"运行中 ({self._keepalive_interval} ms)")
        else:
            self._keepalive_label.setText(f"已暂停 ({self._keepalive_interval} ms)")

    def _maybe_hint_keepalive(self, session_type: int):
        """切入非默认会话且未开保活时提示，防S3超时退回"""
        if session_type != 0x01 and not self._keepalive_enabled:
            self._log("提示: 当前未开启会话保持，ECU可能因S3超时自动退回默认会话，"
                      "建议勾选“会话保持”")

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
                    self._maybe_hint_keepalive(session_type)
                else:
                    self._log("切换失败: 负响应或无响应")
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
                self._maybe_hint_keepalive(session_type)
            else:
                self._log("切换失败: 负响应或无响应")

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
        if self._uds_client is not None:
            self._uds_client.stop_tester_present()
        if self._worker_thread and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        super().closeEvent(event)

    def _log(self, msg: str, level: str = "INFO"):
        self.business_log.emit(msg, level)

    @property
    def current_session(self) -> int:
        return self._current_session
