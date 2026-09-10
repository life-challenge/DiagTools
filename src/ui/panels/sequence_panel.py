"""自定义序列面板"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                              QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                              QHeaderView, QLineEdit, QSpinBox,
                              QCheckBox, QFileDialog, QFormLayout,
                              QProgressBar, QAbstractItemView, QApplication)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QFont
from src.business.sequence_manager import SequenceManager, UdsSequence, SequenceStep
import threading


class StepTable(QTableWidget):
    """支持按住行拖拽排序的步骤表格。
    行内嵌有 QCheckBox/QSpinBox 等控件，QTableWidget 自带拖放不搬移它们且无 moveRow，
    故自行追踪拖动位置，实际行搬移交由外部回调（读取→重排→重建）完成。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_row = -1
        self._drag_moved = False   # 本次按住期间是否实际发生过行搬移
        self._press_pos = None
        self._move_handler = None  # callable(src, dst)，由面板设置

    def mousePressEvent(self, e: QMouseEvent):
        super().mousePressEvent(e)
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_row = self.rowAt(e.position().toPoint().y())
            self._press_pos = e.position()
            self._drag_moved = False

    def mouseMoveEvent(self, e: QMouseEvent):
        # 只有先按住过某一行才启动拖动，避免空表格拖动异常
        if self._drag_row >= 0:
            # 超过系统拖拽阈值才视为行排序拖拽——
            # 避免点击/双击编辑单元格时的轻微抖动误触发整行重排
            if (self._press_pos is not None and
                    (e.position() - self._press_pos).manhattanLength()
                    < QApplication.startDragDistance()):
                return
            y = e.position().toPoint().y()
            target = self.rowAt(y)
            if target < 0:
                # 拖到表格空白区: 视为拖到最前/最后（取决于相对拖动行的位置）
                target = (self.rowCount() - 1) if y > self.rowViewportPosition(self._drag_row) else 0
            if 0 <= target < self.rowCount() and target != self._drag_row:
                self._move_row(self._drag_row, target)
                self._drag_moved = True
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        # 仅在真正发生过拖拽搬移后才整行选中并定位到名称列；
        # 普通点击交给基类处理——否则每次单击松开都会把当前单元格
        # 强制跳到名称列（点击请求HEX后焦点"跳走"的根因）
        if self._drag_row >= 0 and self._drag_moved:
            row = self.rowAt(e.position().toPoint().y())
            if row < 0:
                row = self._drag_row
            self._drag_row = -1
            self._drag_moved = False
            self.selectRow(row)
            self.setCurrentCell(row, 1)
            self.setFocus()
        else:
            self._drag_row = -1
            super().mouseReleaseEvent(e)

    def set_move_handler(self, handler):
        self._move_handler = handler

    def _move_row(self, src: int, dst: int):
        """将src行插入到dst行位置，实际搬移由面板回调完成（重建行内容）"""
        if self._move_handler is None:
            return
        self._move_handler(src, dst)
        self._drag_row = dst


