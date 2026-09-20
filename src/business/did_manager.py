"""DID定义管理与数据解析模块

功能：
- JSON定义文件管理（导入/导出/编辑）
- 多种数据类型解析（raw/ascii/uint/int/float/struct）
- 缩放因子、偏移量、单位支持
- 单条读取、批量读取、定时轮询
- DID分组管理
"""

import json
import time
import threading
from typing import Optional, Callable
from src.log.log_manager import get_log_manager


class DidDefinition:
    """DID定义"""

    def __init__(self, did_id: int, name: str, description: str = "",
                 data_type: str = "raw", length: int = 1,
                 scaling: float = 1.0, offset: float = 0.0,
                 unit: str = "", min_val: float = None, max_val: float = None,
                 group: str = "default", byte_order: str = "big",
                 struct_format: str = "", enum_map: dict = None,
                 bit_fields: list = None):
        self.did_id = did_id
        self.name = name
        self.description = description
        self.data_type = data_type  # raw/ascii/uint/int/float/struct
        self.length = length  # 数据长度（字节）
        self.scaling = scaling
        self.offset = offset
        self.unit = unit
        self.min_val = min_val
        self.max_val = max_val
        self.group = group
        self.byte_order = byte_order  # big/little
        self.struct_format = struct_format
        # 枚举映射（调查表Data Content的“值：描述”行，如
        # {1: "剩余保养里程重置"}）: 解码命中即显示语义文本
        self.enum_map = dict(enum_map) if enum_map else {}
        # 位段子字段（调查表Bit列有位段范围且各有枚举，如0600
        # bit0~3前轮学习状态+bit4~7后轮）：[{name, bit_text,
        # lo, hi, enum_map}]，解码时按位段切分显示语义
        self.bit_fields = [dict(b) for b in bit_fields] if bit_fields else []

    def to_dict(self) -> dict:
        return {
            "did_id": f"0x{self.did_id:04X}",
            "name": self.name,
            "description": self.description,
            "data_type": self.data_type,
            "length": self.length,
            "scaling": self.scaling,
            "offset": self.offset,
            "unit": self.unit,
            "min_val": self.min_val,
            "max_val": self.max_val,
            "group": self.group,
            "byte_order": self.byte_order,
            "struct_format": self.struct_format,
            # enum_map int键JSON无法序列化，转str存储
            "enum_map": {str(k): v for k, v in self.enum_map.items()} \
                if self.enum_map else {},
            # 位段子字段（含各段枚举，int键同样转str）
            "bit_fields": [
                {**f, "enum_map": {str(k): v for k, v in f.get("enum_map",
                                                            {}).items()}}
                for f in self.bit_fields
            ] if self.bit_fields else [],
        }

    @staticmethod
    def from_dict(d: dict) -> 'DidDefinition':
        # 兼容两种格式: 内部格式 did_id/length, 定义文件 id/data_length
        did_id = d.get("did_id", d.get("id"))
        if did_id is None:
            raise ValueError("DID定义缺少 did_id/id 字段")
        if isinstance(did_id, str):
            did_id = int(did_id, 16)
        return DidDefinition(
            did_id=did_id,
            name=d.get("name", ""),
            description=d.get("description", ""),
            data_type=d.get("data_type", "raw"),
            length=d.get("length", d.get("data_length", 1)),
            scaling=d.get("scaling", 1.0),
            offset=d.get("offset", 0.0),
            unit=d.get("unit", ""),
            min_val=d.get("min_val", d.get("min_value")),
            max_val=d.get("max_val", d.get("max_value")),
            group=d.get("group", "default"),
            byte_order=d.get("byte_order", "big"),
            struct_format=d.get("struct_format", ""),
            enum_map={int(k, 0) if isinstance(k, str) else k: v
                      for k, v in (d.get("enum_map") or {}).items()},
            bit_fields=[
                {**f, "enum_map": {
                    int(k, 0) if isinstance(k, str) else k: v
                    for k, v in (f.get("enum_map") or {}).items()}}
                for f in (d.get("bit_fields") or [])
            ],
        )


