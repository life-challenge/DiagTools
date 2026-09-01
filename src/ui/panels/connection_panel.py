"""通信连接配置面板（CAN / DoIP）"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QComboBox, QLineEdit, QPushButton,
                              QFormLayout, QSpinBox, QMessageBox, QCheckBox)
from PyQt6.QtCore import pyqtSignal, Qt
from src.can_layer.can_factory import CanFactory
from src.utils.config_manager import get_config_manager

# 接口类型: 配置字符串 <-> 下拉框索引
_TYPE_TO_INDEX = {"virtual": 0, "pcan": 1, "vector": 2, "doip": 3}
_INDEX_TO_TYPE = {0: "virtual", 1: "pcan", 2: "vector", 3: "doip"}


class ConnectionPanel(QWidget):
    """通信连接配置面板（CAN/DoIP接口选择、地址配置与连接控制）"""

    connection_changed = pyqtSignal(bool)  # 连接状态变化信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self._can_interface = None
        self._connected = False
        self._virtual_doip_ecu = None  # 本地虚拟DoIP ECU（勾选回环时启动）
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

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
        self._channel_combo.setMinimumWidth(120)
        channel_row.addWidget(self._channel_combo, 1)

        self._scan_btn = QPushButton("扫描")
        self._scan_btn.setFixedWidth(50)
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
        self._doip_ip.setMinimumWidth(120)
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

        self._tx_id = QSpinBox()
        self._tx_id.setRange(0, 0x7FF)
        self._tx_id.setDisplayIntegerBase(16)
        self._tx_id.setPrefix("0x")
        self._tx_id.setValue(0x7E0)
        addr_form.addRow("请求地址 (TX):", self._tx_id)

        self._rx_id = QSpinBox()
        self._rx_id.setRange(0, 0x7FF)
        self._rx_id.setDisplayIntegerBase(16)
        self._rx_id.setPrefix("0x")
        self._rx_id.setValue(0x7E8)
        addr_form.addRow("响应地址 (RX):", self._rx_id)

        addr_group.setLayout(addr_form)
        layout.addWidget(addr_group)
        self._addr_group = addr_group

        # 连接按钮
        btn_layout = QHBoxLayout()
        self._connect_btn = QPushButton("连接")
        self._connect_btn.setStyleSheet("QPushButton { padding: 8px; font-weight: bold; }")
        self._connect_btn.clicked.connect(self._on_connect)
        btn_layout.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("断开")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.setStyleSheet("QPushButton { padding: 8px; }")
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

        # DoIP时隐藏CAN专属配置（通道/波特率/CAN地址），显示DoIP配置组
        self._channel_label.setVisible(not is_doip)
        self._channel_combo.parentWidget().setVisible(not is_doip)
        self._bitrate_label.setVisible(not is_doip)
        self._bitrate_combo.setVisible(not is_doip)
        self._addr_group.setVisible(not is_doip)
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
                self._status_label.setText(
                    f"状态: 已连接 - {self._can_interface.channel_info}")
                self._status_label.setStyleSheet("color: #4CAF50; padding: 5px;")
                self.connection_changed.emit(True)
                # 连接成功后自动保存配置，下次启动时自动恢复
                self.save_to_config()
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
        self._connected = False
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
