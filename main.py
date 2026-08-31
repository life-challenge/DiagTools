"""DiagTools - CAN UDS 诊断仪 程序入口"""

import sys
import os

# 确保项目根目录在Python路径中
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from src.ui.main_window import MainWindow
from src.log.log_manager import LogManager
from src.utils.config_manager import ConfigManager


def main():
    """程序主入口"""
    # 初始化日志管理器
    log_manager = LogManager()
    app_logger = log_manager.get_app_logger()
    app_logger.info("=" * 50)
    app_logger.info("DiagTools 启动")
    app_logger.info("=" * 50)

    # 初始化配置管理器
    config = ConfigManager()

    # 高DPI支持
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    # 创建应用
    app = QApplication(sys.argv)
    app.setApplicationName("DiagTools")
    app.setOrganizationName("DiagTools")
    app.setApplicationVersion("1.0.0")

    # 创建主窗口
    window = MainWindow()

    # 应用保存的主题
    theme = config.get("ui.theme", "dark")
    if hasattr(window, '_current_theme'):
        window._current_theme = theme
        window._apply_theme()

    window.show()
    app_logger.info("主窗口已显示")

    # 运行事件循环
    exit_code = app.exec()

    app_logger.info("DiagTools 退出")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
