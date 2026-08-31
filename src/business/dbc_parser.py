"""诊断数据库解析（DBC）

支持加载标准 .dbc 文件，解析 BO_（报文）与 SG_（信号）定义，
并提供按 CAN ID 对原始数据帧进行信号级物理值解码。

解析范围（够用即可，不追求完整 DBC 规范覆盖）:
  BO_ <id> <name>: <dlc> <sender>
   SG_ <name> : <start>|<len>@<endian><sign> (<factor>,<offset>) [<min>|<max>] "<unit>" <receivers>
  仅支持大/小端整数信号（@0/@1），浮点信号(@1+ 浮点)按原样跳过。
"""

import re
import logging

_BO_RE = re.compile(
    r"^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)")
_SG_RE = re.compile(
    r"^SG_\s+(\w+)\s*:\s*(\d+)\|(\d+)@([01])([+-])"
    r"\s*\(([^,]+),([^)]+)\)\s*\[([^|]*)\|([^\]]*)\]\s*\"([^\"]*)\"")


class DbcSignal:
    """DBC信号定义"""

    def __init__(self, name: str, start_bit: int, bit_length: int,
                 little_endian: bool, signed: bool,
                 factor: float, offset: float, unit: str = ""):
        self.name = name
        self.start_bit = start_bit
        self.bit_length = bit_length
        self.little_endian = little_endian
        self.signed = signed
        self.factor = factor
        self.offset = offset
        self.unit = unit

    def decode(self, data: bytes):
        """从数据帧提取该信号的物理值; 数据不足返回None"""
        if len(data) * 8 < self._need_bits():
            return None
        raw = self._extract_raw(data)
        if self.signed and raw >= (1 << (self.bit_length - 1)):
            raw -= (1 << self.bit_length)
        return raw * self.factor + self.offset

    def _need_bits(self) -> int:
        if self.little_endian:
            return self.start_bit + self.bit_length
        # 大端(Motorola): start为MSB所在位, 跨字节展开
        msb_byte = self.start_bit // 8
        bits_first_byte = self.start_bit % 8 + 1
        remain = max(0, self.bit_length - bits_first_byte)
        return (msb_byte + 1 + (remain + 7) // 8) * 8

    def _extract_raw(self, data: bytes) -> int:
        if self.little_endian:
            value = 0
            for i in range(self.bit_length):
                bit_pos = self.start_bit + i
                if data[bit_pos // 8] & (1 << (bit_pos % 8)):
                    value |= (1 << i)
            return value
        # 大端: 从MSB位起, 每字节内位序递减, 跨字节时跳到下一字节位7
        value = 0
        bit_pos = self.start_bit
        for i in range(self.bit_length):
            if data[bit_pos // 8] & (1 << (bit_pos % 8)):
                value |= (1 << (self.bit_length - 1 - i))
            if bit_pos % 8 == 0:
                bit_pos += 15  # 本字节位0 -> 下一字节位7
            else:
                bit_pos -= 1
        return value


class DbcMessage:
    """DBC报文定义"""

    def __init__(self, can_id: int, name: str, dlc: int, sender: str):
        self.can_id = can_id
        self.name = name
        self.dlc = dlc
        self.sender = sender
        self.signals: list = []

    def decode(self, data: bytes) -> dict:
        """解码数据帧 -> {信号名: (物理值, 单位)}"""
        result = {}
        for sig in self.signals:
            val = sig.decode(data)
            if val is not None:
                result[sig.name] = (val, sig.unit)
        return result


class DbcParser:
    """DBC数据库"""

    def __init__(self):
        self._logger = logging.getLogger("diag.dbc")
        self.name = ""
        self.messages: dict = {}   # can_id -> DbcMessage

    # ---------------- 加载 ----------------

    def load(self, filepath: str) -> bool:
        """加载 .dbc 文件; 成功返回True"""
        self.messages.clear()
        self.name = ""
        current: DbcMessage = None
        skipped = 0
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()
                    if not self.name and stripped.startswith("BS_:"):
                        # 头部之后首个BO_之前取文件名作为库名
                        import os
                        self.name = os.path.splitext(
                            os.path.basename(filepath))[0]
                    m = _BO_RE.match(stripped)
                    if m:
                        can_id = int(m.group(1))
                        current = DbcMessage(
                            can_id, m.group(2),
                            int(m.group(3)), m.group(4))
                        self.messages[can_id] = current
                        continue
                    m = _SG_RE.search(stripped)
                    if m and current is not None:
                        endian = m.group(4)
                        sign = m.group(5)
                        try:
                            sig = DbcSignal(
                                name=m.group(1),
                                start_bit=int(m.group(2)),
                                bit_length=int(m.group(3)),
                                little_endian=(endian == "1"),
                                signed=(sign == "-"),
                                factor=float(m.group(6)),
                                offset=float(m.group(7)),
                                unit=m.group(10),
                            )
                            current.signals.append(sig)
                        except (ValueError, IndexError):
                            skipped += 1
                    elif line.strip() and not line.startswith((" ", "\t")):
                        current = None  # 离开报文定义区
        except OSError as e:
            self._logger.error("DBC文件读取失败 %s: %s", filepath, e)
            return False
        if skipped:
            self._logger.warning("DBC解析跳过 %d 条无效信号", skipped)
        self._logger.info(
            "DBC加载完成: %s, %d 条报文",
            self.name or filepath, len(self.messages))
        return True

    # ---------------- 查询/解码 ----------------

    def get_message(self, can_id: int):
        return self.messages.get(can_id)

    def decode_frame(self, can_id: int, data: bytes) -> dict:
        """按ID解码数据帧; 无匹配报文返回空dict"""
        msg = self.messages.get(can_id)
        if msg is None:
            return {}
        return msg.decode(data)

    @property
    def signal_count(self) -> int:
        return sum(len(m.signals) for m in self.messages.values())
