"""独立日志模块 - 日志管理器"""

import os
import logging
import time
from datetime import datetime
from typing import Optional


# 日志目录定义
LOG_SUBDIRS = {
    "diag": "diag",
    "flash": "flash",
    "comm": "comm",
    "sequence": "sequence",
    "app": "app",
}

# 日志文件前缀
LOG_PREFIXES = {
    "diag": "diag",
    "flash": "flash",
    "comm": "comm",
    "sequence": "seq",
    "app": "app",
}

# 日志格式
LOG_FORMAT = "[%(asctime)s.%(msecs)03d] [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class LogManager:
    """统一日志管理器
    
    管理所有模块的日志记录，自动按模块分类输出到对应子目录，
    文件名根据"时间戳+模块标识"自动生成。
    """

    _instance: Optional["LogManager"] = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, log_base_dir: str = None):
        if LogManager._initialized:
            return

        if log_base_dir is None:
            # 默认使用项目根目录下的logs文件夹
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            log_base_dir = os.path.join(project_root, "logs")

        self._log_base_dir = log_base_dir
        self._loggers = {}
        self._session_time = datetime.now()
        self._timestamp_str = self._session_time.strftime("%Y%m%d_%H%M%S")

        self._init_log_directories()
        self._init_all_loggers()
        LogManager._initialized = True

    def _init_log_directories(self):
        """启动时自动创建所有日志目录"""
        os.makedirs(self._log_base_dir, exist_ok=True)
        for subdir in LOG_SUBDIRS.values():
            dir_path = os.path.join(self._log_base_dir, subdir)
            os.makedirs(dir_path, exist_ok=True)

    def _init_all_loggers(self):
        """初始化所有模块日志器"""
        for module_name, subdir in LOG_SUBDIRS.items():
            prefix = LOG_PREFIXES[module_name]
            logger = self._create_logger(module_name, subdir, prefix)
            self._loggers[module_name] = logger

    def _create_logger(self, name: str, subdir: str, prefix: str) -> logging.Logger:
        """创建模块日志器
        
        Args:
            name: 日志器名称
            subdir: 子目录名
            prefix: 文件名前缀
            
        Returns:
            配置好的Logger实例
        """
        logger = logging.getLogger(f"diagtools.{name}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        # 避免重复添加Handler
        if logger.handlers:
            return logger

        # 文件Handler
        log_dir = os.path.join(self._log_base_dir, subdir)
        log_file = os.path.join(log_dir, f"{prefix}_{self._timestamp_str}.log")

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        # 控制台Handler (INFO级别以上)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        return logger

    def get_diag_logger(self) -> logging.Logger:
        """获取诊断日志器"""
        return self._loggers.get("diag", self._get_fallback())

    def get_flash_logger(self) -> logging.Logger:
        """获取刷写日志器"""
        return self._loggers.get("flash", self._get_fallback())

    def get_comm_logger(self) -> logging.Logger:
        """获取通信日志器"""
        return self._loggers.get("comm", self._get_fallback())

    def get_sequence_logger(self) -> logging.Logger:
        """获取序列执行日志器"""
        return self._loggers.get("sequence", self._get_fallback())

    def get_app_logger(self) -> logging.Logger:
        """获取应用程序日志器"""
        return self._loggers.get("app", self._get_fallback())

    def _get_fallback(self) -> logging.Logger:
        """获取备用日志器"""
        return logging.getLogger("diagtools.fallback")

    @property
    def log_base_dir(self) -> str:
        """日志根目录"""
        return self._log_base_dir

    @property
    def session_timestamp(self) -> str:
        """当前会话时间戳"""
        return self._timestamp_str

    def cleanup_old_logs(self, days: int = 30) -> int:
        """清理超过指定天数的旧日志文件
        
        Args:
            days: 保留天数
            
        Returns:
            删除的文件数量
        """
        cutoff_time = time.time() - (days * 86400)
        deleted_count = 0

        for subdir in LOG_SUBDIRS.values():
            dir_path = os.path.join(self._log_base_dir, subdir)
            if not os.path.exists(dir_path):
                continue
            for filename in os.listdir(dir_path):
                filepath = os.path.join(dir_path, filename)
                if os.path.isfile(filepath) and filename.endswith(".log"):
                    if os.path.getmtime(filepath) < cutoff_time:
                        try:
                            os.remove(filepath)
                            deleted_count += 1
                        except OSError:
                            pass

        return deleted_count

    def get_log_files(self, module: str = None) -> list[dict]:
        """获取日志文件列表
        
        Args:
            module: 指定模块名，None则返回所有
            
        Returns:
            日志文件信息列表
        """
        files = []
        subdirs = [LOG_SUBDIRS[module]] if module and module in LOG_SUBDIRS else LOG_SUBDIRS.values()

        for subdir in subdirs:
            dir_path = os.path.join(self._log_base_dir, subdir)
            if not os.path.exists(dir_path):
                continue
            for filename in sorted(os.listdir(dir_path), reverse=True):
                if filename.endswith(".log"):
                    filepath = os.path.join(dir_path, filename)
                    stat = os.stat(filepath)
                    files.append({
                        "name": filename,
                        "path": filepath,
                        "module": subdir,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    })

        return files

    @classmethod
    def reset(cls):
        """重置单例（用于测试）"""
        cls._instance = None
        cls._initialized = True
        # 清理所有diagtools logger的handlers
        for name in logging.root.manager.loggerDict:
            if name.startswith("diagtools."):
                logger = logging.getLogger(name)
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)
        cls._initialized = False


# 便捷函数
def get_log_manager() -> LogManager:
    """获取日志管理器实例"""
    return LogManager()


def log_diag(message: str, level: int = logging.INFO):
    """快捷记录诊断日志"""
    LogManager().get_diag_logger().log(level, message)


def log_flash(message: str, level: int = logging.INFO):
    """快捷记录刷写日志"""
    LogManager().get_flash_logger().log(level, message)


def log_comm(message: str, level: int = logging.INFO):
    """快捷记录通信日志"""
    LogManager().get_comm_logger().log(level, message)


def log_sequence(message: str, level: int = logging.INFO):
    """快捷记录序列日志"""
    LogManager().get_sequence_logger().log(level, message)


def log_app(message: str, level: int = logging.INFO):
    """快捷记录应用日志"""
    LogManager().get_app_logger().log(level, message)
