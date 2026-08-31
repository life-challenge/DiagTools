"""HEX输入控件 - 自动格式化HEX字符串"""

from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QValidator


class HexValidator(QValidator):
    """HEX输入验证器"""

    def validate(self, text, pos):
        cleaned = text.replace(" ", "")
        for ch in cleaned:
            if ch not in "0123456789abcdefABCDEF":
                return QValidator.State.Invalid, text, pos
        return QValidator.State.Acceptable, text, pos


class HexInput(QLineEdit):
    """HEX输入控件
    
    自动格式化输入为HEX格式（每字节空格分隔）。
    """

    value_changed = pyqtSignal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("输入HEX数据, 如: 10 03")
        self.setFont(self._get_mono_font())
        self._validator = HexValidator()
        self.setValidator(self._validator)

    @staticmethod
    def _get_mono_font():
        from PyQt6.QtGui import QFont
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        return font

    def get_bytes(self) -> bytes:
        """获取输入的字节数据"""
        text = self.text().replace(" ", "").replace("\n", "")
        if not text:
            return b""
        try:
            return bytes.fromhex(text)
        except ValueError:
            return b""

    def set_bytes(self, data: bytes):
        """设置字节数据（自动格式化为HEX）"""
        self.setText(data.hex(" ").upper())

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        # 自动添加空格（每2个HEX字符后）
        text = self.text().replace(" ", "")
        if len(text) > 0 and len(text) % 2 == 0:
            formatted = " ".join(text[i:i+2] for i in range(0, len(text), 2))
            cursor_pos = self.cursorPosition()
            self.setText(formatted.upper())
            self.setCursorPosition(cursor_pos)
