"""协议一致性用例生成器（配置驱动）

从诊断调查表JSON / 调查表XLSX（OEM多sheet表格，survey_xlsx_parser解析）/
ODX导出JSON / ODX·PDX·CDD原始文件解析服务、子功能、DID声明，
自动生成测试中心的UdsSequence用例（全功能覆盖）。

生成规则（ISO 14229-1 响应格式）:
- 每个声明的请求前缀一条独立用例: 发送前缀 → 期望正响应回显前缀
  （回显字节数按服务规则表 _RESP_ECHO: 会话/复位等回显子功能1字节、
  DID服务回显2字节、例程回显子功能+例程ID 3字节、清DTC不回显）
- 子功能含抑制位(0x80)时改为"期望无响应"校验
- 非默认会话服务自动加前置会话切换+清理还原（含抑制响应变体）:
  14/28/31/85及27应用等级(01~0x07)→扩展会话；27 Boot/FBL等级(≥0x09)→
  编程会话（先扩展后编程）；10 02编程会话切换需先经扩展会话
  （实测默认会话直切回NRC 0x7E）
- 高危服务(11复位/14清DTC)生成但默认禁用(enabled=False)，名称带⚠，
  由用户在表格确认后手动启用
- 刷写例程(31 0202/FF00/FF01)生成但默认禁用: 需编程会话+FBL解锁+
  运行时参数(CRC/擦除地址/依赖数据)，静态前缀独立发送只能测到
  0x13，eraseMemory更会真实触发擦除——确认环境后手动启用
- 0x27偶数等级(发密钥)跳过——前缀不含密钥数据，无法构成有效请求
- 需运行时参数的服务(2E写/34-37下载/23/3D)跳过并记录原因
- DID按调查表读写属性(access: r/w/rw)分流:
  r→读用例(DID-组); w→写用例(WR-组，默认禁用，写入值按调查表
  min/max/换算反推合法中间值); rw→两者都生成
- 负向用例全排列(NEG-组，Diva/AE3i风格): 从声明的服务出发生成
  InvalidService(未定义SID 00/01/02)/InvalidSubfunctions(非法子功能
  矩阵: 保留值0x00/0x7F+声明集外探针02/04)/InvalidRequestMessageLength
  (缺参+超长填充)/InvalidRequestData(无效DID网格)/PositiveResponse
  SuppressionBit 维度用例
- 会话/安全维度(参照AE3i套件全排列):
  SES-组: 可读DID在扩展会话下读(验证会话不限制读);
  NEG-SES: 默认会话发需扩展会话的请求→NRC 0x7F;
  NEG-SEC: 扩展会话未解锁写带安全等级DID→NRC 0x33(默认禁用防误写)

调查表JSON格式（services与dids至少一项，可共存）:
{
  "services": [
    {"name": "ExtendedSession", "request": "10 03", "description": "..."},
    {"name": "ReadDTC", "sid": "0x19", "sub_functions": ["01", "02 FF"]}
  ],
  "dids": [ {"id": "0xF190", "name": "VIN", "access": "r", ...} ]
  // access: r只读/w只写/rw读写（调查表App/Boot会话权限矩阵归纳）
}
ODX导出JSON（{"comms": [{"name","request","description"}]}）与
.odx/.pdx/.cdd 原始文件（经OdxParser）同样支持。
"""

import json
import os
from src.log.log_manager import get_log_manager

# 各服务正响应的参数回显字节数（SID后）: ISO 14229-1 各服务响应定义
_RESP_ECHO = {
    0x10: 1,   # 50 <sessionType>
    0x11: 1,   # 51 <resetType>
    0x14: 0,   # 54（无回显）
    0x19: 1,   # 59 <reportType>
    0x22: 2,   # 62 <DID>
    0x27: 1,   # 67 <securityLevel>
    0x28: 1,   # 68 <controlType>
    0x31: 3,   # 71 <routineCtrlType> <routineId 2字节>
    0x3E: 1,   # 7E <subFunction>
    0x85: 1,   # C5 <controlType>
}

# 可自动生成有效请求的服务（前缀即完整请求或可独立发送）
_SUPPORTED_SIDS = set(_RESP_ECHO.keys())

# 需非默认会话的服务: 生成时自动加前置会话切换+清理还原（见 _required_session）


def _required_session(req: bytes):
    """服务请求所需的最低会话（None=默认会话即可）

    依据 ISO 14229-1 及 OEM 常见实现（实测 NRC 0x7F/0x7E/0x12 反馈）:
    - 14清DTC/28通信控制/31例程/85DTC设置: 扩展会话
    - 27安全访问: 应用等级(01~0x07)扩展会话; Boot/FBL等级(≥0x09)编程会话
    - 10 02编程会话切换: 多数ECU不允许默认直切编程会话，需先经扩展会话
    """
    sid = req[0]
    if sid in (0x14, 0x28, 0x31, 0x85):
        return "extended"
    if sid == 0x27 and len(req) >= 2:
        return "programming" if (req[1] & 0x7F) >= 0x09 else "extended"
    if sid == 0x10 and len(req) >= 2 and (req[1] & 0x7F) == 0x02:
        return "extended"
    return None

# 含子功能参数的服务（ISO 14229-1）: 仅这些服务的第2字节才有"抑制正响应"位语义，
# 其它服务（0x14的DTC组/0x22的DID等）第2字节高位为1时不得误判为抑制
_HAS_SUBFUNC_SIDS = {0x10, 0x11, 0x19, 0x27, 0x28, 0x2F, 0x31, 0x3E, 0x85}