class DidValue:
    """DID读取结果"""

    def __init__(self, did_id: int, raw_data: bytes, definition: Optional[DidDefinition] = None,
                 timestamp: float = None):
        self.did_id = did_id
        self.raw_data = raw_data
        self.definition = definition
        self.timestamp = timestamp or time.time()

    @property
    def physical_value(self) -> float:
        """解析为物理值"""
        if self.definition is None:
            return self.raw_value

        raw = self.raw_value
        return raw * self.definition.scaling + self.definition.offset

    @property
    def raw_value(self) -> float:
        """解析为数值"""
        if not self.raw_data:
            return 0.0

        byte_order = self.definition.byte_order if self.definition else 'big'
        # 兼容 big_endian/little_endian 写法，非法值回退big
        if str(byte_order).startswith("little"):
            byte_order = "little"
        elif byte_order != "little":
            byte_order = "big"

        dtype = self.definition.data_type if self.definition else "raw"

        if dtype == 'uint':
            return int.from_bytes(self.raw_data, byte_order, signed=False)
        elif dtype == 'int':
            return int.from_bytes(self.raw_data, byte_order, signed=True)
        elif dtype.startswith("uint") and dtype[4:].isdigit():
            # uint8/uint16/uint32: 按实际数据长度解析（截断到定义位宽）
            nbits = int(dtype[4:])
            nbytes = min(len(self.raw_data), max(1, nbits // 8))
            raw = int.from_bytes(self.raw_data[:nbytes], byte_order, signed=False)
            return raw & ((1 << nbits) - 1)
        elif dtype.startswith("int") and dtype[3:].isdigit():
            nbits = int(dtype[3:])
            nbytes = min(len(self.raw_data), max(1, nbits // 8))
            raw = int.from_bytes(self.raw_data[:nbytes], byte_order, signed=False)
            if raw >= (1 << (nbits - 1)):
                raw -= (1 << nbits)
            return raw
        elif dtype == 'float' and len(self.raw_data) == 4:
            import struct
            fmt = '>f' if byte_order == 'big' else '<f'
            return struct.unpack(fmt, self.raw_data)[0]
        else:
            return int.from_bytes(self.raw_data, byte_order, signed=False)

    @property
    def ascii_value(self) -> str:
        """解析为ASCII字符串"""
        try:
            return self.raw_data.decode('ascii', errors='replace')
        except Exception:
            return self.raw_data.hex()

    @property
    def display_value(self) -> str:
        """显示值（根据数据类型自动选择）"""
        if self.definition is None:
            return self.raw_data.hex()

        dtype = self.definition.data_type
        sized = (dtype[:4] == 'uint' and dtype[4:].isdigit()) \
            or (dtype[:3] == 'int' and dtype[3:].isdigit())
        if dtype == 'ascii':
            return self.ascii_value
        elif dtype in ('uint', 'int', 'float') or sized:
            # 超过4字节的整数（密钥/MAC/网络配置字等多字节字段）
            # 无标量意义，按HEX分组显示——大端整数以科学计数法显示失真
            if dtype != 'float' and len(self.raw_data) > 4:
                return self.raw_data.hex(' ').upper()
            # 位段子字段解码（多段且至少一段有枚举即启用）:
            # 按Bit段切分后各自查枚举，0x11 → 前轮: 已学习; 后轮: 已学习;
            # 3200 → 近光灯输出: ON; 远光灯输出: OFF; …（逐位IO控制）。
            # 无枚举段与保留位段（Reserved/预留）跳过；全部段无枚举
            # （如0803传感器ID）不启用；单段（F200整字节）走原链路
            # 保留单位/换算
            bfs = self.definition.bit_fields
            if len(bfs) > 1 and dtype in ('uint', 'int'):
                raw = int(self.raw_value)
                parts = []
                for f in bfs:
                    if not f.get("enum_map"):
                        continue   # 无枚举段（保留位/纯数值）不显示
                    nm = f.get("name") or f.get("bit_text") or \
                        f"bit{f['lo']}~{f['hi']}"
                    if "reserved" in nm.lower() or "预留" in nm:
                        continue   # 保留位无语义不显示
                    width = f["hi"] - f["lo"] + 1
                    seg = (raw >> f["lo"]) & ((1 << width) - 1)
                    text = f["enum_map"].get(seg)
                    if text is None:
                        text = str(seg)   # 段值未定义回退数值
                    parts.append(f"{nm}: {text}")
                if parts:
                    return "；".join(parts)
            # 枚举映射命中: 显示语义文本（调查表Data Content的“值：描述”），
            # 如F200读到1 → "1 (剩余保养里程重置)"而非裸数值
            raw = int(self.raw_value)
            if raw in self.definition.enum_map:
                return f"{raw} ({self.definition.enum_map[raw]})"
            val = self.physical_value
            # .4g保留物理量分辨率（如110.9V、166.83；.3g会舍入到111/167）
            if self.definition.unit:
                return f"{val:.4g} {self.definition.unit}"
            return f"{val:.4g}"
        elif self.definition.data_type == 'struct':
            return self.raw_data.hex()
        else:  # raw
            # 智能显示: 全部字节为可打印ASCII（如ICCID数字串/BCD文本）
            # 时以文本显示更符合语义，否则回退HEX
            if self.raw_data and all(32 <= b < 127 for b in self.raw_data):
                return self.ascii_value
            return self.raw_data.hex()

    @property
    def is_in_range(self) -> Optional[bool]:
        """检查是否在值域范围内"""
        if self.definition is None:
            return None
        if self.definition.min_val is None or self.definition.max_val is None:
            return None
        val = self.physical_value
        return self.definition.min_val <= val <= self.definition.max_val


class DidManager:
    """DID管理器"""

    def __init__(self):
        self._definitions: dict[int, DidDefinition] = {}  # did_id -> definition
        self._last_values: dict[int, DidValue] = {}  # did_id -> last value
        self._polling = False
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_interval = 1.0
        self._poll_callback: Optional[Callable] = None
        self._logger = get_log_manager().get_app_logger()

    @property
    def definitions(self) -> dict[int, DidDefinition]:
        return dict(self._definitions)

    @property
    def last_values(self) -> dict[int, DidValue]:
        return dict(self._last_values)

    def add_definition(self, defn: DidDefinition):
        """添加DID定义"""
        self._definitions[defn.did_id] = defn

    def clear_definitions(self):
        """清空全部DID定义（切换调查表/ECU时替换语义用）"""
        self._definitions.clear()

    def remove_definition(self, did_id: int):
        """移除DID定义"""
        self._definitions.pop(did_id, None)

    def get_definition(self, did_id: int) -> Optional[DidDefinition]:
        """获取DID定义"""
        return self._definitions.get(did_id)

    def get_groups(self) -> dict[str, list[DidDefinition]]:
        """按分组获取DID定义"""
        groups = {}
        for defn in self._definitions.values():
            group = defn.group
            if group not in groups:
                groups[group] = []
            groups[group].append(defn)
        return groups

    def load_definitions_from_json(self, file_path: str) -> int:
        """从JSON文件加载DID定义"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            count = 0
            skipped = 0
            did_list = data if isinstance(data, list) else data.get('dids', [])
            for item in did_list:
                try:
                    defn = DidDefinition.from_dict(item)
                except Exception as e:
                    skipped += 1
                    self._logger.warning(f"跳过无效DID定义项 {item}: {e}")
                    continue
                self._definitions[defn.did_id] = defn
                count += 1

            self._logger.info(f"从 {file_path} 加载了 {count} 个DID定义")
            return count

        except Exception as e:
            self._logger.error(f"加载DID定义文件失败: {e}")
            return 0

    def save_definitions_to_json(self, file_path: str):
        """保存DID定义到JSON文件"""
        try:
            data = [defn.to_dict() for defn in self._definitions.values()]
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._logger.info(f"保存 {len(data)} 个DID定义到 {file_path}")
        except Exception as e:
            self._logger.error(f"保存DID定义失败: {e}")

    def update_value(self, did_id: int, raw_data: bytes):
        """更新DID值"""
        defn = self._definitions.get(did_id)
        val = DidValue(did_id, raw_data, defn)
        self._last_values[did_id] = val
        return val

    def get_value(self, did_id: int) -> Optional[DidValue]:
        """获取最近的DID值"""
        return self._last_values.get(did_id)

    # --- 轮询功能 ---

    def start_polling(self, did_ids: list[int], uds_client,
                      interval_s: float = 1.0,
                      callback: Callable = None):
        """启动DID轮询

        Args:
            did_ids: 要轮询的DID ID列表
            uds_client: UDS客户端实例
            interval_s: 轮询间隔（秒）
            callback: 每次读取完成的回调 (did_id, DidValue) -> None
        """
        self.stop_polling()
        self._polling = True
        self._poll_interval = interval_s
        self._poll_callback = callback

        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            args=(did_ids, uds_client),
            daemon=True,
        )
        self._poll_thread.start()
        self._logger.info(f"启动DID轮询: {len(did_ids)} 个DID, 间隔 {interval_s}s")

    def stop_polling(self):
        """停止DID轮询"""
        self._polling = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None

    def _poll_loop(self, did_ids: list[int], uds_client):
        """轮询循环"""
        while self._polling:
            for did_id in did_ids:
                if not self._polling:
                    break
                try:
                    resp = uds_client.read_data_by_identifier(did_id)
                    if resp and len(resp) >= 3 and resp[0] == 0x62:
                        raw_data = resp[3:]
                        val = self.update_value(did_id, raw_data)
                        if self._poll_callback:
                            self._poll_callback(did_id, val)
                except Exception as e:
                    self._logger.debug(f"轮询DID 0x{did_id:04X} 失败: {e}")

            time.sleep(self._poll_interval)

    @property
    def is_polling(self) -> bool:
        return self._polling
