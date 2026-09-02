"""协议一致性用例生成器（配置驱动）

从诊断调查表JSON / ODX导出JSON / ODX·PDX·CDD原始文件解析服务、子功能、
DID声明，自动生成测试中心的UdsSequence用例（全功能覆盖）。

生成规则（ISO 14229-1 响应格式）:
- 每个声明的请求前缀一条独立用例: 发送前缀 → 期望正响应回显前缀
  （回显字节数按服务规则表 _RESP_ECHO: 会话/复位等回显子功能1字节、
  DID服务回显2字节、例程回显子功能+例程ID 3字节、清DTC不回显）
- 子功能含抑制位(0x80)时改为"期望无响应"校验
- 非默认会话服务(27/28/31/85/14)自动加前置扩展会话+清理还原默认会话
- 高危服务(11复位/14清DTC)生成但默认禁用(enabled=False)，名称带⚠，
  由用户在表格确认后手动启用
- 0x27偶数等级(发密钥)跳过——前缀不含密钥数据，无法构成有效请求
- 需运行时参数的服务(2E写/34-37下载/23/3D)跳过并记录原因

调查表JSON格式（services与dids至少一项，可共存）:
{
  "services": [
    {"name": "ExtendedSession", "request": "10 03", "description": "..."},
    {"name": "ReadDTC", "sid": "0x19", "sub_functions": ["01", "02 FF"]}
  ],
  "dids": [ {"id": "0xF190", "name": "VIN", ...} ]   // 同DID定义表格式
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

# 需非默认会话的服务: 生成时自动加前置扩展会话+清理还原
_SESSION_REQUIRED_SIDS = {0x14, 0x27, 0x28, 0x31, 0x85}

# 含子功能参数的服务（ISO 14229-1）: 仅这些服务的第2字节才有"抑制正响应"位语义，
# 其它服务（0x14的DTC组/0x22的DID等）第2字节高位为1时不得误判为抑制
_HAS_SUBFUNC_SIDS = {0x10, 0x11, 0x19, 0x27, 0x28, 0x2F, 0x31, 0x3E, 0x85}

# 高危服务: 生成但默认禁用，用户确认后手动启用
_DANGEROUS_SIDS = {0x11, 0x14}

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
    return SequenceStep(
        name, bytes.fromhex(req_hex.replace(" ", "")),
        expected_response=(bytes.fromhex(exp_hex.replace(" ", ""))
                           if exp_hex else None),
        expect_no_response=no_resp, check_positive=True)


def _build_service_seq(entry: dict):
    """单条服务前缀 → UdsSequence；返回 (seq, skip_reason)"""
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
    seq_name = f"SVC-{sid:02X}-{req[1:].hex().upper() or '00'} {name}"
    if dangerous:
        seq_name = "⚠" + seq_name
        step.enabled = False  # 高危默认禁用，用户确认后手动启用
    seq = UdsSequence(
        seq_name,
        f"{desc or name}: TX {req.hex(' ').upper()} → {sub_txt}"
        + ("（高危，默认禁用）" if dangerous else ""))

    if sid in _SESSION_REQUIRED_SIDS and not suppressed:
        seq.pre_steps.append(_make_step("进入扩展会话", "10 03", exp_hex="50 03"))
        seq.post_steps.append(_make_step("还原默认会话", "10 01", exp_hex="50 01"))
    seq.steps.append(step)
    return seq, None


def generate_from_file(filepath: str) -> GenReport:
    """从调查表JSON/ODX导出JSON/ODX·PDX·CDD文件生成全部用例

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
    seen = set()
    for entry in entries:
        key = entry["request"]
        if key in seen:
            continue
        seen.add(key)
        seq, skip_reason = _build_service_seq(entry)
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

    # ---- DID用例（调查表含dids字段时） ----
    if did_data is not None and did_data.get("dids"):
        report.sequences.extend(generate_did_cases(did_data))

    logger.info("用例生成完成 %s: %s", os.path.basename(filepath),
                report.summary())
    return report


def generate_did_cases(data: dict) -> list:
    """DID定义表({"dids": [...]}) → 每个DID一条读取用例

    与测试中心"生成DID用例"共用命名规则（DID-组），重复生成时整组替换。
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
        desc = defn.description or defn.name
        seq = UdsSequence(
            f"DID-{did_id:04X} {defn.name}",
            f"读取 {desc}（22 {did_id:04X} → 62 {did_id:04X}+数据）")
        did_bytes = did_id.to_bytes(2, "big")
        seq.steps.append(SequenceStep(
            f"读取 {defn.name}",
            bytes([0x22]) + did_bytes,
            expected_response=bytes([0x62]) + did_bytes))
        cases.append(seq)
    return cases
