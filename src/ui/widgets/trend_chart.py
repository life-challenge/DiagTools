"""DID实时趋势折线图控件"""

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from collections import deque


class TrendChart(QWidget):
    """DID实时趋势折线图（简易实现，无需matplotlib依赖）
    
    使用QPainter绘制简单的折线图。
    """

    def __init__(self, max_points: int = 100, parent=None):
        super().__init__(parent)
        self._max_points = max_points
        self._data = deque(maxlen=max_points)
        self._title = ""
        self._unit = ""
        self._min_val = float('inf')
        self._max_val = float('-inf')
        self.setMinimumHeight(150)

    def set_title(self, title: str, unit: str = ""):
        self._title = title
        self._unit = unit

    def add_point(self, value: float):
        """添加数据点"""
        self._data.append(value)
        self._min_val = min(self._min_val, value)
        self._max_val = max(self._max_val, value)
        self.update()

    def clear(self):
        self._data.clear()
        self._min_val = float('inf')
        self._max_val = float('-inf')
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QPen, QColor, QFont
        from PyQt6.QtCore import QRectF

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        margin = 40

        # 背景
        painter.fillRect(0, 0, w, h, QColor(30, 30, 30))

        if len(self._data) < 2:
            painter.setPen(QColor(150, 150, 150))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "等待数据...")
            painter.end()
            return

        # 标题
        painter.setPen(QColor(200, 200, 200))
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        title_text = self._title
        if self._unit:
            title_text += f" ({self._unit})"
        painter.drawText(5, 15, title_text)

        # 当前值
        current = self._data[-1]
        painter.drawText(w - 100, 15, f"当前: {current:.2f}")

        # 绘图区域
        plot_x = margin
        plot_y = 25
        plot_w = w - margin * 2
        plot_h = h - 40

        if self._max_val == self._min_val:
            self._max_val = self._min_val + 1

        # 绘制折线
        painter.setPen(QPen(QColor(76, 175, 80), 2))
        points = list(self._data)
        for i in range(len(points) - 1):
            x1 = plot_x + (i / (self._max_points - 1)) * plot_w
            y1 = plot_y + plot_h - ((points[i] - self._min_val) / (self._max_val - self._min_val)) * plot_h
            x2 = plot_x + ((i + 1) / (self._max_points - 1)) * plot_w
            y2 = plot_y + plot_h - ((points[i + 1] - self._min_val) / (self._max_val - self._min_val)) * plot_h
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Y轴标签
        painter.setPen(QColor(150, 150, 150))
        painter.setFont(QFont("Consolas", 8))
        painter.drawText(0, plot_y, f"{self._max_val:.1f}")
        painter.drawText(0, plot_y + plot_h, f"{self._min_val:.1f}")

        painter.end()
