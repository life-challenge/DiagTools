# 贡献指南

感谢你对 DiagTools 的关注！以下是参与贡献的方式。

## 提交 Issue

- **Bug 报告**：请附重现步骤、日志文件（`logs/` 目录）与环境信息
  （Python 版本、操作系统、CAN 硬件类型）。
- **功能建议**：说明使用场景与预期行为。

## 提交 Pull Request

1. Fork 本仓库并创建特性分支：`git checkout -b feature/<名称>`
2. 遵循现有代码风格：
   - 模块顶部必须有中文 docstring，说明模块职责与关键约定
   - 类与公共方法需有 docstring
   - UI 与业务逻辑分离：业务放 `src/business/`，界面放 `src/ui/`
3. 涉及协议/解析逻辑的改动请补充 `tests/` 下的单元测试
4. 提交前运行测试确认通过：
   ```bash
   python -m unittest discover tests -v
   ```
5. PR 描述中说明改动动机、影响范围与验证方式

## 开发约定

- 不破坏既有 CAN/UDS 分层架构（见 `docs/MODULE_MAP.md`）
- 新增 ECU/硬件优先通过配置或插件扩展，避免硬编码
- 后台任务（UDS 通信等）不得阻塞 UI 线程
- 日志走 `src/log/` 分模块记录，不直接 print

## 许可证

提交代码即表示同意以 [MIT License](LICENSE) 发布。
