"""OEM诊断调查表(XLSX)解析器

解析整车厂UDSonCAN诊断调查表（FB63/CFMOTO风格: 多sheet、中英双语表头、
合并单元格纵向留空），转换为项目统一的调查表JSON结构:

{
  "services": [{"name", "request", "description"}],
      # 测试中心"调查表/ODX生成用例"输入（conformance_generator）
  "dids": [{"id", "name", "description", "data_length", "data_type",
            "scaling", "offset", "unit", "access", "security"}],
      # access: r/w/rw; security: Level1/LevelFBL/N（写入所需安全等级）
  "dtcs": [{"dtc_id", "description"}],       # DTC定义表（dtc_manager）
}

sheet识别按表头关键词（兼容sheet改名/增删）:
- 服务矩阵: 表头含 "Service ID"        （1_1应用服务 / 1_2 Boot服务）
- DID列表:  表头含 "DID Num"+"Conversion"（4_1读写DID; 4_2 IO表无Conversion不误判）
- DTC列表:  表头含 "DTC Display"       （3_1 DTC信息）
- 例程列表: 表头含 "RoutineDID"        （4_3例程控制）
- 带删除线的行（OEM标记废弃条目）全部剔除: 服务/DID/DTC/例程均不统计

解析要点:
- 合并单元格: SID/DID/描述等键列向下填充（forward-fill）
- DID读写权限矩阵（App/Boot会话×R/W/RW/N）归纳为 access: r只读/w只写/rw读写
- 子功能文本 "0x01 DefaultSession" → 提取hex码 + 英文名
- Conversion "phy=XX*0.05625" / "phy=XX*0.01+3" → scaling/offset
- Data Type "Hex(Unsigned)/ASCII/Signed/BCD" → uint/ascii/int/raw
- 服务请求构造: 仅生成可静态补全参数的完整请求
  (10/11/27/3E/85=sid+子功能, 28=+通信类型03, 19 02=+状态掩码FF, 14=FF FF FF,
   31=例程ID; 其余需运行时参数的服务记录跳过原因)
- SPRMB2=Y的子功能服务额外生成 +0x80 抑制正响应变体（期望无响应用例）
"""

import json
import os
import re

from src.log.log_manager import get_log_manager

# 命令行独立运行时允许解析（CLI下日志尚未初始化也能工作）


def _clean(v) -> str:
    """单元格 → 去除首尾空白的字符串"""
    if v is None:
        return ""
    return str(v).strip()


def _one_line(text: str) -> str:
    """多行双语文本 → 单行（保留全部内容，换行转空格）"""
    return re.sub(r"\s*\n\s*", " ", _clean(text))


def _first_line(text: str) -> str:
    """取第一行（通常为英文）"""
    return _clean(text).split("\n", 1)[0].strip()


def _parse_int(text, default=None):
    """提取字符串中第一个整数"""
    m = re.search(r"\d+", _clean(text))
    return int(m.group()) if m else default


def _parse_float(text):
    """转float，'-'或空返回None"""
    try:
        return float(_clean(text))
    except (ValueError, TypeError):
        return None


def _hex_token(text: str):
    """提取文本首行开头的hex标记，返回 (hex字符串如'01', 剩余文本首行)

    兼容 '0x01 Name' / '0x01Name' / '01 Name' 等写法（多行双语文本只取首行）
    """
    first = _first_line(text)
    m = re.match(r"^\s*(?:0[xX])?([0-9A-Fa-f]{1,2})(?![0-9A-Fa-f])", first)
    if not m:
        return None, first
    return m.group(1).upper(), first[m.end():].strip()


def _cell_text(c) -> str:
    """单元格对象 → 去除首尾空白的字符串"""
    return _clean(c.value) if c is not None else ""


def _is_strike(c) -> bool:
    """单元格是否带删除线（OEM表标记废弃条目的方式）"""
    try:
        return bool(c is not None and c.font and c.font.strike)
    except Exception:
        return False


def _row_strike(row) -> bool:
    """行内任一带删除线的非空单元格 → 该条目已被OEM标记废弃，整体剔除"""
    return any(_is_strike(c) and c.value not in (None, "") for c in row)



def _parse_conversion(text: str):
    """换算公式 'phy=XX*0.05625' / 'phy=XX*0.01+3' → (scaling, offset)

    无公式或格式不识别时返回 (1.0, 0.0)
    """
    m = re.search(r"XX\s*\*\s*([\d.]+)\s*(?:\+\s*([\d.]+))?", _clean(text))
    if not m:
        return 1.0, 0.0
    scaling = float(m.group(1))
    offset = float(m.group(2)) if m.group(2) else 0.0
    return scaling, offset


