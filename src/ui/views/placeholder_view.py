"""占位视图（预留功能分区，如标定中心/数据流）"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt


class PlaceholderView(QWidget):
    """功能预留占位页"""

    def __init__(self, title: str, description: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addStretch(2)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 22px; font-weight: bold; color: #888;")
        layout.addWidget(title_label)

        if description:
            desc_label = QLabel(description)
            desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet("font-size: 13px; color: #666; padding: 8px 40px;")
            layout.addWidget(desc_label)

        layout.addStretch(3)