# 高危服务: 生成但默认禁用，用户确认后手动启用
_DANGEROUS_SIDS = {0x11, 0x14}

# 刷写例程ID: 需编程会话+FBL解锁+运行时参数(CRC/擦除地址/依赖数据)，
# 静态前缀独立发送无法构成有效请求（实测DEM在编程会话+FBL解锁后
# 31 01 0202/FF00仍回NRC 0x13），且eraseMemory会真实触发擦除 ——
# 生成但默认禁用，确认刷写环境后手动启用
_FLASH_ROUTINES = {0x0202, 0xFF00, 0xFF01}

# 跳过的服务（需运行时参数，静态前缀无法构成有效请求）
_SKIPPED_SIDS = {
    0x23: "ReadMemoryByAddress需地址参数",
    0x2E: "WriteDataByIdentifier需写入数据",
    0x34: "RequestDownload需地址/大小参数",
    0x35: "RequestUpload需地址/大小参数",
    0x36: "TransferData需数据块",
    0x37: "RequestTransferExit依赖下载流程",
    0x3D: "WriteMemoryByAddress需地址+数据",
}


class GenReport:
    """生成结果报告"""

    def __init__(self):
        self.sequences = []      # 生成的UdsSequence列表
        self.skipped = []        # [(名称, 原因)]
        self.dangerous = 0       # 默认禁用的高危用例数
        self.suppressed = 0      # 抑制正响应(期望无响应)用例数

    def summary(self) -> str:
        parts = [f"生成 {len(self.sequences)} 条用例"]
        if self.dangerous:
            parts.append(f"高危默认禁用 {self.dangerous} 条")
        if self.suppressed:
            parts.append(f"抑制响应 {self.suppressed} 条")
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 条")
        return "，".join(parts)


def _parse_request(value) -> bytes:
    """请求前缀归一化: '10 03'/bytes/list[int] → bytes"""
    if isinstance(value, bytes):
        return value
    if isinstance(value, (list, tuple)):
        return bytes(int(v, 0) if isinstance(v, str) else int(v) for v in value)
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith("0x"):
            text = text[2:]
        try:
            return bytes.fromhex(text.replace(" ", ""))
        except ValueError:
            return b""
    return b""


def _entries_from_survey(data: dict) -> list:
    """调查表/ODX导出JSON → 服务条目列表 [{"name","request","description"}]

    services 支持两种写法:
      {"name", "request": "10 03"}                    直接给请求前缀
      {"name", "sid": "0x19", "sub_functions": [...]} SID+子功能列表展开
    comms 为 ODX 导出 JSON 字段（request 已是 hex 字符串）。
    """
    entries = []
    for item in data.get("services", []) or []:
        name = item.get("name", "")
        if "request" in item:
            req = _parse_request(item.get("request"))
            if req:
                entries.append({"name": name, "request": req,
                                "description": item.get("description", "")})
            continue
        sid = _parse_request(item.get("sid", ""))
        if not sid:
            continue
        for sub in item.get("sub_functions", []) or [""]:
            sub_bytes = _parse_request(sub)
            entries.append({
                "name": f"{name}_{sub.strip() or 'nofunc'}".rstrip("_"),
                "request": sid + sub_bytes,
                "description": item.get("description", "")})
    for comm in data.get("comms", []) or []:
        req = _parse_request(comm.get("request", ""))
        if req:
            entries.append({"name": comm.get("name", ""), "request": req,
                            "description": comm.get("description", "")})
    return entries


def _make_step(name: str, req_hex: str, exp_hex: str = None,
               no_resp: bool = False):
    """步骤构造（与测试中心 _step 同规则，但保持business层不依赖UI层）"""
    from src.business.sequence_manager import SequenceStep
    step = SequenceStep(
        name, bytes.fromhex(req_hex.replace(" ", "")),
        expected_response=(bytes.fromhex(exp_hex.replace(" ", ""))
                           if exp_hex else None),
        expect_no_response=no_resp, check_positive=True)
    if step.request_data[:1] == b"\x10" and step.expected_response:
        # 会话切换请求幂等且无副作用，ECU切会话/落盘偶有~2s无响应窗口
        # （实测连续会话切换后ECU短暂沉默）: 超时自动重试最多5次
        # （P2超时~500ms即天然重试间隔），收到任何答复即停
        step.retry_on_timeout = True
        step.send_count = 5
    return step


def _read_step(name: str, did_bytes: bytes):
    """读DID步骤（22→62）: 幂等无副作用，ECU偶发~2s沉默窗口内
    超时自动重试最多5次（与 _make_step 的10 xx会话切换同规则）"""
    from src.business.sequence_manager import SequenceStep
    step = SequenceStep(name, bytes([0x22]) + did_bytes,
                        expected_response=bytes([0x62]) + did_bytes)
    step.retry_on_timeout = True
    step.send_count = 5
    return step