class SequencePanel(QWidget):
    """自定义序列面板"""

    # 业务日志转发(msg, level): 面板不再内置日志窗口，统一由主窗口业务日志呈现。
    # 注: 执行回调在后台线程发射，信号自动队列投递到GUI线程，线程安全
    business_log = pyqtSignal(str, str)

    # 请求直达安全算法页（加载/管理DLL算法，供安全访问步骤计算密钥）
    open_security_requested = pyqtSignal()

    def __init__(self, uds_client=None, parent=None):
        super().__init__(parent)
        self._uds_client = uds_client
        self._seq_manager = SequenceManager()
        self._current_seq = UdsSequence("新建序列")
        self._exec_thread = None
        self._init_ui()

    def set_uds_client(self, client):
        self._uds_client = client

    def set_key_generator(self, fn):
        """注入密钥计算钩子: fn(level, seed) -> key（安全访问步骤用）"""
        self._seq_manager.set_key_generator(fn)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 序列参数
        param_group = QGroupBox("序列参数")
        param_form = QFormLayout()

        self._name_edit = QLineEdit("新建序列")
        param_form.addRow("名称:", self._name_edit)

        self._desc_edit = QLineEdit()
        param_form.addRow("描述:", self._desc_edit)

        loop_layout = QHBoxLayout()
        self._loop_spin = QSpinBox()
        self._loop_spin.setRange(1, 999)
        self._loop_spin.setValue(1)
        loop_layout.addWidget(self._loop_spin)
        loop_layout.addWidget(QLabel("循环间隔(ms):"))
        self._loop_delay_spin = QSpinBox()
        self._loop_delay_spin.setRange(0, 60000)
        self._loop_delay_spin.setValue(0)
        loop_layout.addWidget(self._loop_delay_spin)
        param_form.addRow("循环次数:", loop_layout)

        opt_layout = QHBoxLayout()
        self._stop_on_error_check = QCheckBox("遇错停止")
        self._stop_on_error_check.setChecked(True)
        opt_layout.addWidget(self._stop_on_error_check)
        self._auto_tp_check = QCheckBox("自动TesterPresent")
        opt_layout.addWidget(self._auto_tp_check)
        param_form.addRow(opt_layout)

        param_group.setLayout(param_form)
        layout.addWidget(param_group)

        # 步骤编辑表格
        step_toolbar = QHBoxLayout()
        add_btn = QPushButton("+ 添加步骤")
        add_btn.clicked.connect(self._add_step)
        step_toolbar.addWidget(add_btn)
        del_btn = QPushButton("- 删除步骤")
        del_btn.clicked.connect(self._delete_step)
        step_toolbar.addWidget(del_btn)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_steps)
        step_toolbar.addWidget(clear_btn)
        up_btn = QPushButton("↑ 上移")
        up_btn.setToolTip("将选中步骤上移一步")
        up_btn.clicked.connect(lambda: self._move_selected(-1))
        step_toolbar.addWidget(up_btn)
        down_btn = QPushButton("↓ 下移")
        down_btn.setToolTip("将选中步骤下移一步")
        down_btn.clicked.connect(lambda: self._move_selected(1))
        step_toolbar.addWidget(down_btn)
        step_toolbar.addStretch()

        # 文件操作
        load_btn = QPushButton("加载序列")
        load_btn.clicked.connect(self._load_sequence)
        step_toolbar.addWidget(load_btn)
        save_btn = QPushButton("保存序列")
        save_btn.clicked.connect(self._save_sequence)
        step_toolbar.addWidget(save_btn)

        # 直达安全算法页（序列安全访问步骤依赖已加载的DLL算法）
        sec_btn = QPushButton("🔐 安全算法...")
        sec_btn.setToolTip(
            "打开安全算法页面加载/管理DLL算法\n"
            "序列中 27 <level> 请求会自动调用该算法完成: 请求种子→算钥→发密钥")
        sec_btn.clicked.connect(self.open_security_requested.emit)
        step_toolbar.addWidget(sec_btn)
        layout.addLayout(step_toolbar)

        # 步骤表格（支持按住行拖拽排序）
        self._table = StepTable()
        self._table.set_move_handler(self._move_step)
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels(
            ["启用", "步骤名称", "请求HEX", "发送次数", "前延时(ms)", "后延时(ms)",
             "检查正响应", "安全算法", "结果"])
        header = self._table.horizontalHeader()
        # 列宽策略: 步骤名称定宽（不再Stretch独占整行——否则请求HEX被挤窄，
        # 点击高概率误中名称列），请求HEX作为主编辑列给足宽度，结果列弹性拉伸
        for col, w in ((0, 50), (1, 110), (2, 210),
                       (3, 76), (4, 92), (5, 92), (6, 92), (7, 70)):
            self._table.setColumnWidth(col, w)
        self._table.horizontalHeaderItem(7).setToolTip(
            "安全访问步骤自动调用已加载算法(DLL/Python)完成全流程:\n"
            "27 <level> 请求种子 → 算法计算密钥 → 27 <level+1> 发送密钥\n"
            "请求HEX填 27 09（或发密钥 27 0A）时自动识别并调用，无需勾选；\n"
            "勾选后强制生效（请求带额外数据时也走全流程）")
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        # 加高默认行，便于精确点击与双击编辑
        self._table.verticalHeader().setDefaultSectionSize(30)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

        # 进度
        progress_layout = QHBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        progress_layout.addWidget(self._progress_bar)
        self._loop_label = QLabel("循环: 0/0")
        progress_layout.addWidget(self._loop_label)
        layout.addLayout(progress_layout)

        # 执行按钮
        btn_layout = QHBoxLayout()
        self._exec_btn = QPushButton("执行序列")
        self._exec_btn.setStyleSheet("QPushButton { padding: 10px; font-weight: bold; background: #2196F3; color: white; }")
        self._exec_btn.clicked.connect(self._execute_sequence)
        btn_layout.addWidget(self._exec_btn)

        self._stop_btn = QPushButton("停止")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet("QPushButton { padding: 10px; background: #F44336; color: white; }")
        self._stop_btn.clicked.connect(self._stop_execution)
        btn_layout.addWidget(self._stop_btn)
        layout.addLayout(btn_layout)

        layout.addStretch()

    def _add_step(self):
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._fill_row(row, {"name": f"步骤{row + 1}"})

    def _fill_row(self, row: int, data: dict):
        """按给定数据填充一行（缺省值用于新建行）"""
        cb = QCheckBox()
        cb.setChecked(data.get("enabled", True))
        self._table.setCellWidget(row, 0, cb)
        self._table.setItem(row, 1, QTableWidgetItem(data.get("name", "")))
        hex_item = QTableWidgetItem(data.get("request", ""))
        hex_item.setFont(QFont("Consolas"))
        hex_item.setToolTip("双击编辑请求HEX，如: 22 F1 90")
        self._table.setItem(row, 2, hex_item)

        count_spin = QSpinBox()
        count_spin.setRange(1, 999)
        count_spin.setValue(data.get("count", 1))
        self._table.setCellWidget(row, 3, count_spin)

        db_spin = QSpinBox()
        db_spin.setRange(0, 60000)
        db_spin.setValue(data.get("db", 0))
        self._table.setCellWidget(row, 4, db_spin)

        da_spin = QSpinBox()
        da_spin.setRange(0, 60000)
        da_spin.setValue(data.get("da", 0))
        self._table.setCellWidget(row, 5, da_spin)

        check_cb = QCheckBox()
        check_cb.setChecked(data.get("check", True))
        self._table.setCellWidget(row, 6, check_cb)

        sec_cb = QCheckBox()
        sec_cb.setChecked(data.get("sec", False))
        sec_cb.setToolTip(
            "安全访问全流程: 请求种子→算法算钥→发密钥\n"
            "请求HEX填 27 <level> 时会自动识别调用，无需勾选")
        self._table.setCellWidget(row, 7, sec_cb)

        result_item = QTableWidgetItem(data.get("result", ""))
        if data.get("result_color"):
            result_item.setForeground(QColor(data["result_color"]))
        self._table.setItem(row, 8, result_item)

    def _read_rows(self) -> list:
        """读取全部行的编辑状态（含结果列文本与颜色）"""
        rows = []
        for r in range(self._table.rowCount()):
            name_item = self._table.item(r, 1)
            hex_item = self._table.item(r, 2)
            result_item = self._table.item(r, 8)
            rows.append({
                "enabled": self._table.cellWidget(r, 0).isChecked(),
                "name": name_item.text() if name_item else "",
                "request": hex_item.text() if hex_item else "",
                "count": self._table.cellWidget(r, 3).value(),
                "db": self._table.cellWidget(r, 4).value(),
                "da": self._table.cellWidget(r, 5).value(),
                "check": self._table.cellWidget(r, 6).isChecked(),
                "sec": self._table.cellWidget(r, 7).isChecked(),
                "result": result_item.text() if result_item else "",
                "result_color": (result_item.foreground().color().name()
                                 if result_item else ""),
            })
        return rows

    def _write_rows(self, rows: list):
        """清空表格并按给定数据重建全部行"""
        self._table.setRowCount(0)
        for data in rows:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._fill_row(row, data)

    def _move_step(self, src: int, dst: int):
        """拖拽/上移/下移回调: 重排行顺序并选中目标行"""
        rows = self._read_rows()
        rows.insert(dst, rows.pop(src))
        self._write_rows(rows)
        self._table.selectRow(dst)
        self._table.setCurrentCell(dst, 1)

    def _delete_step(self):
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)

    def _move_selected(self, delta: int):
        """将当前选中行上移(-1)/下移(+1)一步"""
        row = self._table.currentRow()
        if row < 0:
            return
        dst = row + delta
        if not 0 <= dst < self._table.rowCount():
            return
        self._table._move_row(row, dst)
        self._table.selectRow(dst)
        self._table.setCurrentCell(dst, 1)

    def _clear_steps(self):
        self._table.setRowCount(0)

    def _load_sequence(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "加载序列", "resources/sequences", "JSON Files (*.json)")
        if filepath:
            seq = self._seq_manager.load_sequence(filepath)
            if seq:
                self._current_seq = seq
                self._name_edit.setText(seq.name)
                self._desc_edit.setText(seq.description)
                self._loop_spin.setValue(seq.loop_count)
                self._loop_delay_spin.setValue(seq.loop_delay_ms)
                self._stop_on_error_check.setChecked(seq.stop_on_error)
                self._auto_tp_check.setChecked(seq.auto_tester_present)
                self._populate_table(seq)
                self._log(f"加载序列: {seq.name}")

    def _populate_table(self, seq: UdsSequence):
        rows = []
        for step in seq.steps:
            rows.append({
                "enabled": step.enabled,
                "name": step.step_name,
                "request": step.request_data.hex(" "),
                "count": step.send_count,
                "db": step.delay_before_ms,
                "da": step.delay_after_ms,
                "check": step.check_positive,
                "sec": step.security_access,
            })
        self._write_rows(rows)

    def _save_sequence(self):
        seq = self._build_sequence()
        filepath, _ = QFileDialog.getSaveFileName(
            self, "保存序列", "resources/sequences", "JSON Files (*.json)")
        if filepath:
            self._seq_manager.save_sequence(seq, filepath)
            self._log(f"保存序列: {seq.name}")

    def _build_sequence(self) -> UdsSequence:
        seq = UdsSequence(self._name_edit.text(), self._desc_edit.text())
        seq.loop_count = self._loop_spin.value()
        seq.loop_delay_ms = self._loop_delay_spin.value()
        seq.stop_on_error = self._stop_on_error_check.isChecked()
        seq.auto_tester_present = self._auto_tp_check.isChecked()

        for row in range(self._table.rowCount()):
            cb = self._table.cellWidget(row, 0)
            name_item = self._table.item(row, 1)
            hex_item = self._table.item(row, 2)
            count_spin = self._table.cellWidget(row, 3)
            db_spin = self._table.cellWidget(row, 4)
            da_spin = self._table.cellWidget(row, 5)
            check_cb = self._table.cellWidget(row, 6)
            sec_cb = self._table.cellWidget(row, 7)

            hex_str = hex_item.text() if hex_item else ""
            req_data = bytes.fromhex(hex_str.replace(" ", "")) if hex_str else b""

            step = SequenceStep(
                step_name=name_item.text() if name_item else "",
                request_data=req_data,
                send_count=count_spin.value() if count_spin else 1,
                check_positive=check_cb.isChecked() if check_cb else True,
                delay_before_ms=db_spin.value() if db_spin else 0,
                delay_after_ms=da_spin.value() if da_spin else 0,
                enabled=cb.isChecked() if cb else True,
                security_access=sec_cb.isChecked() if sec_cb else False,
            )
            seq.steps.append(step)

        return seq

    def _execute_sequence(self):
        if not self._uds_client:
            self._log("未连接UDS客户端")
            return

        seq = self._build_sequence()
        self._exec_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._log(f"开始执行: {seq.name} ({len(seq.steps)} 步骤)")

        def step_cb(result):
            color = "#4CAF50" if result.is_positive else "#F44336"
            status = "OK" if result.is_positive else "FAIL"
            # 失败时附带具体原因（NRC描述/超时/密钥被拒等），不再只显示FAIL
            detail = (f" - {result.error_message}"
                      if not result.is_positive and result.error_message else "")
            row = result.step_index
            if row < self._table.rowCount():
                item = QTableWidgetItem(
                    f"{status} ({result.elapsed_ms:.0f}ms){detail}")
                item.setForeground(QColor(color))
                # 悬浮提示给出完整收发报文，便于定位失败原因
                req = result.request_sent.hex(" ").upper() \
                    if result.request_sent else "--"
                rsp = result.response_received.hex(" ").upper() \
                    if result.response_received else "无响应"
                item.setToolTip(f"TX: {req}\nRX: {rsp}\n{result.error_message}")
                self._table.setItem(row, 8, item)
            self._log(f"  Step {row + 1}: {result.step_name} -> {status}{detail}",
                      "INFO" if result.is_positive else "ERROR")

        def finish_cb(result):
            self._exec_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            success_count = sum(1 for r in result.step_results if r.is_positive)
            total = len(result.step_results)
            self._log(f"执行完成: {success_count}/{total} 成功, 总耗时 {result.total_elapsed_ms:.0f}ms")

        self._exec_thread = threading.Thread(
            target=self._seq_manager.execute_sequence,
            args=(seq, self._uds_client, step_cb, finish_cb),
            daemon=True)
        self._exec_thread.start()

    def _stop_execution(self):
        self._seq_manager.stop_execution()
        self._log("正在停止...")

    def _log(self, msg: str, level: str = "INFO"):
        self.business_log.emit(msg, level)
