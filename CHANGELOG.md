# 更新日志

本文件记录 DiagTools 各版本的重要变更，格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [1.3.0] - 2026-08-31

### 新增
- Trace标准格式导出（发给他人在CANoe/Wireshark中分析）:
  - **ASC** (Vector ASCII) / **BLF** (Vector二进制) — CAN报文，
    经python-can写入，CANoe原生可读；BLF导出后回读验证
  - **pcap** — DoIP/以太网报文，手工重建完整报文链
    (Ethernet+IPv4+UDP 13400+DoIP诊断消息，含正确IPv4校验和)，
    Wireshark原生可读
  - 导出入口: 报文分析/底部日志面板的"导出"按钮，
    按扩展名自动选择格式，DoIP逻辑地址自动携带
- 连接面板命名修正: "CAN接口配置"→"通信接口配置"（下拉含DoIP）
- DoIP"本地虚拟ECU（回环模拟）"勾选项: 连接时自动在本机
  TCP端口启动虚拟ECU，无硬件即可本地测试；断开自动回收

### 变更
- 导出完成/失败增加结果提示弹窗

## [1.2.0] - 2026-08-31

### 新增
- DoIP 诊断支持（ISO 13400-2）：TCP 路由激活 + 诊断消息收发，
  UDS 客户端可无缝切换 CAN/以太网传输
- 连接面板新增 DoIP 接口类型（IP/端口/Tester 与 ECU 逻辑地址），
  支持 UDP 广播「发现ECU」自动填入地址
- 虚拟 DoIP ECU（回环模拟，复用 VirtualEcuSimulator），
  无硬件即可体验完整 DoIP 诊断链路
- 日志路径显眼化：日志面板头部路径标签（跟随页签）点击打开对应目录、
  打开日志文件夹按钮、状态栏 Logging 块可点击
- DoIP 单元测试 11 项（回环，无硬件依赖）

### 变更
- `UdsClient` 支持注入自定义传输层（`transport_layer` 参数）

## [1.1.0] - 2026-08-31

### 新增
- 特殊功能页：ECU Definition `special_functions` 配置驱动
  （routine/did/reset/raw 四类动作，后台线程执行，正/负响应判定）
- 全局异常防护：未捕获异常记日志并弹窗，不再静默闪退；
  Qt 消息接入日志系统
- 版本号集中管理（`src.__version__`）
- GitHub Actions CI：双 Python 版本编译 + 单元测试

### 变更
- 删除最后一个占位视图（placeholder_view.py）
- 新增特殊功能单元测试（36 项全部通过）

## [1.0.0] - 2026-08-31

### 新增
- 完整 UDS 协议栈（ISO 14229-1 核心服务 + ISO 15765-2 传输层）
- CAN 硬件抽象层：Virtual（内置 ECU 仿真）/ PCAN（32 位桥接）/ Vector
- 8 个一级工作台：车辆总览、ECU 诊断、刷写中心、标定中心、测试中心、
  报文分析、报告中心、工具中心
- DID / DTC 管理器：JSON 定义、轮询、状态位可视化、时间线、CSV 导出
- 刷写中心：配置驱动 6 步流程，支持 bin/s19/s28/s37/hex 固件
- 安全算法插件：Python / DLL 动态加载（32 位桥接）
- 序列引擎、全车扫描、诊断报告生成（HTML/文本）
- 报文分析：CAN/UDS Trace、原始报文、报文重放、DBC 信号级解码
- 数据导入：DID/DTC 配置、Flash 配置、A2L、ODX/PDX/CDD、ECU Definition
- `.dproj` 项目文件：配置持久化、导入恢复、自动保存
- 分模块日志系统（通信/诊断/刷写/序列）
- 暗色 / 亮色主题
- unittest 单元测试套件