def _build_service_seq(entry: dict, preprog: dict = None):
    """单条服务前缀 → UdsSequence；返回 (seq, skip_reason)

    preprog: 调查表preprogramming配置（{"required", "check_request"}），
    编程会话前置链中在10 02前插入预编程条件检查。
    """
    from src.business.sequence_manager import UdsSequence

    req = entry["request"]
    sid = req[0]
    name = entry.get("name") or f"SID_{sid:02X}"
    desc = entry.get("description", "")

    if sid in _SKIPPED_SIDS:
        return None, _SKIPPED_SIDS[sid]
    if sid not in _SUPPORTED_SIDS:
        return None, f"SID 0x{sid:02X} 不在可生成范围"
    # 安全访问偶数等级(发密钥)需密钥数据，静态前缀无法构成有效请求
    if sid == 0x27 and len(req) >= 2 and (req[1] & 0x7F) % 2 == 0:
        return None, "安全访问发密钥(偶数等级)需密钥数据，跳过"
    # 19的04/06子功能需DTC号(3字节)+记录号(1字节)，仅发SID+子功能
    # 只能测到长度错误(0x13)到不了目标检查；用占位参数构成完整请求
    if sid == 0x19 and len(req) == 2 and (req[1] & 0x7F) in (0x04, 0x06):
        req = req + bytes([0xFF, 0xFF, 0xFF, 0x01])

    # 期望响应: 含子功能服务的抑制位(0x80)→无响应；否则正响应前缀回显
    suppressed = (len(req) >= 2 and sid in _HAS_SUBFUNC_SIDS
                  and bool(req[1] & 0x80))
    if suppressed:
        step = _make_step(name, req.hex(" "), no_resp=True)
        sub_txt = f"子功能0x{req[1]:02X}(抑制正响应，应无回复)"
    else:
        echo = _RESP_ECHO.get(sid, 0)
        expected = bytes([sid + 0x40]) + req[1:1 + echo]
        step = _make_step(name, req.hex(" "), exp_hex=expected.hex(" "))
        sub_txt = f"期望 {expected.hex(' ').upper()}"

    dangerous = sid in _DANGEROUS_SIDS
    # 刷写例程(CheckProgrammingIntegrity/eraseMemory/
    # checkProgrammingDependencies): 默认禁用，见 _FLASH_ROUTINES 注释
    flash_routine = (sid == 0x31 and len(req) >= 4
                     and ((req[2] << 8) | req[3]) in _FLASH_ROUTINES)
    seq_name = f"SVC-{sid:02X}-{req[1:].hex().upper() or '00'} {name}"
    if dangerous or flash_routine:
        seq_name = "⚠" + seq_name
        step.enabled = False  # 高危/刷写例程默认禁用，用户确认后手动启用
    if sid == 0x11:
        # ECU复位后需重启时间（实测~1.5s无响应窗口），复位后等待
        # 3s再继续，否则紧随用例的超时都是重启余波而非真实失败
        step.delay_after_ms = 3000
    seq = UdsSequence(
        seq_name,
        f"{desc or name}: TX {req.hex(' ').upper()} → {sub_txt}"
        + ("（高危，默认禁用）" if dangerous else
           "（刷写例程，需编程会话+FBL解锁+运行时参数，默认禁用）"
           if flash_routine else ""))

    # 会话适配: 按服务所需最低会话加前置切换（含抑制响应变体——
    # 抑制位只影响响应期望，不改变服务的会话依赖）与清理还原
    session = _required_session(req)
    # 本链是否会发送10 02: 主请求即编程会话切换，或编程会话类服务的前置切换
    # （两类链都需预编程条件检查通过，否则10 02被NRC 0x22拒绝）
    is_prog_switch = (sid == 0x10 and len(req) >= 2
                      and (req[1] & 0x7F) == 0x02)
    if session == "programming" or is_prog_switch:
        seq.pre_steps.append(_make_step("进入扩展会话", "10 03", exp_hex="50 03"))
        # 预编程条件检查（调查表声明NRC 0x22时必需，否则10 02被拒）
        if preprog and preprog.get("required"):
            check_req = _parse_request(preprog.get("check_request", ""))
            if check_req:
                echo = _RESP_ECHO.get(check_req[0], 0)
                exp = bytes([check_req[0] + 0x40]) + check_req[1:1 + echo]
                seq.pre_steps.append(_make_step(
                    "预编程条件检查", check_req.hex(" "),
                    exp_hex=exp.hex(" ")))
        if not is_prog_switch:
            # 主请求不是10 02时，前置链还需先切入编程会话。
            # 10 02正响应(50 02)后控制器需≥2s完成应用→Boot跳转，
            # 跳转完成前的请求会被残留应用拒绝（如27 09回7F 27 7F），
            # 等待2.5s再发主请求（Boot下27 09正响应67 09+种子）
            jump = _make_step("进入编程会话", "10 02", exp_hex="50 02")
            jump.delay_after_ms = 2500
            seq.pre_steps.append(jump)
    elif session == "extended":
        seq.pre_steps.append(_make_step("进入扩展会话", "10 03", exp_hex="50 03"))
    if sid == 0x28 and len(req) >= 2 and (req[1] & 0x7F) == 0x03:
        # 禁用通信(28 03/28 83)会真实关闭ECU常规通信，殃及后续85/19等
        # DTC服务（一律回NRC 0x31）；结束前在扩展会话下恢复通信并等待
        # 通信栈恢复窗口（实测恢复后~1.3s内仍无响应）
        recover = _make_step("恢复通信使能", "28 00 03", exp_hex="68 00")
        recover.delay_after_ms = 1500
        seq.post_steps.append(recover)
    if sid == 0x10 and len(req) >= 2 and (req[1] & 0x7F) == 0x03:
        # 10 03/10 83会真实切入扩展会话（抑制位只抑制响应不改变切换行为），
        # 结束后还原默认会话，避免残留扩展会话让后续用例的前置10 03
        # 撞上同会话重复切换的无响应窗口（实测扩展→扩展10 03超时）
        seq.post_steps.append(_make_step("还原默认会话", "10 01", exp_hex="50 01"))
    if session:
        restore = _make_step("还原默认会话", "10 01", exp_hex="50 01")
        if session == "programming" or is_prog_switch:
            # 编程会话下10 01被拒(7F 10 7F)无法直接切出，须等S3超时
            # (~2.5s)自动回落默认会话后再还原——也避免残留编程会话
            # 污染紧随的末尾用例（如SVC-27-09前置10 03被拒）
            restore.delay_before_ms = 3000
        seq.post_steps.append(restore)
    seq.steps.append(step)
    return seq, None


