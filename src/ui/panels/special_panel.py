"""特殊功能面板（ECU Definition 配置驱动）

从当前 ECU 的 ecu.json `special_functions` 列表生成可执行功能入口，
支持四种动作类型:
  routine  -> 0x31 RoutineControl（sub_func=01 启动）
  did      -> 0x22 ReadDataByIdentifier
  reset    -> 0x11 ECUReset（reset_type 可配）
  raw      -> 任意HEX请求

执行走 UdsWorker 后台线程，结果区显示原始响应与正/负响应判定。
新增特殊功能只需修改 ecu.json，无需改代码。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QPushButton, QLabel, QTextEdit,
    QSplitter
)
from PyQt6.QtCore import Qt, QThread

from src.log.log_manager import get_log_manager
from src.protocol.uds_services import (
    UdsService, NRC_CODES)
from src.ui.async_uds import UdsWorker


def _build_request(func: dict) -> bytes:
    """把单条特殊功能定义翻译为UDS请求字节; 无效定义返回None"""
    ftype = (func.get("type") or "").strip().lower()
    try:
        if ftype == "routine":
            if not func.get("id"):
                return None
            return UdsService.encode_routine_control(
                0x01, int(func["id"], 16))
        if ftype == "did":
            if not func.get("id"):
                return None
            return UdsService.encode_read_did(int(func["id"], 16))
        if ftype == "reset":
            return UdsService.encode_ecu_reset(
                int(func.get("reset_type") or "01", 16))
        if ftype == "raw":
            data = (func.get("data") or "").replace(" ", "")
            return bytes.fromhex(data) if data else None
    except (ValueError, TypeError):
        return None
    return None


class SpecialPanel(QWidget):
    """ECU特殊功能执行面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logger = get_log_manager().get_app_logger()
        self._uds_client = None
        self._functions = []
        self._worker = None   # UdsWorker引用（防GC）
        self._thread = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        self._lbl_ecu = QLabel("当前ECU: --")
        self._lbl_ecu.setStyleSheet("color: #888;")
        layout.addWidget(self._lbl_ecu)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(
            ["功能名称", "类型", "请求", "描述"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(lambda _: self._execute())
        splitter.addWidget(self._table)

        # 结果区
        result_box = QWidget()
        result_lay = QVBoxLayout(result_box)
        result_lay.setContentsMargins(0, 6, 0, 0)
        btn_row = QHBoxLayout()
        self._btn_exec = QPushButton("执行所选功能")
        self._btn_exec.setObjectName("btn_primary")
        self._btn_exec.clicked.connect(self._execute)
        btn_row.addWidget(self._btn_exec)
        self._btn_exec.setEnabled(False)
        btn_row.addStretch()
        result_lay.addLayout(btn_row)
        self._result_view = QTextEdit()
        self._result_view.setReadOnly(True)
        self._result_view.setMaximumHeight(140)
        result_lay.addWidget(self._result_view)
        splitter.addWidget(result_box)
        splitter.setSizes([300, 120])

        layout.addWidget(splitter, 1)

    # ---------------- 外部注入 ----------------

    def set_uds_client(self, client):
        self._uds_client = client

    def set_ecu(self, ecu_def):
        """加载ECU定义中的特殊功能列表"""
        self._functions = list(getattr(ecu_def, "special_functions", []) or [])
        self._lbl_ecu.setText(
            f"当前ECU: {ecu_def.name}"
            f"（{len(self._functions)} 项特殊功能，双击行或点按钮执行）")
        self._refresh_table()

    # ---------------- 内部 ----------------

    def _refresh_table(self):
        self._table.setRowCount(len(self._functions))
        for row, func in enumerate(self._functions):
            request = _build_request(func)
            self._table.setItem(row, 0, QTableWidgetItem(
                str(func.get("name", f"功能{row + 1}"))))
            self._table.setItem(row, 1, QTableWidgetItem(
                str(func.get("type", ""))))
            self._table.setItem(row, 2, QTableWidgetItem(
                request.hex(" ").upper() if request else "配置无效"))
            self._table.setItem(row, 3, QTableWidgetItem(
                str(func.get("description", ""))))
        self._btn_exec.setEnabled(bool(self._functions))

    def _selected_function(self):
        row = self._table.currentRow()
        if 0 <= row < len(self._functions):
            return row, self._functions[row]
        return -1, None

    def _execute(self):
        if self._uds_client is None:
            self._result_view.setHtml(
                '<span style="color:#F44336">未连接：请先建立诊断连接</span>')
            return
        row, func = self._selected_function()
        if func is None:
            self._result_view.setHtml(
                '<span style="color:#FF9800">请先在表格中选择一个功能</span>')
            return
        request = _build_request(func)
        if request is None:
            self._result_view.setHtml(
                f'<span style="color:#F44336">'
                f'功能 [{func.get("name")}] 配置无效，请检查 ecu.json</span>')
            return

        name = func.get("name", "")
        self._result_view.setPlainText(f"执行 [{name}] 请求: "
                                       f"{request.hex(' ').upper()} ...")
        self._btn_exec.setEnabled(False)

        self._worker = UdsWorker(self._uds_client.send_raw, request)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._worker.finished.connect(
            lambda resp, n=name, req=request:
            self._on_result(n, req, resp))
        self._worker.error.connect(
            lambda err, n=name: self._on_error(n, err))
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    def _on_result(self, name: str, request: bytes, resp):
        self._cleanup_thread()
        self._btn_exec.setEnabled(True)
        if not resp:
            self._result_view.setPlainText(
                f"[{name}] 无响应（超时）")
            return
        if UdsService.is_negative_response(resp):
            nrc = resp[2] if len(resp) > 2 else 0
            desc = NRC_CODES.get(nrc, "未知")
            color = "#F44336"
            verdict = f"负响应 NRC=0x{nrc:02X} {desc}"
            self._logger.warning("特殊功能[%s]负响应: %s", name, verdict)
        else:
            color = "#4CAF50"
            verdict = "正响应"
            self._logger.info("特殊功能[%s]执行成功", name)
        self._result_view.setHtml(
            f"<b>[{name}]</b> <span style='color:{color}'>{verdict}</span><br>"
            f"请求: {request.hex(' ').upper()}<br>"
            f"响应: {resp.hex(' ').upper()}")

    def _on_error(self, name: str, err: str):
        self._cleanup_thread()
        self._btn_exec.setEnabled(True)
        self._result_view.setHtml(
            f"<b>[{name}]</b> <span style='color:#F44336'>执行异常: "
            f"{err}</span>")
        self._logger.error("特殊功能[%s]异常: %s", name, err)

    def _cleanup_thread(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None
        self._worker = None
