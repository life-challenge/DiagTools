"""安全访问面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QPushButton, QSpinBox, QLineEdit,
                              QTextEdit, QComboBox, QFormLayout, QProgressBar,
                              QFileDialog, QMessageBox)
from PyQt6.QtCore import QTimer
from src.business.security_manager import SecurityManager
from src.utils.config_manager import get_config_manager


class SecurityPanel(QWidget):
    """安全访问面板"""

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        self._security_manager = SecurityManager()
        self._timeout_timer = QTimer()
        self._timeout_timer.timeout.connect(self._update_timeout)
        self._init_ui()
        self._security_manager.load_all_plugins()

    def set_uds_client(self, client):
        self._uds_client = client

    @property
    def security_manager(self) -> SecurityManager:
        """安全管理器（供刷写面板注入密钥计算钩子）"""
        return self._security_manager

    def shutdown(self):
        """退出时清理桥接子进程"""
        try:
            self._security_manager.shutdown()
        except Exception:
            pass

    def unlocked_levels(self) -> list:
        """当前已解锁且未超时的安全等级（供状态栏轮询）"""
        try:
            return self._security_manager.unlocked_levels()
        except Exception:
            return []

    def _browse_py32(self):
        """手动指定32位Python可执行文件"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择32位Python的python.exe", "", "python.exe (python.exe);;所有文件 (*)")
        if not filepath:
            return
        if self._security_manager.set_python32_path(filepath):
            self._py32_edit.setText(filepath)
            self._log(f"已指定32位Python: {filepath}")
        else:
            QMessageBox.warning(
                self, "无效的32位Python",
                f"该文件不是有效的32位Python解释器:\n{filepath}\n\n"
                "请从 python.org 下载 Windows x86 (32位) 安装包，"
                "安装后选择其目录下的 python.exe。")

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 算法加载
        algo_group = QGroupBox("安全算法")
        algo_layout = QVBoxLayout()

        load_layout = QHBoxLayout()
        self._algo_combo = QComboBox()
        self._refresh_algo_list()
        load_layout.addWidget(self._algo_combo)
        load_btn = QPushButton("加载算法")
        load_btn.clicked.connect(self._load_selected_algo)
        load_layout.addWidget(load_btn)
        load_file_btn = QPushButton("从文件加载...")
        load_file_btn.setToolTip("支持Python脚本(.py)和DLL(.dll)算法文件")
        load_file_btn.clicked.connect(self._load_algo_file)
        load_layout.addWidget(load_file_btn)
        algo_layout.addLayout(load_layout)

        # 32位Python路径（用于桥接加载32位算法DLL）
        py32_layout = QHBoxLayout()
        py32_layout.addWidget(QLabel("32位Python:"))
        self._py32_edit = QLineEdit()
        self._py32_edit.setPlaceholderText("加载32位DLL时需要；自动探测失败时在此指定python.exe路径")
        self._py32_edit.setText(get_config_manager().get("security.python32_path", "") or "")
        py32_layout.addWidget(self._py32_edit, 1)
        py32_browse_btn = QPushButton("浏览...")
        py32_browse_btn.clicked.connect(self._browse_py32)
        py32_layout.addWidget(py32_browse_btn)
        algo_layout.addLayout(py32_layout)

        self._algo_info = QLabel("未加载算法")
        self._algo_info.setStyleSheet("color: #888;")
        algo_layout.addWidget(self._algo_info)

        algo_group.setLayout(algo_layout)
        layout.addWidget(algo_group)

        # 安全访问操作
        access_group = QGroupBox("安全访问")
        access_form = QFormLayout()

        self._level_spin = QSpinBox()
        self._level_spin.setRange(1, 255)
        self._level_spin.setValue(1)
        access_form.addRow("安全等级:", self._level_spin)

        self._seed_label = QLabel("--")
        access_form.addRow("当前种子:", self._seed_label)

        self._key_label = QLabel("--")
        access_form.addRow("计算密钥:", self._key_label)

        self._status_label = QLabel("未解锁")
        self._status_label.setStyleSheet("color: #F44336; font-weight: bold;")
        access_form.addRow("状态:", self._status_label)

        self._timeout_progress = QProgressBar()
        self._timeout_progress.setRange(0, 100)
        self._timeout_progress.setValue(0)
        self._timeout_progress.setFormat("超时倒计时: %v%")
        access_form.addRow(self._timeout_progress)

        access_group.setLayout(access_form)
        layout.addWidget(access_group)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self._request_seed_btn = QPushButton("请求种子")
        self._request_seed_btn.clicked.connect(self._request_seed)
        btn_layout.addWidget(self._request_seed_btn)

        self._send_key_btn = QPushButton("发送密钥")
        self._send_key_btn.clicked.connect(self._send_key)
        self._send_key_btn.setEnabled(False)
        btn_layout.addWidget(self._send_key_btn)

        self._one_click_btn = QPushButton("一键解锁")
        self._one_click_btn.setStyleSheet("QPushButton { background: #2196F3; color: white; padding: 8px; font-weight: bold; }")
        self._one_click_btn.clicked.connect(self._one_click_unlock)
        btn_layout.addWidget(self._one_click_btn)
        layout.addLayout(btn_layout)

        # 离线测试
        test_group = QGroupBox("离线测试")
        test_layout = QHBoxLayout()
        test_layout.addWidget(QLabel("测试种子(HEX):"))
        self._test_seed_edit = QLineEdit()
        self._test_seed_edit.setPlaceholderText("如: A5 5A")
        test_layout.addWidget(self._test_seed_edit)
        test_btn = QPushButton("测试")
        test_btn.clicked.connect(self._offline_test)
        test_layout.addWidget(test_btn)
        test_group.setLayout(test_layout)
        layout.addWidget(test_group)

        # 日志
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(120)
        layout.addWidget(self._log_text)

        layout.addStretch()

    def _refresh_algo_list(self):
        self._algo_combo.clear()
        for level, info in self._security_manager.loaded_algorithms.items():
            type_tag = "[DLL]" if info.is_dll else "[PY]"
            self._algo_combo.addItem(f"Level {level}: {info.name} {type_tag}", level)

    def _load_algo_file(self):
        """从文件加载算法插件（支持.py和.dll）"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择安全算法文件", "", "算法文件 (*.py *.dll);;所有文件 (*)")
        if not filepath:
            return
        info = self._security_manager.load_algorithm_file(filepath)
        if info:
            self._refresh_algo_list()
            # 将等级同步到操作区，方便直接测试/解锁
            self._level_spin.setValue(info.level)
            self._algo_info.setText(
                f"已加载: {info.name} | Level: {info.level} | "
                f"{'DLL' if info.is_dll else 'Python'} | {info.file_path}")
            self._algo_info.setStyleSheet("color: #4CAF50;")
            self._log(f"已加载算法文件: {info.name} (Level {info.level})")
        else:
            self._algo_info.setText("算法文件加载失败")
            self._algo_info.setStyleSheet("color: #F44336;")
            # 显示具体失败原因（位数不匹配/缺少导出函数等）
            detail = self._security_manager.last_error or "未知错误"
            QMessageBox.warning(
                self, "加载失败",
                f"无法加载算法文件:\n{filepath}\n\n原因: {detail}")
            self._log(f"算法文件加载失败: {detail}")

    def _load_selected_algo(self):
        self._refresh_algo_list()
        level = self._level_spin.value()
        if level in self._security_manager.loaded_algorithms:
            info = self._security_manager.loaded_algorithms[level]
            self._algo_info.setText(
                f"已加载: {info.name} | 版本: {info.version} | Level: {info.level}")
            self._algo_info.setStyleSheet("color: #4CAF50;")
        else:
            self._algo_info.setText("未找到对应等级的算法")
            self._algo_info.setStyleSheet("color: #F44336;")

    def _request_seed(self):
        if not self._uds_client:
            self._log("未连接UDS客户端")
            return
        level = self._level_spin.value()
        resp = self._uds_client.security_access_request_seed(level)
        if resp and resp[0] == 0x67:
            seed = resp[2:]
            self._seed_label.setText(seed.hex(" ").upper())
            self._send_key_btn.setEnabled(True)
            self._log(f"请求种子成功: {seed.hex(' ').upper()}")
        else:
            self._log("请求种子失败")

    def _send_key(self):
        if not self._uds_client:
            return
        level = self._level_spin.value()
        seed_hex = self._seed_label.text()
        if seed_hex == "--":
            self._log("请先请求种子")
            return
        seed = bytes.fromhex(seed_hex.replace(" ", ""))
        key = self._security_manager.generate_key(level, seed)
        if key:
            self._key_label.setText(key.hex(" ").upper())
            resp = self._uds_client.security_access_send_key(level, key)
            if resp and resp[0] == 0x67:
                self._status_label.setText("已解锁")
                self._status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
                self._security_manager.mark_unlocked(level)
                self._timeout_timer.start(1000)
                self._log(f"解锁成功! Level {level}")
            else:
                self._log("发送密钥失败 (invalidKey)")
        else:
            self._log("密钥计算失败")

    def _one_click_unlock(self):
        self._request_seed()
        if self._send_key_btn.isEnabled():
            self._send_key()

    def _offline_test(self):
        seed_hex = self._test_seed_edit.text().strip()
        if not seed_hex:
            return
        try:
            seed = bytes.fromhex(seed_hex.replace(" ", ""))
            level = self._level_spin.value()
            result = self._security_manager.test_algorithm_offline(level, seed)
            if result["success"]:
                self._log(f"离线测试: seed={seed.hex()}, key={result['key'].hex()}, 耗时{result['elapsed_ms']:.2f}ms")
            else:
                self._log("离线测试失败: 未加载对应算法")
        except ValueError:
            self._log("种子格式错误")

    def _update_timeout(self):
        level = self._level_spin.value()
        remaining = self._security_manager.get_unlock_remaining_time(level)
        if remaining > 0:
            self._timeout_progress.setValue(int(remaining))
            self._timeout_progress.setFormat(f"剩余: {remaining:.0f}s")
        else:
            self._timeout_timer.stop()
            self._status_label.setText("已超时")
            self._status_label.setStyleSheet("color: #FF9800; font-weight: bold;")

    def _log(self, msg: str):
        import time
        ts = time.strftime("%H:%M:%S")
        self._log_text.append(f"[{ts}] {msg}")
