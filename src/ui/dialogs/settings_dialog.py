"""选项设置对话框（V2.1 §2.1: 主题等系统设置移入设置，不占工具栏）"""

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox,
                              QDialogButtonBox, QLabel)


class SettingsDialog(QDialog):
    """应用设置对话框"""

    THEMES = [("深色主题", "dark"), ("浅色主题", "light")]

    def __init__(self, current_theme: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选项设置")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self._theme_combo = QComboBox()
        for text, value in self.THEMES:
            self._theme_combo.addItem(text, value)
        idx = self._theme_combo.findData(current_theme)
        self._theme_combo.setCurrentIndex(max(idx, 0))
        form.addRow("主题:", self._theme_combo)

        layout.addLayout(form)
        layout.addWidget(QLabel("提示: ECU定义文件位于 resources/ecu/，新增ECU通过添加配置目录实现。"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def selected_theme(self) -> str:
        return self._theme_combo.currentData()
