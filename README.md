# DiagTools — CAN/UDS ECU 诊断工具

一个基于 **Python + PyQt6** 的专业级汽车 ECU 诊断工具（诊断仪），实现完整的
**ISO 14229-1 (UDS)** 协议栈与 **ISO 15765-2 (CAN TP)** 传输层，提供
诊断、刷写、标定、测试、报文分析一体化工作台。内置虚拟 CAN 仿真环境，
**无需任何硬件即可运行和开发**。

> 支持中文界面 / 暗色与亮色主题 / 多 ECU 项目管理 / 配置驱动扩展。

---

## ✨ 功能特性

### 诊断核心
- **完整 UDS 服务**：会话控制、安全访问、DID 读写、DTC 读取、例程控制、
  IO 控制、通信控制、ECU 复位等（ISO 14229-1 核心服务全覆盖）
- **ISO 15765-2 传输层**：单帧/首帧/连续帧/流控帧自动分段与重组，
  支持标准帧与扩展帧
- **TesterPresent 保持**、NRC 处理、ResponsePending (0x78) 支持

### 硬件抽象层
- 统一 `CanInterface` 接口 + 工厂模式，可插拔：
  - **Virtual** — 内置完整虚拟 ECU 仿真（会话/安全/刷写逻辑）
  - **PCAN** — 通过 32 位 Python 桥接进程自动调用（无需手动配置）
  - **Vector** — python-can 驱动

### 业务模块
- **DID 管理**：JSON 定义、数据类型解析（raw/ascii/uint/int/float）、
  缩放偏移、轮询、分组
- **DTC 管理**：多模式读取、8 位状态位可视化、严重程度分级、时间线、CSV 导出
- **刷写中心**：6 步配置驱动流程（擦除→下载→传输→退出→校验→复位），
  支持 bin/s19/s28/s37/hex 多格式固件
- **安全算法插件**：动态加载 Python 脚本或 DLL（支持 32 位桥接）
- **序列引擎**：步骤级发送次数、延时、循环、错误策略
- **标定中心**：22/2E 读写 + 回读校验
- **测试中心**：内置用例（会话切换/TesterPresent/读 DTC/读 DID）+ 自定义序列
- **全车扫描**：地址遍历 + 会话探测

### 报文分析
- CAN / UDS Trace 双视图、原始报文手工发送
- **报文重放**：CSV/HEX 文件、固定间隔或原始时序、循环次数
- **DBC 数据库**：BO_/SG_ 解析，大小端 + 有符号信号物理值解码，
  CAN Trace 实时联动显示

### 数据导入
- DID / DTC 配置（JSON，自动识别）
- Flash 配置（刷写参数回填）
- A2L（ASAP2 标定描述轻量解析）
- **ODX / PDX / CDD**（ISO 22901 诊断数据，DTC 定义可注入故障码面板）
- ECU Definition（JSON 配置驱动，新增 ECU 零代码）

### 工程能力
- 分模块日志（通信/诊断/刷写/序列），按模块/时间自动生成文件
- `.dproj` 项目文件：配置持久化 + 导入恢复 + 自动保存
- HTML / 文本诊断报告生成

---

## 🚀 快速开始

### 环境要求
- Python 3.10+（推荐 3.12/3.13）
- Windows / Linux / macOS（PCAN 桥接仅 Windows）

### 安装与运行

```bash
git clone https://github.com/<your-name>/DiagTools.git
cd DiagTools
pip install -r requirements.txt
python main.py
```

启动后默认使用 **虚拟 CAN**，点击「连接」即可与内置仿真 ECU 交互。

### 运行测试

```bash
python -m unittest discover tests -v
```

---

## 📁 项目结构

```
DiagTools/
├── main.py                  # 应用入口
├── requirements.txt
├── src/
│   ├── models/              # 数据模型（CAN/UDS 报文）
│   ├── can_layer/           # CAN 硬件抽象层（虚拟/PCAN/Vector + 工厂）
│   ├── protocol/            # ISO 15765-2 传输层 + UDS 服务编解码/客户端
│   ├── business/            # 业务管理器与解析器（DBC/ODX/A2L）
│   ├── log/                 # 分模块日志
│   ├── config/              # ECU Definition 配置驱动
│   ├── utils/               # 配置管理(.dproj)/CRC/辅助函数
│   └── ui/                  # PyQt6 界面（主窗口/视图/面板/控件/对话框）
├── plugins/security_algorithms/   # 安全算法插件（Python 示例）
├── resources/               # ECU定义、DID定义、序列、默认配置
├── tests/                   # unittest 单元测试
└── docs/                    # 架构文档（MODULE_MAP / CURRENT_ARCHITECTURE）
```

详细模块说明见 [docs/MODULE_MAP.md](docs/MODULE_MAP.md)。

---

## 🔌 扩展指南

### 新增 ECU
在 `resources/ecu/<名称>/` 下创建 `ecu.json`（name/description/tx_id/rx_id/
info_dids），无需修改任何代码。

### 安全算法插件
实现 `plugins/security_algorithms/algorithm_base.py` 定义的接口，
放入同目录即可被自动发现（支持 `.py` 与 `.dll`）。

### CAN 硬件
继承 `src/can_layer/can_interface.py` 的 `CanInterfaceBase`，
并在 `can_factory.py` 注册。

---

## 🧪 已验证场景

- 虚拟 CAN 端到端：会话控制 / DID 读写 / 安全访问 / DTC / TesterPresent / 刷写
- DBC 大小端与有符号信号解码
- ODX/PDX 容器解析与 J2012 DTC 编码
- GUI offscreen 冒烟测试（主窗口、8 个一级页签、全部导入菜单）

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

## 🤝 贡献

欢迎提交 Issue 与 Pull Request，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## ⚠️ 免责声明

本工具仅用于合法的诊断开发与测试目的。请遵守所在地区的法规，
对车辆进行刷写等操作前务必确认已获得授权，由此产生的后果由使用者自行承担。