def generate_from_file(filepath: str) -> GenReport:
    """从调查表JSON/调查表XLSX/ODX导出JSON/ODX·PDX·CDD文件生成全部用例

    Returns:
        GenReport: sequences=服务用例+DID用例, skipped/suppressed/dangerous统计
    """
    report = GenReport()
    logger = get_log_manager().get_app_logger()
    ext = os.path.splitext(filepath)[1].lower()

    did_data = None
    entries = []

    if ext in (".odx", ".pdx", ".cdd"):
        from src.business.odx_parser import OdxParser
        db = OdxParser().parse_file(filepath)
        if db is None:
            logger.warning("用例生成: ODX解析失败 %s", filepath)
            return report
        entries = [{"name": c.name, "request": c.request,
                    "description": c.desc} for c in db.comms if c.request]
    elif ext in (".xlsx", ".xlsm"):
        # OEM诊断调查表Excel（FB63/CFMOTO风格多sheet表格）
        from src.business.survey_xlsx_parser import parse_survey_xlsx
        try:
            data = parse_survey_xlsx(filepath)
        except Exception as e:
            logger.warning("用例生成: 调查表xlsx解析失败 %s: %s", filepath, e)
            return report
        entries = _entries_from_survey(data)
        did_data = data
        # 调查表内无法静态构造请求的服务（如0x31例程需ID参数）透传跳过原因
        for name, reason in data.get("_skipped", []) or []:
            report.skipped.append((name, reason))
    elif ext == ".json":
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("用例生成: JSON读取失败 %s: %s", filepath, e)
            return report
        entries = _entries_from_survey(data)
        did_data = data
    else:
        logger.warning("用例生成: 不支持的文件类型 %s", ext)
        return report

    # ---- 服务/子功能用例（前缀去重） ----
    preprog = (did_data or {}).get("preprogramming") or {}
    seen = set()
    for entry in entries:
        key = entry["request"]
        if key in seen:
            continue
        seen.add(key)
        seq, skip_reason = _build_service_seq(entry, preprog)
        if seq is None:
            if skip_reason:
                report.skipped.append(
                    (entry.get("name") or key.hex(" ").upper(), skip_reason))
            continue
        report.sequences.append(seq)
        if seq.name.startswith("⚠"):
            report.dangerous += 1
        if seq.steps and seq.steps[0].expect_no_response:
            report.suppressed += 1

    # ---- DID读写用例（调查表含dids字段时） ----
    if did_data is not None and did_data.get("dids"):
        report.sequences.extend(generate_did_cases(did_data))

    # ---- 负向用例全排列（NEG-组，从声明的服务出发，22/2E由DID属性隐含） ----
    if entries:
        report.sequences.extend(_generate_negative_cases(entries, did_data))

    # ---- 会话/安全状态维度（SES-/NEG-SES/NEG-SEC，参照AE3i状态全排列） ----
    if did_data is not None and did_data.get("dids"):
        report.sequences.extend(_generate_state_cases(did_data))

    # ---- 复位/编程会话类用例移到末尾 ----
    # 复位(0x11)会让ECU重启（~1.5s无响应窗口），编程会话进得去出不来
    # （只能等S3超时或断电）——排在中间会连环污染后续用例；
    # 复位组排在编程组之前: 复位→3s等待→ECU回默认会话，编程组前置链
    # 正常执行，结束后困在编程会话但已是末尾无后续用例
    prog = [s for s in report.sequences if _enters_programming(s)]
    reset = [s for s in report.sequences if _resets_ecu(s)
             and s not in prog]
    if prog or reset:
        moved = set(id(s) for s in prog) | set(id(s) for s in reset)
        report.sequences = ([s for s in report.sequences
                             if id(s) not in moved] + reset + prog)

    logger.info("用例生成完成 %s: %s", os.path.basename(filepath),
                report.summary())
    return report


def _enters_programming(seq) -> bool:
    """用例执行链是否会进入编程会话（主请求10 02或FBL等级安全访问）

    负向用例（带NRC期望）会被ECU拒绝，不会真实进入编程会话，不迁移。
    """
    for s in list(seq.pre_steps) + list(seq.steps):
        if not s.enabled or s.expected_nrc:
            continue
        if s.request_data[:2] == b"\x10\x02":
            return True
        if (s.request_data[:1] == b"\x27" and len(s.request_data) >= 2
                and (s.request_data[1] & 0x7F) >= 0x09):
            return True
    return False


def _resets_ecu(seq) -> bool:
    """用例是否会触发ECU复位（ECUReset主/抑制变体，含抑制位）

    负向NRC用例（非法子功能/超长）会被ECU拒绝不会真复位，不迁移。
    """
    for s in list(seq.pre_steps) + list(seq.steps):
        if s.expected_nrc:
            continue
        if s.request_data[:1] == b"\x11" and len(s.request_data) >= 2:
            return True
    return False


