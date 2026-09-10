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
        layout.setSpacing(12)

        # 左列通信配置: 宽度上限需覆盖面板内容最小宽（~440），
        # 过窄会导致行尾控件（扫描按钮等）被裁切
        connection_panel.setMinimumWidth(420)
        connection_panel.setMaximumWidth(460)
        layout.addWidget(connection_panel)

        hint = QLabel(
            "<b>VCI / 通信配置</b><br>"
            "<span style='color:#aaa;'>连接参数在此配置，工具栏 连接(F5)/断开(F6) 同步可控</span>"
            "<hr style='border:none;border-top:1px solid #444;'>"
            "· 选择接口类型（Virtual/PCAN/Vector/DoIP）后点击\"连接\"，"
            "或使用工具栏 连接(F5)/断开(F6)<br>"
            "· 连接参数在连接成功与退出时自动保存，下次启动自动恢复<br>"
            "· PCAN/Vector 接口可用通道行右侧\"扫描\"按钮自动发现硬件<br>"
            "· 休眠ECU（如DEM）可勾选\"连接时自动唤醒\"后再连接<br>"
            "· DoIP无真实ECU时可勾选\"本地虚拟ECU（回环模拟）\"在本机测试<br>"
            "· 安全算法32位桥接所需的Python路径在 ECU诊断→高级诊断→安全访问 中配置")
        hint.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        hint.setStyleSheet(
            "color: #999; padding: 16px;"
            "background: rgba(255,255,255,0.03);"
            "border: 1px solid #3a3a3a; border-radius: 6px;")
        hint.setWordWrap(True)
        layout.addWidget(hint, 1)

    @property
    def connection_panel(self) -> ConnectionPanel:
        return self._connection_panel
