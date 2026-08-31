"""DTC状态位可视化控件（符合ISO 14229状态位定义）"""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QCheckBox, QVBoxLayout, QLabel, QGroupBox
from PyQt6.QtCore import Qt


DTC_STATUS_BITS = {
    0: ("testFailed", "测试失败"),
    1: ("testFailedThisOperationCycle", "本周期测试失败"),
    2: ("pendingDtc", "待确认DTC"),
    3: ("confirmedDtc", "已确认DTC"),
    4: ("testNotCompletedSinceLastClear", "上次清除后未完成测试"),
    5: ("testFailedSinceLastClear", "上次清除后测试失败"),
    6: ("testNotCompletedThisOperationCycle", "本周期未完成测试"),
    7: ("warningIndicatorRequested", "警告指示灯请求"),
}


class DtcStatusWidget(QGroupBox):
    """DTC状态位可视化控件"""

    def __init__(self, parent=None):
        super().__init__("DTC状态位 (ISO 14229)", parent)
        self._checkboxes = []
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 状态位
        bits_layout = QHBoxLayout()
        for i in range(8):
            name, desc = DTC_STATUS_BITS[i]
            cb = QCheckBox(f"Bit{i}\n{desc}")
            cb.setEnabled(False)
            cb.setToolTip(f"Bit {i}: {name}")
            self._checkboxes.append(cb)
            bits_layout.addWidget(cb)
        layout.addLayout(bits_layout)

        # 状态描述
        self._desc_label = QLabel("无激活状态")
        self._desc_label.setWordWrap(True)
        layout.addWidget(self._desc_label)

    def set_status(self, status_byte: int):
        """设置状态字节（0-255）"""
        for i in range(8):
            self._checkboxes[i].setChecked(bool(status_byte & (1 << i)))

        active = [DTC_STATUS_BITS[i][1] for i in range(8) if status_byte & (1 << i)]
        self._desc_label.setText(", ".join(active) if active else "无激活状态")

    def clear(self):
        """清除所有状态"""
        for cb in self._checkboxes:
            cb.setChecked(False)
        self._desc_label.setText("无激活状态")
