"""通信连接配置面板（CAN / DoIP）"""

import threading
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QComboBox, QLineEdit, QPushButton,
                              QFormLayout, QSpinBox, QMessageBox, QCheckBox,
                              QTextEdit, QScrollArea)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer
from src.can_layer.can_factory import CanFactory
from src.models.can_message import CanMessage
from src.utils.config_manager import get_config_manager

# 接口类型: 配置字符串 <-> 下拉框索引
_TYPE_TO_INDEX = {"virtual": 0, "pcan": 1, "vector": 2, "doip": 3}
_INDEX_TO_TYPE = {0: "virtual", 1: "pcan", 2: "vector", 3: "doip"}


class ConnectionPanel(QWidget):
    """通信连接配置面板（CAN/DoIP接口选择、地址配置与连接控制）"""

    connection_changed = pyqtSignal(bool)  # 连接状态变化信号
    _auto_wake_done = pyqtSignal()          # 连接时自动唤醒完成（后台线程回UI线程）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._can_interface = None
        self._connected = False
        self._virtual_doip_ecu = None  # 本地虚拟DoIP ECU（勾选回环时启动）
        self._init_ui()

    def _init_ui(self):
        # 外层滚动区: 小窗口/低分辨率下配置项纵向滚动不再被截断。
        # 工具中心左列是固定窄列（~380px），只允许纵向滚动:
        # 横向滚动会把行尾控件（如通道行的扫描按钮）挤出初始可视区
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)

        # 接口配置组
        config_group = QGroupBox("通信接口配置")
        form = QFormLayout()

        self._interface_type = QComboBox()
        self._interface_type.addItems(
            ["Virtual (虚拟CAN)", "PCAN", "Vector", "DoIP (Ethernet)"])
        self._interface_type.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("接口类型:", self._interface_type)

        # 通道选择：下拉框（可编辑）+ 扫描按钮
        channel_row = QHBoxLayout()
        self._channel_combo = QComboBox()
        self._channel_combo.setEditable(True)
        # 窄列下与扫描按钮同排（120+64+间距<可用列宽），
        # 过大的最小宽会把扫描按钮顶出可视区
        self._channel_combo.setMinimumWidth(120)
        self._channel_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        channel_row.addWidget(self._channel_combo, 1)

        self._scan_btn = QPushButton("扫描")
        self._scan_btn.setMinimumWidth(64)
        self._scan_btn.setToolTip("扫描可用通道")
        self._scan_btn.clicked.connect(self._on_scan_channels)
        self._scan_btn.setVisible(False)  # 仅PCAN/Vector时显示
        channel_row.addWidget(self._scan_btn)

        channel_widget = QWidget()
        channel_widget.setLayout(channel_row)
        form.addRow("通道:", channel_widget)
        self._channel_label = form.labelForField(channel_widget)

        self._bitrate_combo = QComboBox()
        self._bitrate_combo.addItems(["500000", "250000", "125000", "1000000"])
        self._bitrate_combo.setEditable(True)
        form.addRow("波特率:", self._bitrate_combo)
        self._bitrate_label = form.labelForField(self._bitrate_combo)

        config_group.setLayout(form)
        layout.addWidget(config_group)

        # DoIP配置组（仅接口类型为DoIP时显示）
        doip_group = QGroupBox("DoIP配置")
        doip_form = QFormLayout()

        ip_row = QHBoxLayout()
        self._doip_ip = QLineEdit("127.0.0.1")
        self._doip_ip.setMinimumWidth(100)
        ip_row.addWidget(self._doip_ip, 1)
        self._doip_scan_btn = QPushButton("发现ECU")
        self._doip_scan_btn.setToolTip("UDP广播搜索局域网内的DoIP节点")
        self._doip_scan_btn.clicked.connect(self._on_doip_discover)
        ip_row.addWidget(self._doip_scan_btn)
        ip_widget = QWidget()
        ip_widget.setLayout(ip_row)
        doip_form.addRow("ECU IP地址:", ip_widget)

        self._doip_port = QSpinBox()
        self._doip_port.setRange(1, 65535)
        self._doip_port.setValue(13400)
        doip_form.addRow("TCP端口:", self._doip_port)

        self._doip_tester_addr = QSpinBox()
        self._doip_tester_addr.setRange(0, 0xFFFF)
        self._doip_tester_addr.setDisplayIntegerBase(16)
        self._doip_tester_addr.setPrefix("0x")
        self._doip_tester_addr.setValue(0x0E80)
        doip_form.addRow("Tester逻辑地址:", self._doip_tester_addr)

        self._doip_ecu_addr = QSpinBox()
        self._doip_ecu_addr.setRange(0, 0xFFFF)
        self._doip_ecu_addr.setDisplayIntegerBase(16)
        self._doip_ecu_addr.setPrefix("0x")
        self._doip_ecu_addr.setValue(0x1000)
        doip_form.addRow("ECU逻辑地址:", self._doip_ecu_addr)

        # 本地虚拟ECU回环（无真实ECU时的本机测试入口）
        self._doip_virtual_check = QCheckBox("本地虚拟ECU（回环模拟）")
        self._doip_virtual_check.setToolTip(
            "连接时在本机TCP端口启动虚拟DoIP ECU（复用CAN虚拟ECU模拟器），\n"
            "无需真实ECU即可测试完整DoIP诊断链路；断开时自动停止")
        doip_form.addRow("", self._doip_virtual_check)

        doip_group.setLayout(doip_form)
        doip_group.setVisible(False)
        layout.addWidget(doip_group)
        self._doip_group = doip_group

        # UDS地址配置组
        addr_group = QGroupBox("UDS地址配置")
        addr_form = QFormLayout()

        # TX/RX 并排一行: 窄列布局减少纵向占位，前缀区分方向
        addr_row = QHBoxLayout()
        self._tx_id = QSpinBox()
        self._tx_id.setRange(0, 0x7FF)
        self._tx_id.setDisplayIntegerBase(16)
        self._tx_id.setPrefix("请求 0x")
        self._tx_id.setValue(0x7E0)
        addr_row.addWidget(self._tx_id, 1)
        self._rx_id = QSpinBox()
        self._rx_id.setRange(0, 0x7FF)
        self._rx_id.setDisplayIntegerBase(16)
        self._rx_id.setPrefix("响应 0x")
        self._rx_id.setValue(0x7E8)
        addr_row.addWidget(self._rx_id, 1)
        addr_form.addRow("诊断地址:", addr_row)

        addr_group.setLayout(addr_form)
        layout.addWidget(addr_group)
        self._addr_group = addr_group

        # 唤醒报文配置组（仅CAN模式显示）
        wake_group = QGroupBox("ECU唤醒报文")
        wake_group.setToolTip("部分ECU（如DEM）在休眠状态下需要先发送唤醒报文才能进行诊断通信")
        wake_form = QFormLayout()

        # CAN ID 与周期并排一行（窄列紧凑布局）
        id_row = QHBoxLayout()
        self._wake_can_id = QSpinBox()
        self._wake_can_id.setRange(0, 0x7FF)
        self._wake_can_id.setDisplayIntegerBase(16)
        self._wake_can_id.setPrefix("0x")
        self._wake_can_id.setValue(0x160)
        self._wake_can_id.setToolTip("唤醒报文的CAN ID（16进制）")
        id_row.addWidget(self._wake_can_id, 1)
        id_row.addWidget(QLabel("周期:"))
        self._wake_period = QSpinBox()
        self._wake_period.setRange(10, 10000)
        self._wake_period.setValue(100)
        self._wake_period.setSuffix(" ms")
        self._wake_period.setToolTip("唤醒报文发送周期（毫秒）")
        id_row.addWidget(self._wake_period)
        wake_form.addRow("CAN ID:", id_row)

        self._wake_data = QLineEdit("FF FF FF FF FF FF FF FF")
        self._wake_data.setPlaceholderText("如: FF FF FF FF FF FF FF FF")
        self._wake_data.setStyleSheet("font-family: 'Consolas', monospace;")
        self._wake_data.setToolTip("唤醒报文数据（HEX格式，最多8字节）")
        wake_form.addRow("数据(HEX):", self._wake_data)

        # 控制行: 自动唤醒开关 + 启动/停止
        wake_ctrl_row = QHBoxLayout()

        # 连接时自动唤醒（市场通行做法: 总线连接后先周期发唤醒帧再发自检，
        # 避免ECU休眠时自检必失败; CANoe/CANape同款"wake-up on connect"）
        self._wake_auto_check = QCheckBox("连接时自动唤醒")
        self._wake_auto_check.setToolTip(
            "勾选后连接流程变为: 打开总线 → 周期发送唤醒报文约2秒 → 再发诊断自检。\n"
            "适用于休眠ECU（如DEM）; 已醒的ECU多收几帧唤醒报文无副作用")
        wake_ctrl_row.addWidget(self._wake_auto_check)

        self._wake_start_btn = QPushButton("启动")
        self._wake_start_btn.setObjectName("btn_wake_start")
        self._wake_start_btn.setToolTip("开始周期发送唤醒报文")
        self._wake_start_btn.clicked.connect(self._on_start_wake)
        self._wake_start_btn.setFixedWidth(56)
        wake_ctrl_row.addWidget(self._wake_start_btn)

        self._wake_stop_btn = QPushButton("停止")
        self._wake_stop_btn.setObjectName("btn_wake_stop")
        self._wake_stop_btn.setEnabled(False)
        self._wake_stop_btn.setToolTip("停止发送唤醒报文")
        self._wake_stop_btn.clicked.connect(self._on_stop_wake)
        self._wake_stop_btn.setFixedWidth(56)
        wake_ctrl_row.addWidget(self._wake_stop_btn)

        wake_ctrl_row.addStretch()

        wake_ctrl_widget = QWidget()
        wake_ctrl_widget.setLayout(wake_ctrl_row)
        wake_form.addRow("", wake_ctrl_widget)

        # 唤醒日志
        self._wake_log = QTextEdit()
        self._wake_log.setReadOnly(True)
        self._wake_log.setMinimumHeight(72)
        self._wake_log.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 11px;")
        wake_form.addRow("日志:", self._wake_log)

        wake_group.setLayout(wake_form)
        layout.addWidget(wake_group)
        self._wake_group = wake_group

        # 周期发送定时器
        self._wake_timer = QTimer()
        self._wake_timer.timeout.connect(self._send_wake_frame)
        self._wake_running = False
        # 连接时自动唤醒完成 → 通知上层自检（后台线程经信号回UI线程）
        self._auto_wake_done.connect(self._on_auto_wake_done)
        self._auto_wake_thread = None

        # 连接按钮（与工具栏同一套黄/红配色与互补置灰规则，
        # objectName 挂全局QSS；不再本地setStyleSheet，否则会覆盖主题样式）
        btn_layout = QHBoxLayout()
        self._connect_btn = QPushButton("连接")
        self._connect_btn.setObjectName("btn_connect")
        self._connect_btn.clicked.connect(self._on_connect)
        btn_layout.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("断开")
        self._disconnect_btn.setObjectName("btn_disconnect")
        self._disconnect_btn.setEnabled(False)  # 未连接时置灰不可点（与连接钮互补）
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        btn_layout.addWidget(self._disconnect_btn)
        layout.addLayout(btn_layout)

        # 状态显示
        self._status_label = QLabel("状态: 未连接")
        self._status_label.setStyleSheet("color: #999; padding: 5px;")
        layout.addWidget(self._status_label)

        layout.addStretch()

        # 初始化默认通道，然后从配置恢复上次的连接参数
        self._on_type_changed(0)
        self.load_saved_config()

        # 收尾: 内容装进滚动区
        scroll.setWidget(content)
        outer.addWidget(scroll)

    @staticmethod
    def _parse_id(value, default: int) -> int:
        """解析配置中的CAN ID（兼容int和'0x7E0'字符串）"""
        try:
            if isinstance(value, str):
                return int(value, 16) if value.lower().startswith("0x") else int(value)
            return int(value)
        except (ValueError, TypeError):
            return default

    def load_saved_config(self):
        """从配置管理器恢复上次的连接参数（启动时自动调用）"""
        cfg = get_config_manager()

        itype = cfg.get("can.interface_type", "virtual")
        index = _TYPE_TO_INDEX.get(itype, 0) if isinstance(itype, str) else 0
        self._interface_type.setCurrentIndex(index)  # 触发_on_type_changed重置通道选项

        # 恢复通道（切换接口类型后选项已重建，优先按userData匹配）
        channel = cfg.get("can.channel", "")
        if channel:
            idx = self._channel_combo.findData(channel)
            if idx >= 0:
                self._channel_combo.setCurrentIndex(idx)
            else:
                self._channel_combo.setCurrentText(str(channel))

        # 恢复波特率
        bitrate = cfg.get("can.bitrate", 500000)
        bitrate_text = str(bitrate)
        if self._bitrate_combo.findText(bitrate_text) < 0:
            self._bitrate_combo.addItem(bitrate_text)
        self._bitrate_combo.setCurrentText(bitrate_text)

        # 恢复UDS地址
        self._tx_id.setValue(self._parse_id(cfg.get("can.req_id"), 0x7E0))
        self._rx_id.setValue(self._parse_id(cfg.get("can.resp_id"), 0x7E8))

        # 恢复DoIP参数
        self._doip_ip.setText(str(cfg.get("doip.ip", "127.0.0.1")))
        try:
            self._doip_port.setValue(int(cfg.get("doip.port", 13400)))
        except (ValueError, TypeError):
            pass
        self._doip_tester_addr.setValue(
            self._parse_id(cfg.get("doip.tester_addr"), 0x0E80))
        self._doip_ecu_addr.setValue(
            self._parse_id(cfg.get("doip.ecu_addr"), 0x1000))

        # 恢复唤醒报文配置
        self._wake_can_id.setValue(
            self._parse_id(cfg.get("wake.can_id"), 0x160))
        wake_data = cfg.get("wake.data", "FF FF FF FF FF FF FF FF")
        if isinstance(wake_data, str):
            self._wake_data.setText(wake_data)
        else:
            self._wake_data.setText("FF FF FF FF FF FF FF FF")
        try:
            self._wake_period.setValue(int(cfg.get("wake.period", 100)))
        except (ValueError, TypeError):
            pass
        # 恢复“连接时自动唤醒”开关
        self._wake_auto_check.setChecked(bool(cfg.get("wake.auto", False)))

    def save_to_config(self):
        """将当前连接参数写入配置并持久化到磁盘"""
        try:
            cfg = get_config_manager()
            cfg.set("can.interface_type",
                    _INDEX_TO_TYPE.get(self._interface_type.currentIndex(), "virtual"))
            channel = self._channel_combo.currentData()
            if channel is None:
                channel = self._channel_combo.currentText().strip()
            cfg.set("can.channel", channel)
            try:
                cfg.set("can.bitrate", int(self._bitrate_combo.currentText()))
            except ValueError:
                pass
            cfg.set("can.req_id", self._tx_id.value())
            cfg.set("can.resp_id", self._rx_id.value())
            cfg.set("doip.ip", self._doip_ip.text().strip() or "127.0.0.1")
            cfg.set("doip.port", self._doip_port.value())
            cfg.set("doip.tester_addr", self._doip_tester_addr.value())
            cfg.set("doip.ecu_addr", self._doip_ecu_addr.value())
            # 保存唤醒报文配置
            cfg.set("wake.can_id", self._wake_can_id.value())
            cfg.set("wake.data", self._wake_data.text().strip() or "FF FF FF FF FF FF FF FF")
            cfg.set("wake.period", self._wake_period.value())
            cfg.set("wake.auto", self._wake_auto_check.isChecked())
            cfg.save_config()
        except Exception:
            pass

    def _on_type_changed(self, index):
        """接口类型切换时更新默认通道和扫描按钮"""
        is_doip = (index == 3)

        # 通道默认值
        defaults = ["Virtual_0", "PCAN_USBBUS1", "0", ""]
        default = defaults[index] if index < len(defaults) else "Virtual_0"

        # 填充下拉框选项
        self._channel_combo.clear()
        if index == 0:  # Virtual
            self._channel_combo.addItems(["Virtual_0", "Virtual_1"])
        elif index == 1:  # PCAN
            from src.can_layer.pcan_interface import PcanInterface
            self._channel_combo.addItems(PcanInterface.DEFAULT_CHANNELS)
        elif index == 2:  # Vector
            self._channel_combo.addItems(["0", "1", "2", "3"])

        # 设置当前值
        if not is_doip:
            idx = self._channel_combo.findText(default)
            if idx >= 0:
                self._channel_combo.setCurrentIndex(idx)
            else:
                self._channel_combo.setCurrentText(default)

        # 扫描按钮仅对真实硬件显示
        self._scan_btn.setVisible(index in (1, 2))

        # DoIP时隐藏CAN专属配置（通道/波特率/CAN地址/唤醒报文），显示DoIP配置组
        self._channel_label.setVisible(not is_doip)
        self._channel_combo.parentWidget().setVisible(not is_doip)
        self._bitrate_label.setVisible(not is_doip)
        self._bitrate_combo.setVisible(not is_doip)
        self._addr_group.setVisible(not is_doip)
        self._wake_group.setVisible(not is_doip)
        self._doip_group.setVisible(is_doip)

    def _on_doip_discover(self):
        """UDP广播发现局域网内的DoIP节点"""
        from src.protocol.doip_layer import DoipTransportLayer
        self._doip_scan_btn.setEnabled(False)
        self._doip_scan_btn.setText("...")
        try:
            nodes = DoipTransportLayer.discover(timeout=1.0)
            if nodes:
                node = nodes[0]
                self._doip_ip.setText(node["ip"])
                if node["logical_addr"]:
                    self._doip_ecu_addr.setValue(node["logical_addr"])
                self._status_label.setText(
                    f"状态: 发现 {len(nodes)} 个DoIP节点，"
                    f"已填入 {node['ip']} (0x{node['logical_addr']:04X})")
            else:
                self._status_label.setText("状态: 未发现DoIP节点")
                QMessageBox.information(
                    self, "发现结果",
                    "未发现DoIP节点。\n请检查:\n"
                    "1. ECU与本机是否在同一网段\n"
                    "2. UDP 13400端口是否被防火墙拦截")
        except Exception as e:
            QMessageBox.warning(self, "发现失败", f"DoIP节点发现异常:\n{e}")
        finally:
            self._doip_scan_btn.setEnabled(True)
            self._doip_scan_btn.setText("发现ECU")

    def _on_scan_channels(self):
        """扫描可用通道"""
        iface_type = self._interface_type.currentIndex()
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("...")

        try:
            if iface_type == 1:  # PCAN
                from src.can_layer.pcan_interface import PcanInterface
                channels = PcanInterface.detect_channels()
                if channels:
                    self._channel_combo.clear()
                    for ch in channels:
                        label = ch["channel"]
                        if ch.get("device_name"):
                            label += f"  ({ch['device_name']})"
                        self._channel_combo.addItem(label, ch["channel"])
                    self._channel_combo.setCurrentIndex(0)
                    self._status_label.setText(
                        f"状态: 发现 {len(channels)} 个PCAN通道")
                else:
                    QMessageBox.information(
                        self, "扫描结果",
                        "未发现可用的PCAN设备。\n请检查:\n"
                        "1. PCAN硬件是否已连接\n"
                        "2. PCAN驱动是否已安装")
            elif iface_type == 2:  # Vector
                try:
                    import can
                    configs = can.detect_available_configs(interfaces=["vector"])
                    if configs:
                        self._channel_combo.clear()
                        for cfg in configs:
                            ch = str(cfg.get("channel", ""))
                            app = cfg.get("app_name", "")
                            label = f"ch{ch}" + (f" ({app})" if app else "")
                            self._channel_combo.addItem(label, ch)
                        self._channel_combo.setCurrentIndex(0)
                    else:
                        QMessageBox.information(
                            self, "扫描结果", "未发现可用的Vector设备。")
                except Exception as e:
                    QMessageBox.warning(self, "扫描失败", f"Vector扫描异常:\n{e}")
        except Exception as e:
            QMessageBox.warning(self, "扫描失败", f"扫描异常:\n{e}")
        finally:
            self._scan_btn.setEnabled(True)
            self._scan_btn.setText("扫描")

    def _on_connect(self):
        try:
            type_map = {0: "virtual", 1: "pcan", 2: "vector", 3: "doip"}
            iface_type = type_map.get(self._interface_type.currentIndex(), "virtual")

            if iface_type == "doip":
                # DoIP: 建立TCP连接并完成路由激活
                from src.protocol.doip_layer import DoipTransportLayer
                ip = self._doip_ip.text().strip() or "127.0.0.1"
                port = self._doip_port.value()

                # 勾选本地虚拟ECU时先启动回环模拟器（复用虚拟CAN的ECU仿真）
                if self._doip_virtual_check.isChecked():
                    if not self._start_virtual_doip_ecu(port):
                        return
                elif ip in ("127.0.0.1", "localhost"):
                    # 本机无监听时的友好提示
                    QMessageBox.information(
                        self, "提示",
                        "本机 "+ ip + ":" + str(port) + " 无DoIP服务。\n\n"
                        "如需无硬件测试，请勾选\"本地虚拟ECU（回环模拟）\"。")
                    return

                self._can_interface = DoipTransportLayer(
                    target_ip=ip, tcp_port=port,
                    tester_address=self._doip_tester_addr.value(),
                    ecu_address=self._doip_ecu_addr.value(),
                    timeout=3.0)
                ok = self._can_interface.connect()
                error_detail = self._can_interface.last_error
            else:
                # 获取通道值：下拉框有userData则用userData，否则用文本
                channel = self._channel_combo.currentData()
                if channel is None:
                    channel = self._channel_combo.currentText().strip()

                config = {
                    "channel": channel,
                    "bitrate": int(self._bitrate_combo.currentText()),
                    "req_id": self._tx_id.value(),
                    "resp_id": self._rx_id.value(),
                }
                self._can_interface = CanFactory.create(iface_type)
                ok = self._can_interface.connect(config)
                error_detail = getattr(self._can_interface, 'last_error', None)

            if ok:
                self._connected = True
                self._connect_btn.setEnabled(False)
                self._disconnect_btn.setEnabled(True)
                # 连接成功后自动保存配置，下次启动时自动恢复
                self.save_to_config()
                if (iface_type != "doip" and self._wake_auto_check.isChecked()
                        and self._parse_wake_params(log_err=True)[0]):
                    # 勾选"连接时自动唤醒": 总线已打开，先后台周期发唤醒帧
                    # 约2s让休眠ECU起床，完成后再通知上层发自检（3E 00）
                    # ——否则休眠ECU场景下自检必失败造成"连接失败"误判
                    self._status_label.setText(
                        "状态: 已连接 - 正在唤醒ECU（约2s）...")
                    self._status_label.setStyleSheet(
                        "color: #FF9800; padding: 5px;")
                    self._start_auto_wake()
                else:
                    self._status_label.setText(
                        f"状态: 已连接 - {self._can_interface.channel_info}")
                    self._status_label.setStyleSheet(
                        "color: #4CAF50; padding: 5px;")
                    self.connection_changed.emit(True)
            else:
                # 获取详细错误信息
                self._stop_virtual_doip_ecu()
                error_msg = "无法建立连接"
                if error_detail:
                    error_msg = f"连接失败:\n{error_detail}"
                QMessageBox.warning(self, "连接失败", error_msg)

        except Exception as e:
            self._stop_virtual_doip_ecu()
            QMessageBox.critical(self, "连接错误", f"连接失败:\n{e}")

    def _parse_wake_params(self, log_err: bool = False):
        """解析唤醒参数，返回 (ok, can_id, data_bytes, period_ms)"""
        can_id = self._wake_can_id.value()
        data_hex = self._wake_data.text().strip().replace(" ", "")
        if not data_hex:
            data_hex = "FFFFFFFFFFFFFFFF"
        try:
            data = bytes.fromhex(data_hex)
            if len(data) > 8:
                if log_err:
                    self._append_wake_log("错误: 数据长度超过8字节", "#F44336")
                return False, 0, b"", 0
            # 填充到8字节（CAN帧标准长度）
            data = data + bytes(8 - len(data))
        except ValueError:
            if log_err:
                self._append_wake_log("错误: HEX格式无效", "#F44336")
            return False, 0, b"", 0
        return True, can_id, data, self._wake_period.value()

    _AUTO_WAKE_DURATION_S = 2.0

    def _start_auto_wake(self):
        """连接时自动唤醒: 后台线程周期发唤醒帧约2秒，
        完成后经 _auto_wake_done 信号回UI线程触发上层诊断自检"""
        ok, can_id, data, period = self._parse_wake_params(log_err=True)
        if not ok:
            self._on_auto_wake_done()
            return
        iface = self._can_interface
        duration = self._AUTO_WAKE_DURATION_S
        self._append_wake_log(
            f">> 连接自动唤醒: CAN ID=0x{can_id:03X} "
            f"数据={data.hex(' ').upper()} 周期={period}ms "
            f"持续约{duration:.0f}s", "#2196F3")

        def _run():
            import time as _t
            sent = 0
            end = _t.monotonic() + duration
            while _t.monotonic() < end and self._connected:
                try:
                    if iface.send(CanMessage(
                            can_id=can_id, data=data, dlc=8)):
                        sent += 1
                except Exception:
                    break
                _t.sleep(period / 1000.0)
            self._auto_wake_sent = sent
            self._auto_wake_done.emit()   # 跨线程信号→UI线程

        self._auto_wake_thread = threading.Thread(
            target=_run, daemon=True, name="auto-wake")
        self._auto_wake_thread.start()

    def _on_auto_wake_done(self):
        """自动唤醒完成: 恢复状态并通知上层执行诊断自检"""
        self._auto_wake_thread = None
        if not self._connected or not self._can_interface:
            return   # 唤醒期间已断开，不再触发自检
        sent = getattr(self, "_auto_wake_sent", 0)
        self._append_wake_log(
            f"<< 自动唤醒完成（发送{sent}帧），开始诊断自检", "#4CAF50")
        self._status_label.setText(
            f"状态: 已连接 - {self._can_interface.channel_info}")
        self._status_label.setStyleSheet("color: #4CAF50; padding: 5px;")
        self.connection_changed.emit(True)

    def _start_virtual_doip_ecu(self, port: int) -> bool:
        """启动本地虚拟DoIP ECU（回环模拟）"""
        from src.protocol.doip_layer import VirtualDoipEcu
        self._stop_virtual_doip_ecu()
        ecu = VirtualDoipEcu(
            logical_address=self._doip_ecu_addr.value(), host="127.0.0.1")
        try:
            ecu.start(tcp_port=port)
        except OSError as e:
            QMessageBox.warning(
                self, "虚拟ECU启动失败",
                f"无法在本机端口 {port} 启动虚拟DoIP ECU:\n{e}\n\n"
                "端口可能已被占用，请更换TCP端口。")
            return False
        self._virtual_doip_ecu = ecu
        return True

    def _stop_virtual_doip_ecu(self):
        """停止本地虚拟DoIP ECU（断开时自动回收）"""
        if self._virtual_doip_ecu is not None:
            self._virtual_doip_ecu.stop()
            self._virtual_doip_ecu = None

    def _on_disconnect(self):
        # 断开时自动停止唤醒报文发送
        if self._wake_running:
            self._on_stop_wake()
        self._connected = False
        # 等待自动唤醒线程退出（标志已清，至多再发一帧），
        # 避免与总线关闭并发发送（PCAN新开/关闭交错会坏驱动状态）
        th = self._auto_wake_thread
        if th is not None and th.is_alive():
            th.join(timeout=1.0)
        self._auto_wake_thread = None
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._status_label.setText("状态: 未连接")
        self._status_label.setStyleSheet("color: #999; padding: 5px;")
        # 先通知所有使用者停止(保活定时器/后台worker/报文监听)，
        # 再关闭总线——PCAN驱动在Uninitialize与并发读写的交错下
        # 可能进入异常状态，导致同进程内重连Initialize失败(0x40)
        self.connection_changed.emit(False)
        if self._can_interface:
            self._can_interface.disconnect()
        self._stop_virtual_doip_ecu()

    def set_uds_ids(self, tx_id: int, rx_id: int):
        """由外部（如项目树选中ECU）注入UDS地址"""
        self._tx_id.setValue(tx_id)
        self._rx_id.setValue(rx_id)

    @property
    def can_interface(self):
        return self._can_interface

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def tx_id(self) -> int:
        return self._tx_id.value()

    @property
    def rx_id(self) -> int:
        return self._rx_id.value()

    @property
    def channel(self) -> str:
        """当前选中的通道（优先userData）"""
        data = self._channel_combo.currentData()
        if data is None:
            data = self._channel_combo.currentText().strip()
        return str(data)

    @property
    def bitrate(self) -> int:
        try:
            return int(self._bitrate_combo.currentText())
        except ValueError:
            return 500000

    @property
    def is_doip(self) -> bool:
        """当前接口类型是否为DoIP"""
        return self._interface_type.currentIndex() == 3

    @property
    def doip_ip(self) -> str:
        return self._doip_ip.text().strip() or "127.0.0.1"

    @property
    def doip_port(self) -> int:
        return self._doip_port.value()

    def _on_start_wake(self):
        """启动周期发送唤醒报文"""
        if not self._can_interface:
            self._append_wake_log("错误: 请先连接CAN接口", "#F44336")
            return

        # 解析CAN ID
        self._wake_can_id_val = self._wake_can_id.value()

        # 解析数据
        data_hex = self._wake_data.text().strip().replace(" ", "")
        if not data_hex:
            data_hex = "FFFFFFFFFFFFFFFF"
        try:
            self._wake_data_bytes = bytes.fromhex(data_hex)
            if len(self._wake_data_bytes) > 8:
                self._append_wake_log("错误: 数据长度超过8字节", "#F44336")
                return
            # 填充到8字节（CAN帧标准长度）
            self._wake_data_bytes = self._wake_data_bytes + bytes(8 - len(self._wake_data_bytes))
        except ValueError:
            self._append_wake_log("错误: HEX格式无效", "#F44336")
            return

        period = self._wake_period.value()

        # 更新UI状态
        self._wake_start_btn.setEnabled(False)
        self._wake_stop_btn.setEnabled(True)
        self._wake_can_id.setEnabled(False)
        self._wake_data.setEnabled(False)
        self._wake_period.setEnabled(False)

        self._wake_running = True
        self._append_wake_log(
            f">> 启动周期唤醒: CAN ID=0x{self._wake_can_id_val:03X} "
            f"数据={self._wake_data_bytes.hex(' ').upper()} "
            f"周期={period}ms", "#2196F3")

        # 启动定时器
        self._wake_timer.start(period)

    def _on_stop_wake(self):
        """停止周期发送唤醒报文"""
        self._wake_timer.stop()
        self._wake_running = False

        # 恢复UI状态
        self._wake_start_btn.setEnabled(True)
        self._wake_stop_btn.setEnabled(False)
        self._wake_can_id.setEnabled(True)
        self._wake_data.setEnabled(True)
        self._wake_period.setEnabled(True)

        self._append_wake_log("<< 停止唤醒报文发送", "#FF9800")

    def _send_wake_frame(self):
        """发送单帧唤醒报文（定时器回调）"""
        if not self._can_interface or not self._wake_running:
            return

        try:
            msg = CanMessage(
                can_id=self._wake_can_id_val,
                data=self._wake_data_bytes,
                dlc=8
            )
            if self._can_interface.send(msg):
                # 静默发送，不记录每帧日志避免刷屏
                pass
        except Exception as e:
            self._append_wake_log(f"发送异常: {e}", "#F44336")
            self._on_stop_wake()

    def _append_wake_log(self, text: str, color: str = ""):
        """追加唤醒日志"""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if color:
            self._wake_log.append(
                f'<span style="color:{color}">[{ts}] {text}</span>')
        else:
            self._wake_log.append(f"[{ts}] {text}")
