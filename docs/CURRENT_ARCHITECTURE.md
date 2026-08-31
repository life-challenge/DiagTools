# ECU Diagnostic Studio — 当前架构分析（V2.2 Phase 0 代码审计）

> 审计时间: 2026-08-29 | 技术栈: Python 3.13 + PyQt6 + python-can | 入口: `main.py`

## 1. 分层架构

```
main.py                     启动入口 (QApplication + MainWindow)
└── src/ui                  UI层
    ├── main_window.py      主窗口: 菜单/工具栏/项目树/一级导航/状态栏
    ├── styles.py           QSS主题 (dark/light)
    ├── async_uds.py        UdsWorker: QThread后台UDS调用
    ├── views/              一级工作区: 诊断/报文分析/工具中心/占位
    ├── panels/             10个业务面板: 连接/会话/安全/DID/DTC/数据流/IO/例程/序列/刷写/原始报文
    ├── widgets/            日志Dock/LogWidget/趋势图/进度对话框/十六进制输入
    └── dialogs/            设置对话框
└── src/protocol            协议层
    ├── transport_layer.py  ISO 15765-2 分段/重组 (含0x78 pending)
    ├── uds_client.py       UDS客户端: 超时/重发/常用服务封装
    └── uds_services.py     ISO 14229-1 服务编解码 + NRC表
└── src/can_layer           CAN/VCI层
    ├── can_interface.py    抽象基类 + 消息监听器
    ├── can_factory.py      工厂: virtual/pcan/vector
    └── pcan/vector/virtual 具体驱动适配
└── src/business            业务层
    ├── did_manager / dtc_manager / ecu_scanner
    ├── flash_manager / sequence_manager / security_manager
    ├── report_generator (HTML/文本)
    └── bridge_manager/bridge_worker (32位安全算法DLL桥接)
└── src/config              ecu_definition.py: resources/ecu/<NAME>/ecu.json 配置驱动
└── src/models              can_message / uds_message
└── src/utils               config_manager(单例,诊断项目) / crc32 / helpers
└── src/log                 按模块分目录的滚动日志 (diag/flash/comm/sequence/app)
```

## 2. 关键机制

- **配置驱动**: 新增ECU只需 `resources/ecu/<NAME>/ecu.json`，不改代码；禁止在核心UI硬编码ECU名。
- **诊断项目**: `ConfigManager` 单例管理 `resources/config/default_config.json`；文件菜单已统一"诊断项目"语义（新建/打开/保存/另存为/最近/导入导出）。
- **后台任务**: 所有阻塞UDS调用经 `UdsWorker + QThread`，保持引用防GC；数据流轮询用独立线程+信号回GUI。
- **日志**: 底部 `LogDock` 三层（业务日志按等级着色 / UDS Trace仅可解析帧 / CAN Trace全量），支持折叠/清空/导出。
- **安全算法**: Python算法直接加载；32位DLL经 `bridge_manager` 子进程桥接（`D:/Python32/python.exe`）。

## 3. 与 V2.2 计划的差距（本轮改造目标）

| 差距 | 现状 | 目标 |
|------|------|------|
| 一级导航 | 6页签(无车辆总览/报告中心) | 8页签 |
| 车辆总览 | 无 | 车型/VIN/在线统计 + ECU拓扑(状态色) |
| 全车扫描 | 结果弹MessageBox | 结果表+进度+导出 |
| 项目文件 | *.json | *.dproj + 版本/校验 + 自动保存 |
| 报文分析 | 仅Raw工具 | 整合CAN/UDS Trace + ID过滤 |
| 报告中心 | 仅DTC报告导出 | 统一报告中心页 |

## 4. 稳定性基线（改造时不得破坏）

- CAN连接流程（connection_panel → can_factory → uds_client）
- ISO-TP多帧/流控/0x78 pending 处理
- ECU Definition 加载与项目树构建
- 32位桥接子进程生命周期（closeEvent关闭）
