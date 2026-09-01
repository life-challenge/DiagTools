"""IO控制面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QPushButton, QLineEdit,
                              QSpinBox, QFormLayout, QComboBox)
from PyQt6.QtCore import pyqtSignal


class IoPanel(QWidget):
    """IO控制面板"""

    # 业务日志转发(msg, level): 面板不再内置日志窗口，统一由主窗口业务日志呈现
    business_log = pyqtSignal(str, str)

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        self._init_ui()

    def set_uds_client(self, client):
        self._uds_client = client

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # IO控制参数
        param_group = QGroupBox("IO控制参数 (0x2F)")
        form = QFormLayout()

        self._did_spin = QSpinBox()
        self._did_spin.setRange(0, 0xFFFF)
        self._did_spin.setDisplayIntegerBase(16)
        self._did_spin.setPrefix("0x")
        form.addRow("DID:", self._did_spin)

        self._control_combo = QComboBox()
        self._control_combo.addItems([
            "0x00 - returnControlToECU",
            "0x01 - resetToDefault",
            "0x02 - freezeCurrentState",
            "0x03 - shortTermAdjustment",
        ])
        form.addRow("控制选项:", self._control_combo)

        self._data_edit = QLineEdit()
        self._data_edit.setPlaceholderText("控制数据(HEX), 如: FF")
        form.addRow("数据:", self._data_edit)

        param_group.setLayout(form)
        layout.addWidget(param_group)

        # 按钮
        btn_layout = QHBoxLayout()
        send_btn = QPushButton("发送IO控制")
        send_btn.clicked.connect(self._send_io_control)
        btn_layout.addWidget(send_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addStretch()

    def _send_io_control(self):
        if not self._uds_client:
            self._log("未连接UDS客户端", "WARNING")
            return

        did_id = self._did_spin.value()
        control_idx = self._control_combo.currentIndex()
        control_options = [0x00, 0x01, 0x02, 0x03]
        control_option = control_options[control_idx]

        data_hex = self._data_edit.text().strip()
        data = bytes.fromhex(data_hex.replace(" ", "")) if data_hex else b""

        resp = self._uds_client.io_control(did_id, control_option, data)
        if resp and resp[0] == 0x6F:
            self._log(f"IO控制 0x{did_id:04X} 成功, 响应: {resp.hex(' ')}",
                      "SUCCESS")
        else:
            self._log(f"IO控制 0x{did_id:04X} 失败", "ERROR")

    def _log(self, msg: str, level: str = "INFO"):
        self.business_log.emit(msg, level)