def _map_data_type(text: str) -> str:
    """调查表数据类型 → 项目数据类型"""
    t = _clean(text).lower()
    if "ascii" in t:
        return "ascii"
    if "unsigned" in t or "hex" in t:  # 先判unsigned，避免被signed子串误判
        return "uint"
    if "signed" in t:
        return "int"
    return "raw"  # BCD等按原始数据处理


# ----------------------------------------------------------------------------
# sheet 定位与表头解析
# ----------------------------------------------------------------------------

def _sheet_text_head(ws, max_row=15, max_col=30) -> str:
    """汇总sheet前几行文本（用于关键词识别，限制列数避免超宽表）"""
    parts = []
    for row in ws.iter_rows(min_row=1, max_row=max_row,
                            max_col=max_col, values_only=True):
        parts.extend(_clean(c) for c in row)
    return " | ".join(parts)


def _detect_kind(ws) -> str:
    """按表头关键词判定sheet类型"""
    head = _sheet_text_head(ws)
    if "InputOutputControlParameter" in head:
        return "io"      # 4_2 IO控制DID（位级控制，暂不解析）
    if "Communication Contorl Type" in head or "Communication Control Type" in head:
        return "commctrl"  # 0x28通信控制类型子矩阵（兼容OEM表头拼写Contorl）
    if "Service ID" in head:
        return "service"
    if "DTC Display" in head:
        return "dtc"
    if "RoutineDID" in head:
        return "routine"
    if "DID Num" in head and "Conversion" in head:
        return "did"
    return ""


def _find_header(ws, keyword: str, max_row=15):
    """定位表头行（含keyword的行），返回 (行号1基, 单元格文本列表)"""
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=max_row,
                                         values_only=True), start=1):
        cells = [_clean(c) for c in row]
        if any(c.startswith(keyword) for c in cells):
            return i, cells
    return None, []


def _col(cells, *tokens, forbid=()):
    """在表头行中查找列号: 单元格需包含全部tokens且不含forbid词"""
    for j, c in enumerate(cells):
        if not c:
            continue
        if all(t in c for t in tokens) and not any(t in c for t in forbid):
            return j
    return None


# ----------------------------------------------------------------------------
# 各sheet解析
# ----------------------------------------------------------------------------

# 可静态构造完整请求的服务 → 请求构造规则（sid, 子功能hex）→ 请求hex串
def _build_request(sid: int, sub: str):
    if sid in (0x10, 0x11, 0x27, 0x3E, 0x85):
        return f"{sid:02X} {sub}"
    if sid == 0x28:
        return f"28 {sub} 03"  # controlType + communicationType(03正常通信)
    if sid == 0x19:
        # 01按状态掩码报数量/02按状态掩码报DTC 均需状态掩码字节（实测缺掩码回NRC 0x13）
        return f"19 {sub} FF" if sub in ("01", "02") else f"19 {sub}"
    if sid == 0x14:
        return "14 FF FF FF"  # 清DTC: DTC组通配
    return None


