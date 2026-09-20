"""UDS诊断控制台（CANoe Diagnostic Console 风格，V2.1 §4 高级诊断）

布局（参照CANoe诊断控制台三栏）:
  左侧: 诊断服务库——内置服务按分类组织 + 调查表服务条目，
        双击一键发送，单击载入请求编辑器；
  中间: 请求编辑——结构化参数表单（子功能下拉/动态参数行），
        实时HEX预览；F8发送；
  右侧: 响应解析——正响应（绿色: 报文+逐字段解析）与
        负响应（橙色: NRC码+原因说明）分栏呈现。

通信历史不在此页面呈现（主窗口底部日志面板已有全局通信
Trace，重复占空间）——请求/响应/耗时改发 business_log。
"""

import time

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QShortcut, QKeySequence
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox,
    QLineEdit, QLabel, QTreeWidget, QTreeWidgetItem,
    QGroupBox, QFormLayout, QSplitter
)
from src.ui.async_uds import UdsWorker
from src.ui.widgets.click_combo import make_combo_popup_on_click
from src.protocol.uds_services import UdsService

_MONO = QFont("Consolas", 10)

# ---- 服务分类（左侧服务树） ----
_CATEGORIES = [
    ("会话与复位", [0x10, 0x11, 0x3E]),
    ("故障码 DTC", [0x14, 0x19, 0x85]),
    ("数据 Data", [0x22, 0x23, 0x2E, 0x2F]),
    ("例程与传输", [0x31, 0x34, 0x35, 0x36, 0x37]),
    ("安全与通信", [0x27, 0x28]),
]

_SERVICE_NAMES = {
    0x10: "DiagnosticSessionControl", 0x11: "ECUReset",
    0x14: "ClearDiagnosticInformation", 0x19: "ReadDTCInformation",
    0x22: "ReadDataByIdentifier", 0x23: "ReadMemoryByAddress",
    0x27: "SecurityAccess", 0x28: "CommunicationControl",
    0x2E: "WriteDataByIdentifier", 0x2F: "InputOutputControlByIdentifier",
    0x31: "RoutineControl", 0x34: "RequestDownload",
    0x35: "RequestUpload", 0x36: "TransferData",
    0x37: "RequestTransferExit", 0x3E: "TesterPresent",
    0x85: "ControlDTCSetting",
}

_SERVICE_DESCS = {
    0x10: "切换诊断会话（默认/编程/扩展）", 0x11: "ECU复位（硬/软/钥匙关）",
    0x14: "清除故障码（按DTC组）", 0x19: "读取DTC信息（数量/快照/扩展数据）",
    0x22: "按DID读取数据（可多个DID）", 0x23: "按地址读取内存",
    0x27: "安全访问（请求种子/发送密钥）", 0x28: "通信控制（启停收发）",
    0x2E: "按DID写入数据", 0x2F: "IO控制（按DID控制输出）",
    0x31: "例程控制（启动/停止/查询结果）", 0x34: "请求下载",
    0x35: "请求上传", 0x36: "传输数据",
    0x37: "退出传输", 0x3E: "TesterPresent（会话保持）",
    0x85: "DTC设置控制（开/关）",
}

# 子功能选项: SID → [(值, 标签)]
_SUB_OPTIONS = {
    0x10: [(0x01, "0x01 默认会话"), (0x02, "0x02 编程会话"),
           (0x03, "0x03 扩展会话"), (0x04, "0x04 安全系统会话")],
    0x11: [(0x01, "0x01 硬复位"), (0x02, "0x02 钥匙关复位"),
           (0x03, "0x03 软复位")],
    0x19: [(0x01, "0x01 读取DTC数量"), (0x02, "0x02 按状态掩码读取"),
           (0x04, "0x04 读取快照数据"), (0x06, "0x06 读取扩展数据"),
           (0x0A, "0x0A 读取所有支持的DTC")],
    0x27: [(0x01, "0x01 请求种子 L1"), (0x02, "0x02 发送密钥 L1"),
           (0x03, "0x03 请求种子 L3"), (0x04, "0x04 发送密钥 L3"),
           (0x09, "0x09 请求种子 L9"), (0x0A, "0x0A 发送密钥 L9"),
           (0x0B, "0x0B 请求种子 LB"), (0x0C, "0x0C 发送密钥 LB")],
    0x28: [(0x00, "0x00 启用收发"), (0x01, "0x01 禁用接收"),
           (0x02, "0x02 禁用发送"), (0x03, "0x03 禁用收发")],
    0x2F: [(0x00, "0x00 交还ECU控制"), (0x01, "0x01 复位默认值"),
           (0x02, "0x02 冻结当前状态"), (0x03, "0x03 短期调整")],
    0x31: [(0x01, "0x01 启动例程"), (0x02, "0x02 停止例程"),
           (0x03, "0x03 查询例程结果")],
    0x3E: [(0x00, "0x00 请求响应"), (0x80, "0x80 抑制正响应")],
    0x85: [(0x01, "0x01 开启DTC记录"), (0x02, "0x02 关闭DTC记录")],
}

# 内置服务Request列示例（默认参数下的完整请求，与_add_extra_params
# 的默认值保持一致——双击内置服务即发送该默认构造报文）。
# 子功能支持自定义hex输入（如0x10输入05 → 预览 10 05），
# 示例仅展示最常用形态。
_REQ_EXAMPLES = {
    0x10: "10 01", 0x11: "11 01", 0x3E: "3E 00",
    0x14: "14 FF FF FF", 0x19: "19 01 FF", 0x85: "85 01",
    0x22: "22 F1 90", 0x23: "23 00 00 00 00 01",
    0x2E: "2E F1 90 …", 0x2F: "2F 00 32 00 00",
    0x31: "31 01 FF 00", 0x34: "34 00 00 00 00 00 00 00 01",
    0x35: "35 00 00 00 00 00 00 00 01", 0x36: "36 01 …",
    0x37: "37", 0x27: "27 01", 0x28: "28 00 01",
}

