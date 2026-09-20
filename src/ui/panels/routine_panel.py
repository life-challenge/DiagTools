"""例程控制面板"""

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QPushButton, QLineEdit, QFormLayout, QComboBox)
from PyQt6.QtCore import pyqtSignal

from src.ui.widgets.click_combo import make_combo_popup_on_click
from src.ui.dialogs.definition_picker import DefinitionPickerDialog

_MONO = QFont("Consolas", 10)


class RoutinePanel(QWidget):
    """例程控制面板"""

    # 业务日志转发(msg, level): 面板不再内置日志窗口，统一由主窗口业务日志呈现
    business_log = pyqtSignal(str, str)

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        # 已加载的例程定义[(routine_id, 描述)]，定义库统一导入入口同步
        self._routine_defs: list = []
        self._init_ui()

    def set_uds_client(self, client):
        self._uds_client = client

    # ---------------- 定义同步（定义库统一导入入口调用） ----------------

    def sync_routine_definitions(self, routines: list):
        """同步调查表/定义库的例程定义到例程ID下拉

        routines: [(routine_id, 描述)]，来源于调查表例程sheet
        （如 0x5200 前轮胎压传感器匹配）；同时保留手动输入
        """
        self._routine_defs = list(routines)
        combo = self._routine_id_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("手动输入例程ID...", "")
        for rid, desc in sorted(self._routine_defs):
            combo.addItem(f"0x{rid:04X} - {desc}", f"{rid:04X}")
        combo.lineEdit().clear()
        combo.setCurrentIndex(-1)
        combo.blockSignals(False)

    @property
    def routine_count(self) -> int:
        """已加载的例程定义数量（定义库总览用）"""
        return len(self._routine_defs)

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

        self._routine_id_combo = QComboBox()
        self._routine_id_combo.setEditable(True)
        self._routine_id_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._routine_id_combo.setFixedWidth(280)
        self._routine_id_combo.setFont(_MONO)
        self._routine_id_combo.lineEdit().setPlaceholderText("如 5200 或 0xFF00")
        self._routine_id_combo.setToolTip(
            "例程ID（0x31服务，2字节hex）；FF00~FF0x为标准刷写例程，\n"
            "52xx等多为OEM自定义例程。已加载定义库/调查表时可直接下拉选择")
        self._routine_id_combo.currentIndexChanged.connect(self._on_routine_pick)
        # 点击输入框即弹出候选（不依赖右侧箭头命中）
        make_combo_popup_on_click(self._routine_id_combo)

        id_row = QHBoxLayout()
        id_row.addWidget(self._routine_id_combo)
        pick_btn = QPushButton("选…")
        pick_btn.setFixedWidth(48)
        pick_btn.setToolTip("打开定义选择窗口（列表中选例程ID）\n不依赖下拉弹窗的可靠选择路径")
        pick_btn.clicked.connect(self._pick_routine)
        id_row.addWidget(pick_btn)
        form.addRow("例程ID:", id_row)

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

    def _on_routine_pick(self):
        """下拉选择定义后仅保留例程ID到输入框；选中提示项则清空待手动输入

        editable combo点选item时Qt会把item文本填入lineEdit，提示项
        “手动输入例程ID...”的文字会残留并与后续输入拼接，需主动清掉。
        """
        if self._routine_id_combo.currentIndex() == 0:
            edit = self._routine_id_combo.lineEdit()
            edit.clear()
            edit.setFocus()
            return
        data = self._routine_id_combo.currentData()
        if data:
            self._routine_id_combo.lineEdit().setText(data)

    def _pick_routine(self):
        """定义选择窗口选例程ID（模态对话框，可靠兜底路径）"""
        if not self._routine_defs:
            self._log("未加载例程定义（先通过 📖 定义库 导入调查表）", "WARNING")
            return
        items = [(f"{rid:04X}", f"0x{rid:04X} - {desc}", desc)
                 for rid, desc in sorted(self._routine_defs)]
        value = DefinitionPickerDialog.pick("选择例程ID", items, self)
        if value:
            self._routine_id_combo.lineEdit().setText(value)

    def _current_routine_id(self) -> int:
        """当前输入的例程ID，非法输入返回 -1

        提示项残留容错: 若“手动输入例程ID...”提示文字与输入混在
        一起（如“手动输入例程ID...0203”），取“...”之后的尾部解析。
        """
        text = self._routine_id_combo.currentText().strip().replace(" ", "")
        if "..." in text:
            text = text.split("...")[-1].strip()
        if not text:
            return -1
        try:
            return int(text.replace("0x", ""), 16) & 0xFFFF
        except ValueError:
            return -1

    def _send_routine(self):
        if not self._uds_client:
            self._log("未连接UDS客户端", "WARNING")
            return

        sub_funcs = [0x01, 0x02, 0x03]
        sub_func = sub_funcs[self._sub_func_combo.currentIndex()]
        routine_id = self._current_routine_id()
        if routine_id < 0:
            self._log("例程ID格式错误（应为1~4位十六进制）", "ERROR")
            return

        data_hex = self._data_edit.text().strip()
        data = bytes.fromhex(data_hex.replace(" ", "")) if data_hex else b""

        resp = self._uds_client.routine_control(sub_func, routine_id, data)
        if resp and resp[0] == 0x71:
            self._log(f"例程控制 0x{routine_id:04X} 成功, 响应: {resp.hex(' ')}",
                      "SUCCESS")
        else:
            self._log(f"例程控制 0x{routine_id:04X} 失败", "ERROR")

    def _quick_routine(self, routine_id: int):
        self._routine_id_combo.lineEdit().setText(f"{routine_id:04X}")
        self._send_routine()

    def _log(self, msg: str, level: str = "INFO"):
        self.business_log.emit(msg, level)