def _parse_service_sheet(ws, services: list, skipped: list, flags: dict = None):
    """服务矩阵sheet → services条目（子功能粒度）

    flags: 跨sheet收集的声明标志，当前收集:
      preprog_required —— 10 02编程会话行NRC声明含0x22 conditionsNotCorrect
      （进入编程会话前需预编程条件检查，否则切换被拒）
    """
    hrow, cells = _find_header(ws, "Service ID")
    if hrow is None:
        return
    sid_col = _col(cells, "Service ID")
    name_col = _col(cells, "Services Name")
    sub_col = _col(cells, "Sub-functions", forbid=("Supported",))
    subsup_col = _col(cells, "Sub-functions", "Supported")
    sprm_col = _col(cells, "SPRMB")

    # NRC支持声明列（无固定表头，值为逗号分隔hex串如"12,13,22"），
    # 按行内容特征识别——要求至少含一个逗号避免与Y/N/P/F等选项值混淆
    _nrc_re = re.compile(r"^[0-9A-Fa-f]{1,2}(\s*,\s*[0-9A-Fa-f]{1,2})+$")

    cur_sid = None
    cur_name = ""
    cur_prm = False
    seen_requests = set()
    seen_skips = set()

    def cell(row, col):
        return _cell_text(row[col]) if col is not None and col < len(row) else ""

    def row_nrcs(row):
        """行内声明的NRC码集（扫全部单元格找逗号分隔hex串）"""
        for c in row:
            text = _cell_text(c)
            if text and _nrc_re.match(text):
                return {int(x, 16) for x in text.split(",")}
        return set()

    for row in ws.iter_rows(min_row=hrow + 1):
        if _row_strike(row):
            continue  # 删除线条目已废弃，不生成用例
        sid_text = cell(row, sid_col)
        if sid_text.upper().startswith("0X"):
            try:
                cur_sid = int(sid_text, 16)
            except ValueError:
                cur_sid = None
            cur_name = _first_line(cell(row, name_col))
            cur_prm = cell(row, sprm_col).upper() == "Y"
        if cur_sid is None:
            continue

        sub_text = cell(row, sub_col)
        sub_sup = cell(row, subsup_col).upper()
        # 10 02编程会话行NRC声明含0x22 → 进入编程会话前需预编程条件检查
        # （在子功能支持过滤前检测：应用服务表可能标N但NRC声明仍有效）
        if (flags is not None and cur_sid == 0x10
                and sub_text.upper().startswith("0X02")
                and 0x22 in row_nrcs(row)):
            flags["preprog_required"] = True
        if not sub_sup or not sub_text or sub_text.upper() in ("N/A", "-"):
            continue
        if sub_sup != "Y":
            continue  # 该子功能ECU不支持

        sub, sub_name = _hex_token(sub_text)
        name = sub_name or cur_name
        if not sub:
            # 子功能列是自由文本（如0x14的DTC组），尝试按服务规则构造
            if cur_sid == 0x14:
                sub = "FF"
            else:
                continue

        request = _build_request(cur_sid, sub)
        if request is None:
            skip_key = f"skip-0x{cur_sid:02X}"
            if skip_key not in seen_skips:
                seen_skips.add(skip_key)
                skipped.append((f"0x{cur_sid:02X} {cur_name}",
                                "该服务需运行时参数（DID/地址/数据），未生成请求"))
            continue

        if request not in seen_requests:
            seen_requests.add(request)
            services.append({
                "name": f"{cur_name}_{name}" if sub and name != cur_name else name,
                "request": request,
                "description": f"0x{cur_sid:02X} {cur_name}"
                               + (f" / {name}" if sub else ""),
            })
        # SPRMB2支持 → 生成抑制正响应变体（期望无响应）
        if cur_prm and cur_sid in (0x10, 0x27, 0x28, 0x3E, 0x85):
            try:
                sup_req = _build_request(cur_sid, f"{int(sub, 16) | 0x80:02X}")
            except ValueError:
                sup_req = None
            if sup_req and sup_req not in seen_requests:
                seen_requests.add(sup_req)
                services.append({
                    "name": f"{name}_Suppress",
                    "request": sup_req,
                    "description": f"0x{cur_sid:02X} {cur_name} / {name}"
                                   "（抑制正响应）",
                })


def _parse_commctrl_sheet(ws, services: list, skipped: list):
    """通信控制类型sheet（0x28子矩阵）→ 按通信类型细分的服务条目

    行=控制类型(0x00~0x03)，列=通信类型(NCM=01/NMCM=02/NCM+NMCM=03)，
    交叉格Y即支持 → 生成 '28 <控制类型> <通信类型>' 完整请求
    """
    hrow, cells = _find_header(ws, "Service ID")
    if hrow is None:
        return
    sid_col = _col(cells, "Service ID")
    sub_col = _col(cells, "Sub-functions", forbid=("Supported",))

    # 通信类型子表头行: 表头行下一行内多个单元格以hex结尾（如 'NCM\n常规通信报文\n0x01'）
    comm_cols = {}  # 列号 → (通信类型hex, 名称)
    for row in ws.iter_rows(min_row=hrow + 1, max_row=hrow + 3):
        for j, c in enumerate(row):
            text = _cell_text(c)
            m = re.search(r"(?:0[xX])([0-9A-Fa-f]{1,2})\s*$", text)
            if m and j != sid_col and j != sub_col and j not in comm_cols:
                comm_cols[j] = (m.group(1).upper(),
                                _first_line(text.split("\n")[0]))
        if len(comm_cols) >= 2:
            break
    if not comm_cols:
        return

    cur_sid = None
    seen = set()
    for row in ws.iter_rows(min_row=hrow + 1):
        if _row_strike(row):
            continue  # 删除线条目已废弃，不生成用例
        sid_text = _cell_text(row[sid_col]) if sid_col < len(row) else ""
        if sid_text.upper().startswith("0X"):
            try:
                cur_sid = int(sid_text, 16)
            except ValueError:
                cur_sid = None
        if cur_sid is None or cur_sid != 0x28:
            continue
        sub_text = _cell_text(row[sub_col]) if sub_col < len(row) else ""
        sub, sub_name = _hex_token(sub_text)
        if not sub:
            continue
        for j, (comm, comm_name) in comm_cols.items():
            val = _cell_text(row[j]) if j < len(row) else ""
            if val.upper() != "Y":
                continue
            request = f"28 {sub} {comm}"
            if request in seen:
                continue
            seen.add(request)
            services.append({
                "name": f"{sub_name}_{comm_name}",
                "request": request,
                "description": f"通信控制 0x28 / {sub_name}"
                               f"（通信类型: {comm_name}）",
            })