# 常用负响应码（名称+中文原因说明）
_NRC_NAMES = {
    0x10: "generalReject", 0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported", 0x13: "incorrectMessageLength",
    0x14: "responseTooLong", 0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect", 0x24: "requestSequenceError",
    0x31: "requestOutOfRange", 0x33: "securityAccessDenied",
    0x35: "invalidKey", 0x36: "exceededNumberOfAttempts",
    0x70: "uploadDownloadNotAccepted", 0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure", 0x73: "wrongBlockSequenceCounter",
    0x78: "requestCorrectlyReceivedResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}

_NRC_DESC_CN = {
    0x10: "ECU拒绝该请求（一般性拒绝）",
    0x11: "ECU不支持该服务（SID未实现）",
    0x12: "当前会话不支持该子功能",
    0x13: "请求长度错误（参数多/缺失）",
    0x21: "ECU忙，请重发请求",
    0x22: "条件不满足（如会话等级/速度条件）",
    0x24: "请求顺序错误（如未发种子先发密钥）",
    0x31: "请求参数超范围（如不存在的DID/例程ID）",
    0x33: "安全访问被拒绝（当前未解锁或等级不足）",
    0x35: "发送的密钥无效（算法或等级不匹配）",
    0x36: "安全访问尝试次数超限，ECU锁死",
    0x72: "刷写编程失败（数据/擦除错误）",
    0x73: "块序号错误（36传输序号不连续）",
    0x78: "响应未就绪——ECU正在处理，稍后自动重收",
    0x7E: "当前会话不支持该子功能（需切换会话）",
    0x7F: "当前会话不支持该服务（需切换会话）",
}

# DTC状态位中文（正响应解析用）
_DTC_BITS = {0: "当前失败", 1: "本周期失败", 2: "待定", 3: "已确认",
             4: "清除后未完成", 5: "清除后失败", 6: "本周期未完成",
             7: "警告灯请求"}


def _dtc_text(dtc: bytes) -> str:
    """3字节DTC → ISO 15031-6 P/C/B/U码（5位）

    布局: b0 bit7-6=字母P/C/B/U, bit5-4=首字符(0-3),
    b0低4位+b1为四位hex；b2为故障类型计数不进主码
    （如 010000 → P0100, 410000 → C0100）
    """
    if len(dtc) != 3:
        return ""
    b0, b1, _b2 = dtc
    letter = {0: "P", 1: "C", 2: "B", 3: "U"}.get(b0 >> 6, "P")
    return f"{letter}{(b0 >> 4) & 0x3:01X}{b0 & 0xF:01X}{b1:02X}"

# SID → 已有专用功能页（控制台可一键发原始请求，专用页做引导式操作）
# 0x10/0x3E 指向"会话保持"页（一级快捷入口名，含 0x10 切换 + 3E 保活）
_DEDICATED_PAGES = {
    0x10: "会话保持", 0x14: "故障码DTC", 0x19: "故障码DTC",
    0x22: "DID", 0x2E: "DID", 0x2F: "IO控制", 0x31: "例程",
    0x27: "安全算法", 0x3E: "会话保持",
}


def _hex_ok(text: str) -> bool:
    """HEX字段格式校验（空串视为有效）"""
    t = text.strip().replace(" ", "").replace("0x", "")
    if not t:
        return True
    if len(t) % 2:
        return False
    try:
        bytes.fromhex(t)
        return True
    except ValueError:
        return False


def _parse_bytes(text: str, n: int, default: bytes) -> bytes:
    """HEX输入 → 定长字节；非法/空返回default"""
    t = text.strip().replace(" ", "").replace("0x", "")
    if not t:
        return default
    if len(t) % 2:
        t = "0" + t
    try:
        b = bytes.fromhex(t)
    except ValueError:
        return default
    return b[-n:] if len(b) >= n else b.rjust(n, b"\x00")


def _delete_layout_item(item):
    """递归删除QLayoutItem: 控件deleteLater；子布局先清空再删

    DID行是嵌套QHBoxLayout（takeAt取出后widget为None），
    若不递归删除其中控件，它们仍挂在父容器上继续渲染残留。
    """
    if item is None:
        return
    w = item.widget()
    if w is not None:
        w.deleteLater()
        return
    sub = item.layout()
    while sub is not None and sub.count():
        _delete_layout_item(sub.takeAt(0))
    if sub is not None:
        sub.deleteLater()


class UdsServiceView(QWidget):
    """UDS诊断控制台: 服务库 + 结构化请求编辑 + 响应解析"""

    # 业务日志转发(msg, level): 通信结果发主窗口底部日志面板
    # （页面内不再内置Timing历史表，避免与全局通信Trace重复占空间）
    business_log = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._uds_client = None
        self._worker = None
        self._worker_thread = None
        self._survey_services: list = []
        self._current_sid = 0x10
        # 结构化参数控件（每次服务切换重建）
        self._sub_combo = None
        self._param_rows: list = []      # [(label, widget, kind)]
        self._did_rows: list = []        # 0x22多DID行 [(edit, 删除btn)]
        self._last_sub_value = None      # 上次生效子功能值（editingFinished去重）
        self._init_ui()
        self._load_service(0x10)

    def set_uds_client(self, client):
        self._uds_client = client

    # ---------------- 定义同步（定义库统一导入入口调用） ----------------

    def sync_service_definitions(self, services: list):
        """同步调查表services条目到服务库（双击一键发送原始请求）"""
        self._survey_services = [
            dict(s) for s in (services or [])
            if str(s.get("request", "")).strip()
        ]
        self._refresh_service_tree()

    @property
    def service_count(self) -> int:
        """服务库条目数量（定义库总览用）"""
        return len(self._survey_services)

    # ---------------- UI ----------------

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # 左服务库 + 右(编辑+解析)水平分割
        main = QSplitter(Qt.Orientation.Horizontal)

        # ===== 左: 诊断服务库 =====
        left_group = QGroupBox("诊断服务库")
        left_lay = QVBoxLayout(left_group)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("筛选:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("SID/服务名/Request字节")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._refresh_service_tree)
        filter_row.addWidget(self._filter_edit, 1)
        left_lay.addLayout(filter_row)

        self._service_tree = QTreeWidget()
        self._service_tree.setHeaderLabels(["服务", "SID", "Request"])
        self._service_tree.setColumnWidth(0, 118)
        self._service_tree.setColumnWidth(1, 40)
        self._service_tree.setColumnWidth(2, 118)
        self._service_tree.setToolTip(
            "单击: 载入请求编辑器（结构化填参）\n"
            "双击: 直接一键发送该服务/请求\n"
            "Request列: 调查表条目的完整请求字节（双击即发送该报文）")
        self._service_tree.itemClicked.connect(self._on_tree_clicked)
        self._service_tree.itemDoubleClicked.connect(
            self._on_tree_double_clicked)
        left_lay.addWidget(self._service_tree, 1)

        self._lib_hint = QLabel(
            "未加载调查表——通过 📖 定义库 导入后\n"
            "调查表服务条目追加到树底部")
        self._lib_hint.setWordWrap(True)
        self._lib_hint.setStyleSheet("color: #888;")
        left_lay.addWidget(self._lib_hint)
        self._lib_count_label = QLabel("共 0 个服务")
        self._lib_count_label.setStyleSheet("color: #888;")
        left_lay.addWidget(self._lib_count_label)
        main.addWidget(left_group)

        # ===== 右: 请求编辑 + 响应解析 =====
        top = QSplitter(Qt.Orientation.Horizontal)

        # ---- 中: 请求编辑 ----
        req_group = QGroupBox("请求编辑")
        req_lay = QVBoxLayout(req_group)

        self._svc_name_label = QLabel("--")
        self._svc_name_label.setStyleSheet(
            "font-size: 14px; font-weight: bold;")
        req_lay.addWidget(self._svc_name_label)
        self._svc_desc_label = QLabel("")
        self._svc_desc_label.setStyleSheet("color: #888;")
        self._svc_desc_label.setWordWrap(True)
        req_lay.addWidget(self._svc_desc_label)

        # 参数表单容器（结构化参数唯一构造方式；子功能/参数常用值
        # 下拉选择，也支持手输自定义hex）
        self._param_container = QWidget()
        self._param_form = QFormLayout(self._param_container)
        self._param_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        req_lay.addWidget(self._param_container)

        # 0x22多DID动态行容器
        self._did_container = QWidget()
        self._did_grid = QVBoxLayout(self._did_container)
        self._did_grid.setContentsMargins(0, 0, 0, 0)
        req_lay.addWidget(self._did_container)

        # 预览
        preview_group = QGroupBox("请求报文预览")
        pv_lay = QVBoxLayout(preview_group)
        self._preview_label = QLabel("--")
        self._preview_label.setFont(_MONO)
        self._preview_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._preview_label.setStyleSheet("padding: 4px;")
        pv_lay.addWidget(self._preview_label)
        self._preview_len_label = QLabel("长度 0 字节 | 方向: 请求")
        self._preview_len_label.setStyleSheet("color: #888;")
        pv_lay.addWidget(self._preview_len_label)
        req_lay.addWidget(preview_group)

        btn_row = QHBoxLayout()
        self._send_btn = QPushButton("发送请求 (F8)")
        self._send_btn.setObjectName("btn_primary")
        self._send_btn.setFixedHeight(32)
        self._send_btn.clicked.connect(self._send)
        btn_row.addWidget(self._send_btn, 1)
        self._clear_req_btn = QPushButton("清除")
        self._clear_req_btn.clicked.connect(self._clear_request)
        btn_row.addWidget(self._clear_req_btn)
        # 当前会话（0x50正响应自动更新，参照CANoe状态栏）
        self._session_label = QLabel("当前会话: --")
        self._session_label.setStyleSheet("color: #888;")
        self._session_label.setToolTip("0x50正响应自动更新当前诊断会话")
        btn_row.addWidget(self._session_label)
        req_lay.addLayout(btn_row)
        top.addWidget(req_group)

        # ---- 右: 响应解析 ----
        resp_group = QGroupBox("响应解析")
        resp_lay = QVBoxLayout(resp_group)

        # 正响应（绿色）
        self._pos_group = QGroupBox("正响应 Positive Response")
        self._pos_group.setStyleSheet(
            "QGroupBox { color: #4CAF50; font-weight: bold; }")
        pos_lay = QVBoxLayout(self._pos_group)
        self._pos_hex_label = QLabel("--")
        self._pos_hex_label.setFont(_MONO)
        self._pos_hex_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        pos_lay.addWidget(self._pos_hex_label)
        self._pos_meta_label = QLabel("")
        self._pos_meta_label.setStyleSheet("color: #888;")
        pos_lay.addWidget(self._pos_meta_label)
        self._pos_tree = QTreeWidget()
        self._pos_tree.setHeaderLabels(["字段", "值"])
        self._pos_tree.setColumnWidth(0, 150)
        self._pos_tree.setAlternatingRowColors(True)
        pos_lay.addWidget(self._pos_tree, 1)
        resp_lay.addWidget(self._pos_group, 1)

        # 负响应（橙色）
        self._neg_group = QGroupBox("负响应 Negative Response")
        self._neg_group.setStyleSheet(
            "QGroupBox { color: #FF9800; font-weight: bold; }")
        neg_lay = QVBoxLayout(self._neg_group)
        self._neg_hex_label = QLabel("--")
        self._neg_hex_label.setFont(_MONO)
        self._neg_hex_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        neg_lay.addWidget(self._neg_hex_label)
        self._neg_nrc_label = QLabel("")
        self._neg_nrc_label.setWordWrap(True)
        neg_lay.addWidget(self._neg_nrc_label)
        resp_lay.addWidget(self._neg_group)
        top.addWidget(resp_group)

        top.setStretchFactor(0, 5)
        top.setStretchFactor(1, 4)
        top.setSizes([560, 460])
        main.addWidget(top)

        main.setStretchFactor(0, 2)
        main.setStretchFactor(1, 5)
        main.setSizes([300, 980])
        layout.addWidget(main)

        # F8 快捷发送（CANoe习惯）
        QShortcut(QKeySequence("F8"), self).activated.connect(self._send)

        self._refresh_service_tree()

    # ---------------- 服务库 ----------------

    def _refresh_service_tree(self):
        """重建服务树: 内置分类 + 调查表条目，按筛选关键字过滤"""
        kw = self._filter_edit.text().strip().lower()
        tree = self._service_tree
        tree.clear()
        n_total = 0

        for cat_name, sids in _CATEGORIES:
            cat_item = QTreeWidgetItem(tree, [cat_name, "", ""])
            cat_item.setFlags(Qt.ItemFlag.ItemIsEnabled)   # 分类节点不可选
            cat_item.setExpanded(True)
            for sid in sids:
                name = _SERVICE_NAMES.get(sid, f"SID_{sid:02X}")
                if kw and kw not in f"{sid:02x} {name}".lower():
                    continue
                # 内置服务: Request列显示默认参数下的示例报文
                # （与双击发送的默认构造一致；子功能可在编辑器自定义）
                item = QTreeWidgetItem(
                    cat_item, [name, f"0x{sid:02X}",
                               _REQ_EXAMPLES.get(sid, "(填参)")])
                item.setData(0, Qt.ItemDataRole.UserRole, sid)
                dedicated = _DEDICATED_PAGES.get(sid)
                if dedicated:
                    item.setToolTip(0, f"专用页: {dedicated}")
                item.setToolTip(
                    2, f"默认参数示例（双击即发送）:\n"
                       f"{_REQ_EXAMPLES.get(sid, '')}\n"
                       f"参数在中间请求编辑器修改；子功能支持自定义hex输入")
                n_total += 1

        # 调查表条目（全量追加到"调查表服务"分类）
        if self._survey_services:
            surv_item = QTreeWidgetItem(tree, ["调查表服务", "", ""])
            surv_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            surv_item.setExpanded(True)
            for svc in sorted(self._survey_services,
                              key=lambda s: str(s.get("request", ""))):
                req = str(svc.get("request", "")).strip().replace(" ", "")
                name = str(svc.get("name") or svc.get("description") or req)
                # 同一SID多条子功能: Request列直接显示完整字节，
                # 不点击也能区分各条目发送的具体内容（如10 01/10 02/10 03）
                req_disp = " ".join(
                    req[i:i + 2].upper() for i in range(0, len(req), 2))
                if kw:
                    # 筛选同时命中: 无空格hex(f199)/带空格显示形(10 03)/名称
                    hay = f"{req} {req_disp} {name}".lower()
                    if kw not in hay and kw.replace(" ", "") not in req.lower():
                        continue
                item = QTreeWidgetItem(
                    surv_item, [name[:28], f"0x{req[:2].upper()}", req_disp])
                item.setData(0, Qt.ItemDataRole.UserRole, req)
                item.setToolTip(
                    0, f"Request: {req.upper()}\n"
                       f"{str(svc.get('description') or '')[:120]}")
                n_total += 1

        self._lib_count_label.setText(f"共 {n_total} 个服务")
        self._lib_hint.setVisible(not self._survey_services)

    def _on_tree_clicked(self, item, _col):
        """单击: 载入请求编辑器"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return
        if isinstance(data, str):
            # 调查表条目 → 反解hex预填结构化表单
            # （子功能/参数各归各位，可直接修改后再发送）
            try:
                req = bytes.fromhex(data)
            except ValueError:
                return
            self._prefill_from_request(req)
            self._svc_name_label.setText(
                f"{self._service_label()}  (调查表条目)")
            self._svc_desc_label.setText(
                f"{item.text(0)} — Request: {data.upper()}")
        else:
            self._load_service(data)

    def _on_tree_double_clicked(self, item, _col):
        """双击: 一键发送"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return
        if isinstance(data, str):
            try:
                req = bytes.fromhex(data)
            except ValueError:
                return
            label = f"{item.text(0)[:30]} (调查表)"
        else:
            self._load_service(data)
            req = self._build_request()
            label = self._service_label()
        if req:
            self._dispatch(req, label)

    # ---------------- 请求编辑（结构化） ----------------

    def _service_label(self) -> str:
        return f"0x{self._current_sid:02X} {_SERVICE_NAMES.get(self._current_sid, '')}"

    def _current_sub(self):
        """当前子功能值。

        预定义项（文本与选中项一致）取currentData；手输的自定义
        hex文本解析为1字节子功能值（如输入05 → 0x05，预览 10 05）。
        无子功能服务（非editable combo）返回 None；
        手输非法（非hex/超1字节）返回 -1，构造失败预览标红。
        """
        if self._sub_combo is None:
            return None
        if not self._sub_combo.isEditable():
            return self._sub_combo.currentData()   # None即"无子功能"项
        idx = self._sub_combo.currentIndex()
        if (idx >= 0
                and self._sub_combo.currentText() == self._sub_combo.itemText(idx)
                and self._sub_combo.currentData() is not None):
            return self._sub_combo.currentData()   # 选中预定义项
        # 手输自定义子功能（无匹配项时Qt将index置-1）
        text = self._sub_combo.currentText().strip().replace(" ", "")
        if not text:
            return -1
        try:
            v = int(text.replace("0x", ""), 16)
        except ValueError:
            return -1
        return v if 0 <= v <= 0xFF else -1

    def _load_service(self, sid: int):
        """载入服务到编辑器: 标题 + 重建结构化参数表单"""
        self._current_sid = sid
        self._svc_name_label.setText(self._service_label())
        self._svc_desc_label.setText(_SERVICE_DESCS.get(sid, ""))
        self._rebuild_param_form()
        self._update_preview()

    def _prefill_from_request(self, req: bytes):
        """调查表条目hex反解 → 结构化表单预填

        按服务定义逐字节归位: 首字节SID、次字节子功能（有子功能的
        服务），其余按参数行依次填充（key/data类吃剩余全部，
        0x22多DID每行2字节）——点击调查表条目即可在结构化表单
        中继续修改后发送，不再切原始HEX模式。
        """
        if not req:
            return
        self._load_service(req[0])
        pos = 1
        # 子功能（editable combo手输文本，触发参数行重建）
        if (self._sub_combo is not None and self._sub_combo.isEditable()
                and pos < len(req)):
            self._sub_combo.lineEdit().setText(f"{req[pos]:02X}")
            self._on_sub_changed()
            pos += 1
        # 参数行依次消费（nbytes固定长度；key/data吃剩余全部）
        for edit, kind, nbytes in self._param_rows:
            if kind in ("key", "data"):
                rest = req[pos:]
                if rest:
                    edit.setText(rest.hex().upper())
                pos = len(req)
                break
            if pos + nbytes <= len(req):
                edit.setText(req[pos:pos + nbytes].hex().upper())
                pos += nbytes
        # 0x22多DID行（每行2字节，不足则补充行）
        if self._current_sid == 0x22:
            dids = [req[i:i + 2] for i in range(pos, len(req) - 1, 2)]
            for i, did in enumerate(dids):
                if i < len(self._did_rows):
                    self._did_rows[i][0].setText(did.hex().upper())
                else:
                    self._append_did_row(did.hex().upper())
        self._update_preview()

    def _clear_param_form(self):
        """清空参数表单（删除全部行控件，含嵌套DID行）"""
        while self._param_form.count():
            item = self._param_form.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._param_rows = []
        # 多DID容器: 行为嵌套QHBoxLayout，需递归删除其中控件
        # （否则切换服务后DID输入框残留渲染，如0x22→0x10后仍显示F199）
        while self._did_grid.count():
            _delete_layout_item(self._did_grid.takeAt(0))
        self._did_rows = []

    def _rebuild_param_form(self):
        """按当前服务重建结构化参数表单"""
        self._clear_param_form()
        sid = self._current_sid

        # DID列表容器仅0x22服务可见（其他服务无DID列表参数）
        self._sync_did_container()

        # 子功能（有选项的服务）：支持自定义hex输入（如10输入05→预览 10 05）
        sub_opts = _SUB_OPTIONS.get(sid)
        if sub_opts is not None or sid in (0x14, 0x22, 0x23, 0x2E, 0x36):
            self._sub_combo = QComboBox()
            if sub_opts:
                for v, label in sub_opts:
                    self._sub_combo.addItem(label, v)
                # 下拉选项之外可手动输入自定义子功能（OEM扩展值）
                self._sub_combo.setEditable(True)
                self._sub_combo.setInsertPolicy(
                    QComboBox.InsertPolicy.NoInsert)
                le = self._sub_combo.lineEdit()
                le.setFont(_MONO)
                le.setPlaceholderText("自定义hex如05；双击选预定义项")
                # 手输实时更新预览；完成后重建子功能相关参数行（如19的04/06）
                le.textEdited.connect(
                    lambda _t: self._update_preview())
                le.editingFinished.connect(self._on_sub_edited)
                # 交互: 单击=编辑（定位光标/直接打字），双击=弹候选
                # 列表，右侧箭头也弹——三种路径均可选预定义项
                make_combo_popup_on_click(self._sub_combo, dblclick=True)
                self._sub_combo.setToolTip(
                    "单击输入框可直接输入自定义hex（如 05）；\n"
                    "双击输入框或点击右侧箭头弹出预定义子功能候选")
            else:
                self._sub_combo.addItem("无子功能", None)
            self._sub_combo.currentIndexChanged.connect(
                self._on_sub_changed)
            self._param_form.addRow("子功能:", self._sub_combo)
        else:
            self._sub_combo = None

        # 重置上次生效子功能值（服务重建后为默认项，避免旧值误判变化）
        self._last_sub_value = self._current_sub()

        self._add_extra_params()

    def _add_extra_params(self):
        """按服务+子功能追加参数行"""
        sid = self._current_sid
        sub = self._current_sub()

        if sid in (0x10, 0x11, 0x3E, 0x85):
            pass   # 仅子功能
        elif sid == 0x14:
            self._add_hex_row("DTC组:", "FFFFFF", 3, "group")
        elif sid == 0x19:
            if sub in (0x01, 0x02):
                self._add_hex_row("状态掩码:", "FF", 1, "mask")
            elif sub in (0x04, 0x06):
                self._add_hex_row("DTC号:", "FFFFFF", 3, "dtc")
                self._add_hex_row("记录号:", "FF", 1, "record")
        elif sid == 0x22:
            self._add_did_rows()
        elif sid == 0x23:
            self._add_hex_row("地址(4B):", "00000000", 4, "addr")
            self._add_hex_row("字节数(1B):", "01", 1, "size")
        elif sid == 0x27:
            if sub is not None and sub % 2 == 0:   # 偶数=发密钥
                self._add_hex_row("密钥:", "", 0, "key")
        elif sid == 0x28:
            self._add_hex_row("通信类型:", "01", 1, "commtype")
        elif sid == 0x2E:
            self._add_hex_row("DID:", "F190", 2, "did")
            self._add_hex_row("数据:", "", 0, "data")
        elif sid == 0x2F:
            self._add_hex_row("DID:", "3200", 2, "did")
            self._add_hex_row("控制状态(1B):", "00", 1, "state")
        elif sid == 0x31:
            self._add_hex_row("例程ID:", "FF00", 2, "rid")
            self._add_hex_row("数据:", "", 0, "data")
        elif sid in (0x34, 0x35):
            self._add_hex_row("地址(4B):", "00000000", 4, "addr")
            self._add_hex_row("大小(4B):", "00000001", 4, "size")
        elif sid == 0x36:
            self._add_hex_row("块计数器:", "01", 1, "block")
            self._add_hex_row("数据:", "", 0, "data")

        self._update_preview()

    def _add_hex_row(self, label: str, default: str, nbytes: int, kind: str):
        """追加一个HEX参数行"""
        edit = QLineEdit(default)
        edit.setFont(_MONO)
        edit.setFixedWidth(160)
        edit.textChanged.connect(self._update_preview)
        self._param_form.addRow(label, edit)
        self._param_rows.append((edit, kind, nbytes))

    def _add_did_rows(self):
        """0x22多DID动态行（+ 添加DID按钮，参照CANoe）"""
        row = QHBoxLayout()
        hint = QLabel("DID列表（可多个，一次请求读取多个DID）:")
        hint.setStyleSheet("color: #888;")
        row.addWidget(hint)
        row.addStretch()
        add_btn = QPushButton("＋ 添加DID")
        add_btn.clicked.connect(lambda: self._append_did_row("F190"))
        row.addWidget(add_btn)
        self._did_grid.addLayout(row)
        self._append_did_row("F190")

    def _append_did_row(self, default: str):
        """追加一个DID行（输入框+删除按钮）"""
        if len(self._did_rows) >= 8:
            return
        row = QHBoxLayout()
        edit = QLineEdit(default)
        edit.setFont(_MONO)
        edit.setFixedWidth(100)
        edit.textChanged.connect(self._update_preview)

        def _remove():
            edit.deleteLater()
            del_btn.deleteLater()
            row.deleteLater()
            if (edit, del_btn) in [(e, b) for e, b in self._did_rows]:
                self._did_rows.remove(
                    next(p for p in self._did_rows if p[0] is edit))
            self._update_preview()

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(26, 24)
        del_btn.setToolTip("删除该DID")
        del_btn.clicked.connect(_remove)
        row.addWidget(edit)
        row.addWidget(del_btn)
        row.addStretch()
        self._did_grid.addLayout(row)
        self._did_rows.append((edit, del_btn))
        self._update_preview()

    def _on_sub_changed(self):
        """子功能切换: 重建附加参数行（如19的04/06需DTC号+记录号）

        QFormLayout中子功能行占前两项（label+下拉框），保留之，
        只删除其后追加的参数行（防止误删子功能下拉成野指针）
        """
        self._last_sub_value = self._current_sub()
        while self._param_form.count() > 2:
            item = self._param_form.takeAt(self._param_form.count() - 1)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._param_rows = []
        self._add_extra_params()

    def _on_sub_edited(self):
        """手输自定义子功能完成后: 值变化才重建参数行

        editingFinished在焦点离开时也触发（未改值时不应重建，
        否则会清掉已填的参数）；选中预定义项由
        currentIndexChanged处理，此处兜底纯手输路径
        （如19输入04后补DTC号+记录号行）。
        """
        if self._current_sub() == self._last_sub_value:
            return
        self._on_sub_changed()

    def _sync_did_container(self):
        """DID列表容器显隐: 当前服务为0x22"""
        self._did_container.setVisible(self._current_sid == 0x22)

    # ---------------- 请求构造 ----------------

    def _build_request(self):
        """构造请求字节（结构化参数），失败返回None"""
        sid = self._current_sid
        out = bytearray([sid])
        sub = self._current_sub()
        if sub == -1:
            return None   # 自定义子功能输入非法
        if sub is not None:
            out.append(sub)
        for edit, kind, nbytes in self._param_rows:
            if kind == "group":
                out += _parse_bytes(edit.text(), 3, b"\xFF\xFF\xFF")
            elif kind == "mask":
                out += _parse_bytes(edit.text(), 1, b"\xFF")
            elif kind in ("dtc",):
                out += _parse_bytes(edit.text(), 3, b"\xFF\xFF\xFF")
            elif kind == "record":
                out += _parse_bytes(edit.text(), 1, b"\xFF")
            elif kind in ("did", "rid"):
                out += _parse_bytes(edit.text(), 2, b"\x00\x00")
            elif kind == "state":
                out += _parse_bytes(edit.text(), 1, b"\x00")
            elif kind == "addr":
                out += _parse_bytes(edit.text(), 4, b"\x00" * 4)
            elif kind == "size":
                out += _parse_bytes(edit.text(), 1, b"\x01")
            elif kind in ("block", "commtype"):
                out += _parse_bytes(edit.text(), 1, b"\x01")
            elif kind == "xsize":
                out += _parse_bytes(edit.text(), 4, b"\x00" * 4)
            elif kind in ("key", "data"):
                t = edit.text().strip().replace(" ", "")
                if t:
                    if len(t) % 2:
                        t = "0" + t
                    try:
                        out += bytes.fromhex(t)
                    except ValueError:
                        return None
        # 0x22多DID
        if sid == 0x22:
            for edit, _btn in self._did_rows:
                out += _parse_bytes(edit.text(), 2, b"")
        return bytes(out)

    def _update_preview(self):
        """实时刷新请求预览与字段格式提示"""
        req = self._build_request()
        if req is None or not req:
            self._preview_label.setText("--")
            self._preview_len_label.setText("长度 0 字节 | 方向: 请求")
            self._preview_label.setStyleSheet("color: #F44336; padding: 4px;")
            return
        self._preview_label.setText(req.hex(" ").upper())
        self._preview_len_label.setText(
            f"长度 {len(req)} 字节 | 方向: 请求")
        self._preview_label.setStyleSheet("padding: 4px;")
        # HEX字段错误标红
        for edit, _kind, _n in self._param_rows:
            edit.setStyleSheet(
                "" if _hex_ok(edit.text())
                else "border: 1px solid #F44336;")
        for edit, _btn in self._did_rows:
            edit.setStyleSheet(
                "" if _hex_ok(edit.text())
                else "border: 1px solid #F44336;")

    def _clear_request(self):
        """清除响应显示（预览保留当前构造）"""
        self._pos_hex_label.setText("--")
        self._pos_meta_label.setText("")
        self._pos_tree.clear()
        self._neg_hex_label.setText("--")
        self._neg_nrc_label.setText("")

    # ---------------- 发送 ----------------

    def _send(self):
        req = self._build_request()
        self._dispatch(req, self._service_label())

    def _dispatch(self, req: bytes, label: str):
        """发送请求（服务库一键发送与手动构造共用路径）"""
        if not req:
            self._log_timing(label, None, None, 0.0,
                             False, "请求为空或格式错误")
            return
        if not self._uds_client:
            self._log_timing(label, req, None, 0.0, False, "未连接")
            return
        if self._worker_thread is not None:
            return

        self._send_btn.setEnabled(False)
        client = self._uds_client
        t0 = time.perf_counter()
        sid = req[0]

        worker = UdsWorker(client.send_raw, req)
        thread = QThread(self)
        worker.moveToThread(thread)

        def _finish(resp, elapsed_ms, status_ok, status_text):
            thread.quit()
            thread.wait()
            self._worker = None
            self._worker_thread = None
            self._send_btn.setEnabled(True)
            self._log_timing(label, req, resp, elapsed_ms,
                             status_ok, status_text)
            self._show_response(sid, resp, elapsed_ms)

        def _on_done(resp):
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if resp and resp[0] == 0x7F:
                nrc = resp[2] if len(resp) > 2 else 0
                _finish(resp, elapsed_ms, False,
                        f"负响应 0x{nrc:02X} ({_NRC_NAMES.get(nrc, 'unknown')})")
            elif resp:
                _finish(resp, elapsed_ms, True,
                        f"{UdsService.get_service_name(sid)} 正响应")
            else:
                _finish(resp, elapsed_ms, False, "无响应 (超时)")

        def _on_error(msg):
            _finish(None, (time.perf_counter() - t0) * 1000.0,
                    False, f"异常: {msg}")

        worker.finished.connect(_on_done)
        worker.error.connect(_on_error)
        thread.started.connect(worker.run)
        self._worker = worker
        self._worker_thread = thread
        thread.start()

    # ---------------- 响应解析 ----------------

    def _show_response(self, sid: int, resp, elapsed_ms: float):
        """右侧解析区呈现正/负响应"""
        if not resp:
            self._pos_hex_label.setText("--")
            self._pos_meta_label.setText("")
            self._neg_hex_label.setText("无响应（超时）")
            self._neg_nrc_label.setText("")
            return
        if resp[0] == 0x7F and len(resp) >= 3:
            # 负响应 → 橙色区
            self._neg_hex_label.setText(resp.hex(" ").upper())
            nrc = resp[2]
            name = _NRC_NAMES.get(nrc, "unknown")
            desc = _NRC_DESC_CN.get(nrc, "（未收录的NRC码，查ISO 14229-1附录A）")
            self._neg_nrc_label.setText(
                f"NRC 0x{nrc:02X} {name}\n"
                f"原因: {desc}\n"
                f"服务: 0x{resp[1]:02X} {_SERVICE_NAMES.get(resp[1], '')}")
            self._pos_hex_label.setText("--")
            self._pos_meta_label.setText("")
            self._pos_tree.clear()
            return
        # 正响应 → 绿色区
        self._neg_hex_label.setText("--")
        self._neg_nrc_label.setText("")
        self._pos_hex_label.setText(resp.hex(" ").upper())
        self._pos_meta_label.setText(
            f"耗时 {elapsed_ms:.1f} ms | {len(resp)} 字节")
        self._pos_tree.clear()
        root = self._parse_positive(sid, resp)
        if root is not None:
            self._pos_tree.addTopLevelItem(root)
            self._pos_tree.expandAll()

    def _parse_positive(self, sid: int, resp: bytes):
        """按服务解析正响应 → QTreeWidgetItem树"""
        root = QTreeWidgetItem(["服务响应",
                                f"0x{resp[0]:02X} (SID+0x40)"])
        try:
            if resp[0] == 0x50 and len(resp) >= 6:
                # 会话控制: 会话类型 + P2/P2*；同步底部当前会话显示
                sub = resp[1]
                sess = {0x01: "默认会话", 0x02: "编程会话",
                        0x03: "扩展会话", 0x04: "安全系统会话"}.get(sub, "--")
                QTreeWidgetItem(root, ["会话类型", f"0x{sub:02X} {sess}"])
                QTreeWidgetItem(root, [
                    "P2超时", f"{(resp[2] << 8 | resp[3]) * 0.001:.3f} s"])
                QTreeWidgetItem(root, [
                    "P2*超时", f"{(resp[4] << 8 | resp[5]) * 0.01:.3f} s"])
                self._session_label.setText(
                    f"当前会话: {sess} (0x{sub:02X})")
            elif resp[0] == 0x59 and len(resp) >= 3:
                sub = resp[1]
                QTreeWidgetItem(root, ["子功能", f"0x{sub:02X}"])
                if sub in (0x02, 0x0A) and len(resp) >= 4:
                    QTreeWidgetItem(root, [
                        "状态位可用掩码", f"0x{resp[2]:02X}"])
                    rec_node = QTreeWidgetItem(root, [
                        "DTC记录", f"{(len(resp) - 3) // 4} 条"])
                    for i in range(3, len(resp) - 3, 4):
                        dtc = resp[i:i + 3]
                        status = resp[i + 3]
                        bits = [v for b, v in _DTC_BITS.items()
                                if status & (1 << b)]
                        title = f"DTC 0x{dtc.hex().upper()}"
                        pcode = _dtc_text(dtc)
                        if pcode:
                            title += f" ({pcode})"
                        child = QTreeWidgetItem(rec_node, [
                            title, f"状态 0x{status:02X}"])
                        QTreeWidgetItem(child, [
                            "状态位", "、".join(bits) or "无激活位"])
                        rec_node.addChild(child)
                elif sub == 0x01 and len(resp) >= 6:
                    # 19 01响应: 59 01 <可用掩码> <格式标识> <计数2B>
                    # 部分ECU在计数后附带DTC明细（非标准但实际存在）
                    QTreeWidgetItem(root, [
                        "状态位可用掩码", f"0x{resp[2]:02X}"])
                    fmt_name = {
                        0x01: "ISO 15031-6 (P/C/B/U)", 0x02: "SAE J2012",
                        0x03: "ISO 14229 DTC", 0x04: "SAE J1939-73",
                    }.get(resp[3], "未知格式")
                    QTreeWidgetItem(root, [
                        "格式标识", f"0x{resp[3]:02X} {fmt_name}"])
                    QTreeWidgetItem(root, [
                        "DTC数量", f"{(resp[4] << 8) | resp[5]}"])
                    if len(resp) > 6:
                        rec_node = QTreeWidgetItem(root, [
                            "DTC记录", f"{(len(resp) - 6) // 4} 条（附带明细）"])
                        for i in range(6, len(resp) - 3, 4):
                            dtc = resp[i:i + 3]
                            status = resp[i + 3]
                            bits = [v for b, v in _DTC_BITS.items()
                                    if status & (1 << b)]
                            title = f"DTC 0x{dtc.hex().upper()}"
                            pcode = _dtc_text(dtc)
                            if pcode:
                                title += f" ({pcode})"
                            child = QTreeWidgetItem(rec_node, [
                                title, f"状态 0x{status:02X}"])
                            QTreeWidgetItem(child, [
                                "状态位", "、".join(bits) or "无激活位"])
                            rec_node.addChild(child)
                else:
                    QTreeWidgetItem(root, ["记录数据",
                                            resp[3:].hex(" ").upper()])
            elif resp[0] in (0x62, 0x6E) and len(resp) >= 3:
                did = (resp[1] << 8) | resp[2]
                QTreeWidgetItem(root, ["DID", f"0x{did:04X}"])
                data = resp[3:]
                QTreeWidgetItem(root, ["数据", data.hex(" ").upper() or "(空)"])
                try:
                    text = data.decode("ascii")
                    if text and all(32 <= ord(c) < 127 for c in text):
                        QTreeWidgetItem(root, ["ASCII", text])
                except UnicodeDecodeError:
                    pass
            elif resp[0] == 0x67 and len(resp) >= 2:
                QTreeWidgetItem(root, ["安全等级", f"0x{resp[1]:02X}"])
                QTreeWidgetItem(root, ["种子", resp[2:].hex(" ").upper()])
            elif resp[0] == 0x71 and len(resp) >= 4:
                QTreeWidgetItem(root, ["子功能", f"0x{resp[1]:02X}"])
                QTreeWidgetItem(root, [
                    "例程ID", f"0x{(resp[2] << 8) | resp[3]:04X}"])
                if len(resp) > 4:
                    QTreeWidgetItem(root, ["例程状态记录",
                                            resp[4:].hex(" ").upper()])
            elif resp[0] == 0x6D and len(resp) >= 6:
                QTreeWidgetItem(root, [
                    "地址", f"0x{resp[1:5].hex().upper()}"])
                if len(resp) >= 6:
                    QTreeWidgetItem(root, [
                        "数据", resp[5:].hex(" ").upper() or "(空)"])
            elif resp[0] == 0x74 and len(resp) >= 2:
                QTreeWidgetItem(root, ["块序号", f"0x{resp[1]:02X}"])
                QTreeWidgetItem(root, [
                    "数据长度", f"{len(resp) - 2} 字节"])
            elif resp[0] == 0x7C:
                QTreeWidgetItem(root, ["通信控制已确认", ""])
            else:
                QTreeWidgetItem(root, [
                    "原始数据", resp[1:].hex(" ").upper()])
        except Exception:
            QTreeWidgetItem(root, ["解析异常", "（原始数据见上方HEX）"])
        return root

    # ---------------- 通信结果日志 ----------------

    def _log_timing(self, svc_text: str, req, resp, elapsed_ms: float,
                    ok: bool, status_text: str):
        """通信结果发主窗口日志面板（单行摘要，与全局通信Trace互补）

        页面内不再内置Timing历史表；请求/响应完整hex由主窗口
        底部日志面板的通信Trace呈现，此处仅记一行结果摘要。
        """
        level = "SUCCESS" if ok else "ERROR"
        req_hex = req.hex(" ").upper() if req else "--"
        resp_hex = resp.hex(" ").upper() if resp else "--"
        self.business_log.emit(
            f"{svc_text} | Req: {req_hex} | Resp: {resp_hex} | "
            f"耗时 {elapsed_ms:.1f} ms | {status_text}", level)

    def closeEvent(self, event):
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        super().closeEvent(event)
