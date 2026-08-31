"""DiagTools — CAN/UDS ECU 诊断工具源码根包。

版本号集中在此维护（main.py / CHANGELOG 引用此处，不重复硬编码）。

分层结构（自底向上）:
  models/     数据模型（CAN/UDS 报文）
  can_layer/  CAN 硬件抽象层（虚拟/PCAN/Vector，工厂创建）
  protocol/   ISO 15765-2 传输层 + ISO 14229-1 UDS 协议栈
  business/   业务管理器（DID/DTC/刷写/安全/序列/扫描/报告）
  log/        分模块日志（通信/诊断/刷写/序列）
  config/     ECU Definition 配置驱动
  utils/      配置管理、CRC、辅助函数
  ui/         PyQt6 界面层（主窗口/视图/面板/控件/对话框）
"""

__version__ = "1.3.0"
