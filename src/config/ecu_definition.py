"""ECU Definition 配置加载器（配置驱动架构）

核心原则: 新增ECU尽可能只新增配置，不修改核心业务代码。

配置目录结构（参见 resources/ecu/）:
    resources/ecu/
    ├── DEM/
    │   └── ecu.json        # ECU基础定义（名称/描述/地址/信息DID）
    ├── BCM/
    └── ABS/

ecu.json 格式:
    {
        "name": "DEM",
        "description": "Drive Electronic Module",
        "tx_id": "0x714",
        "rx_id": "0x794",
        "functional_tx_id": "0x7DF",
        "info_dids": {"VIN": "F190", "硬件版本": "F193", "软件版本": "F195"},
        "special_functions": [
            {"name": "清除历史故障", "type": "routine", "id": "0x0203", "description": "..."},
            {"name": "读软件版本", "type": "did", "id": "F195"},
            {"name": "软复位", "type": "reset", "reset_type": "01"},
            {"name": "自定义命令", "type": "raw", "data": "10 03"}
        ]
    }

functional_tx_id 可选，缺省 0x7DF（标准功能寻址请求ID）。
special_functions 可选，驱动诊断视图-特殊功能页（无则显示无定义提示）。
"""

import os
import json
from dataclasses import dataclass, field
from src.log.log_manager import get_log_manager
from src.utils.paths import get_resource_path


@dataclass
class EcuDefinition:
    """单个ECU的定义"""
    name: str
    description: str = ""
    tx_id: int = 0x7E0
    rx_id: int = 0x7E8
    # 功能寻址请求ID（刷写流程刷前准备/刷后恢复广播用，标准 0x7DF）
    functional_tx_id: int = 0x7DF
    # 信息DID: {显示名: DID十六进制字符串}，用于ECU信息卡读取
    info_dids: dict = field(default_factory=dict)
    # 特殊功能: [{name, type(routine/did/reset/raw), id, reset_type, data, description}]
    special_functions: list = field(default_factory=list)
    # 定义文件所在目录（后续可扩展 did.json / dtc.json / flash.json 等）
    def_dir: str = ""


def _parse_id(value, default: int) -> int:
    """兼容int和'0x714'字符串"""
    try:
        if isinstance(value, str):
            return int(value, 16) if value.lower().startswith("0x") else int(value)
        return int(value)
    except (ValueError, TypeError):
        return default


def _default_definitions() -> list:
    """内置兜底定义（配置目录缺失时使用）"""
    return [EcuDefinition(
        name="ECU", description="默认ECU", tx_id=0x7E0, rx_id=0x7E8,
        info_dids={"VIN": "F190", "硬件版本": "F193", "软件版本": "F195"},
    )]


def load_ecu_definitions(base_dir: str = None) -> list:
    """扫描ECU定义目录，返回所有ECU定义

    Args:
        base_dir: 定义根目录，默认 <项目根>/resources/ecu

    Returns:
        EcuDefinition列表（按名称排序），目录缺失/为空时返回内置默认定义
    """
    logger = get_log_manager().get_app_logger()

    if base_dir is None:
        base_dir = get_resource_path("ecu")

    defs = []
    if not os.path.isdir(base_dir):
        logger.warning(f"ECU定义目录不存在，使用内置默认定义: {base_dir}")
        return _default_definitions()

    for entry in sorted(os.listdir(base_dir)):
        ecu_json = os.path.join(base_dir, entry, "ecu.json")
        if not os.path.isfile(ecu_json):
            continue
        try:
            with open(ecu_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            defs.append(EcuDefinition(
                name=data.get("name", entry),
                description=data.get("description", ""),
                tx_id=_parse_id(data.get("tx_id"), 0x7E0),
                rx_id=_parse_id(data.get("rx_id"), 0x7E8),
                functional_tx_id=_parse_id(
                    data.get("functional_tx_id"), 0x7DF),
                info_dids=data.get("info_dids", {}) or {},
                special_functions=data.get("special_functions") or [],
                def_dir=os.path.join(base_dir, entry),
            ))
        except Exception as e:
            logger.error(f"ECU定义加载失败 [{ecu_json}]: {e}")

    if not defs:
        logger.warning(f"ECU定义目录为空，使用内置默认定义: {base_dir}")
        return _default_definitions()
    return defs
