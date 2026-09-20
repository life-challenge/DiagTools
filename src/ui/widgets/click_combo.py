"""可编辑下拉框交互增强: 点击/双击输入框弹出候选列表"""

import weakref

from PyQt6.QtCore import QObject, QEvent, QTimer
from PyQt6.QtWidgets import QComboBox


class _ClickPopupFilter(QObject):
    """lineEdit 鼠标点击/双击 → 弹出选择列表（不干扰输入路径）"""

    def __init__(self, combo: QComboBox, dblclick: bool, parent=None):
        super().__init__(parent)
        self._combo = combo
        self._dblclick = dblclick

    def eventFilter(self, watched, event):
        if self._dblclick:
            # 双击弹候选: 单击留给编辑（定位光标/选中可输入），
            # 双击才弹出——输入优先型字段（诊断控制台子功能）用
            if event.type() == QEvent.Type.MouseButtonDblClick:
                QTimer.singleShot(0, self._combo.showPopup)
        elif event.type() == QEvent.Type.MouseButtonPress:
            # 单击弹候选: 选择优先型字段（DID/IO/例程定义库下拉）用
            # 延迟到本次点击事件处理完后再弹（避免与焦点/光标定位冲突）
            QTimer.singleShot(0, self._combo.showPopup)
        return False


_applied = weakref.WeakSet()


def make_combo_popup_on_click(combo: QComboBox, dblclick: bool = False) -> None:
    """可编辑下拉: 点击（或双击）输入框弹出候选列表

    Qt 默认行为: editable QComboBox 只有点击右侧箭头区域才弹列表，
    点击输入框(lineEdit)只进入编辑状态。

    - dblclick=False（默认）: 单击输入框任意位置即弹候选列表
      （CANoe/网页风格，选择优先型字段——DID/IO/例程定义库下拉）。
      注意QSS下箭头命中区域窄，此模式让选择不依赖箭头。
    - dblclick=True: 双击输入框才弹候选，单击保留为编辑
      （定位光标/直接打字）——输入优先型字段（诊断控制台子功能/
      参数）用。单击弹模式会抢交互（用户想点击中间改值时列表
      抢弹出），双击模式输入与选单两不误。

    不可编辑的combo无需此处理（点击控件任意位置本就弹列表）。
    """
    le = combo.lineEdit()
    if le is None:
        return
    if combo in _applied:
        return
    le.installEventFilter(_ClickPopupFilter(combo, dblclick, combo))
    _applied.add(combo)
