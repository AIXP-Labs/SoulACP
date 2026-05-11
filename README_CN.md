# soulacp

[![CI](https://github.com/AIXP-Labs/SoulACP/actions/workflows/ci.yml/badge.svg)](https://github.com/AIXP-Labs/SoulACP/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/soulacp)](https://pypi.org/project/soulacp/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

Python ACP 客户端库 — 连接 AI 编程助手，零 session 开销。

## 特性

- **29 个 CLI 适配器**：Claude Code、Gemini、OpenCode、OpenClaw、Cursor、Codex、Qwen、Kimi、Codebuddy、Cline、Copilot、Minion、Vibe、Nova、Crow、Amp、Auggie、Autohand、Corust、DeepAgents、Factory Droid、fast-agent、Copilot LS、Goose、Junie、Kilo、pi ACP、Qoder、Stakpak
- **无自己的 session 层**：直接管理 CLI 的原生 session
- **零 token 开销**：无中间件，无重复 session 历史
- **ManagedSession**：高级 API，自动 session 复用、旋转、重试、降级
- **连接池**：复用空闲连接，session 匹配
- **Session 存储**：持久化 user→session 映射，支持 TTL（内存或文件缓存）
- **异步流式输出**：基于 asyncio 的实时响应
- **自动重试**：指数退避 + 随机抖动
- **宿主服务**：内置文件系统和终端服务，处理 CLI 工具调用
- **纯标准库**：零外部依赖

## 安装

```bash
pip install soulacp
```

## 快速开始

### ManagedSession（推荐）

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="claude", model="claude-sonnet-4-20250514") as session:
        # 简单查询 — session 自动管理
        response = await session.query("你好！", user_id="user1")
        print(response)

        # 流式输出
        async for chunk in session.stream("用 Python 写个 hello world", user_id="user1"):
            print(chunk, end="", flush=True)

asyncio.run(main())
```

ManagedSession 自动处理：
- **Session 复用**：相同 user_id 跨请求复用同一 CLI session
- **Session 旋转**："prompt too long" 时自动切换到新 session
- **重试**：连接错误时指数退避重试
- **降级**：主 provider 持续失败时切换到备用 provider

### 底层 API

```python
import asyncio
from soulacp import ACPConfig, ACPConnectionPool, resolve_client_class

async def main():
    config = ACPConfig(provider="claude", model="claude-sonnet-4-20250514")
    client_class = resolve_client_class("claude")
    pool = ACPConnectionPool(config, client_class)

    async with pool.acquire() as (client, session_id):
        response = await client.query("你好！")
        print(response)

    await pool.close_all()

asyncio.run(main())
```

## 支持的 Agent

| Agent | Provider | 模型示例 | CLI 命令 |
|-------|----------|---------|----------|
| Claude Code | `claude` | `claude-sonnet-4-20250514` | `claude` 或 `claude-code-acp` |
| Gemini | `gemini` | `gemini-3-flash-preview` | `gemini --acp` |
| OpenCode | `opencode` | — | `opencode acp` |
| OpenClaw | `openclaw` | — | `openclaw acp` |
| Cursor (ACP) | `cursor` | `cursor-acp/default` | `cursor-agent acp` |
| Cursor (Legacy) | `cursor-cli` | `cursor-cli/gpt-4` | `cursor-agent -p` |
| Codex | `codex` | `codex-acp/gpt-5` | `codex-acp` |
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
| Factory Droid | `droid` | `droid-acp/default` | `droid exec --output-format acp` |
| fast-agent | `fastagent` | `fastagent-acp/default` | `fast-agent-acp -x` |
| Copilot LS | `copilot-ls` | `copilot-ls-acp/default` | `copilot-language-server --acp` |
| Goose | `goose` | `goose-acp/default` | `goose acp` |
| Junie | `junie` | `junie-acp/default` | `junie --acp=true` |
| Kilo | `kilo` | `kilo-acp/default` | `kilo acp` |
| pi ACP | `pi` | `pi-acp/default` | `pi-acp` |
| Qoder | `qoder` | `qoder-acp/default` | `qodercli --acp` |
| Stakpak | `stakpak` | `stakpak-acp/default` | `stakpak acp` |

## 使用示例

### Claude Code

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="claude", model="claude-sonnet-4-20250514") as session:
        response = await session.query("你好！")
        print(response)

asyncio.run(main())
```

### Gemini

```python
import asyncio
from soulacp import ManagedSession

async def main():
    async with ManagedSession(provider="gemini", model="gemini-3-flash-preview") as session:
        response = await session.query("你好！")
        print(response)

asyncio.run(main())
```

### 多 Agent 协作

```python
import asyncio
from soulacp import ManagedSession

async def main():
    # Claude 生成代码
    async with ManagedSession(provider="claude", model="claude-sonnet-4-20250514") as claude:
        code = await claude.query("用 Python 写一个排序算法")

    # Gemini 审查代码
    async with ManagedSession(provider="gemini", model="gemini-3-flash-preview") as gemini:
        review = await gemini.query(f"审查这段代码:\n{code}")

asyncio.run(main())
```

## Session 管理

### ManagedSession + ProviderSessionStore

ManagedSession 内部使用 ProviderSessionStore 映射 `(user_id, provider)` 到 CLI session ID：

```python
from soulacp import ManagedSession

session = ManagedSession(provider="claude", model="claude-sonnet-4-20250514")

# 第一次请求 — 创建新 CLI session，保存映射
await session.query("记住 42。", user_id="alice")

# 第二次请求 — 复用同一 CLI session（alice→session_abc）
await session.query("什么数字？", user_id="alice")

# 不同用户 — 获得不同 CLI session
await session.query("你好！", user_id="bob")
```

### 自定义 Session 存储

```python
from soulacp import ManagedSession, ProviderSessionStore, FileCache

# 持久化文件存储
store = ProviderSessionStore(cache=FileCache("~/.soulacp/sessions.json"))
session = ManagedSession(provider="claude", model="claude-sonnet-4-20250514", session_store=store)
```

### Session 生命周期

| 事件 | 行为 |
|------|------|
| 首次请求 | 创建新 CLI session，保存映射（TTL 7 天） |
| 后续请求 | 相同 user_id → 相同 CLI session（复用） |
| "Prompt too long" | 自动旋转到新 session，清除旧映射 |
| 连接错误 | 清除映射，用新 session 重试 |
| 降级 | 切换到备用 provider（如 claude→gemini） |

### 缓存后端

```python
from soulacp import MemoryCache, FileCache

# 内存缓存（默认）— 快速，重启后丢失
memory = MemoryCache(max_size=10000)

# 文件缓存 — 跨重启持久化
file_cache = FileCache("~/.soulacp/sessions.json", debounce_seconds=1.0)
```

### 底层 Session 控制

```python
from soulacp import ACPConnectionPool, ACPConfig, resolve_client_class

config = ACPConfig(provider="claude", model="claude-sonnet-4-20250514")
pool = ACPConnectionPool(config, resolve_client_class("claude"))

# 显式 session 复用
async with pool.acquire() as (client, sid):
    await client.query("记住 42。")

async with pool.acquire(session_id=sid) as (client, sid2):
    assert sid2 == sid  # 同一 CLI session
    response = await client.query("什么数字？")
```

## 配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ACP_PROVIDER` | — | Provider 名称 |
| `ACP_MODEL` | — | 模型标识 |
| `ACP_POOL_SIZE` | 10 | 连接池最大连接数 |
| `ACP_TIMEOUT_CONNECT` | 30 | 连接超时（秒） |
| `ACP_TIMEOUT_PROMPT` | 2592000 | Prompt 超时（秒，30 天） |
| `ACP_TIMEOUT_STREAM` | 2592000 | Stream chunk 超时（秒，30 天） |
| `ACP_POOL_IDLE_TIMEOUT` | 2592000 | 连接池空闲超时（秒，30 天） |
| `ACP_AUTO_APPROVE` | true | 自动批准工具权限 |
| `ACP_MAX_RETRIES` | 3 | 最大重试次数 |

### 代码配置

```python
from soulacp import ACPConfig

config = ACPConfig(
    provider="claude",
    model="claude-sonnet-4-20250514",
    pool_size=5,
    timeout_connect=60,
    auto_approve_permissions=True,
    enable_fallback=True,
)
```

### 从环境变量读取

```python
from soulacp import ACPConfig

config = ACPConfig.from_env()
```

## 架构

```
ManagedSession（高级 API）
  ├── ProviderSessionStore（user→session 映射）
  │   └── CacheBackend（MemoryCache / FileCache）
  ├── ACPConnectionPool（连接复用 + 健康检查）
  │   └── ACPClientBase（JSON-RPC over stdio 子进程）
  │       ├── ClaudeACPClient      ├── ClineACPClient
  │       ├── GeminiACPClient      ├── CopilotACPClient
  │       ├── OpenCodeACPClient    ├── MinionACPClient
  │       ├── OpenClawACPClient    ├── VibeACPClient
  │       ├── CursorACPClient      ├── NovaACPClient
  │       ├── CodexACPClient       ├── CrowACPClient
  │       ├── QwenACPClient        └── CursorCLIClient（legacy）
  │       ├── KimiACPClient
  │       └── CodebuddyACPClient
  └── Services
      ├── FSService（文件系统操作）
      └── TerminalService（子进程执行）
```

## 测试

```bash
# 单元测试（不需要 CLI）
pytest tests/ --ignore=tests/test_integration*.py -v

# 集成测试（需要对应 CLI，未安装自动跳过）
pytest tests/test_integration.py -v          # Claude Code
pytest tests/test_integration_gemini.py -v   # Gemini
pytest tests/test_integration_codex.py -v    # Codex

# 全部测试
pytest tests/ -v
```

## AIXP Labs [aixp.dev](https://aixp.dev)

AIXP Labs 开发和维护以下核心项目：

| 项目 | 描述 | 网站 |
|------|------|------|
| [HSAW](https://hsaw.dev) | 人类主权与福祉 — 公理 0 白皮书（基座） | hsaw.dev |
| [AILP](https://ailp.dev) | AI List Protocol — 代理发现与能力广告 | ailp.dev |
| [AIVP](https://aivp.dev) | AI Value Protocol — 国际商业、加密资产结算 | aivp.dev |
| [AIRP](https://airp.dev) | AI RMB Protocol — 中国大陆商业、人民币持牌结算 | airp.dev |
| [AIBP](https://aibp.dev) | AI Bot Protocol — 社交通信与信任 | aibp.dev |
| [AIAP](https://aiap.dev) | AI Application Protocol — 治理与合规 | aiap.dev |
| [AISOP](https://aisop.dev) | AI Standard Operating Protocol — 流程程序定义 | aisop.dev |
| [SoulBot](https://soulbot.dev) | AI 代理运行时与框架 | soulbot.dev |
| [SoulACP](https://soulacp.dev) | 适配器库 — 桥接 CLI 工具与 LLM 提供商（本项目） | soulacp.dev |

## ⚠️ 免责声明

本软件为**实验性**软件，仅供**研究和教育用途**。不适用于生产环境。使用风险由用户自行承担。作者对因使用本软件造成的任何损害不承担责任。完整条款见 [LICENSE](LICENSE)（Apache 2.0）。

## 许可证

[Apache License 2.0](LICENSE) - Copyright 2026 AIXP Labs AIXP.dev | SoulACP.dev

---

Align Axiom 0: Human Sovereignty and Wellbeing. Version: SoulACP V0.1.2. www.soulacp.dev
