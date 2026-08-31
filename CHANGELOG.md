# 更新日志

本文件记录 DiagTools 各版本的重要变更，格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

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
