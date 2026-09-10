"""DiagTools - CAN UDS 诊断仪 程序入口"""

import sys
import os
import traceback

# 确保项目根目录在Python路径中
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import qInstallMessageHandler, QtMsgType
from PyQt6.QtGui import QIcon

import src
from src.ui.main_window import MainWindow
from src.log.log_manager import LogManager
from src.utils.config_manager import ConfigManager
from src.utils.paths import get_resource_path


def _install_crash_handlers(logger):
    """安装全局异常钩子: 未捕获异常记日志并弹窗，避免静默闪退。

    此前会话切换等场景的闪退难以定位，问题在于异常被吞掉且无日志。
    """
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        detail = "".join(traceback.format_exception(
            exc_type, exc_value, exc_tb))
        logger.error("未捕获异常:\n%s", detail)
        try:
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None, "DiagTools - 程序异常",
                    f"发生未处理的错误，程序将尝试继续运行。\n\n"
                    f"{exc_type.__name__}: {exc_value}\n\n"
                    f"详情已记录到日志（logs/app/）。")
        except Exception:
            pass  # 弹窗失败时至少保证日志已落盘

    sys.excepthook = _excepthook

    def _qt_message_handler(msg_type, context, message):
        level = {QtMsgType.QtDebugMsg: "debug",
                 QtMsgType.QtInfoMsg: "info",
                 QtMsgType.QtWarningMsg: "warning",
                 QtMsgType.QtCriticalMsg: "error",
                 QtMsgType.QtFatalMsg: "error"}.get(msg_type, "info")
        getattr(logger, level, logger.info)("Qt: %s", message)

    qInstallMessageHandler(_qt_message_handler)


def main():
    """程序主入口"""
    # 初始化日志管理器
    log_manager = LogManager()
    app_logger = log_manager.get_app_logger()
    app_logger.info("=" * 50)
    app_logger.info("DiagTools %s 启动", src.__version__)
    app_logger.info("=" * 50)

    # 全局异常钩子（闪退防护）
    _install_crash_handlers(app_logger)

    # 初始化配置管理器
    config = ConfigManager()

    # 启动时自动清理过期日志（保留天数来自配置，默认30天）——
    # 此前 cleanup_old_logs 为死代码，长期使用后 logs/ 每子目录堆积数百文件
    retention_days = int(config.get("log.retention_days", 30))
    deleted = log_manager.cleanup_old_logs(retention_days)
    if deleted:
        app_logger.info("已清理 %d 个超过 %d 天的旧日志文件",
                        deleted, retention_days)

    # 高DPI支持
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    # 创建应用
    app = QApplication(sys.argv)
    app.setApplicationName("DiagTools")
    app.setOrganizationName("DiagTools")
    app.setApplicationVersion(src.__version__)

    # 应用图标（诊断仪主题: OBD接头+总线波形）——任务栏/窗口/关于框统一；
    # 源码运行与打包运行都经 get_resource_path 解析（冻结时资源在exe旁）
    icon_path = get_resource_path("icons", "diagtools.ico")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    else:
        app_logger.warning("应用图标未找到: %s", icon_path)

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
