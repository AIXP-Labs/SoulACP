# 贡献 SoulACP

感谢您有兴趣为 SoulACP 做出贡献！

> ⚠️ **当前阶段的贡献政策**
>
> 我们欢迎通过 **GitHub Issues 进行讨论**。
>
> **当前不接受外部 Pull Request。** 如果您有任何建议 — bug 报告、功能想法、新适配器建议或改进 — 请通过 Issue 描述。如果我们认为有价值，由维护者实现并在 commit/release notes 中署名感谢您。
>
> 此政策未来会重新审视。

> **阶段状态（v0.2.x）**
>
> SoulACP 处于早期开发阶段。下方流程描述的是**目标**开发模型。初期决策由 AIXP Labs 核心维护者做出；社区讨论窗口将随贡献者基数增长而扩大。

## 如何贡献

### 报告 Issue

- 使用 [GitHub Issues](https://github.com/AIXP-Labs/SoulACP/issues) 报告 bug、提议功能或建议新适配器
- 新 CLI 适配器请求使用 `new-adapter` 标签
- bug 报告请附复现步骤和 SoulACP 版本（`python -c "import soulacp; print(soulacp.__version__)"`）
- 提供清晰的描述和示例

### 讨论驱动开发

1. 通过 issue 提出讨论
2. 维护者评估价值、可行性和公理 0 合规性
3. 讨论达成共识后，由维护者实现变更
4. 贡献者在 commit / release notes 中获得署名

### 提议新 CLI 适配器

提议新适配器时，请在 issue 中包含：

- **名称** + GitHub / 官网链接
- **ACP 启动命令**（如 `acp` 子命令、`--acp` 标志或特殊模式）
- **认证**要求（API key、OAuth 等）
- **为什么**应该加入（使用场景、需求）

维护者将评估并实现（如批准）。

## 贡献原则

### 质量标准

- 所有新代码必须包含测试（单元 + 集成测试，在可行时）
- 单元测试：`python -m pytest tests/ -W error -q`
- 最低支持开发工具链：`python -m pip install -e ".[dev]" -c requirements-min.txt`
- 仓库 hook：首次运行 `pre-commit install`，之后使用 `pre-commit run --all-files` 校验整个 checkout
- 覆盖率同时统计语句和分支；项目配置的阈值作用于二者的综合结果
- 代码风格：`python -m ruff check src/soulacp/ tests/ examples/` 和 `python -m ruff format --check src/soulacp/ tests/ examples/`
- 必须含类型注解（`py.typed` 包）；使用 `python -m pyright` 与
  `PYTHONPATH=src python -m pyright --verifytypes soulacp --ignoreexternal` 验证
  （PowerShell 在第二条命令前设置 `$env:PYTHONPATH = "src"`）
- 安全扫描：`python -m bandit -q -r src/soulacp`
- 分发构建：先运行 `python -m build`，再运行 `python -m twine check dist/*`
- 向后兼容的 API 变更（minor 版本间不破坏）
- 不引入必需的 runtime 依赖；OpenTelemetry API 保持可选

### 双语要求

README 和 CONTRIBUTING 同时维护英文和中文版本。Issue 可使用任一语言。

## 行为准则

参与本项目即表示您同意遵守 [Code of Conduct](CODE_OF_CONDUCT.md)。

## 贡献的许可

提交即同意您的贡献以 [Apache License 2.0](LICENSE) 授权（无论通过 issue 或未来的 PR）。

Copyright 2026 AIXP Labs AIXP.dev | SoulACP.dev

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.2.0. www.soulacp.dev
