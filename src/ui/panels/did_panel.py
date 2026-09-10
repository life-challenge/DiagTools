"""DID读取/写入面板"""

import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                              QHeaderView, QLineEdit, QFileDialog, QCheckBox, QSpinBox, QGridLayout)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from src.business.did_manager import DidManager, DidDefinition
from src.ui.async_uds import UdsWorker
from src.utils.paths import get_resource_path


class DidPanel(QWidget):
    """DID读取/写入面板"""

    # 业务日志转发(msg, level): 面板不再内置日志窗口，统一由主窗口业务日志呈现
    business_log = pyqtSignal(str, str)

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        self._did_manager = DidManager()
        self._poll_timer = None
        # 后台任务队列: UDS读写必须在后台线程执行，真实总线上等ECU响应
        # 可达数秒，同步调用会冻结整个界面（表现为"点击无反应"）
        self._queue = []          # 待处理任务 [(kind, did_id, arg), ...]
        self._worker = None       # 保持引用防GC（见async_uds.UdsWorker说明）
        self._thread = None
        self._init_ui()

    def set_uds_client(self, client):
        self._uds_client = client

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 工具栏
        toolbar = QHBoxLayout()
        self._load_def_btn = QPushButton("加载定义")
        self._load_def_btn.clicked.connect(self._load_definitions)
        toolbar.addWidget(self._load_def_btn)

        self._read_btn = QPushButton("读取选中")
        self._read_btn.clicked.connect(self._read_selected)
        toolbar.addWidget(self._read_btn)

        self._read_all_btn = QPushButton("批量读取")
        self._read_all_btn.clicked.connect(self._read_all)
        toolbar.addWidget(self._read_all_btn)

        self._write_btn = QPushButton("写入")
        self._write_btn.clicked.connect(self._write_did)
        toolbar.addWidget(self._write_btn)

        self._export_btn = QPushButton("导出")
        self._export_btn.clicked.connect(self._export_csv)
        toolbar.addWidget(self._export_btn)

        self._poll_check = QCheckBox("自动刷新")
        self._poll_check.stateChanged.connect(self._toggle_polling)
        toolbar.addWidget(self._poll_check)

        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(100, 60000)
        self._interval_spin.setValue(1000)
        self._interval_spin.setSuffix(" ms")
        toolbar.addWidget(self._interval_spin)

        toolbar.addStretch()

        toolbar.addWidget(QLabel("搜索:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("DID或名称")
        self._search_edit.setFixedWidth(140)
        self._search_edit.textChanged.connect(self._apply_search)
        toolbar.addWidget(self._search_edit)
        layout.addLayout(toolbar)

        # DID列表表格
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["DID", "名称", "类型", "当前值", "原始数据", "单位", "状态"])
        header = self._table.horizontalHeader()
        # 名称列独占弹性空间；数据列随内容自适应，保证“当前值/原始数据”
        # 不被长名称挤压显示不全
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3, 4, 5):
            header.setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        # 超长名称以省略号截断（完整内容见tooltip）
        self._table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._table.setWordWrap(False)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

        # 手动操作区（§5: DID + 读取/写入 + Request/Response/Decoded）
        manual_group = QGroupBox("手动读取/写入")
        manual_layout = QGridLayout()
        mono = QFont("Consolas", 10)

        manual_layout.addWidget(QLabel("DID:"), 0, 0)
        self._manual_did = QLineEdit()
        self._manual_did.setPlaceholderText("如 F190")
        self._manual_did.setMaximumWidth(100)
        manual_layout.addWidget(self._manual_did, 0, 1)

        read_btn = QPushButton("读取")
        read_btn.clicked.connect(self._manual_read)
        manual_layout.addWidget(read_btn, 0, 2)

        write_btn = QPushButton("写入")
        write_btn.clicked.connect(self._manual_write)
        manual_layout.addWidget(write_btn, 0, 3)

        manual_layout.addWidget(QLabel("写入数据(HEX):"), 0, 4)
        self._manual_data = QLineEdit()
        self._manual_data.setPlaceholderText("如: 48 65 6C 6C 6F")
        manual_layout.addWidget(self._manual_data, 0, 5)

        def _mk_val_label():
            lbl = QLabel("--")
            lbl.setFont(mono)
            lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            return lbl

        for row, title in enumerate(["Request:", "Response:", "Decoded:"], start=1):
            key = QLabel(title)
            key.setStyleSheet("color: #888;")
            manual_layout.addWidget(key, row, 0)
        self._lbl_req = _mk_val_label()
        self._lbl_resp = _mk_val_label()
        self._lbl_decoded = _mk_val_label()
        manual_layout.addWidget(self._lbl_req, 1, 1, 1, 5)
        manual_layout.addWidget(self._lbl_resp, 2, 1, 1, 5)
        manual_layout.addWidget(self._lbl_decoded, 3, 1, 1, 5)
        manual_layout.setColumnStretch(5, 1)

        manual_group.setLayout(manual_layout)
        layout.addWidget(manual_group)

        layout.addStretch()

    def _load_definitions(self):
        start_dir = get_resource_path("did_definitions")
        filepath, _ = QFileDialog.getOpenFileName(
            self, "加载DID定义", start_dir,
            "DID定义/调查表 (*.json *.xlsx *.xlsm);;JSON Files (*.json);;"
            "调查表Excel (*.xlsx *.xlsm)")
        if filepath:
            self.import_definitions(filepath)

    def import_definitions(self, filepath: str) -> int:
        """程序化导入DID定义（菜单/项目加载入口），返回导入数量

        支持JSON与OEM调查表xlsx（解析其中的dids sheet）
        """
        import os as _os
        if _os.path.splitext(filepath)[1].lower() in (".xlsx", ".xlsm"):
            try:
                from src.business.survey_xlsx_parser import parse_survey_xlsx
                dids = parse_survey_xlsx(filepath).get("dids", [])
            except Exception as e:
                self._log(f"调查表解析失败: {e}", "ERROR")
                return 0
            count = 0
            for item in dids:
                try:
                    self._did_manager.add_definition(
                        DidDefinition.from_dict(item))
                    count += 1
                except Exception:
                    pass  # 无效条目跳过（from_dict已校验did_id）
            if count > 0:
                self._refresh_table()
                self._log(f"从调查表加载了 {count} 个DID定义: "
                          f"{_os.path.basename(filepath)}")
            else:
                self._log(f"调查表内未解析到DID定义: {filepath}")
            return count

        count = self._did_manager.load_definitions_from_json(filepath)
        if count > 0:
            self._refresh_table()
            self._log(f"加载了 {count} 个DID定义: {os.path.basename(filepath)}")
        else:
            self._log(f"DID定义导入失败或为空: {filepath}")
        return count

    def _refresh_table(self):
        definitions = self._did_manager.definitions
        self._table.setRowCount(len(definitions))

        for row, (did_id, defn) in enumerate(sorted(definitions.items())):
            self._table.setItem(row, 0, QTableWidgetItem(f"0x{did_id:04X}"))
            name_item = QTableWidgetItem(defn.name)
            name_item.setToolTip(defn.description or defn.name)
            self._table.setItem(row, 1, name_item)
            self._table.setItem(row, 2, QTableWidgetItem(defn.data_type))

            val = self._did_manager.get_value(did_id)
            if val:
                self._table.setItem(row, 3, QTableWidgetItem(val.display_value))
                self._table.setItem(row, 4, QTableWidgetItem(val.raw_data.hex(" ")))
                # 值域颜色
                if val.is_in_range is not None:
                    color = QColor("#4CAF50") if val.is_in_range else QColor("#F44336")
                    self._table.item(row, 3).setForeground(color)
            else:
                self._table.setItem(row, 3, QTableWidgetItem("--"))
                self._table.setItem(row, 4, QTableWidgetItem("--"))

            self._table.setItem(row, 5, QTableWidgetItem(defn.unit))
            self._table.setItem(row, 6, QTableWidgetItem(""))

    def _read_selected(self):
        row = self._table.currentRow()
        if row < 0:
            return
        did_text = self._table.item(row, 0).text()
        did_id = int(did_text, 16)
        self._do_read_did(did_id)

    def _read_all(self):
        for did_id in list(self._did_manager.definitions):
            self._do_read_did(did_id)

    def _do_read_did(self, did_id: int, show_detail: bool = False):
        if not self._uds_client:
            self._log("未连接UDS客户端")
            if show_detail:
                self._show_detail(did_id, None, None, "未连接")
            return
        self._queue.append(("read", did_id, show_detail))
        self._pump_queue()

    def _do_write_did(self, did_id: int, data: bytes):
        if not self._uds_client:
            self._log("未连接UDS客户端")
            return
        self._queue.append(("write", did_id, data))
        self._pump_queue()

    # ---------------- 后台任务队列 ----------------

    def _pump_queue(self):
        """取出队首任务，在后台线程执行阻塞式UDS调用"""
        if self._thread is not None or not self._queue:
            return
        kind, did_id, arg = self._queue.pop(0)
        client = self._uds_client
        if client is None:  # 队列执行中断开连接
            self._log(f"操作 0x{did_id:04X} 取消: 已断开连接")
            QTimer.singleShot(0, self._pump_queue)
            return
        if kind == "read":
            func, args = client.read_data_by_identifier, (did_id,)
        else:
            func, args = client.write_data_by_identifier, (did_id, arg)
        worker = UdsWorker(func, *args)
        thread = QThread(self)
        worker.moveToThread(thread)
        task = (kind, did_id, arg)
        worker.finished.connect(
            lambda resp, t=task: self._on_task_done(t, resp))
        worker.error.connect(
            lambda msg, t=task: self._on_task_error(t, msg))
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(self._on_thread_finished)
        self._worker = worker
        self._thread = thread
        thread.start()

    def _on_thread_finished(self):
        self._worker = None
        self._thread = None
        self._pump_queue()  # 继续处理队列中剩余任务

    def _on_task_done(self, task, resp):
        """后台读写完成（GUI线程回调）: 更新表格/手动操作区/日志"""
        kind, did_id, arg = task
        if kind == "read":
            if resp and len(resp) >= 3 and resp[0] == 0x62:
                raw_data = resp[3:]
                val = self._did_manager.update_value(did_id, raw_data)
                self._log(f"读取 0x{did_id:04X}: {val.display_value}",
                          "SUCCESS")
                if arg:
                    self._show_detail(did_id, resp, resp,
                                       self._decode_payload(raw_data))
                self._refresh_table()
            else:
                detail = self._describe_bad_resp(resp)
                self._log(f"读取 0x{did_id:04X} 失败: {detail}", "ERROR")
                if arg:
                    self._show_detail(did_id, resp, None, detail)
        else:
            data = arg
            ok = bool(resp and resp[0] == 0x6E)
            self._log(f"写入 0x{did_id:04X} {'成功' if ok else '失败'}",
                      "SUCCESS" if ok else "ERROR")
            # 手动操作区展示写入详情（§5）
            req = bytes([0x2E, (did_id >> 8) & 0xFF, did_id & 0xFF]) + data
            self._lbl_req.setText(req.hex(" ").upper())
            self._lbl_resp.setText(resp.hex(" ").upper() if resp else "--")
            self._lbl_decoded.setText("写入成功" if ok else "写入失败")
            if not ok and resp and resp[0] == 0x7F and len(resp) > 2:
                self._lbl_decoded.setText(f"负响应 0x{resp[2]:02X}")

    def _on_task_error(self, task, msg: str):
        kind, did_id, arg = task
        self._log(f"操作 0x{did_id:04X} 异常: {msg}", "ERROR")
        if kind == "read" and arg:
            self._show_detail(did_id, None, None, f"异常: {msg}")

    @staticmethod
    def _describe_bad_resp(resp) -> str:
        """失败响应明细: 超时/负响应NRC/意外格式"""
        if resp is None:
            return "无响应(超时)"
        if len(resp) >= 3 and resp[0] == 0x7F:
            return f"负响应 NRC 0x{resp[2]:02X}"
        return f"意外响应 {resp.hex(' ').upper()}"

    @staticmethod
    def _decode_payload(payload: bytes) -> str:
        """Decoded优先按ASCII显示（如VIN），不可打印内容回退HEX"""
        try:
            text = payload.decode("ascii").strip("\x00 ")
            if text and all(32 <= ord(c) < 127 for c in text):
                return text
        except UnicodeDecodeError:
            pass
        return payload.hex(" ").upper()

    def _show_detail(self, did_id: int, req, resp, decoded: str):
        """手动操作区显示 Request/Response/Decoded（§5）"""
        req_bytes = bytes([0x22, (did_id >> 8) & 0xFF, did_id & 0xFF])
        self._lbl_req.setText(req_bytes.hex(" ").upper())
        self._lbl_resp.setText(resp.hex(" ").upper() if resp else "--")
        self._lbl_decoded.setText(decoded or "--")

    def _write_did(self):
        row = self._table.currentRow()
        if row < 0:
            return
        did_text = self._table.item(row, 0).text()
        did_id = int(did_text, 16)
        data_hex = self._manual_data.text().strip()
        if not data_hex:
            self._log("请输入写入数据")
            return
        try:
            data = bytes.fromhex(data_hex.replace(" ", ""))
            self._do_write_did(did_id, data)
        except ValueError:
            self._log("HEX数据格式错误")

    def _manual_read(self):
        did_text = self._manual_did.text().strip()
        if not did_text:
            return
        try:
            did_id = int(did_text.replace("0x", ""), 16)
            self._do_read_did(did_id, show_detail=True)
        except ValueError:
            self._log("DID格式错误")

    def _manual_write(self):
        did_text = self._manual_did.text().strip()
        data_hex = self._manual_data.text().strip()
        if not did_text or not data_hex:
            self._log("请输入DID和写入数据")
            return
        try:
            did_id = int(did_text.replace("0x", ""), 16)
            data = bytes.fromhex(data_hex.replace(" ", ""))
            self._do_write_did(did_id, data)
        except ValueError:
            self._log("数据格式错误")

    def _apply_search(self, text: str):
        """按DID或名称过滤表格行"""
        text = text.strip().upper()
        for row in range(self._table.rowCount()):
            if not text:
                self._table.setRowHidden(row, False)
                continue
            did_cell = self._table.item(row, 0)
            name_cell = self._table.item(row, 1)
            match = (did_cell and text in did_cell.text().upper()) or \
                    (name_cell and text in name_cell.text().upper())
            self._table.setRowHidden(row, not match)

    def _export_csv(self):
        """导出当前DID表到CSV"""
        filepath, _ = QFileDialog.getSaveFileName(
            self, "导出DID数据", "did_data.csv", "CSV Files (*.csv)")
        if not filepath:
            return
        import csv
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["DID", "名称", "类型", "当前值", "原始数据", "单位"])
            for row in range(self._table.rowCount()):
                writer.writerow([
                    self._table.item(row, c).text() if self._table.item(row, c) else ""
                    for c in range(6)])
        self._log(f"已导出到 {filepath}")

    def _toggle_polling(self, state):
        if state == Qt.CheckState.Checked.value:
            interval = self._interval_spin.value() / 1000.0
            did_ids = list(self._did_manager.definitions.keys())
            if did_ids and self._uds_client:
                self._did_manager.start_polling(
                    did_ids, self._uds_client, interval,
                    lambda did_id, val: self._refresh_table())
                self._log(f"启动轮询: {len(did_ids)} 个DID, 间隔 {interval}s")
        else:
            self._did_manager.stop_polling()
            self._log("停止轮询")

    def _log(self, msg: str, level: str = "INFO"):
        # 面板不内置日志窗口，统一转发到主窗口业务日志（信号线程安全，
        # 后台任务回调线程发射自动降级为队列投递）
        self.business_log.emit(msg, level)
