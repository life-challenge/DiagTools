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
        # 保活寻址与抑制正响应（V2.x会话保持增强）:
        # - functional: True=按功能寻址ID广播保活（同时保活总线上所有ECU）
        # - suppress: 抑制正响应(3E 80)，ECU不回复，总线干净（默认）
        self._keepalive_functional = bool(
            cfg.get("session.tester_present_functional", False))
        self._suppress_pos = bool(
            cfg.get("session.tester_present_suppress", True))
        self._functional_id_dirty = False  # 用户手改过功能ID后不再跟随默认
        self._functional_hint = None       # ECU定义提供的功能ID（CAN）
        self._init_ui()
        self._update_keepalive_status()  # 初始未连接时显示"待连接"

    def set_uds_client(self, client):
        """连接/断开时切换UDS客户端，同步会话保持定时器"""
        # 先停掉旧客户端的保活定时器，避免残留线程
        if self._uds_client is not None:
            self._uds_client.stop_tester_present()
        self._uds_client = client
        self._refresh_func_id_default()
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
            "勾选后周期发送 3E 保持当前非默认会话，\n"
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

        # 保活寻址: 物理寻址（仅当前ECU）或功能寻址（广播，
        # 同时保活总线上所有ECU）。功能ID随传输层自适应——
        # DoCAN为功能CAN ID（标准0x7DF，ECU定义可覆盖），
        # DoIP为功能组逻辑地址（ISO 13400-2标准0xE400）
        addr_row = QHBoxLayout()
        self._addr_combo = QComboBox()
        self._addr_combo.addItem("物理寻址")
        self._addr_combo.addItem("功能寻址")
        self._addr_combo.setCurrentIndex(1 if self._keepalive_functional else 0)
        self._addr_combo.setToolTip(
            "保活报文的寻址方式:\n"
            "物理寻址——仅发给当前ECU（单点保活）;\n"
            "功能寻址——广播到总线上所有ECU（多点保活，\n"
            "适用于同时诊断多个ECU或刷写时保活全车）")
        self._addr_combo.currentIndexChanged.connect(self._on_addr_mode_changed)
        addr_row.addWidget(self._addr_combo)

        self._func_id_combo = QComboBox()
        self._func_id_combo.setEditable(True)
        self._func_id_combo.setToolTip(
            "功能寻址ID（hex，如 7DF / 0x7DF）:\n"
            "DoCAN——功能请求CAN ID，标准0x7DF;\n"
            "DoIP——功能组逻辑地址，标准0xE400 (ISO 13400-2)，\n"
            "如填0x7DF会自动映射为0xE400")
        le = self._func_id_combo.lineEdit()
        le.editingFinished.connect(self._on_func_id_edited)
        self._refresh_func_id_default()
        self._func_id_combo.setEnabled(self._keepalive_functional)
        addr_row.addWidget(self._func_id_combo)
        form.addRow("保活寻址:", addr_row)

        # 抑制正响应: 3E 80（ECU不回复，总线干净）。默认抑制;
        # 取消抑制发送3E 00，可从响应确认ECU仍在（但会占用总线）
        self._suppress_check = QCheckBox("抑制正响应 (3E 80)")
        self._suppress_check.setChecked(self._suppress_pos)
        self._suppress_check.setToolTip(
            "勾选: 发送 3E 80，ECU不回复正响应，总线流量最小（推荐）;\n"
            "取消勾选: 发送 3E 00，ECU回复 7E 00，可确认保活报文\n"
            "被ECU正常接收（功能寻址多ECU时会有多个响应交错）")
        self._suppress_check.toggled.connect(self._on_suppress_toggled)
        form.addRow("正响应:", self._suppress_check)

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

    def _on_addr_mode_changed(self, index: int):
        """寻址方式切换: 物理单点/功能广播，持久化并刷新保活"""
        self._keepalive_functional = (index == 1)
        cfg = get_config_manager()
        cfg.set("session.tester_present_functional", self._keepalive_functional)
        cfg.save_config()
        self._func_id_combo.setEnabled(self._keepalive_functional)
        self._apply_keepalive()
        self._update_keepalive_status()
        if self._keepalive_functional:
            self._log(f"保活切换为功能寻址 0x{self._current_functional_id():04X} "
                      f"(广播总线上所有ECU)")
        else:
            self._log("保活切换为物理寻址 (仅当前ECU)")

    def _on_func_id_edited(self):
        """功能ID手改: 校验hex后归一化显示并持久化"""
        text = self._func_id_combo.currentText().strip()
        try:
            value = int(text, 16)
        except ValueError:
            self._log(f"功能寻址ID无效: {text!r}，已回退上次有效值")
            self._functional_id_dirty = False  # 放弃手改，回退保存值/默认
            self._refresh_func_id_default()
            return
        if not (0 < value <= 0x1FFFFFFF):
            self._log(f"功能寻址ID超范围: {text!r}，已回退上次有效值")
            self._functional_id_dirty = False
            self._refresh_func_id_default()
            return
        self._functional_id_dirty = True  # 手改后不再跟随传输层/ECU默认
        width = 4 if value > 0xFFFF else 3
        self._func_id_combo.setCurrentText(f"0x{value:0{width}X}")
        cfg = get_config_manager()
        cfg.set("session.tester_present_functional_id", f"0x{value:0{width}X}")
        cfg.save_config()
        self._apply_keepalive()
        self._update_keepalive_status()

    def _on_suppress_toggled(self, checked: bool):
        """抑制正响应切换: 持久化并刷新保活"""
        self._suppress_pos = checked
        cfg = get_config_manager()
        cfg.set("session.tester_present_suppress", checked)
        cfg.save_config()
        self._apply_keepalive()
        self._update_keepalive_status()
        self._log("保活正响应已抑制 (3E 80)" if checked
                  else "保活正响应已取消抑制 (3E 00，ECU将回复7E 00)")

    def _apply_keepalive(self):
        """按当前开关状态启停底层保活定时器（含寻址/抑制参数）"""
        if self._uds_client is None:
            return
        if self._keepalive_enabled:
            self._uds_client.functional_id = self._current_functional_id()
            self._uds_client.start_tester_present(
                self._keepalive_interval,
                suppress=self._suppress_pos,
                functional=self._keepalive_functional)
        else:
            self._uds_client.stop_tester_present()

    def _update_keepalive_status(self):
        """刷新保活状态标签（含寻址/抑制摘要，暂停时显示暂停中）"""
        desc = self._keepalive_desc()
        if not self._keepalive_enabled:
            self._keepalive_label.setText("未启用")
        elif self._uds_client is None:
            self._keepalive_label.setText(
                f"待连接 ({self._keepalive_interval} ms · {desc})")
        elif self._uds_client._tester_present_running:
            self._keepalive_label.setText(
                f"运行中 ({self._keepalive_interval} ms · {desc})")
        else:
            self._keepalive_label.setText(
                f"已暂停 ({self._keepalive_interval} ms · {desc})")

    def _keepalive_desc(self) -> str:
        """保活配置摘要，如 '3E 80 · 功能 0x7DF'"""
        sf = "80" if self._suppress_pos else "00"
        if self._keepalive_functional:
            fid = self._current_functional_id()
            width = 4 if fid > 0xFFFF else 3
            return f"3E {sf} · 功能 0x{fid:0{width}X}"
        return f"3E {sf} · 物理"

    # ---------------- 功能寻址ID自适应 ----------------

    def _is_doip(self) -> bool:
        """当前连接是否为DoIP传输（功能ID默认值与提示不同）"""
        if self._uds_client is None:
            return False
        tp = self._uds_client.transport_layer
        return getattr(tp, "interface_name", "") == "DoIP"

    def set_functional_id_hint(self, value):
        """ECU定义提供功能寻址ID默认（DoCAN；DoIP固定标准功能组）"""
        self._functional_hint = value
        self._refresh_func_id_default()

    def _default_func_id_text(self) -> str:
        """功能ID默认值: 连接面板/ECU定义hint优先，无hint用标准默认

        DoIP→功能组逻辑地址（连接面板可配，如0xE500）；
        DoCAN→ECU定义功能CAN ID/标准0x7DF。连接后hint由主窗口
        从连接面板传入（按硬件类型适配后的语义值）
        """
        hint = self._functional_hint
        if self._is_doip():
            if hint and hint <= 0xFFFF:
                return f"0x{hint:04X}"
            return "0xE400"  # DoIP标准功能组逻辑地址（ISO 13400-2）
        if hint:
            return f"0x{hint:03X}"
        return "0x7DF"  # DoCAN标准功能请求ID

    def _refresh_func_id_default(self):
        """刷新功能ID输入框: 用户配置 > 传输层/ECU默认"""
        if not hasattr(self, "_func_id_combo"):
            return
        if self._functional_id_dirty:
            return  # 本次会话手改过，保持用户输入
        cfg = get_config_manager()
        saved = str(cfg.get("session.tester_present_functional_id", "") or "").strip()
        if saved:
            try:
                value = int(saved, 16)
            except ValueError:
                value = None
            if value:
                width = 4 if value > 0xFFFF else 3
                self._func_id_combo.setCurrentText(f"0x{value:0{width}X}")
                return
        default = self._default_func_id_text()
        self._func_id_combo.setCurrentText(default)

    def _current_functional_id(self) -> int:
        """当前生效的功能寻址ID（输入无效时回退默认）"""
        text = self._func_id_combo.currentText().strip() \
            if hasattr(self, "_func_id_combo") else ""
        try:
            value = int(text, 16)
        except ValueError:
            value = None
        if value:
            return value
        if self._is_doip():
            return 0xE400
        return self._functional_hint or 0x7DF

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
