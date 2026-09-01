"""A2L（ASAP2）轻量解析器

只解析标定/测量常用的块，不做完整ASAP2规范覆盖:
  /begin PROJECT  ... 项目名
  /begin MODULE   ... 模块名
  /begin MEASUREMENT   name "描述" datatype conversion ... address
  /begin CHARACTERISTIC name "描述" type address ...
  /begin COMPU_METHOD  name "描述" ...（用于单位提取，可选）

块内参数跨行、以空白分隔；字符串用双引号包裹。
"""

import re
import logging


def _parse_int(text: str, default: int = 0) -> int:
    try:
        return int(text, 0)
    except (ValueError, TypeError):
        return default


class A2lEntry:
    """MEASUREMENT / CHARACTERISTIC 条目"""

    def __init__(self, kind: str, name: str, description: str = "",
                 address: int = 0, data_type: str = "", unit: str = ""):
        self.kind = kind               # "MEASUREMENT" / "CHARACTERISTIC"
        self.name = name
        self.description = description
        self.address = address
        self.data_type = data_type     # 数据类型或标定类型(如 VALUE/CURVE)
        self.unit = unit

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "address": f"0x{self.address:X}",
            "data_type": self.data_type,
            "unit": self.unit,
        }


class A2lDatabase:
    """A2L解析结果"""

    def __init__(self):
        self.project = ""
        self.module = ""
        self.entries: list = []       # A2lEntry

    @property
    def measurements(self) -> list:
        return [e for e in self.entries if e.kind == "MEASUREMENT"]

    @property
    def characteristics(self) -> list:
        return [e for e in self.entries if e.kind == "CHARACTERISTIC"]


class A2lParser:
    """A2L文件解析"""

    def __init__(self):
        self._logger = logging.getLogger("diag.a2l")

    def parse_file(self, filepath: str):
        """解析A2L文件; 失败返回None"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError as e:
            self._logger.error("A2L文件读取失败 %s: %s", filepath, e)
            return None
        return self.parse_text(text)

    def parse_text(self, text: str):
        db = A2lDatabase()

        # 剥离注释（A2L规范支持 /* */ 与 // ；先保护双引号字符串，
        # 避免注释文本误入 PROJECT/MODULE 名等正则）
        text = re.sub(
            r'"[^"]*"|/\*.*?\*/|//[^\n]*',
            lambda m: m.group(0) if m.group(0).startswith('"') else ' ',
            text, flags=re.DOTALL)

        # PROJECT / MODULE 名（块开始后的首个非引号词）
        m = re.search(r"/begin\s+PROJECT\s+(\S+)", text)
        if m:
            db.project = m.group(1)
        m = re.search(r"/begin\s+MODULE\s+(\S+)", text)
        if m:
            db.module = m.group(1)

        # 提取各块内容
        for kind in ("MEASUREMENT", "CHARACTERISTIC"):
            for m in re.finditer(
                    r"/begin\s+" + kind + r"\b(.*?)/end\s+" + kind,
                    text, re.DOTALL):
                entry = self._parse_block(kind, m.group(1))
                if entry:
                    db.entries.append(entry)
        self._logger.info(
            "A2L解析完成: %s/%s, 测量%d 标定%d",
            db.project, db.module,
            len(db.measurements), len(db.characteristics))
        return db

    @staticmethod
    def _tokenize(block: str) -> list:
        """按空白分词，双引号字符串保持整体"""
        return re.findall(r'"[^"]*"|\S+', block)

    def _parse_block(self, kind: str, block: str):
        toks = self._tokenize(block)
        if len(toks) < 2:
            return None
        name = toks[0]
        desc = toks[1].strip('"')
        if kind == "MEASUREMENT":
            # name "desc" datatype conversion display_id ecu_address ...
            data_type = toks[2] if len(toks) > 2 else ""
            address = self._find_address(toks, fallback_idx=5)
            return A2lEntry(kind, name, desc, address, data_type)
        # CHARACTERISTIC: name "desc" type address deposit max_diff conversion ...
        data_type = toks[2] if len(toks) > 2 else ""
        address = self._find_address(toks, fallback_idx=3)
        return A2lEntry(kind, name, desc, address, data_type)

    @staticmethod
    def _find_address(toks: list, fallback_idx: int) -> int:
        """块内地址提取: 优先 ECU_ADDRESS 关键字后一个token
        （真实A2L的IF_DATA场景），否则回退到固定位置参数"""
        if "ECU_ADDRESS" in toks:
            idx = toks.index("ECU_ADDRESS")
            if idx + 1 < len(toks):
                return _parse_int(toks[idx + 1])
        return _parse_int(toks[fallback_idx]) if len(toks) > fallback_idx else 0