def _parse_did_sheet(ws, dids: list, skipped: list):
    """读写DID列表sheet → dids条目（多行位描述合并，DID列向下填充）

    读写属性（Application/Boot会话下按会话ID的 R/W/RW/N 权限矩阵，
    存入 did["access"]: "r"=只读 / "w"=只写 / "rw"=读写）
    """
    hrow, cells = _find_header(ws, "DID Num")
    if hrow is None:
        return
    did_col = _col(cells, "DID Num")
    desc_col = _col(cells, "DID Description")
    size_col = _col(cells, "Size")
    content_col = _col(cells, "Data Content")
    min_col = _col(cells, "Range, Min")
    max_col = _col(cells, "Range, Max")
    unit_col = _col(cells, "Unit")
    conv_col = _col(cells, "Conversion")
    dtype_col = _col(cells, "Data Type")
    sec_col = _col(cells, "Security Level")
    cmt_col = _col(cells, "Comments")
    # 应用程序会话权限列: 表头“Application Software(Diagnostic Session)”
    # 下方的会话ID子表头行（如 0x01/0x03）→ (列号, 会话ID)升序
    app_cols = _session_cols(ws, hrow, "Application Software")
    boot_cols = _session_cols(ws, hrow, "Boot Sofeware")  # OEM拼写Sofeware

    def cell(row, col):
        return _cell_text(row[col]) if col is not None and col < len(row) else ""

    def access_of(row) -> str:
        """综合App/Boot会话权限 → r/w/rw"""
        modes = set()
        for cols in (app_cols, boot_cols):
            for _, c in cols:
                v = cell(row, c).upper().replace(" ", "").replace("/", "")
                if v in ("R", "RW"):
                    modes.add("r")
                if v in ("W", "RW"):
                    modes.add("w")
        if modes == {"r"}:
            return "r"
        if modes == {"w"}:
            return "w"
        if modes == {"r", "w"}:
            return "rw"
        return ""

    cur = None
    for row in ws.iter_rows(min_row=hrow + 1):
        if _row_strike(row):
            continue  # 删除线DID已废弃，不进定义表（含多行位描述续行）
        did_text = cell(row, did_col).replace("0x", "").replace("0X", "")
        if re.fullmatch(r"[0-9A-Fa-f]{1,4}", did_text):
            did_id = int(did_text, 16)
            if cur is not None:
                _finish_did(cur, dids)
            desc = _first_line(cell(row, desc_col))
            dtype_text = cell(row, dtype_col)
            scaling, offset = _parse_conversion(cell(row, conv_col))
            cur = {
                "id": f"0x{did_id:04X}",
                "name": re.sub(r"[^\w]", "_", desc) or f"DID_{did_id:04X}",
                "description": desc,
                "data_length": _parse_int(cell(row, size_col), 1) or 1,
                "data_type": _map_data_type(dtype_text),
                "scaling": scaling,
                "offset": offset,
                "unit": cell(row, unit_col),
                "min_val": _parse_float(cell(row, min_col)),
                "max_val": _parse_float(cell(row, max_col)),
                "group": "survey",
                "access": access_of(row),
                "_sub_fields": [],
                "_dtype_text": dtype_text,
                "_sec": cell(row, sec_col),
                "_comment": _first_line(cell(row, cmt_col)),
            }
        if cur is not None:
            # 同一DID的多行位/字节描述 → 合并子数据名
            sub = cell(row, content_col) if did_text == "" else ""
            if sub and sub not in cur["_sub_fields"]:
                cur["_sub_fields"].append(_first_line(sub))
            if not did_text and cell(row, dtype_col) and not cur["_dtype_text"]:
                cur["_dtype_text"] = cell(row, dtype_col)
                cur["data_type"] = _map_data_type(cell(row, dtype_col))
    if cur is not None:
        _finish_did(cur, dids)


