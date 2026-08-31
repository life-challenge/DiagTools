# MODULE_MAP — 模块边界与职责图（V2.2）

依赖方向自上而下，禁止反向导入（UI→业务→协议→CAN；models/utils/log 为横切层）。

## 目录总览

```
d:\DiagTools
├── main.py                 入口（32位桥接检查 + QApplication）
├── docs/                   CURRENT_ARCHITECTURE.md / MODULE_MAP.md
├── resources/              ECU Definition JSON / 示例DID定义 / 安全算法插件
├── reports/                扫描报告等导出产物（报告中心扫描此目录）
└── src/
    ├── models/             数据模型（无业务逻辑）
    ├── can_layer/          CAN硬件抽象
    ├── protocol/           ISO-TP + UDS 协议栈
    ├── business/           业务管理器（会话无关的能力层）
    ├── log/                分模块日志（diag/flash/comm/sequence）
    ├── config/             ECU Definition 模型
    ├── utils/              配置管理/校验/通用工具
    └── ui/
        ├── main_window.py  主窗口：8页签导航 + 菜单 + 连接流程 + 后台任务调度
        ├── views/          一级工作区
        ├── panels/         功能面板（工作区的组成单元）
        ├── widgets/        复用控件
        └── dialogs/        设置等对话框
```

## 各层模块

### models（横切）
| 文件 | 职责 |
|---|---|
| can_message.py | CanMessage/方向/帧类型，to_dict/from_dict |
| uds_message.py | UdsMessage 服务请求/响应封装 |

### can_layer
| 文件 | 职责 |
|---|---|
| can_interface.py | CanInterface 抽象基类：open/close/send/监听器 |
| can_factory.py | CanFactory.create() 按类型实例化 |
| pcan_interface.py | PCAN-Basic 封装（经32位桥接） |
| vector_interface.py | Vector vxlapi 封装 |
| virtual_interface.py | 虚拟CAN：内置ECU模拟+ISO-TP联调 |

### protocol
| 文件 | 职责 |
|---|---|
| transport_layer.py | ISO 15765-2 分片/重组/流控 |
| uds_services.py | ISO 14229 全服务编解码 + NRC 描述 |
| uds_client.py | 请求/响应匹配、超时、TesterPresent、异步回调 |

### business
| 文件 | 职责 |
|---|---|
| did_manager.py | DID定义(双格式兼容)/数据类型解析/轮询/分组 |
| dtc_manager.py | DTC多模式读取/8-bit状态/时间线/导出 |
| flash_manager.py | 6步刷写流程 + 多格式固件解析 |
| security_manager.py | 安全算法插件动态加载（py/dll） |
| sequence_manager.py | 序列执行引擎（次数/延时/循环/错误策略） |
| ecu_scanner.py | 全车扫描（地址遍历 + 会话探测） |
| dbc_parser.py | DBC解析：BO_/SG_ + 大小端信号物理值解码 |
| a2l_parser.py | A2L轻量解析：PROJECT/MODULE/MEASUREMENT/CHARACTERISTIC |
| odx_parser.py | ODX/PDX/CDD解析：DIAG-COMM服务/DTC(J2012编码)/容器ZIP探测 |
| report_generator.py | HTML/文本报告生成 |
| bridge_manager.py / bridge_worker.py | 32位PCAN桥接进程管理 |

### utils
| 文件 | 职责 |
|---|---|
| config_manager.py | .dproj 项目文件读写（校验+版本戳+自动保存） |
| crc32.py / helpers.py | 校验和与通用工具 |

### ui/views（一级工作区，NAV索引0~7）
| 视图 | 组成 |
|---|---|
| vehicle_overview_view | 信息卡 + Gateway/ECU拓扑(状态色) + 扫描结果表 |
| diagnostic_view | 会话/DID/数据流/DTC/IO/Routine/安全/序列 子页签 |
| flash_panel | 刷写中心（直接作为一级页，支持配置导入） |
| calibration_view | 标定中心：DID标定表 读取(22)/写入(2E)/回读校验 |
| test_center_view | 测试中心：内置用例+自定义序列，后台执行与报告导出 |
| trace_view | CAN/UDS Trace + 原始报文 + 报文重放 + 数据库(5页签) |
| report_center_view | reports/ 目录分类浏览与打开 |
| tools_view | 连接管理 + ECU Definition 管理 |

### ui/panels
connection / session / did / datastream / dtc / io / routine /
security / sequence / raw / replay（重放） / dbc（数据库浏览解码） / flash /
special（特殊功能，ECU配置驱动 routine/did/reset/raw）

### ui/dialogs
settings_dialog（设置）、a2l_import_dialog（A2L解析/导出）、
odx_import_dialog（ODX/CDD解析，DTC定义可注入故障码面板）

### ui/widgets
log_widget（ID过滤/暂停/导出/搜索）、log_dock（底部通信日志，与
trace_view 数据同源）、ecu_tree、hex_input、progress_dialog、
dtc_status_widget、trend_chart

## 关键跨模块约定
- 连接建立后主窗口注入: `set_uds_client()`（诊断/刷写/标定/测试/原始报文）、
  `set_can_interface()`（报文重放）。
- 导入持久化: 菜单导入的 DID/DTC/Flash/A2L/ODX-DTC 路径记录在 `imports.*` 配置，
  启动/打开项目时 `_restore_imports()` 自动恢复。
- 报文流转: CAN监听器 → `main_window._on_can_message` → 底部LogDock +
  TraceView（DBC解码描述在此注入）。
- 后台任务: 统一 `_start_worker()`（QThread+UdsWorker），UI不阻塞。
- 报告导出: `_on_scan_done` → `export_scan_report_csv` → reports/ → 报告中心refresh。
