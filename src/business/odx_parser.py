"""ODX/PDX/CDD 诊断数据解析（ISO 22901 轻量子集）

支持三种入口:
  .odx  直接XML
  .pdx  ODX打包容器（ZIP，内部为catalog.xml + 各层ODX文件）
  .cdd  Vector CANdela容器：若为ZIP封装则尝试解析内部XML；
        纯二进制旧版CDD无法解析（需从CANdelaStudio导出ODX/PDX）

解析目标（命名空间无关，不做完整规范覆盖）:
  DIAG-COMM(DIAG-SERVICE/DIAG-JOB等): 服务短名/描述/请求前缀字节
  DTC: 故障码(支持 P0123/U0100 等J2012写法与纯HEX) + 描述
  COMPU-METHODS: 计数（仅统计）
"""

import re
import zipfile
import logging
import xml.etree.ElementTree as ET

_DTC_RE = re.compile(r"^([PCBU])([0-9A-Fa-f]{4})$")
_DTC_PREFIX = {"P": 0x0, "C": 0x4, "B": 0x8, "U": 0xC}
_COMM_TAGS = {"DIAG-SERVICE", "DIAG-JOB", "SINGLE-ECU-RESET",
              "MULTI-ECU-JOB", "DIAG-COMM"}


def _local(tag: str) -> str:
    """去除XML命名空间前缀"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _short_name(elem) -> str:
    for child in elem:
        if _local(child.tag) == "SHORT-NAME":
            return (child.text or "").strip()
    return ""


def _desc_text(elem) -> str:
    for child in elem:
        if _local(child.tag) in ("DESC", "LONG-NAME"):
            parts = [t.strip() for t in child.itertext() if t.strip()]
            if parts:
                return " ".join(parts)
    return ""


def _parse_dtc_id(text: str):
    """P0123/U0100/6位HEX -> int; 无法识别返回None
    J2012编码: 类前缀(P=0/C=4/B=8/U=C)占bit19-16，后4位HEX为bit15-0，
    如 P0123 -> 0x000123, U0100 -> 0xC0100"""
    text = text.strip().upper()
    m = _DTC_RE.match(text)
    if m:
        return (_DTC_PREFIX[m.group(1)] << 16) | int(m.group(2), 16)
    if re.match(r"^[0-9A-F]{5,6}$", text):
        return int(text, 16)
    return None


class OdxComm:
    """诊断服务/作业条目"""

    def __init__(self, name: str, kind: str, desc: str = "",
                 request: bytes = b""):
        self.name = name
        self.kind = kind          # DIAG-SERVICE 等
        self.desc = desc
        self.request = request    # 请求前缀（SID+子功能等CODED-VALUE）


class OdxDtc:
    """DTC条目"""

    def __init__(self, code: str, dtc_id, desc: str = ""):
        self.code = code          # 原始短名（P0123等）
        self.dtc_id = dtc_id      # int 或 None
        self.desc = desc


class OdxDatabase:
    """ODX解析结果"""

    def __init__(self):
        self.source = ""
        self.project = ""
        self.xml_count = 0
        self.comms: list = []
        self.dtcs: list = []
        self.compu_count = 0


class OdxParser:
    """ODX/PDX/CDD解析"""

    def __init__(self):
        self._logger = logging.getLogger("diag.odx")

    def parse_file(self, filepath: str):
        """解析文件; 失败返回None"""
        try:
            with open(filepath, "rb") as f:
                head = f.read(2)
        except OSError as e:
            self._logger.error("ODX文件读取失败 %s: %s", filepath, e)
            return None
        if head == b"PK":
            return self._parse_container(filepath)
        return self._parse_xml_file(filepath)

    # ---------------- 容器（PDX/新CDD） ----------------

    def _parse_container(self, filepath: str):
        db = OdxDatabase()
        db.source = filepath
        try:
            with zipfile.ZipFile(filepath) as zf:
                xml_names = [n for n in zf.namelist()
                             if n.lower().endswith((".odx", ".xml"))
                             and not n.endswith("/")]
                if not xml_names:
                    self._logger.warning(
                        "容器内无XML（可能是二进制CDD）: %s", filepath)
                    return None
                for name in xml_names:
                    try:
                        with zf.open(name) as f:
                            self._parse_xml_bytes(f.read(), db)
                    except ET.ParseError:
                        continue
        except (zipfile.BadZipFile, OSError) as e:
            self._logger.error("容器解析失败 %s: %s", filepath, e)
            return None
        if db.xml_count == 0:
            return None
        self._log_summary(db)
        return db

    # ---------------- 直接XML ----------------

    def _parse_xml_file(self, filepath: str):
        db = OdxDatabase()
        db.source = filepath
        try:
            with open(filepath, "rb") as f:
                self._parse_xml_bytes(f.read(), db)
        except (ET.ParseError, OSError) as e:
            self._logger.error("ODX XML解析失败 %s: %s", filepath, e)
            return None
        if db.xml_count == 0:
            return None
        self._log_summary(db)
        return db

    def _parse_xml_bytes(self, data: bytes, db: OdxDatabase):
        root = ET.fromstring(data)
        db.xml_count += 1
        # 项目名: ODX根或PROJECT子节点
        if not db.project:
            for elem in root.iter():
                tag = _local(elem.tag)
                if tag in ("SHORT-NAME",) and not db.project:
                    db.project = (elem.text or "").strip()
                    break
        for elem in root.iter():
            tag = _local(elem.tag)
            if tag in _COMM_TAGS:
                self._parse_comm(elem, tag, db)
            elif tag == "DTC":
                self._parse_dtc(elem, db)
            elif tag == "COMPU-METHOD":
                db.compu_count += 1

    # ---------------- 条目解析 ----------------

    def _parse_comm(self, elem, kind: str, db: OdxDatabase):
        name = _short_name(elem)
        if not name:
            return
        request = bytearray()
        for cv in elem.iter():
            if _local(cv.tag) == "CODED-VALUE" and (cv.text or "").strip():
                try:
                    v = int(cv.text)
                except ValueError:
                    continue
                # 超过1字节的值（如DID F190=61840）按大端展开为两字节
                if v > 0xFF:
                    request.append((v >> 8) & 0xFF)
                request.append(v & 0xFF)
                if len(request) >= 4:
                    break
        db.comms.append(OdxComm(name, kind, _desc_text(elem),
                                bytes(request)))

    def _parse_dtc(self, elem, db: OdxDatabase):
        code = _short_name(elem)
        if not code:
            return
        dtc_id = _parse_dtc_id(code)
        # 子节点ID若为数值则优先（部分ODX直接给数值）
        for child in elem:
            if _local(child.tag) == "ID" and (child.text or "").strip():
                try:
                    dtc_id = int(child.text.strip(), 0)
                except ValueError:
                    pass  # XML id属性（如dtc_1）忽略，保留短名解析结果
                break
        db.dtcs.append(OdxDtc(code, dtc_id, _desc_text(elem)))

    def _log_summary(self, db: OdxDatabase):
        self._logger.info(
            "ODX解析完成: %s (%d个XML) 服务%d DTC%d 计算方法%d",
            db.project or db.source, db.xml_count,
            len(db.comms), len(db.dtcs), db.compu_count)