def _build_fbl_write_case(did_id, defn, desc, preprog: dict):
    """FBL等级写DID用例（如F15A刷写指纹）

    实测（DEM）: F15A为只写DID（22回NRC 0x31无法先读后写回），需
    编程会话+FBL等级(27 09)解锁后写入固定指纹数据（格式: 3B BCD
    日期+6B序列号共9B，长度不符回0x13）。
    27 09为裸请求，由测试中心注入的安全算法自动完成取种子→算钥→
    发密钥全流程（未加载算法时前置失败，用例如实失败）。
    """
    from src.business.sequence_manager import SequenceStep, UdsSequence

    did_bytes = did_id.to_bytes(2, "big")
    seq = UdsSequence(
        f"WR-{did_id:04X} {defn.name}",
        f"写入 {desc}[写DID，默认禁用，确认后启用]: FBL等级需编程会话"
        "解锁（前置链自动完成），只写DID不可先读（实测22回0x31），"
        "写入固定指纹数据（3B BCD日期+6B序列号；其它FBL DID如格式"
        "不符请按ECU定义调整步骤数据）")

    # 前置: 扩展会话 → 预编程条件检查(若调查表声明) → 编程会话(等Boot跳转)
    seq.pre_steps.append(_make_step("进入扩展会话", "10 03", exp_hex="50 03"))
    if preprog.get("required"):
        check_req = _parse_request(preprog.get("check_request", ""))
        if check_req:
            echo = _RESP_ECHO.get(check_req[0], 0)
            exp = bytes([check_req[0] + 0x40]) + check_req[1:1 + echo]
            seq.pre_steps.append(_make_step(
                "预编程条件检查", check_req.hex(" "),
                exp_hex=exp.hex(" ")))
    jump = _make_step("进入编程会话", "10 02", exp_hex="50 02")
    jump.delay_after_ms = 2500
    seq.pre_steps.append(jump)
    # FBL安全解锁: 裸27 09请求，由注入的安全算法自动全流程
    seq.pre_steps.append(SequenceStep("FBL安全解锁(自动全流程)",
                                      bytes.fromhex("27 09")))

    # 主步骤: 固定指纹写入（F15A实测格式: BCD日期+6B序列号共9B）
    fingerprint = bytes.fromhex("26 09 10 00 00 00 00 00 00")
    seq.steps.append(SequenceStep(
        f"写入 {defn.name} (固定指纹)",
        bytes([0x2E]) + did_bytes + fingerprint,
        expected_response=bytes([0x6E]) + did_bytes,
        enabled=False))  # 写操作有风险，默认禁用由测试人员启用

    # 清理: 编程会话下10 01可能被拒，先等S3超时回落再还原
    restore = _make_step("还原默认会话", "10 01", exp_hex="50 01")
    restore.delay_before_ms = 3000
    seq.post_steps.append(restore)
    return seq


def generate_did_cases(data: dict) -> list:
    """DID定义表({"dids": [...]}) → 读写用例

    按调查表读写属性(access: r/w/rw)分流:
    - r/rw → 读用例 DID-组（22 → 62+数据）
    - w/rw → 写用例 WR-组（2E → 6E，默认禁用，写入值按调查表
      min/max/换算反推合法中间值; 无值域时用全0）
    与测试中心"生成DID用例"共用命名规则（DID-/WR-组），重复生成时整组替换。
    """
    from src.business.did_manager import DidDefinition, DidManager
    from src.business.sequence_manager import SequenceStep, UdsSequence

    mgr = DidManager()
    for d in data.get("dids", []) or []:
        try:
            mgr.add_definition(DidDefinition.from_dict(d))
        except Exception:
            continue

    cases = []
    for did_id, defn in sorted(mgr.definitions.items()):
        # 调查表原始条目（带access字段）优先，无则按定义可读处理
        raw = next((d for d in data.get("dids", [])
                    if d.get("id", "").upper().endswith(f"{did_id:04X}")), {})
        access = (raw.get("access") or "r").lower()
        desc = defn.description or defn.name
        did_bytes = did_id.to_bytes(2, "big")

        if "r" in access:
            seq = UdsSequence(
                f"DID-{did_id:04X} {defn.name}",
                f"读取 {desc}（22 {did_id:04X} → 62 {did_id:04X}+数据）")
            seq.steps.append(_read_step(f"读取 {defn.name}", did_bytes))
            cases.append(seq)

        if "w" in access:
            # 先读后写回: 读取当前值→原样写回，不改变ECU数据
            # （比写固定值安全，也避免值域反推不准导致写入失败）
            sec = str(raw.get("security") or "").upper()
            if "FBL" in sec or did_id == 0xF15A:
                # FBL等级写DID（如F15A指纹）: 只写DID无法先读（实测
                # 22回0x31），需编程会话+FBL解锁后写入固定指纹数据
                cases.append(_build_fbl_write_case(
                    did_id, defn, desc, data.get("preprogramming") or {}))
                continue
            seq = UdsSequence(
                f"WR-{did_id:04X} {defn.name}",
                f"写入 {desc}: 先读取（22 → 62）后原样写回（2E → 6E），"
                "不改变ECU数据[写DID，默认禁用，确认后启用]")
            # 主步骤: 读取 → 写回（write_back动态拼接读取载荷）
            seq.steps.append(_read_step(f"读取 {defn.name}", did_bytes))
            seq.steps.append(SequenceStep(
                f"写回 {defn.name}",
                bytes([0x2E]) + did_bytes,
                expected_response=bytes([0x6E]) + did_bytes,
                write_back=True,
                enabled=False))  # 写操作有风险，默认禁用由测试人员启用
            # 前置: 扩展会话 + 安全解锁（写DID多需Level1；未加载算法时
            # 前置失败仅警告继续，写回得到ECU的NRC如实记录）
            seq.pre_steps.append(_make_step("进入扩展会话", "10 03", exp_hex="50 03"))
            if "FBL" not in sec:
                seq.pre_steps.append(SequenceStep(
                    "安全解锁 Level1", bytes.fromhex("27 01"),
                    delay_before_ms=200))
            seq.post_steps.append(_make_step("还原默认会话", "10 01", exp_hex="50 01"))
            cases.append(seq)
    return cases