def _session_cols(ws, hrow: int, app_keyword: str) -> list:
    """会话权限子表头: 定位“Application/Boot Software”表头后，
    下方以会话ID（0x01/0x03/0x02）为子表头的列 → [(会话ID, 列号)]

    表头行下最多扫3行，子表头单元格为1~2位hex（如 0x01）
    """
    app_col = None
    for i, row in enumerate(ws.iter_rows(min_row=hrow, max_row=hrow + 1)):
        for j, c in enumerate(row):
            text = _cell_text(c)
            if text.startswith(app_keyword):
                app_col = (i, j)
                break
        if app_col:
            break
    if app_col is None:
        return []
    cols = []
    for row in ws.iter_rows(min_row=hrow + 1, max_row=hrow + 3):
        for j, c in enumerate(row):
            m = re.fullmatch(r"(?:0[xX])?([0-9A-Fa-f]{1,2})", _cell_text(c))
            if m and j > app_col[1]:
                cols.append((int(m.group(1), 16), j))
        if cols:
            break
    return sorted(set(cols))


def _finish_did(cur: dict, dids: list):
    """整理单个DID条目并追加（内部字段收编进description）"""
    desc = cur["description"]
    if len(cur["_sub_fields"]) > 1:
        desc += "（含: " + "、".join(cur["_sub_fields"][:8]) + "）"
    if "BCD" in cur["_dtype_text"].upper():
        desc += "（BCD编码）"
    sec = cur["_sec"]
    cur["security"] = sec  # 结构化保留（Level1/LevelFBL/N），供用例生成器判定安全锁定维度
    if sec and sec.upper() not in ("N", "N/A", "-"):
        desc += f"（写入需安全等级: {sec}）"
    if cur["_comment"]:
        desc += f"；{cur['_comment']}"
    cur["description"] = desc
    for key in ("_sub_fields", "_dtype_text", "_sec", "_comment"):
        cur.pop(key)
    dids.append(cur)


def _parse_dtc_sheet(ws, dtcs: list, skipped: list):
    """DTC列表sheet → dtcs条目（到Bit Field状态位定义区为止）"""
    hrow, cells = _find_header(ws, "DTC Display")
    if hrow is None:
        return
    hex_col = _col(cells, "DTC Byte")
    mean_col = _col(cells, "DTC Meaning")
    attr_col = _col(cells, "Faults Attribute")

    def cell(row, col):
        return _cell_text(row[col]) if col is not None and col < len(row) else ""

    for row in ws.iter_rows(min_row=hrow + 1):
        # 删除线DTC已废弃，不进定义表
        if _row_strike(row):
            continue
        # Bit Field区为DTC状态位定义，不属于DTC列表
        if any(_cell_text(c).startswith("Bit Field") for c in row[:2] if c):
            break
        hex_text = cell(row, hex_col)
        m = re.match(r"^(?:0[xX])?([0-9A-Fa-f]{6})$", hex_text)
        if not m:
            continue
        meaning = _one_line(cell(row, mean_col))
        attr = _one_line(cell(row, attr_col))
        desc = f"{meaning}（{attr}）" if attr else meaning
        dtcs.append({
            "dtc_id": f"0x{m.group(1).upper()}",
            "description": desc,
        })


def _parse_routine_sheet(ws, services: list, skipped: list):
    """例程控制sheet → services条目（31 <控制类型> <例程ID>）"""
    hrow, cells = _find_header(ws, "RoutineDID")
    if hrow is None:
        return
    rid_col = _col(cells, "RoutineDID")
    desc_col = _col(cells, "DID Description")
    type_col = _col(cells, "RoutineControlType")
    sup_col = _col(cells, "Subfuction") or _col(cells, "Subfunction")
    sec_col = _col(cells, "Security Leve")

    def cell(row, col):
        return _cell_text(row[col]) if col is not None and col < len(row) else ""

    cur_rid = None
    cur_desc = ""
    cur_sec = ""
    for row in ws.iter_rows(min_row=hrow + 1):
        if _row_strike(row):
            continue  # 删除线例程已废弃，不生成用例
        rid_text = cell(row, rid_col)
        if rid_text.upper().startswith("0X"):
            try:
                cur_rid = int(rid_text, 16)
            except ValueError:
                cur_rid = None
            cur_desc = _first_line(cell(row, desc_col))
            cur_sec = cell(row, sec_col)
        if cur_rid is None:
            continue

        ctype, cname = _hex_token(cell(row, type_col))
        if not ctype or cell(row, sup_col).upper() != "Y":
            continue
        services.append({
            "name": f"{cur_desc}_{cname}" if cname else f"Routine_{cur_rid:04X}",
            "request": f"31 {ctype} {cur_rid:04X}",
            "description": f"例程控制 0x{cur_rid:04X} {cur_desc}"
                           + (f" / {cname}" if cname else "")
                           + (f"（安全等级: {cur_sec}）" if cur_sec else ""),
        })


