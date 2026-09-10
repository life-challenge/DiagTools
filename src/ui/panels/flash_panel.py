"""ECU刷写面板（V2.1 §9 流程可视化，步骤序列可配置）

布局: 左侧=刷写流程步骤列表(按当前配置动态生成，单列显示，状态图标与步骤同行)，
      右侧=文件/参数/刷前准备/刷后恢复/进度/控制/日志。

支持量产完整流程（可按控制器裁剪/增加步骤）:
  刷前准备(功能寻址): 扩展会话10 03 → 预编程条件检查31 01 0203 →
      DTC设置OFF 85 02 → 禁止通信 28 03 03
  编程: 编程会话10 02 → 安全访问27 → 驱动下载 → 写指纹2E
  刷写: 擦除内存31 01 FF00 → APP下载34/36/37
  刷后检查: 完整性校验31 01 0202 → 依赖性校验31 01 FF01 → 复位11 01
  刷后恢复(功能寻址): 扩展会话 → 打开通信28 00 → DTC设置ON 85 01 →
      默认会话10 01 → 清除DTC 14 FF FF FF

选择 .s19/.s28/.s37/.hex 文件时自动解析出起始地址与数据跨度，
并回填目标地址/驱动地址；.bin 无地址信息需手动填写。
"""

import os
import time
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QPushButton, QLineEdit, QSpinBox,
                              QProgressBar, QTextEdit, QFileDialog, QCheckBox,
                              QFormLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
                              QHeaderView, QAbstractItemView, QScrollArea,
                              QGridLayout, QDoubleSpinBox)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from src.business.flash_manager import (FlashManager, FlashConfig, FlashState,
                                        FlashFileInfo, STEP_REGISTRY)

# 步骤状态标记
_PENDING, _RUNNING, _OK, _FAIL, _SKIP = "○", "▶", "✔", "✘", "⊘"
_STEP_COLORS = {
    _PENDING: "#888888", _RUNNING: "#64B5F6", _OK: "#4CAF50",
    _FAIL: "#F44336", _SKIP: "#9E9E9E",
}


def _parse_hex_addr(text: str, default: int = 0) -> int:
    """解析HEX地址输入（支持0x前缀/空格），无效返回default"""
    try:
        return int(text.strip().replace("0x", "").replace(" ", ""), 16)
    except (ValueError, AttributeError):
        return default