# ---- 负向用例全排列（Diva风格: Format/Length/Subfunction/Service/Data维度） ----

def _generate_negative_cases(entries: list, did_data: dict = None) -> list:
    """从已声明的服务条目出发生成负向用例（NEG-组）

    覆盖维度（参考 Vector DiVa / AE3i 套件分组）:
    - InvalidService: 未定义SID探针(0x00/0x01/0x02/0xAA) → NRC 0x11
    - InvalidSubfunctions: 非法子功能矩阵（保留值0x00/0x7F +
      声明集外探针0x02/0x04，参照AE3i每服务多非法值）→ NRC 0x12
    - InvalidRequestMessageLength: 仅发SID/缺参/超长填充 → NRC 0x13
    - InvalidRequestData: 无效DID网格(0100/0101/0102读写×3) / 例程ID
      → NRC 0x31（容忍0x22）
    仅对调查表中声明的服务生成（ECU声明的服务才值得验负向行为）。
    22/2E服务不出现在服务矩阵sheet，由DID列表读写属性隐含声明，
    传入did_data时按access补齐。
    需扩展会话的服务(27/28/31/85/2E)负向用例也加前置切换——否则ECU
    会话检查先于子功能/长度检查，只能测到0x7F到不了目标NRC。
    """
    from src.business.sequence_manager import UdsSequence

    # 负向用例仍需扩展会话的服务（ECU会话检查先于其它检查回0x7F）
    _NEG_EXT_SIDS = {0x27, 0x28, 0x31, 0x85, 0x2E}

    def _with_ext_session(seq, sid):
        """需扩展会话的服务负向用例加前置切换+清理还原"""
        if sid in _NEG_EXT_SIDS:
            seq.pre_steps.append(
                _make_step("进入扩展会话", "10 03", exp_hex="50 03"))
            seq.post_steps.append(
                _make_step("还原默认会话", "10 01", exp_hex="50 01"))

    sids = {e["request"][0] for e in entries if e["request"]}
    # 各服务已声明的子功能值集（去抑制位），用于避开误伤已声明子功能
    declared_subs = {}
    for e in entries:
        req = e.get("request") or b""
        if len(req) >= 2 and req[0] in _HAS_SUBFUNC_SIDS:
            declared_subs.setdefault(req[0], set()).add(req[1] & 0x7F)
    if did_data and did_data.get("dids"):
        acc = {d.get("access", "") for d in did_data["dids"]}
        if acc & {"r", "rw"}:
            sids.add(0x22)  # ReadDataByIdentifier隐含声明
        if acc & {"w", "rw"}:
            sids.add(0x2E)  # WriteDataByIdentifier隐含声明
    cases = []

    # 1. InvalidService: 未定义SID探针（ISO保留SID 00/01/02 + 高位探针AA）
    for probe in (0x00, 0x01, 0x02, 0xAA):
        if probe in sids:
            continue
        seq = UdsSequence(
            f"NEG-SVC-{probe:02X} 未定义服务SID",
            f"发送未定义SID 0x{probe:02X} 应回NRC 0x11 serviceNotSupported（实现差异时无响应）")
        seq.steps.append(_make_nrc_step("未定义SID", f"{probe:02X}", [0x11]))
        cases.append(seq)

    # 2. InvalidSubfunctions: 非法子功能矩阵（保留值+声明集外探针，参照AE3i）
    for sid in sorted(sids & _HAS_SUBFUNC_SIDS):
        declared = declared_subs.get(sid, set())
        # 3E的0x00是合法zeroSubFunction（TesterPresent固定周期报文），不作探针
        probes = [p for p in (0x00, 0x7F, 0x02, 0x04)
                  if (p not in declared or p == 0x00)
                  and not (sid == 0x3E and p == 0x00)]
        for sub in probes:
            nrcs = [0x12, 0x13]
            if sid == 0x19:
                req = f"19 {sub:02X} FF"      # 19需状态掩码字节
            elif sid == 0x31:
                # 用已声明例程ID: 无效例程ID(FFFF)会先被范围检查拒绝
                # （回0x31）到不了子功能检查；部分ECU范围检查先于子功能
                # 检查，已声明ID+非法子功能仍可能回0x31，一并容忍
                base = next((e["request"] for e in entries
                             if e.get("request", b"")[:1] == b"\x31"
                             and len(e["request"]) >= 4), None)
                tail = base[2:].hex() if base else "FF FF"
                req = f"31 {sub:02X} {tail}"
                nrcs.append(0x31)
            else:
                req = f"{sid:02X} {sub:02X}"
            seq = UdsSequence(
                f"NEG-SUB-{sid:02X}{sub:02X} 0x{sid:02X}非法子功能0x{sub:02X}",
                f"0x{sid:02X} 子功能0x{sub:02X} 应回NRC 0x12 subFunctionNotSupported")
            seq.steps.append(_make_nrc_step(f"子功能0x{sub:02X}", req, nrcs))
            _with_ext_session(seq, sid)
            cases.append(seq)

    # 3. InvalidRequestMessageLength: 仅发SID（缺子功能字节）
    for sid in sorted(sids & _HAS_SUBFUNC_SIDS):
        seq = UdsSequence(
            f"NEG-LEN-{sid:02X} 0x{sid:02X}长度错误",
            f"仅发SID 0x{sid:02X}（缺子功能字节）应回NRC 0x13 incorrectMessageLength")
        seq.steps.append(_make_nrc_step("缺子功能字节", f"{sid:02X}", [0x13]))
        _with_ext_session(seq, sid)
        cases.append(seq)

    # 3b. MessageTooLong: 各声明服务超长填充 → NRC 0x13（多帧发送）
    for sid in sorted(sids):
        fill = "00 " * 12  # 超长填充迫使ISO-TP多帧，ECU收全后应回长度错误
        nrcs = [0x13]
        if sid == 0x31:
            # 用已声明例程ID构造超长，避免无效例程ID被范围检查先行拒绝；
            # 部分ECU例程范围检查先于长度检查，仍容忍0x31
            base = next((e["request"].hex(" ") for e in entries
                         if e.get("request", b"")[:1] == b"\x31"
                         and len(e["request"]) >= 4), None) or "31 01"
            nrcs.append(0x31)
        elif sid == 0x2E:
            base = "2E F190"
            # 可写DID多需安全解锁，未解锁时ECU安全检查可能先于长度检查
            nrcs.append(0x33)
        elif sid == 0x22:
            # 0x22为可变长多DID服务(22 + N×2B DID): 偶数对齐的超长请求
            # (如 F190+12×00=15B)会被ECU按多DID读取解析(实测回62正响应
            # 并忽略无效DID 0000)，不构成长度错误——改用奇数个填充字节
            # 使DID区不对齐(实测 F190+9×00=12B 回0x13)才是真长度错误
            base = "22 F190"
            fill = "00 " * 9
        else:
            base = {0x22: "22 F190", 0x14: "14"}.get(sid,
                   f"{sid:02X} 01" if sid in _HAS_SUBFUNC_SIDS else f"{sid:02X}")
        req = (base + " " + fill.strip()).strip()
        seq = UdsSequence(
            f"NEG-TOOLONG-{sid:02X} 0x{sid:02X}超长报文",
            f"{req[:26]}... 超出服务定义长度，应回NRC 0x13 incorrectMessageLength")
        seq.steps.append(_make_nrc_step("超长报文", req, nrcs))
        _with_ext_session(seq, sid)
        cases.append(seq)

    # 4. InvalidRequestData: 无效DID网格 / 例程ID（声明了才测，参照AE3i网格）
    for bad_did in ("0100", "0101", "0102"):
        if 0x22 in sids:
            seq = UdsSequence(
                f"NEG-DID-{bad_did} 读无效DID",
                f"22 {bad_did} 应回NRC 0x31 requestOutOfRange（容忍0x22）")
            seq.steps.append(_make_nrc_step("无效DID", f"22 {bad_did}", [0x31, 0x22]))
            cases.append(seq)
        if 0x2E in sids:
            seq = UdsSequence(
                f"NEG-WDID-{bad_did} 写无效DID",
                f"2E {bad_did} 00 写入未声明DID应回NRC 0x31（容忍0x22/0x33未解锁）")
            seq.steps.append(
                _make_nrc_step("写无效DID", f"2E {bad_did} 00", [0x31, 0x22, 0x33]))
            _with_ext_session(seq, 0x2E)
            cases.append(seq)
    if 0x31 in sids:
        seq = UdsSequence(
            "NEG-RC-FFFF 无效例程ID",
            "31 01 FF FF 应回NRC 0x31 requestOutOfRange（容忍0x22）")
        seq.steps.append(_make_nrc_step("无效例程", "31 01 FF FF", [0x31, 0x22]))
        _with_ext_session(seq, 0x31)
        cases.append(seq)

    # 4b. 22/2E长度错误（无子功能，单独构造缺参请求）
    if 0x22 in sids:
        seq = UdsSequence(
            "NEG-LEN-22 0x22长度错误",
            "22 00 仅1字节DID应回NRC 0x13 incorrectMessageLength")
        seq.steps.append(_make_nrc_step("DID缺字节", "22 00", [0x13]))
        cases.append(seq)
    if 0x2E in sids:
        seq = UdsSequence(
            "NEG-LEN-2E 0x2E长度错误",
            "2E 11 22 只有DID无数据应回NRC 0x13（未解锁时安全检查"
            "可能先回0x33，无效DID可能先回0x31）")
        seq.steps.append(_make_nrc_step("缺写入数据", "2E 11 22", [0x13, 0x33, 0x31]))
        _with_ext_session(seq, 0x2E)
        cases.append(seq)

    # 5. PositiveResponseSuppressionBit: 各服务发已声明请求+0x80 应无响应
    for sid in sorted(sids & _HAS_SUBFUNC_SIDS - {0x19}):
        # 从已声明条目构造抑制请求（带完整参数——未声明子功能会被0x12/
        # 0x13拦截测不到抑制行为；子功能取最低声明值，通常为"使能/查询"
        # 类无副作用操作，如28 00使能收发、85 01开启DTC、3E 00）
        cands = [e["request"] for e in entries
                 if e.get("request", b"")[:1] == bytes([sid])
                 and len(e["request"]) >= 2]
        if sid == 0x31:
            # 31需带例程ID；优先requestRoutineResults(03)无副作用，
            # start(01)/stop(02)会真实执行例程
            cands = [c for c in cands if len(c) >= 4]
            cands.sort(key=lambda c: ((c[1] & 0x7F) != 0x03, c[1] & 0x7F))
        else:
            cands.sort(key=lambda c: c[1] & 0x7F)
        if not cands:
            continue  # 无可构造的声明条目
        base = cands[0]
        req = bytes([sid, base[1] | 0x80]) + base[2:]
        seq = UdsSequence(
            f"NEG-SPR-{sid:02X} 0x{sid:02X}抑制位无响应",
            f"{req.hex(' ').upper()[:26]} 已声明请求+0x80 抑制正响应，"
            "ECU不得回复（超时即通过）")
        step = _make_step("抑制位应无响应", req.hex(" "), no_resp=True)
        step.request_data = req
        if sid == 0x11:
            # 抑制位复位(11 8x)ECU仍会真实复位且不回复，等待重启完成
            step.delay_after_ms = 3000
        if sid == 0x28:
            # 28 8x抑制变体会真实切换通信状态，通信栈恢复有~1.3s无响应
            # 窗口，等待后再执行清理，避免还原会话步骤超时
            step.delay_after_ms = 1500
        seq.steps.append(step)
        _with_ext_session(seq, sid)
        if sid == 0x10:
            # 10的抑制变体仍会真实切换到扩展会话，需清理还原
            seq.post_steps.append(
                _make_step("还原默认会话", "10 01", exp_hex="50 01"))
        cases.append(seq)

    return cases


