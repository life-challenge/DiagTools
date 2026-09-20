"""诊断定义库统一入口（主工具栏 📖 定义库）

集中管理DID/DTC定义的导入与总览：支持OEM调查表xlsx、DID/DTC混合JSON、
ODX/CDD。导入后自动分发到DID面板、DTC面板与数据流面板（共享定义库），
并通过项目配置持久化，下次启动自动恢复。
"""

import os
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QLabel, QGroupBox
)

from src.utils.paths import get_resource_path


class DefinitionCenterDialog(QDialog):
    """诊断定义库: 统一导入入口 + 已加载定义总览"""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self._mw = main_window
        self.setWindowTitle("诊断定义库")
        self.resize(560, 400)
        self._init_ui()
        self.refresh()

    # ---------------- UI ----------------

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ---- 已加载定义总览 ----
        stats_group = QGroupBox("已加载定义")
        stats_layout = QGridLayout()
        self._lbl_did = self._mk_stat(stats_layout, 0, "DID 定义")
        self._lbl_dtc = self._mk_stat(stats_layout, 1, "DTC 定义")
        self._lbl_routine = self._mk_stat(stats_layout, 2, "例程定义")
        self._lbl_service = self._mk_stat(stats_layout, 3, "服务条目")
        self._lbl_stream = self._mk_stat(stats_layout, 4, "数据流信号")
        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        # ---- 数据源 ----
        src_group = QGroupBox("当前数据源")
        src_layout = QVBoxLayout()
        self._lbl_src_ecu = QLabel()
        self._lbl_src_diddtc = QLabel()
        self._lbl_src_odx = QLabel()
        for w in (self._lbl_src_ecu, self._lbl_src_diddtc, self._lbl_src_odx):
            w.setStyleSheet("color: #888;")
            w.setWordWrap(True)
            src_layout.addWidget(w)
        src_group.setLayout(src_layout)
        layout.addWidget(src_group)

        # ---- 导入操作 ----
        act_group = QGroupBox("导入操作")
        act_layout = QVBoxLayout()
        btn_survey = QPushButton("📥 导入定义文件（调查表/JSON）...")
        btn_survey.setObjectName("btn_primary")
        btn_survey.setToolTip(
            "支持OEM调查表xlsx / DID+DTC混合JSON，自动识别内容类型；\n"
            "导入后替换旧定义并自动关联到当前ECU\n"
            "（之后切换到该ECU时自动加载此表，各面板下拉直接可选）")
        btn_survey.clicked.connect(self._import_did_dtc)
        act_layout.addWidget(btn_survey)

        btn_odx = QPushButton("导入 ODX/CDD...")
        btn_odx.setToolTip("解析诊断数据容器，DTC定义注入故障码面板")
        btn_odx.clicked.connect(self._import_odx)
        act_layout.addWidget(btn_odx)

        btn_stream = QPushButton("将全部DID定义加入数据流")
        btn_stream.setToolTip(
            "把定义库中的DID全部加入数据流面板采集列表\n"
            "（定义本身已自动同步，此操作只影响采集清单）")
        btn_stream.clicked.connect(self._add_to_stream)
        act_layout.addWidget(btn_stream)

        btn_dir = QPushButton("打开定义目录")
        btn_dir.setToolTip("resources/did_definitions（JSON定义存放目录）")
        btn_dir.clicked.connect(self._open_dir)
        act_layout.addWidget(btn_dir)
        act_group.setLayout(act_layout)
        layout.addWidget(act_group)

        tip = QLabel(
            "导入后定义自动同步到各模块并在项目配置中持久化，"
            "下次启动自动恢复。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #888;")
        layout.addWidget(tip)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    @staticmethod
    def _mk_stat(grid: QGridLayout, col: int, caption: str) -> QLabel:
        """统计块: 大号数字 + 小号标题"""
        wrap = QLabel()
        wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wrap.setText(f"<div style='text-align:center'>"
                     f"<span style='font-size:22px; font-weight:bold;'>--</span>"
                     f"<br><span style='color:#888'>{caption}</span></div>")
        grid.addWidget(wrap, 0, col)
        return wrap

    @staticmethod
    def _set_stat(label: QLabel, caption: str, value) -> None:
        label.setText(f"<div style='text-align:center'>"
                      f"<span style='font-size:22px; font-weight:bold;'>"
                      f"{value}</span>"
                      f"<br><span style='color:#888'>{caption}</span></div>")

    # ---------------- 数据读取 ----------------

    def _diag(self):
        return self._mw._diag_view

    def refresh(self):
        """重新统计已加载定义与数据源（导入后调用）"""
        did_n = len(self._diag().did_panel._did_manager.definitions)
        dtc_n = len(self._diag().dtc_panel._dtc_manager.definitions)
        routine_n = self._diag().routine_panel.routine_count
        service_n = self._diag().uds_service_view.service_count
        stream_n = len(self._diag().datastream_panel._stream_ids)
        self._set_stat(self._lbl_did, "DID 定义", did_n)
        self._set_stat(self._lbl_dtc, "DTC 定义", dtc_n)
        self._set_stat(self._lbl_routine, "例程定义", routine_n)
        self._set_stat(self._lbl_service, "服务条目", service_n)
        self._set_stat(self._lbl_stream, "数据流信号", stream_n)

        cfg = self._mw._config
        did_dtc = cfg.get("imports.did_dtc")
        odx = cfg.get("imports.odx_dtcs")
        ecu = self._mw._current_ecu
        survey = getattr(ecu, "survey", "") if ecu else ""
        self._lbl_src_ecu.setText(
            "当前ECU: " + (ecu.name if ecu else "--")
            + "  关联调查表: "
            + (os.path.basename(survey) if survey else "（未关联）"))
        self._lbl_src_diddtc.setText(
            "DID/DTC: " + (os.path.basename(str(did_dtc)) if did_dtc
                           else "（未导入）"))
        self._lbl_src_odx.setText(
            "ODX DTC: " + (os.path.basename(str(odx)) if odx
                           else "（未导入）"))

    # ---------------- 操作 ----------------

    def _import_did_dtc(self):
        """导入定义文件: 替换旧定义并自动关联到当前ECU"""
        cfg = self._mw._config
        before = cfg.get("imports.did_dtc")
        self._mw._on_import_did_dtc()
        after = cfg.get("imports.did_dtc")
        if after and after != before:
            # 新导入成功 → 关联到当前ECU（切换ECU时自动加载）
            self._mw._associate_survey_to_ecu(after)
        self.refresh()

    def _import_odx(self):
        self._mw._on_import_odx()
        self.refresh()

    def _add_to_stream(self):
        self._diag().datastream_panel.add_all_definitions()
        self._mw._log_dock.log_business(
            "数据流: 已将全部DID定义加入采集列表")
        self.refresh()

    def _open_dir(self):
        did_dir = get_resource_path("did_definitions")
        if os.path.isdir(did_dir):
            subprocess.Popen(f'explorer "{did_dir}"')
