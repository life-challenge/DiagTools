"""工具中心视图（V2 §3）

VCI/通信接口配置等系统级工具。接口细节不占据主视觉（§4原则4），
集中放置于此，通过工具栏"连接/断开"按钮快捷操作。
"""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt
from src.ui.panels.connection_panel import ConnectionPanel


class ToolsView(QWidget):
    """工具中心工作区"""

    def __init__(self, connection_panel: ConnectionPanel, parent=None):
        super().__init__(parent)
        self._connection_panel = connection_panel

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        connection_panel.setMaximumWidth(380)
        layout.addWidget(connection_panel)

        hint = QLabel(
            "VCI / 通信配置\n\n"
            "· 选择接口类型（Virtual/PCAN/Vector/DoIP）后点击\"连接\"，或使用工具栏 连接(F5)/断开(F6)\n"
            "· 连接参数在连接成功与退出时自动保存，下次启动自动恢复\n"
            "· DoIP无真实ECU时可勾选\"本地虚拟ECU（回环模拟）\"在本机测试\n"
            "· 安全算法32位桥接所需的Python路径在 ECU诊断→高级诊断→安全访问 中配置")
        hint.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        hint.setStyleSheet("color: #888; padding: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint, 1)

    @property
    def connection_panel(self) -> ConnectionPanel:
        return self._connection_panel
