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

复现须使用合成 prompt 和数据。不得附上凭据、`.env`、认证文件、session 缓存、
原始 provider 回复、认证 header 或私人路径。模块导入位置可在本机核对，分享前
必须脱敏身份路径。安全敏感问题按 [SECURITY.md](SECURITY.md) 私下报告，
不要放入公开 issue。

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
- 仓库 hook：使用下方独立 Python >=3.11 环境，校验全部文件，不省略任何 hook
- 覆盖率同时统计语句和分支；项目配置的阈值作用于二者的综合结果
- 代码风格：`python -m ruff check src/soulacp/ tests/ examples/ tools/` 和 `python -m ruff format --check src/soulacp/ tests/ examples/ tools/`
- 必须含类型注解（`py.typed` 包）；使用 `python -m pyright` 与
  `PYTHONPATH=src python -m pyright --verifytypes soulacp --ignoreexternal` 验证
  （PowerShell 在第二条命令前设置 `$env:PYTHONPATH = "src"`）
- 安全扫描：`python -m bandit -q -r src/soulacp`
- 分发构建及冷安装检查：遵循下方发布身份清单
- 0.2.x patch 保持已记录 API 和行为兼容；任何破坏性变更提案须遵循 [GOVERNANCE.md](GOVERNANCE.md) 的版本与迁移审查
- 不引入必需的 runtime 依赖；OpenTelemetry API 保持可选
- 开发环境包含OpenTelemetry SDK，用于真实内存导出的异常事件隐私测试；它不是运行时必装依赖。

现有语句/分支覆盖率门使用
`python -m pytest tests/ -W error --cov=soulacp --cov-branch --cov-report=term`。
`make check`只覆盖lint、类型与Bandit，不是完整CI；测试、hook、依赖审计和受控构建
须分别报告。Provider集成测试需要明确授权、已安装CLI和认证，不能只为消除默认skip而启用。

检查已安装包的类型完整性时，应激活目标环境，确保`PATH`与虚拟环境的
`VIRTUAL_ENV`指向该环境，并核对verifier报告的包来源。仅覆盖`PYTHONPATH`或
从另一个Python解释器启动，不能证明Node verifier实际找到了哪份包；源码与安装态
类型检查是不同证据。

### 独立仓库 Hook 环境

运行库及开发测试仍支持 Python 3.10。仓库 hook 使用 Python 3.11 或更高版本，
因为其 virtualenv 依赖所需的安全修复版 filelock 已不支持 3.10。不得通过安装旧版
filelock 或忽略审计告警来合并环境。
`hooks` extra 在 3.10 上不会安装 hook 工具，不代表该环境能执行 hook；
全部 hook 命令须使用 >=3.11 的解释器。

```bash
python3.11 -m venv .venv-hooks
.venv-hooks/bin/python -m pip install -e ".[hooks]" -c requirements-hooks.txt
.venv-hooks/bin/python -m pre_commit install
.venv-hooks/bin/python -m pre_commit run --all-files
```

`--all-files`选择Git已跟踪文件，不包含新增未跟踪文件。审查新输入后，还须对它们
显式运行同一组hook，例如：

```bash
.venv-hooks/bin/python -m pre_commit run --files tests/test_fs_integrity_errors.py tests/test_owned_fs_shutdown.py
```

Windows 使用 `py -3.11 -m venv .venv-hooks` 创建环境，将上述解释器路径替换为
`.venv-hooks\Scripts\python.exe`。`requirements-min.txt` 和 `requirements-hooks.txt`
均须通过依赖审计；环境分离不豁免任一审计或 hook 检查。
`make hooks` 使用该独立解释器，不调用 PATH 中的全局 `pre-commit`。
自定义位置用 `HOOKS_PYTHON="解释器的绝对路径"` 指定；没有 make 时运行
`python tools/run_hooks.py --python <hook解释器>`。环境缺失或 Python <3.11
时明确失败，不回落到全局工具。Git 忽略规则覆盖已记录的本地环境及会话缓存位置，
不能代替提交前审查。

### 发布身份与隐私

当前源码目标为 **0.2.3**：

1. 核对 `pyproject.toml`、`src/soulacp/meta.py`、`uv.lock` 中本项目条目、
   当前文档页脚和当前 CHANGELOG 条目。不要改写历史版本，也不要把日期当上传回执。
2. 选择全新且不存在的输出目录。受控入口拒绝已有路径，保留失败产物，
   只验证本次新归档。`make build`复用该入口而不隐式清理；后续构建通过
   `DIST_DIR`另选新目录。`make clean`仍是需显式选择的破坏性清理：

   ```bash
   python tools/build_release.py --outdir dist/0.2.3
   # 或：make build DIST_DIR=dist/another-new-directory
   ```

3. 核对两个归档的元数据、包内 `__version__` 和包正文，记录每份文件的 SHA-256
   与大小。旧 `dist` 文件和生成的 `build`/`egg-info` 元数据不能证明新分发正确。
4. 分享前检查归档内容，排除凭据、认证存储、session 缓存、本地数据库、原始日志、
   私有审计/备份目录和身份路径。不得为了构建通过而修改隐私检查口径。
5. 在 checkout 外创建独立环境，仅安装准确且已核验的 wheel，不获取依赖。
   从 checkout 外使用该环境的解释器及 `-I`，比较
   `importlib.metadata.version('soulacp')` 与 `soulacp.__version__`，并在本机核对
   `soulacp.__file__`。源码测试会优先使用 `src`，不能替代分发验收；私人路径不得外泄。
6. 如实记录测试、失败、skip、平台范围和产物身份。安装 wheel 不等于真实 provider
   集成验收。下游依赖及 lock 独立核对，不静默升级或重启消费应用的活动 worker。
7. 经维护者发布裁定后，仅上传明确选择的文件，不无差别上传 `dist/*`。文档示例
   本身不是发布授权；对外只提供审阅过的最小发布证据，不能包含凭据或原始私人诊断。

仅文档变化的产物也须列出准确变更输入，重新核验归档隐私、元数据、源码完整性及
冷安装来源。只有运行正文、依赖、测试、平台和参数均未变化的组件才可继承原运行
结果；必须标明组件继承，不能冒称新产物又跑过全矩阵。新增或修改的文档断言须有
独立执行证据，不能覆盖此前已验产物。

### 双语要求

README 和 CONTRIBUTING 同时维护英文和中文版本。Issue 可使用任一语言。
除标题外，示例、API名称、默认值、隐私限制及验收范围也须对齐。AIXP项目表以SoulRun
对应语言的README为准，“本项目”标记改为SoulACP。不能将本地CI结果或既有徽标
冒充GitHub Actions或provider验收结果。

离线文档回归命令：
`python -m pytest tests/test_documentation_consistency.py -W error -q`。
外部来源主张单独复核；本地链接检查不能证明远端页面或模型可用。旧CHANGELOG条目保留历史。

配置示例须区分`ACPConfig()`与`ACPConfig.from_env()`，不得暗示自动加载`.env`。
权限示例须区分`session/request_permission`选项选择和`fs/*`、`terminal/*`服务
策略。Python文件中的纯文档改动须比较去掉文档字符串后的执行AST，并保留运行
回归；文字或链接检查通过不能独自证明行为未变。

## 行为准则

参与本项目即表示您同意遵守 [Code of Conduct](CODE_OF_CONDUCT.md)。

## 贡献的许可

提交即同意您的贡献以 [Apache License 2.0](LICENSE) 授权（无论通过 issue 或未来的 PR）。

Copyright 2026 AIXP Labs AIXP.dev | SoulACP.dev

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.2.3. www.soulacp.dev
