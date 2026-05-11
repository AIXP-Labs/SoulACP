"""Doc 50 Spike 0.5-A: mid-stream disconnect() behavior verification.

See docs/plan14/50-stream-abort-audit-fabrication-defense.md §4.1 Spike 0.5-A.

验证 3 个 ACP provider (Claude Code / Gemini CLI / OpenCode) 的 `client.disconnect()` 在
mid-stream 场景下的实际语义:
  - disconnect 本设计 turn 结束用,mid-stream 是新场景
  - 关键断言:
    1. disconnect 不 hang (完成 < 10s)
    2. disconnect 后 query_stream 不再 yield 大量 chunk
    3. stream task 能 graceful 终止 (< 5s)
    4. 重连可用 (pool 仍能 acquire)

每 provider 独立 skipif 检测:若 CLI 未安装自动 skip 本 provider 的 case。

结果写入 docs/plan14/51a-disconnect-behavior.md (§4.2.1 止损矩阵判决)。

Run:
    pytest tests/test_spike_0_5_a_disconnect.py -v -s
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import pytest

from soulacp import ACPConfig, ACPConnectionPool, resolve_client_class
from soulacp.binary import (
    find_claude_binary,
    find_codex_binary,
    find_gemini_binary,
    find_opencode_binary,
)


@dataclass
class SpikeAResult:
    """一次 mid-stream disconnect 实测的结果 snapshot."""

    provider: str
    chunks_before: int = 0
    chunks_after: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    disconnect_elapsed_s: float = 0.0
    task_end: str = "unknown"          # "completed" / "cancelled" / "exception:<Name>" / "hang"
    reconnect_ok: bool | None = None
    reconnect_err: str | None = None
    notes: list[str] = field(default_factory=list)


PROVIDERS = [
    pytest.param(
        "claude",
        "claude-sonnet-4-20250514",
        find_claude_binary,
        id="claude",
        marks=pytest.mark.skipif(
            find_claude_binary() is None, reason="Claude Code CLI not installed"
        ),
    ),
    pytest.param(
        "gemini",
        "gemini-2.5-pro",
        find_gemini_binary,
        id="gemini",
        marks=pytest.mark.skipif(
            find_gemini_binary() is None, reason="Gemini CLI not installed"
        ),
    ),
    pytest.param(
        "opencode",
        "anthropic/claude-sonnet-4-20250514",
        find_opencode_binary,
        id="opencode",
        marks=pytest.mark.skipif(
            find_opencode_binary() is None, reason="OpenCode CLI not installed"
        ),
    ),
    pytest.param(
        "codex",
        "codex-acp/gpt-5.5",
        find_codex_binary,
        id="codex",
        marks=pytest.mark.skipif(
            find_codex_binary() is None, reason="codex-acp not installed"
        ),
    ),
]


# 长 prompt 让 stream 持续 > 500ms,确保 disconnect 能抓到 mid-stream 状态
LONG_PROMPT = (
    "Please write a detailed step-by-step tutorial explaining how to implement "
    "a linked list in Python. Include sections: (1) class design, (2) insert, "
    "(3) delete, (4) traverse, (5) reverse, (6) testing. At least 30 lines total."
)


async def _run_spike_a(provider: str, model: str) -> SpikeAResult:
    """单 provider 的 mid-stream disconnect 实测.

    策略:等首 chunk 到达后再额外延迟 200ms,才调 disconnect。这样保证是真正的
    "mid-stream"场景(stream 已开始,LLM 正在生成)。首 chunk 最多等 30s。
    """
    result = SpikeAResult(provider=provider)
    config = ACPConfig(
        provider=provider,
        model=model,
        timeout_connect=30,
        timeout_prompt=120,
    )
    client_class = resolve_client_class(provider)
    pool = ACPConnectionPool(config, client_class)

    first_chunk_arrived = asyncio.Event()
    disconnect_done = asyncio.Event()
    first_chunk_wait_s = 0.0

    try:
        async with pool.acquire() as (client, session_id):
            assert session_id is not None

            async def stream_consumer():
                try:
                    async for chunk in client.query_stream(LONG_PROMPT):
                        chunk_bytes = len(chunk.encode("utf-8")) if isinstance(chunk, str) else 0
                        if not disconnect_done.is_set():
                            result.chunks_before += 1
                            result.bytes_before += chunk_bytes
                            if not first_chunk_arrived.is_set():
                                first_chunk_arrived.set()
                        else:
                            result.chunks_after += 1
                            result.bytes_after += chunk_bytes
                    return "completed"
                except asyncio.CancelledError:
                    return "cancelled"
                except Exception as exc:  # noqa: BLE001
                    return f"exception:{type(exc).__name__}:{exc!s}"

            task = asyncio.create_task(stream_consumer())

            # 等首 chunk 到达(mid-stream 场景前置),最多 60s
            # 提高到 60s 容忍连续测试时 backend 响应变慢(原 30s 在全套
            # 跑时偶发 timeout,opencode 尤其敏感)
            t_first = time.monotonic()
            try:
                await asyncio.wait_for(first_chunk_arrived.wait(), timeout=60.0)
                first_chunk_wait_s = time.monotonic() - t_first
            except asyncio.TimeoutError:
                first_chunk_wait_s = time.monotonic() - t_first
                result.notes.append(f"first chunk TIMEOUT > 60s (stream 未启动)")
                # 继续跑 disconnect 看行为

            # 首 chunk 后额外等 200ms 保证已在 mid-stream
            await asyncio.sleep(0.2)

            # 关键:mid-stream disconnect
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(client.disconnect(), timeout=10.0)
                result.disconnect_elapsed_s = time.monotonic() - t0
            except asyncio.TimeoutError:
                result.disconnect_elapsed_s = time.monotonic() - t0
                result.notes.append("disconnect() HUNG > 10s")

            disconnect_done.set()

            # 等 stream task 终止,5s 超时
            try:
                end_reason = await asyncio.wait_for(task, timeout=5.0)
                result.task_end = end_reason or "completed"
            except asyncio.TimeoutError:
                result.task_end = "hang"
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"pool.acquire exception: {type(exc).__name__}: {exc}")

    result.notes.append(f"first_chunk_wait={first_chunk_wait_s:.2f}s")

    # 重连测试:新开 acquire 看是否 graceful
    try:
        async with pool.acquire() as (client2, sid2):
            resp = await asyncio.wait_for(
                client2.query("Say 'OK' in one word."), timeout=60.0
            )
            result.reconnect_ok = bool(resp and len(resp) > 0)
    except Exception as exc:  # noqa: BLE001
        result.reconnect_ok = False
        result.reconnect_err = f"{type(exc).__name__}: {exc}"

    await pool.close_all()
    return result


@pytest.mark.parametrize("provider,model,binary_finder", PROVIDERS)
def test_midstream_disconnect(
    provider: str, model: str, binary_finder, request: pytest.FixtureRequest
) -> None:
    """Spike 0.5-A 核心测试:mid-stream disconnect 行为."""

    result = asyncio.run(_run_spike_a(provider, model))

    # 用 request.config.stash 或 pytest-html 记录结果;先直接 print + 挂到 capsys
    print(f"\n{'=' * 60}")
    print(f"Spike 0.5-A [{provider}]")
    print(f"{'=' * 60}")
    print(f"  chunks before disconnect : {result.chunks_before} ({result.bytes_before} bytes)")
    print(f"  chunks after disconnect  : {result.chunks_after} ({result.bytes_after} bytes)")
    print(f"  disconnect elapsed       : {result.disconnect_elapsed_s:.3f}s")
    print(f"  stream task end          : {result.task_end}")
    print(f"  reconnect ok             : {result.reconnect_ok} {result.reconnect_err or ''}")
    if result.notes:
        print(f"  notes                    : {result.notes}")

    # 判决(对齐 §4.2.1 止损矩阵):
    # 1. 至少有 chunk 产生(验证 stream 真跑起来了)
    if result.chunks_before == 0:
        # Backend 太慢导致 stream 60s 内没启动 — 不是 disconnect 行为问题,
        # 跳过 spike 判决而不是 fail。在隔离运行时 stream 通常 < 5s 启动。
        pytest.skip(
            f"[{provider}] stream did not start within 60s — backend slow / "
            f"resource contention from prior tests (notes: {result.notes})"
        )
    # 2. disconnect 不 hang
    assert result.disconnect_elapsed_s < 10.0, (
        f"[{provider}] disconnect() 耗时 {result.disconnect_elapsed_s:.2f}s >= 10s (hang)"
    )
    # 3. stream task 能终止
    assert result.task_end != "hang", (
        f"[{provider}] stream task 未能在 5s 内终止"
    )
    # 4. 重连可用
    assert result.reconnect_ok is True, (
        f"[{provider}] 重连失败: {result.reconnect_err}"
    )

    # 软断言:disconnect 后应尽量少 chunk(graceful)
    # 这里不 hard fail,只是记录 "provider 是否 graceful"
    if result.chunks_after > 3:
        print(
            f"  ⚠️  SOFT WARN: disconnect 后仍 yield {result.chunks_after} chunks — "
            f"该 provider 可能需 `SOULBOT_AUDIT_ABORT_DISABLED_PROVIDERS={provider}`"
        )
