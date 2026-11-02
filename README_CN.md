# SoulACP

**面向 Agent Client Protocol 的 Python 客户端库，复用 CLI 原生会话并提供有界连接池。**

[![CI](https://github.com/AIXP-Labs/SoulACP/actions/workflows/ci.yml/badge.svg)](https://github.com/AIXP-Labs/SoulACP/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/soulacp)](https://pypi.org/project/soulacp/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

[English](README.md) | 简体中文

## 目录

- [特性](#特性)
- [安装](#安装)
- [快速开始](#快速开始)
- [支持的 Agent](#支持的-agent)
- [使用示例](#使用示例)
- [认证与服务条款](#认证与服务条款)
- [Session 管理](#session-管理)
- [配置](#配置)
- [隐私边界](#隐私边界)
- [错误处理](#错误处理)
- [架构](#架构)
- [测试](#测试)
- [项目文档](#项目文档)
- [AIXP Labs](#aixp-labs-aixpdev)
- [许可证](#许可证)
- [价值观与哲学](#values-philosophy)

## 特性

- **30 个 CLI 集成**：29 个 ACP 适配器，加一个旧版 Cursor CLI 集成；完整清单见[支持的 Agent](#支持的-agent)
- **CLI 原生会话**：provider CLI 是会话事实源；soulacp 只保存 session 映射和传输状态
- **不重复维护对话历史**：prompt 不会经由第二份中间件对话记录重放
- **ManagedSession**：高级 API，安全 session 复用、显式重置、启动重试和降级
- **有界连接池**：session 安全复用、FIFO 背压和生命周期退休
- **Session 存储**：TTL 支持的 user→session 映射，可使用内存或文件存储
- **异步流式输出**：基于 asyncio 的实时响应
- **安全重试**：仅对可重放的控制面故障指数退避，不盲目重放 prompt
- **结构化 session 上下文**：校验 additional root 及 stdio/HTTP/SSE MCP server 定义
- **宿主服务**：配置根目录约束的文件访问和有界 ACP 终端进程管理
- **当前 ACP 生命周期**：能力约束的认证、登出、session 关闭、请求取消和配置 API
- **纯标准库核心**：无必需运行时依赖；OpenTelemetry API 为可选依赖

## 安装

SoulACP 需要 Python 3.10 或更高版本，并且至少安装一个受支持的 agent CLI
或 ACP bridge，并在需要时完成认证。

当前源码CI矩阵面向Windows和Ubuntu上的常规有GIL的CPython `3.10`至
`3.15`；安装许可（`>=3.10`）不是测试回执。声称全部矩阵任务通过前，
必须核对准确提交的CI结果。自由线程构建、子解释器和实验性JIT模式
不在本次资格范围内。Provider CLI真实集成与Python兼容性分别验收。

### 包索引版本

```bash
pip install soulacp
```

GitHub 与 PyPI 使用相同版本号同步发布。
如需明确选择 Python 解释器，可使用 `python -m pip install soulacp`。

本checkout目标为`0.2.5`，在记录发布回执前仅是本地候选。
`pip install soulacp`安装最新已发布版本，而不是未发布的checkout。
本地部署不代表已经在PyPI发布。

### 当前源码

也可以克隆 GitHub 仓库并在本地从源码安装。
如需安装某个发布版本，应先核实标签确实存在；否则使用与产物对应的已记录源码提交。
候选版本号不代表已发布。

```bash
git clone https://github.com/AIXP-Labs/SoulACP.git
cd SoulACP
python -m pip install -e .

# 开发与测试工具
python -m pip install -e ".[dev]"
```

仓库 hook 单独安装于 Python >=3.11 环境；运行库和测试仍支持 >=3.10。
详见[独立仓库 Hook 环境](CONTRIBUTING_CN.md#独立仓库-hook-环境)。

应在同一个 Python 环境核对分发元数据和实际导入模块：

```bash
python -c "import importlib.metadata as m, soulacp; print(m.version('soulacp')); print(soulacp.__version__)"
```

验收本 checkout 时两者应为 `0.2.5`。若不一致，可在本机检查
`soulacp.__file__`，分享诊断前须脱敏个人路径。Editable 安装、wheel 和下游
锁文件是不同的安装来源；替换磁盘文件不会重载已经运行的 worker。

### 可选遥测支持

```bash
# PyPI 已发布包
python -m pip install "soulacp[otel]"

# 当前源码 checkout
python -m pip install -e ".[otel]"
```

## 快速开始

这些示例会启动真实且已认证的CLI，并可能以当前OS账号产生用量或工具副作用。
仅在可信测试工作区使用合成输入；打印的回复是原始内容，不是脱敏诊断。
运行前先查看[权限边界](#配置)。

### ManagedSession（推荐）

```python
import asyncio
from soulacp import ACPConfig, ManagedSession

async def main():
    config = ACPConfig(auto_approve_permissions=False)
    async with ManagedSession(provider="claude", model="claude-acp/sonnet", config=config) as session:
        # 简单查询 — session 自动管理
        response = await session.query("Hello!", user_id="user1")
        print(response)

        # 流式输出
        async for chunk in session.stream("Write hello world", user_id="user1"):
            print(chunk, end="", flush=True)

asyncio.run(main())
```

ManagedSession 自动处理：

- **Session 复用**：映射仍有效且 provider 支持恢复时，复用用户的逻辑 session
- **显式重置**：`reset_session(user_id)` 清除经调用方确认的过期/溢出 session，不隐式重放 prompt
- **重试**：仅对 prompt 发送前的安全瞬态故障指数退避
- **降级**：仅在确定 prompt 尚未处于执行状态时切换备用 provider

### 底层 API

```python
import asyncio
from soulacp import ACPConfig, ACPConnectionPool, resolve_client_class

async def main():
    config = ACPConfig(provider="claude", model="claude-acp/sonnet", auto_approve_permissions=False)
    client_class = resolve_client_class("claude")

    async with ACPConnectionPool(config, client_class) as pool:
        async with pool.acquire() as (client, session_id):
            response = await client.query("Hello!")
            print(response)

asyncio.run(main())
```

## 支持的 Agent

| Agent | Provider | 模型示例 | 基础命令 |
|-------|----------|---------|----------|
| Claude Code | `claude` | `claude-acp/sonnet` | `claude-agent-acp`（兼容旧 `claude-code-acp`） |
| Gemini | `gemini` | `gemini-acp/gemini-3-flash-preview` | `gemini --acp` |
| OpenCode | `opencode` | `opencode-acp/default` | `opencode acp` |
| OpenClaw | `openclaw` | `openclaw/default` | `openclaw acp --no-prefix-cwd` |
| Cursor (ACP) | `cursor` | `cursor-acp/default` | `cursor-agent acp` |
| Cursor (Legacy) | `cursor-cli` | `cursor-cli/gpt-4` | `cursor-agent -p` |
| Codex | `codex` | `codex-acp/default` | `codex-acp` |
| Qwen Code | `qwen` | `qwen-acp/qwen-coder` | `qwen-code --acp --experimental-skills` |
| Kimi CLI | `kimi` | `kimi-acp/default` | `kimi acp` |
| Codebuddy Code | `codebuddy` | `codebuddy-acp/default` | `codebuddy-code --acp` |
| Cline | `cline` | `cline-acp/default` | `cline --acp` |
| GitHub Copilot | `copilot` | `copilot-acp/default` | `copilot --acp` |
| Minion Code | `minion` | `minion-acp/default` | `minion-code acp` |
| Mistral Vibe | `vibe` | `vibe-acp/default` | `vibe-acp` |
| Nova | `nova` | `nova-acp/default` | `nova acp` |
| Crow CLI | `crow` | `crow-acp/default` | `crow-cli acp` |
| Amp | `amp` | `amp-acp/default` | `amp-acp` |
| Auggie | `auggie` | `auggie-acp/default` | `auggie --acp` |
| Autohand | `autohand` | `autohand-acp/default` | `autohand-acp` |
| Corust Agent | `corust` | `corust-acp/default` | `corust-agent-acp` |
| DeepAgents | `deepagents` | `deepagents-acp/default` | `deepagents-acp` |
| Factory Droid | `droid` | `droid-acp/default` | `droid exec --output-format acp-daemon` |
| fast-agent | `fastagent` | `fastagent-acp/default` | `fast-agent-acp -x` |
| Copilot LS | `copilot-ls` | `copilot-ls/default` | `copilot-language-server --acp` |
| Goose | `goose` | `goose-acp/default` | `goose acp` |
| Junie | `junie` | `junie-acp/default` | `junie --acp=true` |
| Kilo | `kilo` | `kilo-acp/default` | `kilo acp` |
| pi ACP | `pi` | `pi-acp/default` | `pi-acp` |
| Qoder | `qoder` | `qoder-acp/default` | `qodercli --acp` |
| Stakpak | `stakpak` | `stakpak-acp/default` | `stakpak acp` |

该表表示已实现的集成，不代表每个CLI、账户和模型均已通过真实认证验收。
注册表发现不等于运行资格证明。

“基础命令”列不是安装命令。Adapter 启动时可能追加模型、策略或认证参数；
可用模型取决于已安装的 CLI、账户和认证方式。
命名空间模型 ID `provider-acp/default`（以及 `provider/default`）表示由 provider
自行选择模型；SoulACP 不会把 `default` 当作真实模型名下发，也不会覆盖 provider
已有的模型配置。

也可以直接查询运行时注册表，获取所有 provider 的尽力安装提示，并检查当前
`PATH` 中可发现的 agent：

```python
from soulacp import list_agents, list_installed_agents

for agent in list_agents():
    print(f"{agent.name:12} {agent.install_cmd}")

installed = [agent.name for agent in list_installed_agents()]
print("Installed:", installed)
```

使用以下命令安装当前 Claude 与 Codex ACP bridge：

```bash
npm install -g "@agentclientprotocol/claude-agent-acp@>=0.86.0" "@agentclientprotocol/codex-acp@>=2.1.1"
```

版本范围须保留引号，避免 shell 把 `>` 当作重定向。这些范围与 SoulACP 0.2.5
的安装提示一致，不是精确锁版，也不代表所有后续 bridge/模型组合均已验收。
Bridge 可能自带 CLI/runtime，因此只升级全局 `claude` 或 `codex` 不能证明
bridge 实际使用了哪个 runtime。应核对已安装 bridge 的包元数据和其报告的
`agent_info`，不要复制凭据或整份 session 状态。

优先使用已更名的 Claude bridge；`claude-code-acp` 仅作为旧版兼容回退。
Codex bridge 已包含运行时，但下文的 `codex login` 命令还要求 Codex CLI
位于 `PATH`：

```bash
npm install -g @openai/codex
```

根据[官方迁移公告](https://github.com/google-gemini/gemini-cli/discussions/27274)，
Gemini CLI 已于 2026-06-18 停止为 Google AI Pro、Ultra 和免费个人账户提供请求。
`gemini --acp` 现在需要受支持的企业版、Google Cloud 或付费 API 认证；仅安装
CLI 并不能获得个人层访问权限。

## 使用示例

### Claude Code

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="claude", model="claude-acp/sonnet") as session:
        response = await session.query("Hello!")
        print(response)

asyncio.run(main())
```

### Gemini

**认证警示：**默认 `auth_method_id=None` 时，适配器在连接时请求`authMethods[0]`。
如需主动指定准确方法，见[显式认证选择](#显式认证选择)。Gemini CLI `0.51.0`
若从已保存的认证方式切换到另一种方式，会在刷新成功之前调用
`clearCachedCredentialFile`。请先审阅
[上游实现](https://raw.githubusercontent.com/google-gemini/gemini-cli/v0.51.0/packages/cli/src/acp/acpRpcDispatcher.ts)，
使用经明确批准的测试身份，不要假定会自动采用已有付费API登录。
拒绝权限选项不会阻止这些原生认证副作用。

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="gemini", model="gemini-acp/gemini-3-flash-preview") as session:
        response = await session.query("Hello!")
        print(response)

asyncio.run(main())
```

### Codex (OpenAI)

Codex ACP支持符合条件的ChatGPT订阅或API key认证。本机使用ChatGPT认证时，
先执行一次`codex login`，bridge随后委托其CLI复用凭据。根据`CODEX_HOME`与
`cli_auth_credentials_store`设置，凭据可能保存在文件或OS凭据存储中，
不一定是`~/.codex/auth.json`。详见
[官方认证文档](https://learn.chatgpt.com/docs/auth?surface=cli)。
ChatGPT登录不要求设置`OPENAI_API_KEY`；不要把凭据复制到issue。

OpenAI已于`2026-10-14`停止在ChatGPT登录的Codex中提供`gpt-5.5`，
API key会话不受该变更影响。本例由已安装provider选择模型，不替调用方指定替代模型。
请核对当前账户的[模型可用性](https://learn.chatgpt.com/docs/models)。

```python
import asyncio
from soulacp import ManagedSession

async def main():
    # 由已安装的provider选择模型
    async with ManagedSession(provider="codex", model="codex-acp/default") as session:
        response = await session.query("Hello!")
        print(response)

asyncio.run(main())
```

**环境变量**（全部可选）：

| 变量 | 取值 | 默认 | 作用 |
|------|------|------|------|
| `CODEX_REASONING_EFFORT` | 取决于 bridge/模型，例如 `high` | 未设置时为 `medium` | 转发推理配置，本库不校验其枚举值 |
| `CODEX_SANDBOX_MODE` | `read-only` / `workspace-write` / `danger-full-access` | （codex 默认） | 文件系统权限 |
| `CODEX_APPROVAL_POLICY` | `untrusted` / `on-request` / `never` | （Codex 默认） | 审批提示 |
| `CODEX_NETWORK_ACCESS` | `true` / `1` / `yes` | 不注入 | 在 `workspace-write` 中启用联网；其他值不覆盖 Codex 配置 |

`ACPConfig.extra_args` 中显式的 `model_reasoning_effort` 优先；否则先读取
`extra_env`，再读取允许继承的父环境。显式空字符串不会注入覆盖值。可接受档位
由已安装 bridge 和模型决定。SoulACP 不注入 Claude effort 默认值；需要时通过
`extra_env` 提供 provider 支持的控制项，示例和诊断中不得包含凭据。

以下配置会禁用审批提示、允许写工作区并启用联网。仅在可信工作区中使用：

Bash：

```bash
export CODEX_SANDBOX_MODE=workspace-write
export CODEX_APPROVAL_POLICY=never
export CODEX_NETWORK_ACCESS=true
```

PowerShell：

```powershell
$env:CODEX_SANDBOX_MODE = "workspace-write"
$env:CODEX_APPROVAL_POLICY = "never"
$env:CODEX_NETWORK_ACCESS = "true"
```

如需使用 API key 认证，应让 Codex 持久化所选认证方式，而不是只依赖环境变量。
假设已经设置 `OPENAI_API_KEY`：

```bash
# Bash
printf '%s' "$OPENAI_API_KEY" | codex login --with-api-key
```

```powershell
# PowerShell
$env:OPENAI_API_KEY | codex login --with-api-key
```

模型 ID 格式不变，但可用模型取决于账户和当前 Codex ACP bridge；应查询
[官方模型目录](https://developers.openai.com/api/docs/models)，不要依赖旧别名。

### 多 Provider 调用

本例只是顺序调用两个 provider。SoulACP 本身不派发嵌套代理、不实现业务流程，
也不为该流程提供人类授权；这些责任仍属于调用方应用。

```python
import asyncio
from soulacp import ManagedSession

async def main():
    # Claude 生成代码
    async with ManagedSession(provider="claude", model="claude-acp/sonnet") as claude:
        code = await claude.query("Write a sorting algorithm in Python")

    # Gemini 审查代码
    async with ManagedSession(provider="gemini", model="gemini-acp/gemini-3-flash-preview") as gemini:
        review = await gemini.query(f"Review this code:\n{code}")
        print(review)

asyncio.run(main())
```

## 认证与服务条款

Provider登录属于CLI所在的OS账号及profile。`user_id`只路由逻辑会话，
不会创建独立凭据身份或租户沙箱；未加密的`FileCache`也不是身份保险库。
对彼此不可信的用户，应使用独立且经审查的信任域。

原生CLI认证可能在模型prompt之前更新设置或缓存。
`auto_approve_permissions=False`与逻辑会话隔离都不会阻止这些写入；
连接可能触发`authenticate()`。运行Gemini单独示例或多provider示例之前，
应先阅读其认证警示。未显式提供权限策略的示例使用配置环境及默认值。

个人本机使用、每位用户各自登录，以及集中托管或汇聚用量的身份与服务条款要求不同。
持有key或订阅本身不表示可以共享或转售。须审查当前
[Claude Code法律说明](https://code.claude.com/docs/en/legal-and-compliance)及
[Gemini CLI认证](https://geminicli.com/docs/get-started/authentication/)。
SoulACP不是凭据收集服务，也不提供合规保证；显式提供或继承的凭据仍是敏感应用数据。

## Session 管理

### ManagedSession + ProviderSessionStore

ManagedSession 内部使用 ProviderSessionStore 映射 `(user_id, provider)` 到 CLI session ID：

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="claude", model="claude-acp/sonnet") as session:
        # 第一次请求：创建 CLI session 并保存映射
        await session.query("Remember 42.", user_id="alice")

        # 第二次请求：复用 Alice 的 CLI session
        await session.query("What number?", user_id="alice")

        # 不同用户获得不同的逻辑 session
        await session.query("Hello!", user_id="bob")

asyncio.run(main())
```

### 自定义 Session 存储

```python
import asyncio
from soulacp import ManagedSession, ProviderSessionStore, FileCache

async def main():
    store = ProviderSessionStore(cache=FileCache("~/.soulacp/sessions.json"))
    async with ManagedSession(
        provider="claude",
        model="claude-acp/sonnet",
        session_store=store,
    ) as session:
        await session.query("Remember 42.", user_id="alice")

asyncio.run(main())  # 关闭 session 时会 flush 待写入的文件缓存
```

### Session 生命周期

| 事件 | 行为 |
|------|------|
| 首次成功请求 | 创建新 CLI session，并在 pool 成功释放后保存映射（TTL 7 天） |
| 后续请求 | 映射有效且恢复成功时复用逻辑 session |
| 同一用户/session 并发请求 | 在同一个 `ManagedSession` 实例内串行执行；不同 session 仍可并发 |
| Context overflow | 向调用方抛错；仅在决定提交新 turn 后调用 `reset_session(user_id)` |
| 已确认失效的已存 session | 仅清除该 provider 映射一次，再显式创建新 session |
| Prompt 前连接/超时错误 | 保留映射，并在配置的 attempt budget 内重试同一 session |
| Prompt 传输/RPC 错误 | 不重试、不降级，因为外部副作用可能已经发生 |
| 降级 | 仅在 prompt 执行前切换 provider（如启动失败），并复用备用 provider 自己的映射 |

逻辑 session ID 不保证原 CLI 进程、子代理或内存中的工具在断连后仍存活。
无法恢复时，managed session 可能清理无效映射并创建新 session；要求执行
连续性的应用必须明确处理这一区别。

### 缓存后端

```python
from soulacp import MemoryCache, FileCache

# 内存缓存（默认）— 快速，重启后丢失
memory = MemoryCache(max_size=10000)

# 文件缓存 — 跨重启持久化
file_cache = FileCache("~/.soulacp/sessions.json", debounce_seconds=1.0)
```

文件缓存会展开 `~`，通过临时文件原子替换写盘，`ManagedSession.close()`
会 flush 尚未落盘的 debounce 变更。user ID 以固定长度的 SHA-256
伪匿名 key 片段存储；读取时仅迁移归属无歧义的旧版明文 key。数字 ID、10/32 位
小写十六进制 ID，以及分隔符归属有歧义的旧身份不会自动迁移，因为历史格式无法
证明其所有权；受影响的映射必须重新建立。同一个 `ProviderSessionStore` 会串行执行
复合 lookup、迁移、更新、清除和 flush，避免旧 key 迁移覆盖较新的 session 映射；
多个 store 或 cache 实例之间的协调仍由外部 backend 负责。`FileCache` 会在发布值前将其规范化为
标准 JSON 表示，并在读取时返回独立快照，因此调用方之后的修改不会改变持久化状态；
cache key 必须是有效 UTF-8 字符串，TTL 必须是有限运行时范围内的非负整数。
`FileCache` 是单一所有者 backend：不得由多个
`FileCache` 实例或进程同时打开同一路径。存在多个所有者的部署应提供具备并发协调
能力的外部 `CacheBackend`。存储的 session ID 未加密，因此应使用适当的操作系统
权限保护缓存文件，不要共享该文件。

### 底层 Session 控制

```python
import asyncio
from soulacp import ACPConnectionPool, ACPConfig, resolve_client_class

async def main():
    config = ACPConfig(provider="claude", model="claude-acp/sonnet")
    client_class = resolve_client_class("claude")

    async with ACPConnectionPool(config, client_class) as pool:
        # 显式 session 复用
        async with pool.acquire() as (client, sid):
            await client.query("Remember 42.")

        async with pool.acquire(session_id=sid) as (client, sid2):
            assert sid2 == sid
            response = await client.query("What number?")
            print(response)

        # 强制新传输，但仍受硬连接上限约束
        async with pool.acquire(session_id=sid, fresh=True) as (client, sid3):
            assert sid3 == sid

        stats = pool.get_stats()  # 非阻塞，不执行健康检查 I/O
        print(stats)

asyncio.run(main())
```

退出`pool.acquire()`上下文只是释放租约，可复用的传输仍可能在池内保持连接。
同一连接池内复用该会话是正常行为，不需要每次请求后调用`close_all()`。

由独立新建的client接管同一会话前，应先完成旧拥有者的工作并显式关闭其传输。
单会话集成测试可在创建接收client前调用`pool.close_all()`；接收client的
connect/resume过程应由`try`/`finally`保护，即使失败也等待`disconnect()`完成。
不要为了交接一个会话而关闭应用的整个共享连接池，也不要绕过活动拥有者拒绝。

原生 `ACPClientBase` adapter 会在 `agent_capabilities`、`agent_info`、
`auth_methods` 和 `session_config_options` 中保留 Agent 声明的能力、实现
信息、认证方式和当前 session 配置；`session_mode_state` 跟踪声明的 mode
及当前选择。稳定的 `session_info_update`、`usage_update` 与
`available_commands_update` 通知会经校验后分别写入 `session_info`、
`session_usage` 和 `available_commands`。这五类 session 状态会在
new/load/resume 迁移开始时统一清空，迁移失败时统一恢复。
所有稳定 ACP v1 session update 结构，包括 content block、tool call 和 plan，
都会在状态变更或 callback 交付前完成结构校验。`set_update_callback()` 仍按接收
顺序提供所有原始 update，包括不会被拼入响应
文本的 tool call、plan 与 thought chunk。受能力声明约束的 `list_sessions()` 与
`delete_session()` 用于管理 ACP session 历史；`set_config_option()` 可更新
Agent 声明的平铺或分组 select 及 boolean 选项，`set_mode()` 可切换已声明
的旧式 mode，以保持向后兼容。ACP 正在用 session config options 取代专用
mode 方法，因此 Agent 声明 category `mode` config option 时应优先使用它。
配置模型同样优先使用 Agent 声明的 category `model` select 选项；目标值已是
当前值时不会发送 RPC，只有 Agent 完全未声明模型选项时才把非标准
`session/set_model` 作为兼容回退。Agent 主动推送的 `config_option_update` 和 `current_mode_update`
会同步两类状态。对于 Claude，显式模型还会在 ACP 子进程启动前通过
`ANTHROPIC_MODEL` 提供模型配置，并在 resume 后再次检查模型选择。这是尽力
配置，不是严格锁模保证：最后应用的 `extra_env` 可以覆盖 `ANTHROPIC_MODEL`；
模型选项缺失、匹配不明确或 RPC 被拒绝时，可能继续使用 provider 当前选择。
传输、超时和协议错误仍会向调用方暴露。要求精确模型的调用方须核对 provider
报告的选择，不能将 `ACPConfig.model` 当成请求实际由该模型处理的确认；
报告的元数据也不是独立的模型身份认证。`authenticate()`、`logout()` 与
`close_session()` 暴露当前
ACP 生命周期方法，Agent 未声明对应能力时会拒绝调用。成功 logout 或关闭
当前 session 会使该 client 不可再入池，因此 lease release 时会退休 transport。
session load/resume 在 Agent 未声明 `loadSession` 或
`sessionCapabilities.resume` 时不会执行 transport I/O，而是报告已保存 session
无法恢复；managed session 随后只清理一次该无效映射并新建 session。独立的旧式
`CursorCLIClient` 只实现最小 `ACPClient` 协议。

模型只移除开头的 `<provider>-acp/` 或 `<provider>/` 命名空间；例如
`claude-acp/openai/gpt-5` 会保留为 `openai/gpt-5`。当 executable 对兼容用的
`session/set_model` 明确返回 JSON-RPC `-32601` 时，使用该兼容路径的 adapter
会告警一次，并按 adapter/executable 缓存结果；后续 client 跳过该请求。
跳过不受支持的 RPC 不证明请求的模型已经选中。

### 显式认证选择

`auth_method_id`仅为可选构造参数，不是密码或token。下方占位值必须替换为Agent
实际声明的准确方法ID。`None`保留各适配器原有行为，包括适用时的首项认证；
这不会消除旧默认行为对原生状态的风险。

```python
from soulacp import ACPConfig

config = ACPConfig(
    provider="gemini",
    model="gemini-acp/default",
    auth_method_id="advertised-method-id",
)
```

显式方法必须唯一匹配。选择绑定原Provider，并由pool和client保存快照。
未支持、缺失或有歧义的方法在认证前失败；旧式`cursor-cli`拒绝该配置。
所有原生ACP适配器均在创建session前应用它，不改写ACP正文。

`ACPAuthenticationError`在显式认证被拒或结果不明时阻止自动重试及Provider
fallback。原始诊断只保留在私有`cause`中。认证发送前的传输重试保留原有上限，
但显式选择不会转给其他Provider。手工`authenticate()`不能在同一显式绑定的
client上选择不同方法。

即使选定方法匹配，认证仍可能改变原生凭据状态。该选项不隔离凭据，也不授权
业务操作。真实Provider资格与合成协议测试分开；Gemini自然编辑/拒绝资格仍未
取得。没有环境变量选择该选项，也不会读取凭据文件来推断它。

## 配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ACP_PROVIDER` | `claude` | Provider 名称 |
| `ACP_MODEL` | `claude-acp/sonnet` | 模型标识 |
| `ACP_CWD` | 当前目录 | Agent 子进程工作目录 |
| `ACP_POOL_SIZE` | 5 | 最多保留的空闲连接数 |
| `ACP_POOL_MAX_CONNECTIONS` | 8 | 每个 `ACPConnectionPool` 实例 open + creating 硬上限 |
| `ACP_POOL_ACQUIRE_TIMEOUT` | 30 | FIFO 容量等待超时（秒） |
| `ACP_POOL_IDLE_TIMEOUT` | 600 | release 后连续空闲多少秒退休 |
| `ACP_POOL_KEEPALIVE_INTERVAL` | 300 | 空闲本地传输状态检查间隔；`0` 禁用 |
| `ACP_POOL_MAX_AGE` | 7200 | 租约边界上的连接软寿命 |
| `ACP_POOL_MAX_USES` | 50 | 成功租约次数软上限 |
| `ACP_POOL_CROSS_SESSION_REUSE` | false | 是否允许在其他 session 的传输上显式 resume |
| `ACP_TIMEOUT_CONNECT` | 30 | 连接超时（秒） |
| `ACP_TIMEOUT_PROMPT` | 21600 | Prompt 端到端 deadline（秒，6 小时） |
| `ACP_TIMEOUT_STREAM` | 1800 | 无任何 ACP 活动的 stream 超时（秒，30 分钟） |
| `ACP_INHERIT_PARENT_ENV` | true | Provider 与 ACP terminal 子进程是否继承完整父进程环境 |
| `ACP_AUTO_APPROVE` | true | 优先 `allow_once`；false 时优先 `reject_once` |
| `ACP_ENABLE_FALLBACK` | false | 启用 prompt 前 provider fallback |
| `ACP_MAX_RETRIES` | 3 | prompt 前总 attempt budget；`0` 仍执行一次 |
| `ACP_RETRY_BASE_DELAY` | 1 | 重试退避基数（秒） |
| `ACP_MAX_TURNS` | 未设置 | 可选的 Claude-only maxTurns 覆盖 |
| `ACP_OTEL_PROPAGATE` | 0 | 设为 `1` 时仅在子进程启动阶段注入有效 W3C `traceparent`/`tracestate`；不传递 baggage |
| `ACP_LOG_SENSITIVE_DATA` | 0 | 仅当值严格为 `1` 时，允许 debug/error 日志输出原始 provider 诊断 |

> **安全提示：** `ACP_AUTO_APPROVE=true` 会在 agent 请求权限时自动选择允许项
> （优先 `allow_once`）。这不是操作系统沙箱。对不可信工作区应设为 `false`，
> 并审查 provider CLI 自身的权限模型。

活动prompt回合之外的权限请求返回`cancelled`，包括首轮开始前、正常结束后，
以及所属回合已结束或取消的排队请求。重复选项ID在选择前拒绝。
已经开始发送的回复不能撤回。ACP v1权限请求不带prompt ID，因此在新回合中
才首次到达的迟到请求不一定能归属到原回合。活动回合的allow/reject策略不变；
这不是人类业务授权门。

`auto_approve_permissions=False`只为经由
`session/request_permission`的请求选择拒绝选项，不是对`fs/*`或`terminal/*`
宿主服务的全局禁止开关。这些服务执行自身的配置根、schema和资源检查，不提供
针对敏感配置文件的逐路径人类授权清单。需要更强限制的应用必须通过自己的
host/provider策略和OS隔离落实。

`pool_size` 不是并发上限。物理容量由每个 pool 实例自己的
`pool_max_connections` 约束。每个 `ManagedSession` 实例按 `provider:model` key
各建一个 pool，因此单个实例使用 N 个 key 时最多可保留 N 倍的单池上限；
多个 `ManagedSession` 实例还会继续叠加。当前没有进程级总限流器。在
`pool_cross_session_reuse=False` 时，匿名请求绝不会取得已绑定
其他逻辑 session 的空闲 client。隔离优先，因此连接池主要承担
session-affinity cache 的作用。同一个指定 session ID 同一时刻只允许一个有效 lease。
`ACPConnectionPool` 会在构造时重新校验并保存 `ACPConfig` 快照，每个物理 client
还会获得独立快照。因此，调用方或 client 后续修改配置都不能改变 pool 已校验的容量与
生命周期策略；需要改变策略时应创建新的 pool。

由 `ACPConfig.from_env()` 解析的布尔配置变量接受 `true/false`、`1/0`、
`yes/no` 或 `on/off`；非法值会
抛出 `ValueError`，不会静默当成 False。OpenClaw 默认不传 `--session`，
每个 ACP bridge 因此使用隔离 session key；设置 `OPENCLAW_SESSION_KEY`
表示调用方明确选择 OpenClaw 的共享/挂接 session 路由。
配置错误只报告字段和约束，不回显原始输入；非法数值环境变量会抑制原始解析异常链。
会话控制拒绝消息省略用户提供的ID和值；URL解析异常链及严格JSON重复键错误不暴露输入文本。
包括`capability`在内的原始结构化异常字段仍须按敏感信息处理。

### Provider 专用环境变量

| 变量 | Adapter | 用途 |
|------|---------|------|
| `MOONSHOT_API_KEY` | Kimi | API key 认证 |
| `MISTRAL_API_KEY` | Vibe | API key 认证 |
| `OPENCLAW_URL` | OpenClaw | Gateway URL |
| `OPENCLAW_TOKEN_FILE` | OpenClaw | 通过受保护文件覆盖 Token 认证 |
| `OPENCLAW_PASSWORD_FILE` | OpenClaw | 未配置 token file 时，通过受保护文件覆盖密码认证 |
| `OPENCLAW_SESSION_KEY` | OpenClaw | 显式挂接/共享 Gateway session；未设置时保持隔离的 `acp-bridge:<uuid>` 路由 |
| `OPENCLAW_VERBOSE` | OpenClaw | 使用 `true`、`1` 或 `yes` 启用 bridge 详细日志 |

`OPENCLAW_URL` 必须是没有内嵌凭据的绝对 `ws://` 或 `wss://` URL。明文
`OPENCLAW_TOKEN` 与 `OPENCLAW_PASSWORD` 启动覆盖会被拒绝：OpenClaw 只能通过
命令行接收这些覆盖值，本地进程查看工具可能读取它们。应改用已配置的 Gateway
认证或受保护的凭据文件。

### 代码配置

```python
from soulacp import ACPConfig

config = ACPConfig(
    provider="claude",
    model="claude-acp/sonnet",
    pool_size=5,
    pool_max_connections=8,
    pool_acquire_timeout=30,
    pool_idle_timeout=600,
    pool_keepalive_interval=300,
    pool_max_age=7200,
    pool_max_uses=50,
    pool_cross_session_reuse=False,
    timeout_connect=30,
    timeout_prompt=21600,
    timeout_stream=1800,
    inherit_parent_env=True,
    auto_approve_permissions=True,
    enable_fallback=False,
    additional_directories=["/absolute/path/to/shared"],
    mcp_servers=[
        {
            "name": "local-tools",
            "command": "/absolute/path/to/mcp-server",
            "args": [],
            "env": [],
        }
    ],
)
```

`/absolute/path/to/shared`和`/absolute/path/to/mcp-server`是占位路径，
不是已安装资源。请替换成运行CLI的机器上的真实绝对路径；Windows应使用原生
带盘符绝对路径和可直接执行的MCP命令，而不是shell表达式。

`extra_args` 会直接追加到子进程 argv，不经过 shell 展开，本地进程查看工具可见，
因此绝不能放入凭据。`extra_env` 会合并到子进程环境并覆盖同名 key；两者都应视为
可信配置，不要无差别记录到日志。
`ACPConfig` 的字符串表示会省略工作目录、additional root、MCP 定义、额外参数、
环境覆盖和`auth_method_id`，降低它们被意外写入日志的风险；这些字段仍可直接访问，必须继续按敏感配置处理。
为兼容 CLI 认证，provider 与 ACP terminal
子进程默认继承父进程环境。设置 `inherit_parent_env=False`（或
`ACP_INHERIT_PARENT_ENV=false`）后，只保留有限的运行时、路径与 locale allowlist；
所需凭据必须通过 `extra_env` 或 provider 自身的凭据存储显式提供。该选项只限制
环境变量传播，不是操作系统沙箱。
`additional_directories` 与 `mcp_servers` 是仅支持
构造参数的结构化字段，有意不提供环境变量字符串解析。root 与 stdio command
必须是绝对路径；HTTP/SSE MCP URL 必须是无内嵌凭据的绝对 HTTP(S) URL，认证
应使用经过校验的 header。MCP metadata 必须直接使用 object key 为字符串的严格
JSON 类型；tuple 与非字符串 key 会被拒绝，不会再被静默转换。additional root 和
HTTP/SSE MCP server 只有在 Agent 声明
对应能力后才发送，否则在发出不受支持的 session 请求前直接失败。

### 从环境变量读取

`ACPConfig()`使用构造器默认值及显式参数，不自动读取`ACP_*`。
`ACPConfig.from_env()`读取进程环境，显式关键字参数优先。SoulACP不会加载
`.env`文件；调用应用或shell须在构造配置前加载所需值。不要为排查设置而打印
整个环境。

```python
import asyncio
from soulacp import ACPConfig, ManagedSession

async def main():
    config = ACPConfig.from_env()
    async with ManagedSession(
        provider=config.provider,
        model=config.model,
        config=config,
    ) as session:
        print(await session.query("Hello!"))

asyncio.run(main())
```

`ManagedSession` 要求显式传入 `provider` 和 `model`。未传 `config` 时，
其余设置由 `ACPConfig.from_env()` 读取，但构造参数会覆盖 `ACP_PROVIDER` 和
`ACP_MODEL`。显式传入 config 时，其余字段不会再叠加隐藏的环境变量，但构造
参数仍会替换 `config.provider` 和 `config.model`。上面的写法会明确采用环境变量
选定的 provider 和 model。

Prompt 或 stream deadline 失败会关闭底层传输，且不会自动重试或转交 fallback
provider，避免结果未知或已有部分输出的请求被重复执行。
对于流式 API，调用方处理一个已经 yield 的分片所花时间，不会再计入生成器侧的
prompt deadline；但仍在进行中的原生 ACP `session/prompt` RPC 会保留原始端到端
deadline。
单个出站 RPC 被取消或超时时，soulacp 会先尽力发送标准
`$/cancel_request` notification。收到 Agent 的 `$/cancel_request` 后，只会在
匹配的 host request 尚未开始回复时取消，并返回 JSON-RPC `-32800`；prompt
超时仍会退休 transport，防止迟到响应进入后续 lease。

原生 ACP disconnect 还会尽力清理后代进程：POSIX 使用 CLI 的独立进程组，
Windows 使用后代 PID 快照及父链检查。脱离进程组的进程、快照竞态和权限失败
不属于整棵进程树必定清理成功的保证。取消通知或清理日志不是所有后代已退出的
证明；空的Windows快照，尤其在CLI已退出之后，也不能证明已没有工具进程存活。
需要该保证时应使用OS job/container隔离。这没有把TerminalService或旧版Cursor
扩展成整棵进程树终止API。

原生 client 的 connect/disconnect 操作会串行执行。每个 client 的 prompt/stream 与
session load/resume 也会串行执行，避免旧 prompt 完成时覆盖刚恢复的新 session。
并发调用不会发布重复子进程，disconnect 会等待进行中的 connect，且自有清理完成后
才向调用方传播取消。Pool lease 的释放和退休同样遵循该所有权规则；即使取消发生在
清理等待 pool lock 期间，也不会让物理容量永久滞留在 leased 状态。
有界且可由调用方取消的等待也会保留 Python 3.10 上的调用方取消，即使被等待操作在
同一 event-loop 轮次内完成。
旧版 Cursor 查询的清理同样持有子进程回收所有权，即使调用方重复取消也会完成；直接
子进程退出后的输出管道排空有明确上限，已退出 subprocess 的 transport 也会显式关闭。
terminate/kill 失败时绝不会释放 pool 的物理容量：client 或 terminal 记录继续由
库持有，`close()` 会报告失败，后续再次 `close()` 可重试。`ManagedSession` 只保留
关闭失败的 pool 或 store flush，不会重复关闭已成功的部分。原生 asyncio transport
和 OpenCode 在启动超时后也会认领迟到的子进程：能够回收则立即回收，失败则保留句柄
供后续清理重试。RPC 等待也会观察直接子进程退出，同时保留有界窗口接收缓冲尾帧，
后代继承的 stdout 管道不会再把请求拖到完整配置 deadline。OpenCode reader thread
的 callback 会绑定到调度它的 dispatch queue，因此已断开 transport 的迟到 callback
无法进入新 transport generation。
空闲健康检查只检查本地进程与 pipe 状态，不会向按行分隔的 JSON-RPC 传输注入探针字节。

ACP v1 初始化必须协商到协议版本 `1`，每个广告的认证方法都必须包含 agent
给出的非空 `id`；soulacp 不会臆造认证 ID。已知的 capability 字段和所有稳定 v1 RPC response
都会在提交本地 session、mode 或 config 状态前完成校验；response 及已知嵌套结构中的
`_meta` 只能是 object 或 null。受支持的 host request 遵循同一 metadata 契约，非法
metadata 返回 `-32602`。ACP v1 permission request 必须包含合法 `toolCall`，每个
option 也必须有非空 ID、名称和标准 kind，soulacp 才会选择；只有当全部 option 均省略
`kind` 时，才保留显式的旧版 ID-only 兼容路径。ACP stdout与旧版Cursor JSON stdout
均采用严格UTF-8解码。服务端 request ID 接受协议规定的 null、int64 和 string，
response 只与 soulacp 发出的 int64 整数 ID 关联。所有外部 JSON 输入还会
拒绝重复 object key，避免字段在解码时被静默覆盖；入站和出站 JSON 都会拒绝非法 Unicode
及非有限数值，不输出实现私有 JSON；OpenCode 继承的
`OPENCODE_CONFIG_CONTENT` 同样必须是严格 JSON object。只有对应
`session/prompt` 响应中的
标准 `stopReason`（`end_turn`、`max_tokens`、`max_turn_requests`、`refusal`
或 `cancelled`）才能结束 turn；message-end 类扩展 update 不会结束 turn。
收到的 session update 和所有受支持的 host request 必须属于当前 session；旧 session
的跨会话消息会 fail closed。Prompt 写入后发生传输中断时抛出
`ACPRequestOutcomeUnknownError`；完成响应不合法时抛出 `ACPProtocolError`。
任何 RPC 在写入后被中断，都可能已经到达 Agent；因此客户端会尽力发送
`$/cancel_request` 后淘汰该 transport，不会把可能已发生 session 状态分叉的
连接重新放回池中。

ACP 文件系统请求遵循 v1 的绝对 `path`；schema 边界 `line=0` 映射到第一行，
`limit=0` 返回零行。未提供`limit`时读取至EOF，但所选文本必须满足未改变的
10,000字符和10 MiB文件上限，否则明确失败。成功读取不会插入截断标记，
也不会返回所请求行的片段。显式行数上限仍为10,000。文件不存在返回ACP资源缺失
（`-32002`）；越根和其它宿主失败不会被改称为文件不存在。合法资源失败使用实现码
`10001`（文件预算）、`10002`（文本编码）、`10003`（文本资源）和`10004`
（宿主请求并发）；非法参数仍为`-32602`。终端句柄不存在也使用`-32002`；
这些实现码不是ACP标准码。读取取消会等待所属
工作线程完成。取消与读/写工作线程失败同时发生时，原始工作线程异常保留在取消异常
显式的`cause`字段中；默认展示链只包含脱敏摘要。Python 3.10可能在外层asyncio Task
边界包装取消异常，原取消异常仍保留在外层异常的context中。

自0.2.3起，UTF-8严格解码，读写保留原始LF、CRLF、CR和BOM，不替换坏字节，
也不自动转换平台换行。CRLF计为两个Python字符：3334个`a\r\n`现在超过
10000字符预算，不再归一化为6668字符。非法UTF-8读取拒绝且原始诊断保持私有；
显式合法写入不预读旧正文。零行请求不解码正文，但仍校验路径、类型和文件预算。
缓冲预读可能触及所选逻辑行之后的非法字节；未读尾部不声称已验证。Gemini等整文件
预读消费者仍可能拒绝超额编辑；其ACP SDK错误归类也可能阻止新建文件，不能只凭
正确的缺失码证明消费者行为。没有自动原生工具fallback。

写入只接受普通文件，截断前核验已打开的描述符，并完成短写。写入前校验拒绝不改动
已有目标；开始真实写入后的I/O失败可能留下部分内容。不是原子替换，也不承诺崩溃
持久性。断开时先停止CLI，再等待所属文件操作；不能中断永久阻塞的内核I/O，
也不保证全部后代退出。目录/元数据查询不会改成写入式的不可中断取消等待。
请求只能位于 `cwd` 或校验后的
`additional_directories`，并限制文件、文本、写入和目录条目大小。读取仅接受普通文件，
且针对已打开的数据流执行字节上限，即使文件在路径校验后增长也不会绕过。路径约束仍是
文件系统拓扑稳定时的 best-effort 机制，不提供抗 symlink 竞态保护，也不是操作系统级
沙箱。Terminal cwd 使用同一组配置根目录；请求采用无 shell 的 argv 执行、有界输出
（包括 `outputByteLimit=null` 或 `0`）、
标准 `truncated` 结果字段，并将每个 client service 中未 release 的 terminal
记录（包括取消后仍可能迟到的 spawn）和并发 terminal wait host request 都限制为
32 个。已取消的 terminal create 会持续占用容量，直到迟到子进程被回收。SoulACP
只拥有直接子进程；terminal wait 以该直接子进程退出为准，只提供有界的输出排空窗口。
需要保证后代进程清理时，应使用 OS job/container 隔离。

### 连接池统计与 OpenTelemetry

`get_stats()` 保留旧字段，并增加 closed/idle/active/checking/closing 状态、
creating reservation、unreaped transport、waiter、claimed session 数、容量、
创建/断开失败、清理重试、session 命中、resume 结果、keepalive loop 失败、
淘汰原因、生命周期峰值，以及包含实际 startup attempt budget 的策略快照。
指标只使用低基数 attributes；
session ID、prompt、user ID、路径和 PID 都不会进入 metric attributes。内置 provider
保留规范名称；未知或自定义 provider 在 metric 与 span 中统一折叠为 `custom`，
未识别的 pool 维度值统一折叠为 `other`。

soulacp 只使用 OpenTelemetry API。应用负责配置 SDK、reader、exporter 和
后端；未安装可选 API 包时，全部连接池 instrumentation 自动 no-op。Instrument
在进程内共享；metric 和 RPC span 均不写入 session ID、prompt、错误文本、
user ID、路径或 PID。遥测后端故障会被隔离，不能替换业务结果或异常。

RPC span清理不接收原始异常和堆栈；失败仅保留有界错误/取消分类与错误状态，
不写异常事件或状态原文。开发测试安装SDK以检查真实内存导出，运行库仍只使用可选API。

### 隐私边界

SoulACP 默认会在日志与公开异常字符串中脱敏 provider message、RPC data、session ID、
stderr、user ID 和 cache path。`RPCError`、`ACPProviderError`与`ACPProtocolError`仍保留原始结构化字段
供程序诊断；只有显式调用 `format_diagnostic(include_sensitive=True)` 才会渲染原文。
设置 `ACP_LOG_SENSITIVE_DATA=1` 还会允许库日志输出原始 provider 诊断，只应在短时、
访问受控的调试中使用。
协议错误公开安全原因，将provider原值保存在`details`中。
文件缓存持久化错误保留OSError类别与errno，公开filename脱敏；`cause`保留原始I/O错误，
仅供受保护的显式诊断，不应直接打印。
响应包装异常以`cause`保留原始异常；fallback还以`primary_cause`保留先前primary异常。
默认`__cause__`显示链改为安全摘要，不再是原对象；需要诊断身份时必须使用上述显式原始字段。
完整Python堆栈仍可能包含源文件位置及调用方追加的notes，必须先在私下复核。
部分输出是原始响应内容，不是已脱敏诊断字段，须按私人应用数据保护。
Claude模型选择值默认不写日志；敏感日志显式启用时对其转义，换行不会另起日志行。

分享 issue、trace 或测试报告前，必须移除凭据、认证 header、cookie、prompt/response、
session ID、个人路径和身份信息。不要附上 `.env`、provider 认证文件、session
缓存、原始配置/状态对象或 `format_diagnostic(include_sensitive=True)` 的输出。
应使用合成复现数据和经审阅的最小必要诊断。默认脱敏不保证第三方 CLI 日志或
调用方直接打印原始字段也安全。`ACP_AUTO_APPROVE` 只控制 ACP 权限选项，
不代表人类已经批准应用中的业务决策。

`ProviderSessionStore` 默认仅使用内存。显式配置 `FileCache` 后，provider session ID
会以未加密形式落盘，user key 使用确定性的伪匿名 hash；必须依靠操作系统权限保护，
且不得跨信任域共享。仅有归属无歧义的旧版明文 cache key 会在访问时迁移；歧义映射
会失效关闭，必须重新建立。

旧版Cursor在自动批准开启且未显式提供等价flag时还会追加`--force`。
这一既有行为不变；应按需要显式配置拒绝或使用原生ACP适配器。
最小环境设置不会隔离当前账户保存在磁盘上的CLI登录。

[认证与服务条款边界](#认证与服务条款)同样适用于缓存的CLI身份。
诊断经过脱敏，不证明原生认证操作未改变凭据文件。

旧版 Cursor CLI prompt 已通过 stdin 发送，不再进入 argv；但 Cursor 文档化的 resume
接口仍要求 session ID 出现在 `--resume` 中，显式 `OPENCLAW_SESSION_KEY` 同样属于
命令行元数据。对进程列表隐私要求较高时应优先使用原生 Cursor ACP adapter。
SoulACP agent 子进程以当前 OS 账户运行，并默认继承父进程环境；最小环境模式不会改变
该 OS 信任边界。本库不是凭据沙箱。

### 错误处理

下列异常均可从`soulacp`公开导入；捕获异常本身不能证明外部副作用是否发生：

| 异常 | 调用方责任 |
|------|------------|
| `ACPAuthenticationError` | 显式认证失败或结果不明，不自动重试、改选方法或切换 provider；原始`cause`保持私有。失败不证明凭据状态未改变。 |
| `ACPRequestOutcomeUnknownError` | 执行可能已开始，不自动重放；先核实外部副作用。 |
| `ACPPartialResponseError` | `partial_output`是未完成的原始内容，不是成功答案或可公开诊断。 |
| `ACPProtocolError` | 将不合法的完成响应视为失败，原始`details`保持私有。 |
| `ACPPromptTimeoutError` / `ACPStreamTimeoutError` | 传输会退休，不自动重试或切换provider。 |
| `RPCError` | `is_retryable`只分类控制面错误，不授权重放结果未知的prompt。 |
| `ACPResumeError` | 明确处理会话无法恢复的情况；新的逻辑会话不证明原child连续性。 |

## 架构

```text
ManagedSession（高级 API）
  ├── ProviderSessionStore（user→session 映射）
  │   └── CacheBackend（MemoryCache / FileCache）
  └── ACPConnectionPool（有界复用 + FIFO 背压 + 生命周期）
      └── ACPClient（client协议）
          ├── ACPClientBase（原生JSON-RPC over stdio子进程）
          │   ├── 29 个 ACP adapter 实现（见“支持的 Agent”）
          │   └── Services（由物理原生client拥有）
          │       ├── FSService（文件系统操作）
          │       └── TerminalService（无shell参数化执行、有界输出、kill/release）
          └── CursorCLIClient（独立的旧版非ACP集成）
```

## 测试

```bash
# 默认测试：严格告警及语句/分支覆盖率
# 未显式启用时跳过 provider CLI 和模型调用
python -m pytest tests/ -W error -v --cov=soulacp --cov-branch --cov-report=term

# 离线交接回归，不调用已认证的 CLI
python -m pytest tests/test_resume_test_lifecycle.py -W error -v

# 集成测试（需要安装并认证对应 CLI）
python -m pytest --run-integration tests/test_integration.py -W error -v          # Claude Code
python -m pytest --run-integration tests/test_integration_gemini.py -W error -v   # Gemini
python -m pytest --run-integration tests/test_integration_codex.py -W error -v    # Codex

# 单元 + 集成 + stdio 压测
python -m pytest --run-integration tests/ -W error -v

# CI 使用的静态检查
python -m ruff check src/soulacp/ tests/ examples/ tools/
python -m ruff format --check src/soulacp/ tests/ examples/ tools/
python -m pyright
# Bash；PowerShell 请先设置 `$env:PYTHONPATH = "src"`。
PYTHONPATH=src python -m pyright --verifytypes soulacp --ignoreexternal
python -m bandit -q -r src/soulacp

# 在全新且不存在的目录构建，不与历史发布混用
python tools/build_release.py --outdir dist/0.2.5
python -m twine check dist/0.2.5/soulacp-0.2.5-py3-none-any.whl dist/0.2.5/soulacp-0.2.5.tar.gz
python tools/check_sdist.py dist/0.2.5/soulacp-0.2.5.tar.gz
```

仓库hook使用独立Python >=3.11的[hook环境](CONTRIBUTING_CN.md#独立仓库-hook-环境)。
`make check`执行lint、类型和安全检查，不运行测试、hook、审计或构建。
受控构建要求输出路径尚不存在，不会删除旧产物；后续构建须另选新路径。

CI徽标链接到GitHub Actions，不是本checkout或本地部署的验收回执。每次验收须记录
准确源码/产物身份、平台、实际通过、失败和skip。本地CI复现、远端GitHub CI、
已安装wheel测试及认证provider测试是分别记账的结果。

`test_integration*`、`test_spike_*` 和 `test_stress_*` 文件需要显式传入
`--run-integration`；默认测试会有意跳过它们。

`tests/test_resume_test_lifecycle.py`覆盖十项离线用例：Claude和Codex各包含
成功、会话不可恢复、协议失败、连接失败及首次prompt失败。它核对创建接收
client前已关闭旧拥有者、接收client得到清理、错误未被转换为成功。
这些是合成回归，不是已认证provider的真实调用。

仅要求布尔返回的恢复断言，只能证明其结构测试合同。应记录`resume()`实际返回
`True`还是`False`；若要宣称完整历史保留，还须增加恢复后的上下文断言。
原生`allow_once`/`reject_once`证据、Host函数授权和完整业务输出质量是不同资格。
Claude某次验收成功，不能关闭仍未解决的Codex失败。

2026-11-02，对已发布`0.2.4`运行库采用修正测试生命周期的有限隔离验收中，
Claude SDK七项及SoulBot Host四项通过（11/11）。独立Codex场通过SDK七项及
Host两项，一项Host流式精确输出断言失败，另两项Host工具场未启动。
这些是限定范围的结果，不是全部provider、模型、认证路径或完整业务流程的资格。
发布后的测试/文档修正不改变已发布`0.2.4`归档。

2026-11-02，一份冻结的`0.2.5`候选完成Windows和Ubuntu上常规CPython
`3.10`至`3.15`的本地验收，含最低开发依赖栈和源码包解包，每平台八组。
Windows每组1305通过、91项既有条件跳过；Ubuntu每组1299通过、97项既有条件
跳过，均零失败、零错误。跳过仍是未执行的用例，不是通过；重复矩阵组不计为不同用例。

该候选另有选定的Claude SDK/Host场11/11通过、Codex场12/12通过。Codex工具
拒绝证据覆盖Host边界，不是原生ACP权限请求。后续流式通过不能证明早先`0.2.4`
失败已被运行库修复，旧记录保持不变。这些是某一冻结源码/产物组合的本地回执，
不是GitHub托管CI结果、全部Provider资格或发布授权；后续改动须单独核验。
原始诊断保持私有，只分享经审阅、脱敏的计数、类别和摘要。

源码测试通过 `tests/conftest.py` 优先导入本 checkout 的 `src`，不能单独证明
已安装 wheel 正确。分发 SHA、冷安装和隐私核查见
[发布身份清单](CONTRIBUTING_CN.md#发布身份与隐私)。不要覆盖已有输出目录，
也不要无差别上传 `dist/*`。

## 项目文档

| 文档 | 用途 |
|------|------|
| [CHANGELOG.md](CHANGELOG.md) | 发布历史与行为变更 |
| [SECURITY.md](SECURITY.md) | 安全政策与漏洞报告 |
| [CONTRIBUTING.md](CONTRIBUTING.md) / [CONTRIBUTING_CN.md](CONTRIBUTING_CN.md) | 贡献流程 |
| [GOVERNANCE.md](GOVERNANCE.md) | 项目治理与决策 |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | 社区行为规范 |

## AIXP Labs [aixp.dev](https://aixp.dev)

AIXP Labs 开发并维护以下核心项目：

| 项目 | 描述 | 网站 |
|------|------|------|
| [HSAW](https://hsaw.dev) | 人类主权与福祉 —— Axiom 0 白皮书（基石） | hsaw.dev |
| [AIZP](https://aizp.dev) | AI Zenith-Zero Protocol —— 运行时行为对齐 | aizp.dev |
| [AIIP](https://aiip.dev) | AI Intent Protocol（AI 意图协议）—— 意图树治理、优先级与反馈 | aiip.dev |
| [AIKP](https://aikp.dev) | AI Knowledge Protocol（AI 知识协议）—— 长期知识组织、导航与治理 | aikp.dev |
| [AIJP](https://aijp.dev) | AI Job Protocol（AI 作业协议）—— 可调度的工作树与作业图 | aijp.dev |
| [AINP](https://ainp.dev) | AI Neogenesis Protocol（AI 创生协议）—— 可治理的生成项目：计划 + 内容 + 证据 | ainp.dev |
| [AILP](https://ailp.dev) | AI List Protocol —— agent 发现与能力广告 | ailp.dev |
| [AIVP](https://aivp.dev) | AI Value Protocol —— 国际商务、加密资产结算 | aivp.dev |
| [AIRP](https://airp.dev) | AI RMB Protocol —— 中国大陆商务、人民币持牌结算 | airp.dev |
| [AIBP](https://aibp.dev) | AI Bot Protocol —— 社交通信与信任 | aibp.dev |
| [AIAP](https://aiap.dev) | AI Application Protocol —— 治理与合规 | aiap.dev |
| [AISP](https://aisp.dev) | AI Skill Protocol —— 目录式技能包，以 AISOP 技能入口和可执行契约绑定为核心 | aisp.dev |
| [AISOP](https://aisop.dev) | AI Standard Operating Protocol —— 流程程序定义 | aisop.dev |
| [AIXPStore](https://aixpstore.dev) | AIAP 程序与 AISP 技能官方市场 —— 发现、发布和分发 AI 能力包 | aixpstore.dev |
| [SoulSkill](https://soulskill.dev) | AISP 技能参考库 & 多 CLI 插件分发 | soulskill.dev |
| [SoulAgent](https://soulagent.dev) | 任何 CLI / SDK / IDE 直接调用的 drop-in AI agent | soulagent.dev |
| [SoulBot](https://soulbot.dev) | AI agent 运行时 & 自编排框架（定时、建 agent、agent 间通信） | soulbot.dev |
| [SoulRun](https://soulrun.dev) | 一个调用的 AISOP 执行引擎 —— 为任何宿主运行 AIAP 程序与 AISP 技能 | soulrun.dev |
| [SoulACP](https://soulacp.dev) | 适配库 —— 桥接 CLI 工具与 LLM 提供方 **（本项目）** | soulacp.dev |

---

## ⚠️ 免责声明

本软件为**实验性**软件，仅供**研究和教育用途**。不适用于生产环境。使用风险由用户自行承担。作者对因使用本软件造成的任何损害不承担责任。完整条款见 [LICENSE](LICENSE)（Apache 2.0）。

---

## 许可证

[Apache License 2.0](LICENSE) - Copyright 2026 AIXP Labs AIXP.dev | SoulACP.dev

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.2.5. www.soulacp.dev

<a id="values-philosophy"></a>

## ⚓ 价值观与哲学 (Alignment & Philosophy)

### Axiom 0: [HSAW | Human Sovereignty and Wellbeing](https://hsaw.dev)

- **No HITL, HSAW.**
  *人类主权与福祉是第零公理，无需虚伪的人机协同。*
- **No w.a.s.h, Real h.s.a.w.**
- **人非蝼蚁，人为道。**
- **We are not beggars. We the People.**
