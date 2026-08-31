"""DID定义管理与数据解析模块

功能：
- JSON定义文件管理（导入/导出/编辑）
- 多种数据类型解析（raw/ascii/uint/int/float/struct）
- 缩放因子、偏移量、单位支持
- 单条读取、批量读取、定时轮询
- DID分组管理
"""

import json
import os
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
                 struct_format: str = ""):
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
            val = self.physical_value
            if self.definition.unit:
                return f"{val:.3g} {self.definition.unit}"
            return f"{val:.3g}"
        elif self.definition.data_type == 'struct':
            return self.raw_data.hex()
        else:  # raw
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
