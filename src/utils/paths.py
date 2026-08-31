"""统一路径解析（源码运行与PyInstaller打包后通用）

打包为可执行文件后，模块文件位于解包目录，__file__回溯推导的
"项目根目录"不再有效。本模块提供冻结感知的根目录解析：

- 源码运行: <项目根> = src/utils/paths.py 向上三级（与历史行为一致）
- PyInstaller onedir/onefile: <项目根> = 可执行文件所在目录

资源(resources/)、日志(logs/)、数据(data_recordings/)、插件(plugins/)
均以项目根为锚点解析，保证打包版的工作数据生成在exe旁边、可编辑、
可携带。
"""

import os
import sys


def is_frozen() -> bool:
    """是否运行于PyInstaller冻结环境"""
    return getattr(sys, "frozen", False)


def get_project_root() -> str:
    """项目根目录（源码运行=仓库根；打包后=exe所在目录）"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    # src/utils/paths.py -> 向上三级
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def get_resource_path(*parts: str) -> str:
    """resources/ 下的路径

    Args:
        parts: 相对于resources的子路径，如 ("config", "default_config.json")

    Returns:
        绝对路径（不保证存在）
    """
    return os.path.join(get_project_root(), "resources", *parts)


def get_plugins_dir() -> str:
    """安全算法插件目录"""
    return os.path.join(get_project_root(), "plugins", "security_algorithms")


def get_bridge_worker_path() -> str:
    """32位桥接工作进程脚本路径

    源码运行: src/business/bridge_worker.py
    打包后:   exe旁边的bridge_worker.py（构建脚本负责复制）
    """
    if is_frozen():
        return os.path.join(get_project_root(), "bridge_worker.py")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "business", "bridge_worker.py")