# ----------------------------------------------------------------------------
# 对外接口
# ----------------------------------------------------------------------------

def parse_survey_xlsx(file_path: str) -> dict:
    """解析诊断调查表xlsx → 调查表JSON结构dict

    Returns:
        {"services": [...], "dids": [...], "dtcs": [...], "_source": ...,
         "_skipped": [(名称, 原因), ...]}
        解析失败抛出异常（文件不存在/非xlsx等）
    """
    import openpyxl

    logger = get_log_manager().get_app_logger()
    services, dids, dtcs, skipped = [], [], [], []
    flags = {}  # 跨sheet声明标志（如 preprog_required）

    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    try:
        for ws in wb.worksheets:
            kind = _detect_kind(ws)
            try:
                if kind == "service":
                    _parse_service_sheet(ws, services, skipped, flags)
                elif kind == "commctrl":
                    _parse_commctrl_sheet(ws, services, skipped)
                elif kind == "did":
                    _parse_did_sheet(ws, dids, skipped)
                elif kind == "dtc":
                    _parse_dtc_sheet(ws, dtcs, skipped)
                elif kind == "routine":
                    _parse_routine_sheet(ws, services, skipped)
            except Exception as e:  # 单sheet异常不中断整体解析
                logger.warning("调查表sheet %s 解析失败: %s", ws.title, e)
                skipped.append((ws.title, f"sheet解析异常: {e}"))
    finally:
        wb.close()

    # 跨sheet按请求前缀去重（应用服务/Boot服务/通信类型矩阵会有重叠）
    seen_req = set()
    uniq_services = []
    for s in services:
        if s["request"] in seen_req:
            continue
        seen_req.add(s["request"])
        uniq_services.append(s)
    services = uniq_services

    result = {
        "_source": os.path.basename(file_path),
        "_comment": "由OEM诊断调查表xlsx解析生成: services供测试中心生成用例，"
                    "dids/dtcs供DID/DTC定义导入",
        "services": services,
        "dids": dids,
        "dtcs": dtcs,
        "_skipped": skipped,
    }
    # 预编程条件检查配置: 调查表10 02行NRC声明0x22时自动输出
    # （check_request换项目可改，required=false或删除该字段则不加检查）
    if flags.get("preprog_required"):
        result["preprogramming"] = {
            "required": True,
            "check_request": "31 01 02 03",
            "description": "进入编程会话前需预编程条件检查（服务矩阵10 02声明"
                           "NRC 0x22 conditionsNotCorrect），否则切换被拒",
        }
        logger.info("调查表声明预编程条件检查: 10 02前需先发 31 01 02 03")
    logger.info("调查表解析完成 %s: 服务 %d 项 / DID %d 个 / DTC %d 个 / 跳过 %d 项",
                os.path.basename(file_path), len(services), len(dids),
                len(dtcs), len(skipped))
    return result


def save_survey_json(data: dict, file_path: str):
    """调查表dict保存为JSON（去掉下划线开头的内部统计字段）"""
    out = {k: v for k, v in data.items() if not k.startswith("_")}
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    import argparse
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="OEM诊断调查表xlsx解析器")
    ap.add_argument("xlsx", help="调查表xlsx文件路径")
    ap.add_argument("-o", "--output", help="输出JSON路径（缺省打印摘要）")
    args = ap.parse_args()

    data = parse_survey_xlsx(args.xlsx)
    print(f"服务 {len(data['services'])} 项 / DID {len(data['dids'])} 个 / "
          f"DTC {len(data['dtcs'])} 个 / 跳过 {len(data['_skipped'])} 项")
    for name, reason in data["_skipped"][:10]:
        print(f"  跳过 {name}: {reason}")
    if args.output:
        save_survey_json(data, args.output)
        print(f"已保存: {args.output}")
