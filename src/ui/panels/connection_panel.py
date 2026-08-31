"""CAN连接配置面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QComboBox, QLineEdit, QPushButton,
                              QFormLayout, QSpinBox, QMessageBox)
from PyQt6.QtCore import pyqtSignal, Qt
from src.can_layer.can_factory import CanFactory
from src.utils.config_manager import get_config_manager

# 接口类型: 配置字符串 <-> 下拉框索引
_TYPE_TO_INDEX = {"virtual": 0, "pcan": 1, "vector": 2}
_INDEX_TO_TYPE = {0: "virtual", 1: "pcan", 2: "vector"}


class ConnectionPanel(QWidget):
    """CAN连接配置面板"""

    connection_changed = pyqtSignal(bool)  # 连接状态变化信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self._can_interface = None
        self._connected = False
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 接口配置组
        config_group = QGroupBox("CAN接口配置")
        form = QFormLayout()

        self._interface_type = QComboBox()
        self._interface_type.addItems(["Virtual (虚拟CAN)", "PCAN", "Vector"])
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

        self._bitrate_combo = QComboBox()
        self._bitrate_combo.addItems(["500000", "250000", "125000", "1000000"])
        self._bitrate_combo.setEditable(True)
        form.addRow("波特率:", self._bitrate_combo)

        config_group.setLayout(form)
        layout.addWidget(config_group)

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
            cfg.save_config()
        except Exception:
            pass

    def _on_type_changed(self, index):
        """接口类型切换时更新默认通道和扫描按钮"""
        # 通道默认值
        defaults = ["Virtual_0", "PCAN_USBBUS1", "0"]
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
        idx = self._channel_combo.findText(default)
        if idx >= 0:
            self._channel_combo.setCurrentIndex(idx)
        else:
            self._channel_combo.setCurrentText(default)

        # 扫描按钮仅对真实硬件显示
        self._scan_btn.setVisible(index > 0)

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
            type_map = {0: "virtual", 1: "pcan", 2: "vector"}
            iface_type = type_map.get(self._interface_type.currentIndex(), "virtual")

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
            if self._can_interface.connect(config):
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
                error_msg = "无法建立CAN连接"
                if hasattr(self._can_interface, 'last_error'):
                    detail = self._can_interface.last_error
                    if detail:
                        error_msg = f"连接失败:\n{detail}"
                QMessageBox.warning(self, "连接失败", error_msg)

        except Exception as e:
            QMessageBox.critical(self, "连接错误", f"连接失败:\n{e}")

    def _on_disconnect(self):
        if self._can_interface:
            self._can_interface.disconnect()
        self._connected = False
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._status_label.setText("状态: 未连接")
        self._status_label.setStyleSheet("color: #999; padding: 5px;")
        self.connection_changed.emit(False)

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
