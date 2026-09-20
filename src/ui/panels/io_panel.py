"""IO控制面板"""

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QPushButton, QLineEdit, QFormLayout, QComboBox)
from PyQt6.QtCore import Qt, pyqtSignal

from src.ui.widgets.click_combo import make_combo_popup_on_click
from src.ui.dialogs.definition_picker import DefinitionPickerDialog

_MONO = QFont("Consolas", 10)


class IoPanel(QWidget):
    """IO控制面板"""

    # 业务日志转发(msg, level): 面板不再内置日志窗口，统一由主窗口业务日志呈现
    business_log = pyqtSignal(str, str)

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        # 已加载的DID定义 {did_id: DidDefinition}（回退显示用）
        self._did_defs: dict = {}
        # 调查表4_2的I/O DID List [{id, name, description, bits, ...}]
        self._io_defs: list = []
        self._init_ui()

    def set_uds_client(self, client):
        self._uds_client = client

    # ---------------- 定义同步（定义库统一导入入口调用） ----------------

    def sync_did_definitions(self, definitions: dict, io_dids: list = None):
        """同步I/O DID List到IO控制DID下拉（0x2F服务用）

        调查表4_2表的I/O DID（灯光/电机/唤醒输出控制等）才是IO控制
        的实际作用对象；无4_2数据时（纯JSON定义导入）回退显示全部DID
        定义，保留手动输入。
        """
        self._did_defs = dict(definitions)
        self._io_defs = list(io_dids) if io_dids else []
        combo = self._did_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("手动输入DID...", "")
        if self._io_defs:
            for entry in self._io_defs:
                did = int(str(entry.get("id", "0x0")), 16)
                label = f"0x{did:04X} - {entry.get('description') or entry.get('name') or ''}"
                combo.addItem(label, f"{did:04X}")
                combo.setItemData(
                    combo.count() - 1, self._io_entry_tooltip(entry),
                    Qt.ItemDataRole.ToolTipRole)
            combo.setItemData(
                0, "I/O DID List（调查表4_2）——仅列实际IO控制DID；"
                   "其余DID请手动输入hex", Qt.ItemDataRole.ToolTipRole)
        else:
            for did_id, defn in sorted(definitions.items()):
                combo.addItem(f"0x{did_id:04X} - {defn.name}", f"{did_id:04X}")
        combo.lineEdit().clear()
        combo.setCurrentIndex(-1)
        combo.blockSignals(False)

    def _io_entry_tooltip(self, entry) -> str:
        """I/O DID条目tooltip: 控制参数 + Bit级明细"""
        parts = []
        if entry.get("control_parameter_text"):
            parts.append(f"控制参数: {entry['control_parameter_text']}")
        for b in entry.get("bits") or []:
            name = b.get("name") or ""
            on = b.get("on") or ""
            off = b.get("off") or ""
            parts.append(f"{b.get('bit_text') or b.get('bit')}: "
                         f"{name}（1={on} 0={off}）")
        if entry.get("example"):
            parts.append(f"报文示例: {entry['example']}")
        return "\n".join(parts)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # IO控制参数
        param_group = QGroupBox("IO控制参数 (0x2F)")
        form = QFormLayout()

        self._did_combo = QComboBox()
        self._did_combo.setEditable(True)
        self._did_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # 输入框适中，但弹窗列表加宽显示完整描述（与DTC面板一致）
        self._did_combo.setFixedWidth(280)
        self._did_combo.view().setMinimumWidth(560)
        self._did_combo.setFont(_MONO)
        self._did_combo.lineEdit().setPlaceholderText("如 3200 或 0x3200")
        self._did_combo.setToolTip(
            "IO控制DID（0x2F服务）——下拉仅列调查表4_2的I/O DID List；"
            "其余DID可手动输入hex。\n已加载定义库/调查表时可直接下拉选择")
        self._did_combo.currentIndexChanged.connect(self._on_did_pick)
        # 点击输入框即弹出候选（不依赖右侧箭头命中）
        make_combo_popup_on_click(self._did_combo)

        did_row = QHBoxLayout()
        did_row.addWidget(self._did_combo)
        pick_btn = QPushButton("选…")
        pick_btn.setFixedWidth(48)
        pick_btn.setToolTip("打开定义选择窗口（列表中选DID）\n不依赖下拉弹窗的可靠选择路径")
        pick_btn.clicked.connect(self._pick_did)
        did_row.addWidget(pick_btn)
        form.addRow("DID:", did_row)

        self._control_combo = QComboBox()
        self._control_combo.addItems([
            "0x00 - returnControlToECU",
            "0x01 - resetToDefault",
            "0x02 - freezeCurrentState",
            "0x03 - shortTermAdjustment",
        ])
        form.addRow("控制选项:", self._control_combo)

        self._data_edit = QLineEdit()
        self._data_edit.setPlaceholderText("控制数据(HEX), 如: FF")
        form.addRow("数据:", self._data_edit)

        param_group.setLayout(form)
        layout.addWidget(param_group)

        # 按钮
        btn_layout = QHBoxLayout()
        send_btn = QPushButton("发送IO控制")
        send_btn.clicked.connect(self._send_io_control)
        btn_layout.addWidget(send_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addStretch()

    def _on_did_pick(self):
        """下拉选择定义后仅保留DID号到输入框；选中提示项则清空待手动输入

        editable combo点选item时Qt会把item文本填入lineEdit，提示项
        “手动输入DID...”的文字会残留并与后续输入拼接，需主动清掉。
        """
        if self._did_combo.currentIndex() == 0:
            edit = self._did_combo.lineEdit()
            edit.clear()
            edit.setFocus()
            return
        data = self._did_combo.currentData()
        if data:
            self._did_combo.lineEdit().setText(data)

    def _pick_did(self):
        """定义选择窗口选DID（模态对话框，可靠兜底路径）

        有I/O DID List时仅列IO条目（与下拉一致），
        否则回退全部DID定义。
        """
        if self._io_defs:
            items = []
            for entry in self._io_defs:
                did = int(str(entry.get("id", "0x0")), 16)
                items.append((f"{did:04X}",
                              f"0x{did:04X} - "
                              f"{entry.get('description') or entry.get('name') or ''}",
                              self._io_entry_tooltip(entry)))
            value = DefinitionPickerDialog.pick("选择IO控制DID（I/O DID List）",
                                                items, self)
            if value:
                self._did_combo.lineEdit().setText(value)
            return
        if not self._did_defs:
            self._log("未加载DID定义（先通过 📖 定义库 导入调查表）", "WARNING")
            return
        items = [(f"{did_id:04X}", f"0x{did_id:04X} - {defn.name}",
                  getattr(defn, "description", "") or "")
                 for did_id, defn in sorted(self._did_defs.items())]
        value = DefinitionPickerDialog.pick("选择IO控制DID", items, self)
        if value:
            self._did_combo.lineEdit().setText(value)

    def _current_did(self) -> int:
        """当前输入的DID号，非法输入返回 -1

        提示项残留容错: 若“手动输入DID...”提示文字与输入混在一起
        （如“手动输入DID...3200”），取“...”之后的尾部解析。
        """
        text = self._did_combo.currentText().strip().replace(" ", "")
        if "..." in text:
            text = text.split("...")[-1].strip()
        if not text:
            return -1
        try:
            return int(text.replace("0x", ""), 16) & 0xFFFF
        except ValueError:
            return -1

    def _send_io_control(self):
        if not self._uds_client:
            self._log("未连接UDS客户端", "WARNING")
            return

        did_id = self._current_did()
        if did_id < 0:
            self._log("DID格式错误（应为1~4位十六进制）", "ERROR")
            return
        control_idx = self._control_combo.currentIndex()
        control_options = [0x00, 0x01, 0x02, 0x03]
        control_option = control_options[control_idx]

        data_hex = self._data_edit.text().strip()
        data = bytes.fromhex(data_hex.replace(" ", "")) if data_hex else b""

        resp = self._uds_client.io_control(did_id, control_option, data)
        if resp and resp[0] == 0x6F:
            # 正响应: 6F <DID 2B> <opt 1B> [controlState]
            # controlState按调查表4_2的Bit定义逐位解析（如3200各路
            # 灯光的ON/OFF），未带state或无定义时仅显示原始hex
            state = resp[4:] if len(resp) > 4 else b""
            msg = f"IO控制 0x{did_id:04X} 成功, 响应: {resp.hex(' ').upper()}"
            if state:
                parsed = self._parse_io_state(did_id, state)
                if parsed:
                    msg += f"\n控制状态: {parsed}"
            self._log(msg, "SUCCESS")
        else:
            self._log(f"IO控制 0x{did_id:04X} 失败", "ERROR")

    def _parse_io_state(self, did_id: int, state: bytes) -> str:
        """0x2F响应controlState按4_2表Bit定义逐位解析

        如3200状态0x21 → "近光灯输出: ON；远光灯输出: OFF；…"；
        保留位（Reserved/预留）与无off/on定义的位跳过。
        无该DID的IO定义或全为保留位时返回空串。
        """
        entry = None
        for e in self._io_defs or []:
            try:
                if int(str(e.get("id", "0x0")), 16) == did_id:
                    entry = e
                    break
            except ValueError:
                continue
        if not entry:
            return ""
        parts = []
        for b in entry.get("bits") or []:
            bit = b.get("bit")
            name = b.get("name") or ""
            off, on = b.get("off") or "", b.get("on") or ""
            if bit is None or not (off or on):
                continue
            if "reserved" in name.lower() or "预留" in name:
                continue
            byte_idx = bit // 8
            if byte_idx >= len(state):
                continue
            val = (state[byte_idx] >> (bit % 8)) & 1
            text = on if val else off
            parts.append(f"{name}: {text}")
        return "；".join(parts)

    def _log(self, msg: str, level: str = "INFO"):
        self.business_log.emit(msg, level)
