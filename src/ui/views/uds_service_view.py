"""UDS服务手动执行视图（V2.1 §4 高级诊断）

工程师入口: 手动构造并发送任意UDS服务，显示请求/响应/耗时，
并在下方Timing表中累计每次调用的响应时间。
"""

import time
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QFormLayout, QSplitter
)
from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QFont, QColor
from src.ui.async_uds import UdsWorker
from src.protocol.uds_services import UdsService

# 常用服务 (SID, 说明)
_SERVICES = [
    (0x10, "DiagnosticSessionControl"),
    (0x11, "ECUReset"),
    (0x14, "ClearDiagnosticInformation"),
    (0x19, "ReadDTCInformation"),
    (0x22, "ReadDataByIdentifier"),
    (0x23, "ReadMemoryByAddress"),
    (0x27, "SecurityAccess"),
    (0x28, "CommunicationControl"),
    (0x2E, "WriteDataByIdentifier"),
    (0x2F, "InputOutputControlByIdentifier"),
    (0x31, "RoutineControl"),
    (0x34, "RequestDownload"),
    (0x35, "RequestUpload"),
    (0x36, "TransferData"),
    (0x37, "RequestTransferExit"),
    (0x3E, "TesterPresent"),
    (0x85, "ControlDTCSetting"),
]

# 常用负响应码
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

_MONO = QFont("Consolas", 10)

# 各服务的参数填写提示（服务切换时刷新，避免残留上一服务的参数）
_PARAM_HINTS = {
    0x10: "子功能, 如 03(扩展会话)/02(编程会话)",
    0x11: "子功能, 如 01(硬复位)/03(软复位)",
    0x14: "DTC组(3字节), 如 FF FF FF(清除全部)",
    0x19: "子功能+参数, 如 02 FF FF FF(按状态读DTC)",
    0x22: "DID(2字节), 如 F1 90",
    0x23: "地址+字节数, 如 00 00 00 08 01",
    0x27: "子功能, 如 01(发种子)/02(发密钥)",
    0x28: "子功能+通信类型, 如 00 01",
    0x2E: "DID(2字节)+数据, 如 F1 90 31 32",
    0x2F: "DID(2字节)+控制+状态, 如 F1 90 03 00",
    0x31: "子功能+例程ID(2字节), 如 01 02 03",
    0x34: "地址+字节数+格式, 如 00 00 00 08 01 10",
    0x35: "地址+字节数+格式, 如 00 00 00 08 01 10",
    0x36: "块计数器+数据, 如 01 AA BB CC",
    0x37: "校验等附加数据, 可留空",
    0x3E: "子功能, 如 80(抑制正响应)",
    0x85: "子功能, 如 01(开DTC)/02(关DTC)",
}


class UdsServiceView(QWidget):
    """UDS服务手动执行 + Timing统计"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._uds_client = None
        self._worker = None
        self._worker_thread = None
        self._init_ui()

    def set_uds_client(self, client):
        self._uds_client = client

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter)

        # ---- 发送区 ----
        send_group = QGroupBox("UDS服务发送")
        form = QFormLayout(send_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        svc_row = QHBoxLayout()
        self._service_combo = QComboBox()
        for sid, name in _SERVICES:
            self._service_combo.addItem(f"0x{sid:02X} - {name}", sid)
        self._service_combo.setMinimumWidth(320)
        svc_row.addWidget(self._service_combo)
        svc_row.addStretch()
        form.addRow("服务:", svc_row)
        self._service_combo.currentIndexChanged.connect(
            self._on_service_changed)

        param_row = QHBoxLayout()
        self._param_edit = QLineEdit()
        self._param_edit.setPlaceholderText(
            "参数(不含SID), 留空表示仅发SID")
        self._param_edit.setFont(_MONO)
        self._param_edit.textChanged.connect(self._update_preview)
        param_row.addWidget(self._param_edit)
        self._send_btn = QPushButton("发送")
        self._send_btn.setFixedWidth(80)
        self._send_btn.clicked.connect(self._send)
        param_row.addWidget(self._send_btn)
        form.addRow("参数:", param_row)
        # 请求/响应/状态不再单独展示——统一由下方Timing历史表呈现，
        # 输入时的实时预览悬浮在参数输入框tooltip上，避免同一报文显示两次

        splitter.addWidget(send_group)

        # ---- Timing历史 ----
        timing_group = QGroupBox("Timing 历史")
        tl = QVBoxLayout(timing_group)
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["时间", "服务", "Request", "Response", "耗时(ms)", "状态"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tl.addWidget(self._table)
        splitter.addWidget(timing_group)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)

        self._on_service_changed()  # 初始化提示与预览

    def _on_service_changed(self):
        """服务切换: 清空残留参数并按当前服务刷新填写提示"""
        sid = self._service_combo.currentData()
        self._param_edit.setPlaceholderText(
            f"参数(不含SID), {_PARAM_HINTS[sid]}"
            if sid in _PARAM_HINTS else "参数(不含SID), 十六进制字节; 留空表示仅发SID")
        self._param_edit.clear()
        self._update_preview()

    def _update_preview(self):
        """随服务/参数输入实时预览将要发送的字节（tooltip悬浮显示）"""
        req = self._build_request()
        if req is None:
            self._param_edit.setToolTip("参数HEX格式错误")
            self._param_edit.setStyleSheet("border: 1px solid #F44336;")
        else:
            self._param_edit.setToolTip(f"将发送: {req.hex(' ').upper()}")
            self._param_edit.setStyleSheet("")

    # ---------------- 发送 ----------------

    def _build_request(self):
        """根据界面输入构造请求字节，失败返回None"""
        sid = self._service_combo.currentData()
        text = self._param_edit.text().strip()
        params = b""
        if text:
            try:
                params = bytes.fromhex(text.replace(" ", ""))
            except ValueError:
                return None
        return bytes([sid]) + params

    def _send(self):
        req = self._build_request()
        svc_text = self._service_combo.currentText()
        if req is None:
            # 结果统一在Timing历史中反馈，不再设独立状态标签
            self._append_timing(svc_text, None, None, 0.0,
                                False, "参数HEX格式错误")
            return
        if not self._uds_client:
            self._append_timing(svc_text, req, None, 0.0, False, "未连接")
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
            self._append_timing(svc_text, req, resp, elapsed_ms, status_ok, status_text)

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
            _finish(None, (time.perf_counter() - t0) * 1000.0, False, f"异常: {msg}")

        worker.finished.connect(_on_done)
        worker.error.connect(_on_error)
        thread.started.connect(worker.run)
        self._worker = worker
        self._worker_thread = thread
        thread.start()

    def _append_timing(self, svc_text: str, req, resp, elapsed_ms: float,
                       ok: bool, status_text: str):
        row = self._table.rowCount()
        self._table.insertRow(row)
        cells = [
            time.strftime("%H:%M:%S"),
            svc_text,
            req.hex(" ").upper() if req else "--",
            resp.hex(" ").upper() if resp else "--",
            f"{elapsed_ms:.1f}",
            status_text,
        ]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if col in (0, 2, 3, 4):
                item.setFont(_MONO)
            if col == 5 and not ok:
                item.setForeground(QColor(COLOR_ERR))
            self._table.setItem(row, col, item)
        self._table.scrollToBottom()

    def closeEvent(self, event):
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        super().closeEvent(event)


COLOR_OK = "#4CAF50"
COLOR_ERR = "#F44336"