class FlashPanel(QWidget):
    """ECU刷写面板（流程可视化）"""

    # FlashManager回调运行在刷写后台线程，控件更新必须经信号
    # 切换到GUI线程——直接跨线程操作QTreeWidget/QLabel会触发
    # Qt C++层崩溃（无Python traceback的闪退）
    _progress_sig = pyqtSignal(object)
    _state_sig = pyqtSignal(object, str, str)

    # 请求直达ECU诊断-安全算法页（加载/管理DLL算法，供刷写安全访问算钥）
    open_security_requested = pyqtSignal()

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        self._flash_manager = FlashManager(uds_client)
        self._key_generator = None     # 安全算法钩子（主窗口注入）
        self._ecu_name = "--"
        self._step_keys = []           # 当前步骤列表的key序列
        self._step_items = []          # 与_step_keys对应的树节点
        self._current_step = -1
        self._parsed_path = ""         # 最近解析成功的文件路径
        self._parsed_span = 0          # 该文件的地址跨度（擦除大小候选）
        self._flash_start_ts = 0.0     # 本次刷写开始时间（报告用）
        self._flash_file_path = ""     # 本次刷写文件（报告用）
        # FlashManager回调经信号转发到GUI线程（刷写线程直接操作控件会崩溃）
        self._progress_sig.connect(self._on_progress)
        self._state_sig.connect(self._on_state_changed)
        self._init_ui()
        self._rebuild_steps()

    def set_uds_client(self, client):
        self._uds_client = client
        self._flash_manager = FlashManager(client)

    def set_key_generator(self, fn):
        """注入密钥计算钩子: fn(level: int, seed: bytes) -> bytes"""
        self._key_generator = fn

    def set_ecu_name(self, name: str, functional_tx_id: int = None):
        self._ecu_name = name or "--"
        self._ecu_label.setText(f"当前ECU: {self._ecu_name}")
        # ECU定义中的功能寻址ID回填到刷前准备参数（可在面板手动改）
        if functional_tx_id:
            self._func_addr_edit.setText(f"0x{functional_tx_id:03X}")

    def import_config(self, cfg: dict) -> int:
        """程序化导入刷写配置（菜单/项目加载入口），返回应用项数"""
        applied = 0
        hex_keys = {
            "target_address": self._addr_edit,
            "driver_address": self._drv_addr_edit,
            "functional_tx_id": self._func_addr_edit,
            "erase_block_size": self._erase_bs_edit,
            "erase_format_byte": self._erase_fmt_edit,
            "wake_can_id": self._wake_can_id_edit,
        }
        for key, edit in hex_keys.items():
            if key in cfg:
                edit.setText(str(cfg[key]))
                applied += 1
        if "block_size" in cfg:
            self._block_spin.setValue(int(cfg["block_size"]))
            applied += 1
        if "prog_session_delay" in cfg:
            self._prog_delay_spin.setValue(float(cfg["prog_session_delay"]))
            applied += 1
        if "reset_delay" in cfg:
            self._reset_delay_spin.setValue(float(cfg["reset_delay"]))
            applied += 1
        if "security_level" in cfg:
            self._sec_spin.setValue(int(cfg["security_level"]))
            applied += 1
        if "fingerprint_did" in cfg:
            self._fp_did_spin.setValue(
                int(str(cfg["fingerprint_did"]), 16)
                if isinstance(cfg["fingerprint_did"], str) else int(cfg["fingerprint_did"]))
            applied += 1
        if "fingerprint_data" in cfg:
            self._fp_data_edit.setText(str(cfg["fingerprint_data"]))
            applied += 1
        if "driver_path" in cfg:
            self._drv_edit.setText(str(cfg["driver_path"]))
            applied += 1
        # 唤醒配置
        if "wake_data" in cfg:
            wake_data = cfg["wake_data"]
            if isinstance(wake_data, bytes):
                self._wake_data_edit.setText(wake_data.hex(" ").upper())
            else:
                self._wake_data_edit.setText(str(wake_data))
            applied += 1
        if "wake_period" in cfg:
            self._wake_period_spin.setValue(int(float(cfg["wake_period"]) * 1000))
            applied += 1
        if "wake_duration" in cfg:
            self._wake_duration_spin.setValue(int(cfg["wake_duration"]))
            applied += 1
        bool_keys = {
            "enter_programming": "_session_check",
            "write_fingerprint": "_fp_check",
            "prep_ext_session": "_prep_ext_check",
            "preprog_check": "_preprog_check",
            "dtc_off": "_dtc_off_check",
            "comm_disable": "_comm_disable_check",
            "check_integrity": "_integrity_check",
            "post_ext_session": "_post_ext_check",
            "post_comm_enable": "_post_comm_check",
            "post_dtc_on": "_post_dtc_check",
            "post_default_session": "_post_default_check",
            "post_clear_dtc": "_post_clear_check",
            "reset_after_flash": "_reset_check",
            "wake_enabled": "_wake_check",
        }
        for key, attr in bool_keys.items():
            if key in cfg and hasattr(self, attr):
                getattr(self, attr).setChecked(bool(cfg[key]))
                applied += 1
        return applied

    # ---------------- UI ----------------

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QHBoxLayout()
        self._ecu_label = QLabel("当前ECU: --")
        self._ecu_label.setStyleSheet("font-weight: bold;")
        header.addWidget(self._ecu_label)
        header.addStretch()
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # ---- 左侧: 流程步骤列表（单列，状态图标与步骤名同行，避免截断） ----
        self._step_tree = QTreeWidget()
        self._step_tree.setColumnCount(1)
        self._step_tree.setHeaderLabels(["刷写流程"])
        self._step_tree.header().hide()
        self._step_tree.setMinimumWidth(330)
        self._step_tree.setRootIsDecorated(False)
        self._step_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._step_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(self._step_tree)

        # ---- 右侧: 配置与控制（可滚动） ----
        right = QWidget()
        rlayout = QVBoxLayout(right)
        rlayout.setContentsMargins(0, 0, 0, 0)

        # 文件选择
        file_group = QGroupBox("刷写文件")
        file_layout = QVBoxLayout()
        row = QHBoxLayout()
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("选择刷写文件 (.bin/.s19/.s28/.s37/.hex)")
        row.addWidget(self._file_edit)
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_file)
        row.addWidget(browse_btn)
        parse_btn = QPushButton("解析")
        parse_btn.setToolTip("解析s19/hex文件的起始地址与数据大小，自动回填目标地址")
        parse_btn.clicked.connect(lambda: self._parse_file_info(
            self._file_edit.text().strip(), self._addr_edit, "应用文件"))
        row.addWidget(parse_btn)
        file_layout.addLayout(row)
        self._file_info_label = QLabel(
            "选择 .s19/.hex 文件后可自动解析起始地址与大小")
        self._file_info_label.setStyleSheet("color: #888;")
        self._file_info_label.setWordWrap(True)
        file_layout.addWidget(self._file_info_label)
        file_group.setLayout(file_layout)
        rlayout.addWidget(file_group)

        # 刷写参数（编程与刷写主体）
        param_group = QGroupBox("刷写参数")
        param_form = QFormLayout()

        self._addr_edit = QLineEdit("0x08000000")
        self._addr_edit.setToolTip("应用数据下载目标地址；选择s19/hex文件时可自动回填")
        param_form.addRow("目标地址:", self._addr_edit)

        self._block_spin = QSpinBox()
        self._block_spin.setRange(64, 4096)
        self._block_spin.setValue(1024)
        self._block_spin.setSuffix(" bytes")
        param_form.addRow("块大小:", self._block_spin)

        self._erase_bs_edit = QLineEdit("0x40000")
        self._erase_bs_edit.setToolTip(
            "擦除按整块进行: 擦除大小向上取整到该值的整数倍（设为0则按实际大小擦除）")
        param_form.addRow("擦除块大小:", self._erase_bs_edit)

        self._erase_fmt_edit = QLineEdit("0x44")
        self._erase_fmt_edit.setToolTip(
            "擦除例程(31 01 FF00)参数格式字节: 高半字节=地址长度，低半字节=大小长度，\n"
            "0x44即 地址(4字节)+大小(4字节)，请求为 31 01 FF00 44+地址+大小；\n"
            "设为0则不带格式字节（请求为 31 01 FF00+地址+大小）")
        param_form.addRow("擦除格式字节:", self._erase_fmt_edit)

        self._session_check = QCheckBox("进入编程会话 (0x10 02)")
        self._session_check.setChecked(True)
        param_form.addRow(self._session_check)

        # 10 02切换后的跳转稳定等待（ECU Bootloader跳转/初始化）
        delay_row = QHBoxLayout()
        self._prog_delay_spin = QDoubleSpinBox()
        self._prog_delay_spin.setRange(0.0, 30.0)
        self._prog_delay_spin.setValue(2.0)
        self._prog_delay_spin.setSuffix(" s")
        self._prog_delay_spin.setSingleStep(0.5)
        self._prog_delay_spin.setToolTip(
            "编程会话(10 02)切换后等待ECU跳转初始化的时间；\n"
            "立即发后续请求可能被拒（7F 7F），实测DEM需2s；设为0不等待")
        delay_row.addWidget(self._prog_delay_spin)
        delay_row.addStretch()
        param_form.addRow("10 02 跳转等待:", delay_row)

        sec_row = QHBoxLayout()
        self._sec_spin = QSpinBox()
        self._sec_spin.setRange(0, 0x7F)
        self._sec_spin.setValue(9)
        self._sec_spin.setToolTip(
            "0 = 跳过安全访问；9 = 27 09/0A；>0 时需已加载对应等级的安全算法")
        sec_row.addWidget(self._sec_spin)
        sec_row.addWidget(QLabel("(0=跳过)"))
        # 直达安全算法页（刷写安全访问步骤依赖已加载的DLL算法）
        sec_algo_btn = QPushButton("🔐 安全算法...")
        sec_algo_btn.setToolTip(
            "打开ECU诊断-安全算法页加载/管理DLL算法\n"
            "刷写安全访问步骤将使用该算法计算密钥")
        sec_algo_btn.clicked.connect(self.open_security_requested.emit)
        sec_row.addWidget(sec_algo_btn)
        sec_row.addStretch()
        param_form.addRow("安全等级:", sec_row)

        drv_row = QHBoxLayout()
        self._drv_edit = QLineEdit()
        self._drv_edit.setPlaceholderText("不选 = 跳过驱动下载；选择 = 先驱动后APP")
        self._drv_edit.setToolTip("部分控制器刷写前需先下载 flash driver 到RAM")
        drv_browse = QPushButton("浏览...")
        drv_browse.clicked.connect(self._browse_driver)
        drv_row.addWidget(self._drv_edit, 1)
        drv_row.addWidget(drv_browse)
        param_form.addRow("刷写驱动:", drv_row)

        self._drv_addr_edit = QLineEdit("0x20000000")
        self._drv_addr_edit.setToolTip(
            "flash driver 的下载目标地址（通常为RAM）；选择s19/hex驱动文件时可自动回填")
        param_form.addRow("驱动地址:", self._drv_addr_edit)

        fp_row = QHBoxLayout()
        self._fp_check = QCheckBox("启用")
        self._fp_check.setChecked(True)
        self._fp_check.setToolTip("擦除前写入刷写指纹 (WriteDataByIdentifier)")
        self._fp_did_spin = QSpinBox()
        self._fp_did_spin.setRange(0xF000, 0xFFFF)
        self._fp_did_spin.setDisplayIntegerBase(16)
        self._fp_did_spin.setPrefix("0x")
        self._fp_did_spin.setValue(0xF15A)
        fp_row.addWidget(self._fp_check)
        fp_row.addWidget(QLabel("DID:"))
        fp_row.addWidget(self._fp_did_spin)
        fp_row.addStretch()
        param_form.addRow("写入指纹:", fp_row)

        self._fp_data_edit = QLineEdit()
        self._fp_data_edit.setPlaceholderText("指纹数据(HEX)，如: 26 08 29 00 00 00")
        self._fp_data_edit.setToolTip("常用内容: 刷写日期(年月日) + 测试仪/供应商标识")
        param_form.addRow("指纹数据:", self._fp_data_edit)

        param_group.setLayout(param_form)
        rlayout.addWidget(param_group)

        # ECU 唤醒配置（部分控制器需要先发送唤醒报文才能进行诊断）——两列网格压缩高度
        wake_group = QGroupBox("ECU 唤醒（可选）")
        wake_group.setToolTip("部分控制器（如 DEM）在休眠状态下需要先发送唤醒报文才能进行诊断")
        wake_grid = QGridLayout()
        wake_grid.setHorizontalSpacing(12)
        wake_grid.setVerticalSpacing(2)
        self._wake_check = QCheckBox("启用 ECU 唤醒")
        self._wake_check.setChecked(False)
        self._wake_check.setToolTip("勾选后在刷写前周期发送唤醒报文")
        self._wake_can_id_edit = QLineEdit("0x160")
        self._wake_can_id_edit.setToolTip("唤醒报文 CAN ID（16 进制）")
        self._wake_data_edit = QLineEdit("FF FF FF FF FF FF FF FF")
        self._wake_data_edit.setToolTip("唤醒报文数据（HEX 格式，最多 8 字节）")
        self._wake_period_spin = QSpinBox()
        self._wake_period_spin.setRange(10, 1000)
        self._wake_period_spin.setValue(50)
        self._wake_period_spin.setSuffix(" ms")
        self._wake_period_spin.setToolTip("唤醒报文发送周期")
        self._wake_duration_spin = QSpinBox()
        self._wake_duration_spin.setRange(1, 30)
        self._wake_duration_spin.setValue(3)
        self._wake_duration_spin.setSuffix(" s")
        self._wake_duration_spin.setToolTip("唤醒持续时间")
        wake_grid.addWidget(self._wake_check, 0, 0)
        wake_grid.addWidget(QLabel("周期:"), 0, 1)
        wake_grid.addWidget(self._wake_period_spin, 0, 2)
        wake_grid.addWidget(QLabel("持续:"), 0, 3)
        wake_grid.addWidget(self._wake_duration_spin, 0, 4)
        wake_grid.addWidget(QLabel("CAN ID:"), 1, 0)
        wake_grid.addWidget(self._wake_can_id_edit, 1, 1)
        wake_grid.addWidget(QLabel("数据(HEX):"), 1, 2)
        wake_grid.addWidget(self._wake_data_edit, 1, 3, 1, 2)
        wake_group.setLayout(wake_grid)
        rlayout.addWidget(wake_group)

        # 刷前准备（功能寻址）——复选框两列网格压缩高度
        prep_group = QGroupBox("刷前准备（功能寻址）")
        prep_grid = QGridLayout()
        prep_grid.setHorizontalSpacing(24)
        prep_grid.setVerticalSpacing(2)
        self._prep_ext_check = QCheckBox("扩展会话 (10 03)")
        self._prep_ext_check.setChecked(True)
        self._preprog_check = QCheckBox("预编程条件检查 (31 01 0203)")
        self._preprog_check.setChecked(True)
        self._preprog_check.setToolTip("物理寻址执行，等待检查结果")
        self._dtc_off_check = QCheckBox("DTC设置OFF (85 02)")
        self._dtc_off_check.setChecked(True)
        self._comm_disable_check = QCheckBox("禁止非诊断报文收发 (28 03 03)")
        self._comm_disable_check.setChecked(True)
        prep_grid.addWidget(self._prep_ext_check, 0, 0)
        prep_grid.addWidget(self._preprog_check, 0, 1)
        prep_grid.addWidget(self._dtc_off_check, 1, 0)
        prep_grid.addWidget(self._comm_disable_check, 1, 1)

        func_row = QHBoxLayout()
        self._func_addr_edit = QLineEdit("0x7DF")
        self._func_addr_edit.setToolTip(
            "功能寻址请求CAN ID（标准0x7DF，可在ECU定义ecu.json中配置）")
        func_row.addWidget(QLabel("功能地址:"))
        func_row.addWidget(self._func_addr_edit)
        func_row.addStretch()
        prep_grid.addLayout(func_row, 2, 0, 1, 2)
        prep_group.setLayout(prep_grid)
        rlayout.addWidget(prep_group)

        # 刷后检查与恢复——复选框两列网格压缩高度
        post_group = QGroupBox("刷后检查与恢复")
        post_grid = QGridLayout()
        post_grid.setHorizontalSpacing(24)
        post_grid.setVerticalSpacing(2)
        self._integrity_check = QCheckBox("完整性校验 (31 01 0202)")
        self._integrity_check.setChecked(True)
        self._depend_check = QCheckBox("依赖性校验 (31 01 FF01)")
        self._depend_check.setChecked(True)
        post_grid.addWidget(self._integrity_check, 0, 0)
        post_grid.addWidget(self._depend_check, 0, 1)

        reset_row = QHBoxLayout()
        self._reset_check = QCheckBox("ECU复位 (11 01)")
        self._reset_check.setChecked(True)
        self._reset_delay_spin = QDoubleSpinBox()
        self._reset_delay_spin.setRange(0.0, 30.0)
        self._reset_delay_spin.setValue(2.0)
        self._reset_delay_spin.setSuffix(" s")
        self._reset_delay_spin.setSingleStep(0.5)
        self._reset_delay_spin.setToolTip(
            "ECU复位(11 01)后等待重启稳定的时间；设为0不等待")
        reset_row.addWidget(self._reset_check)
        reset_row.addWidget(QLabel("重启等待:"))
        reset_row.addWidget(self._reset_delay_spin)
        reset_row.addStretch()
        post_grid.addLayout(reset_row, 1, 0, 1, 2)

        recover_label = QLabel("刷后恢复（功能寻址）:")
        recover_label.setStyleSheet("color: #888;")
        post_grid.addWidget(recover_label, 2, 0, 1, 2)
        self._post_ext_check = QCheckBox("扩展会话 (10 03)")
        self._post_ext_check.setChecked(True)
        self._post_comm_check = QCheckBox("打开通信 (28 00 03)")
        self._post_comm_check.setChecked(True)
        self._post_dtc_check = QCheckBox("DTC设置ON (85 01)")
        self._post_dtc_check.setChecked(True)
        self._post_default_check = QCheckBox("默认会话 (10 01)")
        self._post_default_check.setChecked(True)
        self._post_clear_check = QCheckBox("清除DTC (14 FF FF FF)")
        self._post_clear_check.setChecked(True)
        post_grid.addWidget(self._post_ext_check, 3, 0)
        post_grid.addWidget(self._post_comm_check, 3, 1)
        post_grid.addWidget(self._post_dtc_check, 4, 0)
        post_grid.addWidget(self._post_default_check, 4, 1)
        post_grid.addWidget(self._post_clear_check, 5, 0)
        post_group.setLayout(post_grid)
        rlayout.addWidget(post_group)

        # 参数变化时刷新步骤列表预览
        for w in (self._session_check, self._fp_check, self._prep_ext_check,
                  self._preprog_check, self._dtc_off_check, self._comm_disable_check,
                  self._integrity_check, self._depend_check, self._reset_check,
                  self._post_ext_check, self._post_comm_check, self._post_dtc_check,
                  self._post_default_check, self._post_clear_check):
            w.toggled.connect(lambda _=False: self._rebuild_steps())
        self._sec_spin.valueChanged.connect(lambda _: self._rebuild_steps())
        self._fp_did_spin.valueChanged.connect(lambda _: self._rebuild_steps())
        self._drv_edit.textChanged.connect(lambda _: self._rebuild_steps())
        self._erase_bs_edit.textChanged.connect(lambda _: self._rebuild_steps())

        # 进度
        progress_group = QGroupBox("刷写进度")
        progress_layout = QVBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        progress_layout.addWidget(self._progress_bar)

        info_layout = QHBoxLayout()
        self._status_label = QLabel("状态: 空闲")
        self._rate_label = QLabel("速率: --")
        self._time_label = QLabel("剩余: --")
        info_layout.addWidget(self._status_label)
        info_layout.addWidget(self._rate_label)
        info_layout.addWidget(self._time_label)
        info_layout.addStretch()
        progress_layout.addLayout(info_layout)
        progress_group.setLayout(progress_layout)
        rlayout.addWidget(progress_group)

        # 控制按钮——互斥状态: 与工具栏连接/断开一致，任何时刻只有一个可操作；
        # 控件级QSS必须显式声明:disabled样式，否则禁用后仍显示原色、
        # 看起来可点实际点击无反应（覆盖全局QSS的禁用外观）
        btn_layout = QHBoxLayout()
        self._start_btn = QPushButton("开始刷写")
        self._start_btn.setStyleSheet(
            "QPushButton { padding: 10px; font-weight: bold; background: #4CAF50; color: white; }"
            "QPushButton:disabled { background: #4E5B4E; color: #7A857A; }")
        self._start_btn.clicked.connect(self._start_flash)
        btn_layout.addWidget(self._start_btn)

        self._stop_btn = QPushButton("停止")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(
            "QPushButton { padding: 10px; background: #F44336; color: white; }"
            "QPushButton:disabled { background: #5B4E4E; color: #857A7A; }")
        self._stop_btn.clicked.connect(self._stop_flash)
        btn_layout.addWidget(self._stop_btn)
        rlayout.addLayout(btn_layout)

        # 日志
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(120)
        rlayout.addWidget(self._log_text)

        # 右侧内容较多时允许滚动
        scroll = QScrollArea()
        scroll.setWidget(right)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    # ---------------- 文件解析 ----------------

    def _parse_file_info(self, path: str, addr_edit: QLineEdit, label: str):
        """解析s19/hex文件: 回填起始地址，显示地址范围与大小

        .bin 无地址信息，仅提示大小。解析失败显示原因。
        """
        if not path:
            return
        import os
        if not os.path.isfile(path):
            self._file_info_label.setText(f"文件不存在: {path}")
            return
        try:
            info = FlashFileInfo(path)
        except Exception as e:
            self._file_info_label.setText(f"解析失败: {e}")
            return

        if info.start_address is not None:
            addr_edit.setText(f"0x{info.start_address:08X}")
            span = info.address_span
            self._parsed_path = path
            self._parsed_span = span
            self._file_info_label.setText(
                f"{label}已解析: 起始 0x{info.start_address:08X} → "
                f"结束 0x{info.end_address:08X} | 数据 {info.file_size} bytes | "
                f"地址跨度 {span} bytes ({span / 1024:.1f} KB)，将作为擦除大小")
        else:
            self._parsed_path = ""
            self._parsed_span = 0
            self._file_info_label.setText(
                f"{label}大小 {info.file_size} bytes；"
                f".bin 无地址信息，请手动填写目标地址")

    # ---------------- 步骤可视化 ----------------

    def _build_config(self) -> FlashConfig:
        """从当前界面输入构造刷写配置"""
        config = FlashConfig()
        config.file_path = self._file_edit.text().strip()
        config.target_address = _parse_hex_addr(
            self._addr_edit.text(), 0x08000000)
        config.block_size = self._block_spin.value()
        config.erase_address = config.target_address
        config.erase_block_size = _parse_hex_addr(
            self._erase_bs_edit.text(), 0x40000)
        config.erase_format_byte = _parse_hex_addr(
            self._erase_fmt_edit.text(), 0x44) & 0xFF
        # 已解析过当前文件时，擦除大小取文件地址跨度（执行时向上取整到擦除块大小）
        if self._parsed_path == config.file_path and self._parsed_span:
            config.erase_size = self._parsed_span
        config.enter_programming = self._session_check.isChecked()
        config.prog_session_delay = self._prog_delay_spin.value()
        config.reset_delay = self._reset_delay_spin.value()
        config.security_level = self._sec_spin.value()
        config.key_generator = self._key_generator
        config.reset_after_flash = self._reset_check.isChecked()
        # 写入指纹
        config.write_fingerprint = self._fp_check.isChecked()
        config.fingerprint_did = self._fp_did_spin.value()
        try:
            config.fingerprint_data = bytes.fromhex(
                self._fp_data_edit.text().replace(" ", "").replace("0x", ""))
        except ValueError:
            config.fingerprint_data = b""
        # 刷写驱动
        config.driver_path = self._drv_edit.text().strip()
        config.driver_address = _parse_hex_addr(
            self._drv_addr_edit.text(), 0x20000000)
        # 功能寻址（不抑制正响应，等待各ECU正响应）
        config.functional_tx_id = _parse_hex_addr(
            self._func_addr_edit.text(), 0x7DF)
        # 刷前准备
        config.prep_ext_session = self._prep_ext_check.isChecked()
        config.preprog_check = self._preprog_check.isChecked()
        config.dtc_off = self._dtc_off_check.isChecked()
        config.comm_disable = self._comm_disable_check.isChecked()
        # ECU 唤醒
        config.wake_enabled = self._wake_check.isChecked()
        config.wake_can_id = _parse_hex_addr(self._wake_can_id_edit.text(), 0x160)
        try:
            config.wake_data = bytes.fromhex(
                self._wake_data_edit.text().replace(" ", "").replace("0x", ""))
            if len(config.wake_data) > 8:
                config.wake_data = config.wake_data[:8]
            config.wake_data = config.wake_data + bytes(8 - len(config.wake_data))
        except ValueError:
            config.wake_data = bytes([0xFF] * 8)
        config.wake_period = self._wake_period_spin.value() / 1000.0
        config.wake_duration = float(self._wake_duration_spin.value())
        # 刷后检查
        config.check_integrity = self._integrity_check.isChecked()
        config.check_dependency = self._depend_check.isChecked()
        # 刷后恢复
        config.post_ext_session = self._post_ext_check.isChecked()
        config.post_comm_enable = self._post_comm_check.isChecked()
        config.post_dtc_on = self._post_dtc_check.isChecked()
        config.post_default_session = self._post_default_check.isChecked()
        config.post_clear_dtc = self._post_clear_check.isChecked()
        return config

    def _rebuild_steps(self):
        """按当前配置重建左侧步骤列表（与执行顺序完全一致）"""
        if self._flash_manager.is_running:
            return
        config = self._build_config()
        steps = FlashManager.steps(config)
        self._step_keys = [s[0] for s in steps]
        self._step_tree.clear()
        self._step_items = []
        for i, (key, name, desc) in enumerate(steps):
            # 部分步骤显示具体参数，方便核对
            if key == "fingerprint":
                desc = f"0x2E {config.fingerprint_did:04X}"
            elif key == "driver":
                desc = f"0x34→0x{config.driver_address:08X} " \
                       f"块0x{config.driver_block_size:X}"
            elif key == "erase" and config.erase_size:
                # 预览整块擦除后的实际擦除大小（与执行逻辑一致）
                size = config.erase_size
                if config.erase_block_size > 0:
                    size = ((size + config.erase_block_size - 1) //
                            config.erase_block_size * config.erase_block_size)
                desc = f"0x{config.erase_address:08X} +0x{size:X}"
            elif key == "security":
                desc = f"0x27 {config.security_level:02X}/" \
                       f"{(config.security_level + 1):02X}"
            text = f"{i + 1:2d}. {name}"
            if desc:
                text += f"  ({desc})"
            item = QTreeWidgetItem(self._step_tree, [f"{_PENDING} {text}"])
            # 记录无状态标记的基础文本，供状态刷新时重组
            item.setData(0, Qt.ItemDataRole.UserRole, text)
            item.setToolTip(0, STEP_REGISTRY[key][1] or name)
            item.setForeground(0, QColor(_STEP_COLORS[_PENDING]))
            self._step_items.append(item)
        self._current_step = -1
        self._progress_bar.setValue(0)

    def _step_index(self, key: str) -> int:
        return self._step_keys.index(key) if key in self._step_keys else -1

    def _set_step(self, idx: int, mark: str, detail: str = ""):
        if not 0 <= idx < len(self._step_items):
            return
        item = self._step_items[idx]
        base = item.data(0, Qt.ItemDataRole.UserRole)
        text = f"{mark} {base}"
        if detail:
            text += f"  [{detail}]"
        item.setText(0, text)
        item.setForeground(0, QColor(_STEP_COLORS.get(mark, "#888888")))

    def _mark_steps_before(self, idx: int):
        """将idx之前的步骤标记为完成（列表已按配置裁剪，无需处理跳过项）"""
        for i in range(idx):
            self._set_step(i, _OK)

    def _reset_step_marks(self):
        for item in self._step_items:
            item.setText(0, f"{_PENDING} {item.data(0, Qt.ItemDataRole.UserRole)}")
            item.setForeground(0, QColor(_STEP_COLORS[_PENDING]))
        self._current_step = -1
        self._progress_bar.setValue(0)

    # ---------------- 刷写控制 ----------------

    def _browse_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择刷写文件", "",
            "刷写文件 (*.bin *.s19 *.s28 *.s37 *.hex);;All Files (*)")
        if filepath:
            self._file_edit.setText(filepath)
            self._parse_file_info(filepath, self._addr_edit, "应用文件")

    def _browse_driver(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择刷写驱动文件 (flash driver)", "",
            "刷写文件 (*.bin *.s19 *.s28 *.s37 *.hex);;All Files (*)")
        if filepath:
            self._drv_edit.setText(filepath)
            self._parse_file_info(filepath, self._drv_addr_edit, "驱动文件")

    def _start_flash(self):
        config = self._build_config()
        if not config.file_path:
            self._log("请选择刷写文件")
            return
        try:
            int(self._addr_edit.text().strip().replace("0x", ""), 16)
        except ValueError:
            self._log(f"目标地址格式错误: {self._addr_edit.text()}")
            return
        if config.write_fingerprint and not config.fingerprint_data:
            self._log("已启用写入指纹，请填写指纹数据(HEX)")
            return

        if config.security_level > 0 and self._key_generator is None:
            self._log(f"警告: 未加载Level {config.security_level}安全算法，"
                      f"安全访问步骤将失败（安全面板加载算法后重试）")

        self._flash_manager.config = config
        # 回调仅做信号发射（线程安全），控件更新在GUI线程执行
        self._flash_manager.set_callbacks(
            progress_cb=self._progress_sig.emit,
            state_cb=self._state_sig.emit)

        # 刷写报告所需的起止信息（终态时自动生成）
        self._flash_start_ts = time.time()
        self._flash_file_path = config.file_path

        # 以开始刷写时的配置锁定步骤列表
        self._rebuild_steps()
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._log(f"开始刷写 [{self._ecu_name}]: {config.file_path}")

        self._flash_manager.start_flash(config)

    def _stop_flash(self):
        # 互斥: 停止请求发出后立即禁用自身防重复点击，
        # 待刷写线程到步骤边界确认取消（终态回调）后再恢复开始按钮
        self._stop_btn.setEnabled(False)
        self._flash_manager.stop_flash()
        self._log("正在停止刷写...（当前步骤结束后停止）")

    def _on_progress(self, progress):
        self._progress_bar.setValue(int(progress.percentage))
        self._rate_label.setText(f"速率: {progress.transfer_rate:.2f} KB/s")
        remaining = progress.remaining_time
        if remaining > 0:
            self._time_label.setText(f"剩余: {remaining:.0f}s")
        # 传输步骤显示块进度（应用/驱动分别定位到对应步骤行）
        if progress.total_blocks:
            idx = self._step_index("transfer")
            self._set_step(
                idx, _RUNNING, f"{progress.current_block}/{progress.total_blocks}")

    def _on_state_changed(self, state, error_msg, key=""):
        state_names = {
            FlashState.IDLE: "空闲",
            FlashState.PRECHECKING: "前置检查...",
            FlashState.PREPARE: "刷前准备...",
            FlashState.SESSION: "切换编程会话...",
            FlashState.SECURITY: "安全访问...",
            FlashState.DRIVER_DOWNLOAD: "下载刷写驱动...",
            FlashState.FINGERPRINT: "写入指纹...",
            FlashState.ERASING: "擦除中...",
            FlashState.REQUESTING_DOWNLOAD: "请求下载...",
            FlashState.TRANSFERRING: "传输中...",
            FlashState.EXITING_TRANSFER: "退出传输...",
            FlashState.CHECK_INTEGRITY: "完整性校验...",
            FlashState.CHECK_DEPEND: "依赖性校验...",
            FlashState.RESETTING: "复位中...",
            FlashState.POST: "刷后恢复...",
            FlashState.COMPLETED: "刷写完成",
            FlashState.FAILED: "刷写失败",
            FlashState.CANCELLED: "已取消",
        }
        # 状态栏优先显示具体步骤名
        if key and key in STEP_REGISTRY:
            self._status_label.setText(f"状态: {STEP_REGISTRY[key][0]}...")
        else:
            self._status_label.setText(
                f"状态: {state_names.get(state, '未知')}")

        last = len(self._step_items) - 1
        # 终态优先：完成时全部标记成功（含结果步），避免key分支把结果步停在"进行中"
        if state == FlashState.COMPLETED:
            self._mark_steps_before(last)
            self._set_step(last, _OK)
        elif state == FlashState.FAILED:
            idx = self._step_index(key) if key else self._current_step
            if idx >= 0:
                self._set_step(idx, _FAIL)
            self._set_step(last, _FAIL, error_msg or "")
        elif state == FlashState.CANCELLED:
            idx = self._step_index(key) if key else self._current_step
            if idx >= 0:
                self._set_step(idx, _SKIP, "已取消")
            self._set_step(last, _SKIP, "已取消")
        elif key and key in self._step_keys:
            idx = self._step_index(key)
            self._mark_steps_before(idx)
            self._set_step(idx, _RUNNING)
            self._current_step = idx

        if state in (FlashState.COMPLETED, FlashState.FAILED, FlashState.CANCELLED):
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._log(f"刷写结束: {state_names.get(state, '未知')}")
            if error_msg:
                self._log(f"错误: {error_msg}")
            # 刷写终态自动生成报告到项目reports/（报告中心可识别）
            self._save_flash_report(state_names.get(state, "未知"), error_msg)

    def _save_flash_report(self, result: str, error_msg: str):
        """刷写结束后自动生成HTML报告（异常不影响主流程）"""
        try:
            from src.business.report_generator import ReportGenerator
            from src.utils.paths import get_project_root
            steps = [item.text(0) for item in self._step_items]
            start_text = (time.strftime("%H:%M:%S", time.localtime(self._flash_start_ts))
                          if self._flash_start_ts else "")
            gen = ReportGenerator(os.path.join(get_project_root(), "reports"))
            path = gen.generate_flash_report(
                self._ecu_name, self._flash_file_path, steps, result,
                error_msg=error_msg or "",
                start_time=start_text,
                end_time=time.strftime("%H:%M:%S"))
            self._log(f"刷写报告已生成: {path}")
        except Exception as e:
            self._log(f"刷写报告生成失败(不影响刷写结果): {e}")

    def _log(self, msg: str):
        import time
        ts = time.strftime("%H:%M:%S")
        self._log_text.append(f"[{ts}] {msg}")
