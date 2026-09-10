"""例程控制面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QPushButton, QLineEdit,
                              QSpinBox, QFormLayout, QComboBox)
from PyQt6.QtCore import pyqtSignal


class RoutinePanel(QWidget):
    """例程控制面板"""

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

        # 例程控制参数
        param_group = QGroupBox("例程控制参数 (0x31)")
        form = QFormLayout()

        self._sub_func_combo = QComboBox()
        self._sub_func_combo.addItems([
            "0x01 - startRoutine",
            "0x02 - stopRoutine",
            "0x03 - requestRoutineResults",
        ])
        form.addRow("子功能:", self._sub_func_combo)

        self._routine_id_spin = QSpinBox()
        self._routine_id_spin.setRange(0, 0xFFFF)
        self._routine_id_spin.setDisplayIntegerBase(16)
        self._routine_id_spin.setPrefix("0x")
        self._routine_id_spin.setValue(0xFF00)
        form.addRow("例程ID:", self._routine_id_spin)

        self._data_edit = QLineEdit()
        self._data_edit.setPlaceholderText("可选数据(HEX)")
        form.addRow("数据:", self._data_edit)

        param_group.setLayout(form)
        layout.addWidget(param_group)

        # 快捷例程
        quick_group = QGroupBox("常用例程")
        quick_layout = QHBoxLayout()
        quick_routines = [
            (0xFF00, "EraseMemory"),
            (0xFF01, "CheckProgrammingDependencies"),
            (0xFF02, "EraseMirrorMemory"),
        ]
        for routine_id, name in quick_routines:
            btn = QPushButton(f"{name}\n(0x{routine_id:04X})")
            btn.clicked.connect(lambda checked, rid=routine_id: self._quick_routine(rid))
            quick_layout.addWidget(btn)
        quick_group.setLayout(quick_layout)
        layout.addWidget(quick_group)

        # 按钮
        btn_layout = QHBoxLayout()
        send_btn = QPushButton("发送例程控制")
        send_btn.clicked.connect(self._send_routine)
        btn_layout.addWidget(send_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addStretch()

    def _send_routine(self):
        if not self._uds_client:
            self._log("未连接UDS客户端", "WARNING")
            return

        sub_funcs = [0x01, 0x02, 0x03]
        sub_func = sub_funcs[self._sub_func_combo.currentIndex()]
        routine_id = self._routine_id_spin.value()

        data_hex = self._data_edit.text().strip()
        data = bytes.fromhex(data_hex.replace(" ", "")) if data_hex else b""

        resp = self._uds_client.routine_control(sub_func, routine_id, data)
        if resp and resp[0] == 0x71:
            self._log(f"例程控制 0x{routine_id:04X} 成功, 响应: {resp.hex(' ')}",
                      "SUCCESS")
        else:
            self._log(f"例程控制 0x{routine_id:04X} 失败", "ERROR")

    def _quick_routine(self, routine_id: int):
        self._routine_id_spin.setValue(routine_id)
        self._send_routine()

    def _log(self, msg: str, level: str = "INFO"):
        self.business_log.emit(msg, level)
