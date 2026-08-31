"""配置方案管理器"""

import os
import json
from typing import Optional
from datetime import datetime


class ConfigManager:
    """配置方案管理器
    
    管理工具的所有配置，支持保存/加载配置方案。
    一个方案包含完整的工具配置（CAN参数、ECU列表、DID定义等）。
    """

    _instance: Optional["ConfigManager"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_dir: str = None):
        if hasattr(self, '_initialized') and self._initialized:
            return

        if config_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            config_dir = os.path.join(project_root, "resources", "config")

        self._config_dir = config_dir
        self._current_config = {}
        self._current_file = ""
        self._recent_files = []
        self._max_recent = 10

        os.makedirs(self._config_dir, exist_ok=True)
        self._load_default_config()
        self._initialized = True

    def _load_default_config(self):
        """加载默认配置"""
        default_file = os.path.join(self._config_dir, "default_config.json")
        if os.path.exists(default_file):
            self.load_config(default_file)
        else:
            self._current_config = self._get_default_config()
            self.save_config(default_file)

    def _get_default_config(self) -> dict:
        """获取默认配置"""
        return {
            "version": "1.0.0",
            "created": datetime.now().isoformat(),
            "can": {
                "interface_type": "virtual",
                "channel": "Virtual_0",
                "bitrate": 500000,
                "req_id": "0x7E0",
                "resp_id": "0x7E8",
            },
            "ecus": [
                {
                    "name": "ECU_01",
                    "req_id": "0x7E0",
                    "resp_id": "0x7E8",
                    "description": "默认ECU",
                }
            ],
            "session": {
                "auto_tester_present": True,
                "tester_present_interval_ms": 2000,
            },
            "log": {
                "level": "DEBUG",
                "retention_days": 30,
            },
            "ui": {
                "theme": "dark",
                "timestamp_mode": "absolute",
            },
        }

    def load_config(self, filepath: str) -> bool:
        """加载配置方案（含项目校验: 必须为dict且含can或version）"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or (
                    "can" not in data and "version" not in data):
                return False
            self._current_config = data
            self._current_file = filepath
            self._add_recent(filepath)
            return True
        except Exception:
            return False

    def save_config(self, filepath: str = None) -> bool:
        """保存配置方案（自动盖项目版本戳）"""
        if filepath is None:
            filepath = self._current_file
        if not filepath:
            filepath = os.path.join(self._config_dir, "default_config.json")

        try:
            self._current_config["modified"] = datetime.now().isoformat()
            self._current_config["project_version"] = "2.2"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self._current_config, f, indent=2, ensure_ascii=False)
            self._current_file = filepath
            self._add_recent(filepath)
            return True
        except Exception:
            return False

    def get(self, key: str, default=None):
        """获取配置项（支持点号分隔的路径）"""
        keys = key.split(".")
        value = self._current_config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def set(self, key: str, value):
        """设置配置项"""
        keys = key.split(".")
        config = self._current_config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value

    @property
    def current_config(self) -> dict:
        return self._current_config.copy()

    @property
    def current_file(self) -> str:
        """当前诊断项目文件路径"""
        return self._current_file

    @property
    def recent_files(self) -> list[str]:
        return self._recent_files.copy()

    def _add_recent(self, filepath: str):
        """添加到最近文件列表"""
        if filepath in self._recent_files:
            self._recent_files.remove(filepath)
        self._recent_files.insert(0, filepath)
        if len(self._recent_files) > self._max_recent:
            self._recent_files = self._recent_files[:self._max_recent]

    @classmethod
    def reset(cls):
        """重置单例"""
        cls._instance = None


def get_config_manager() -> ConfigManager:
    """获取配置管理器实例"""
    return ConfigManager()