def _generate_state_cases(did_data: dict) -> list:
    """会话/安全状态维度用例（参照AE3i套件 SimpleRequest×状态 全排列）

    - SES-组: 可读DID在扩展会话下读（前置10 03），验证会话切换不限制读
    - NEG-SES: 默认会话直接发需扩展会话的请求（写DID/28/85/31）→ NRC 0x7F
      （写DID变体默认禁用防误写——若ECU意外接受会真实写入）
    - NEG-SEC: 扩展会话下未解锁安全等级写带安全等级DID → NRC 0x33
      （默认禁用防误写；解锁后状态UnlockedL1需27握手密钥无法静态构造，跳过）
    """
    from src.business.sequence_manager import SequenceStep, UdsSequence

    cases = []
    dids = did_data.get("dids", []) or []

    for d in dids:
        access = (d.get("access") or "r").lower()
        did_id = int(d["id"], 16)
        did_bytes = did_id.to_bytes(2, "big")
        name = d.get("name", "")
        desc = d.get("description", "") or name

        if "r" in access:
            # 扩展会话下读（验证会话不限制读）
            seq = UdsSequence(
                f"SES-{did_id:04X} {name}",
                f"扩展会话下读 {desc}（10 03 → 22 {did_id:04X} → 62）")
            seq.pre_steps.append(_make_step("进入扩展会话", "10 03", exp_hex="50 03"))
            seq.steps.append(_read_step(f"扩展会话读 {name}", did_bytes))
            seq.post_steps.append(_make_step("还原默认会话", "10 01", exp_hex="50 01"))
            cases.append(seq)

        if "w" in access:
            # 默认会话直发写请求（无前置）→ 应被会话拒绝
            payload = b"\x00" * max(1, int(d.get("data_length") or 1))
            seq = UdsSequence(
                f"NEG-SES-{did_id:04X} {name}默认会话写拒绝",
                f"默认会话直接写 {desc} 应回NRC 0x7F serviceNotSupportedInActiveSession"
                "[写DID负向，默认禁用]")
            step = SequenceStep(
                "默认会话写被拒", bytes([0x2E]) + did_bytes + payload,
                expected_nrc=[0x7F], check_positive=True)
            step.enabled = False  # 若ECU意外接受会真实写入，默认禁用
            seq.steps.append(step)
            cases.append(seq)

            # 扩展会话未解锁写 → 安全拒绝（带安全等级的DID才生成）
            sec = (d.get("security") or "").strip()
            if sec and sec.upper() not in ("N", "N/A", "-"):
                seq = UdsSequence(
                    f"NEG-SEC-{did_id:04X} {name}未解锁写拒绝",
                    f"扩展会话未解锁{sec}直接写 {desc} 应回NRC 0x33 securityAccessDenied"
                    "[写DID负向，默认禁用]")
                seq.pre_steps.append(_make_step("进入扩展会话", "10 03", exp_hex="50 03"))
                step = SequenceStep(
                    "未解锁写被拒", bytes([0x2E]) + did_bytes + payload,
                    expected_nrc=[0x33], check_positive=True)
                step.enabled = False
                seq.steps.append(step)
                seq.post_steps.append(_make_step("还原默认会话", "10 01", exp_hex="50 01"))
                cases.append(seq)

    # 需扩展会话的服务在默认会话下直发 → 0x7F（28通信控制/85DTC设置/31例程）
    for sid, sub, label in ((0x28, 0x00, "EnableRxAndTx"), (0x28, 0x03, "DisableRxAndTx"),
                            (0x85, 0x01, "DTC设置On"), (0x85, 0x02, "DTC设置Off"),
                            (0x31, 0x01, "例程Start")):
        req = {0x31: bytes([sid, sub, 0xFF, 0xFF])}.get(sid, bytes([sid, sub]))
        seq = UdsSequence(
            f"NEG-SES-{sid:02X}{sub:02X} 0x{sid:02X}默认会话拒绝",
            f"默认会话直接发 0x{sid:02X}({label}) 应回NRC 0x7F serviceNotSupportedInActiveSession")
        seq.steps.append(SequenceStep(
            f"默认会话{label}被拒", req,
            expected_nrc=[0x7F], check_positive=True))
        cases.append(seq)

    return cases


def _make_nrc_step(name: str, req_hex: str, nrc_list: list):
    """期望负响应NRC的步骤"""
    from src.business.sequence_manager import SequenceStep
    return SequenceStep(
        name, bytes.fromhex(req_hex.replace(" ", "")),
        expected_nrc=nrc_list, check_positive=True)
