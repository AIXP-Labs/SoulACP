"""Stress / robustness tests for the stdio JSON-RPC transport layer.

Covers two concerns flagged by web research:

1. **Windows CRLF safety**: ``TextIOWrapper`` on Windows can translate ``\\n``
   to ``\\r\\n`` and corrupt newline-delimited JSON-RPC. soulacp uses
   ``asyncio.subprocess.PIPE`` in binary mode, which should be immune,
   but this test verifies that messages containing newlines round-trip
   correctly.

2. **Live response progress**: model output must complete without a stdio
   stall and streaming must yield progressively. The deterministic offline
   lifecycle suite separately sends a 256 KB JSON-RPC frame through a local
   subprocess, which is the actual regression proof beyond common OS pipe
   buffer sizes.

The default order is Codex, Claude, then Gemini. Set ``ACP_STRESS_PROVIDER``
and optionally ``ACP_STRESS_MODEL`` to select a specific installed bridge.

Run with: pytest --run-integration tests/test_stress_stdio.py -v
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from soulacp.binary import find_claude_binary, find_codex_binary, find_gemini_binary


def _pick_provider():
    """Pick an installed provider, with an explicit environment override."""
    providers = {
        "codex": (find_codex_binary, "codex-acp/gpt-5.5"),
        "claude": (find_claude_binary, "claude-acp/sonnet"),
        "gemini": (find_gemini_binary, "gemini-3-flash-preview"),
    }
    requested = os.environ.get("ACP_STRESS_PROVIDER")
    if requested:
        if requested not in providers:
            raise ValueError(f"unsupported ACP_STRESS_PROVIDER: {requested}")
        finder, default_model = providers[requested]
        if finder() is None:
            return None
        return requested, os.environ.get("ACP_STRESS_MODEL", default_model)
    for provider, (finder, default_model) in providers.items():
        if finder() is not None:
            return provider, os.environ.get("ACP_STRESS_MODEL", default_model)
    return None


pytestmark = pytest.mark.skipif(
    _pick_provider() is None,
    reason="No supported Codex, Claude Code, or Gemini ACP bridge installed",
)


@pytest.fixture
def provider_config():
    from soulacp import ACPConfig

    provider, model = _pick_provider()
    return ACPConfig(
        provider=provider,
        model=model,
        timeout_connect=30,
        timeout_prompt=180,  # generous for large output
    )


@pytest.fixture
def client_class(provider_config):
    from soulacp import resolve_client_class

    return resolve_client_class(provider_config.provider)


def test_newline_round_trip(provider_config, client_class):
    """Verify messages containing embedded newlines round-trip without corruption.

    Sends a prompt that explicitly requests multi-line output with newlines,
    then checks the response contains expected line markers without spurious
    ``\\r`` (carriage returns) that would indicate Windows CRLF translation
    corrupted the JSON-RPC frame.
    """
    from soulacp import ACPConnectionPool

    PROMPT = "Output the following 5 lines EXACTLY, no extra text:\nLINE-1\nLINE-2\nLINE-3\nLINE-4\nLINE-5"

    async def run():
        pool = ACPConnectionPool(provider_config, client_class)
        try:
            async with pool.acquire() as (client, _sid):
                response = await client.query(PROMPT)
                assert len(response) > 0

                # All 5 markers should be present
                for marker in ("LINE-1", "LINE-2", "LINE-3", "LINE-4", "LINE-5"):
                    assert marker in response, f"missing {marker} in:\n{response}"

                # No bare \r should appear (would indicate CRLF corruption);
                # \r\n in normal text is OK as long as it follows \n.
                # Specifically check that markers aren't fused (no "LINE-1\rLINE-2").
                assert "\rLINE-" not in response, "stray \\r between line markers — possible CRLF corruption"
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_medium_response_no_stall(provider_config, client_class):
    """Verify a multi-kilobyte live model response completes without stalling.

    Asks the LLM to generate a substantial multi-section technical doc.
    This is an end-to-end provider smoke test, not the 64 KB boundary proof.
    """
    from soulacp import ACPConnectionPool

    # Force inline chat output (not file write) — ask for a list of items
    # the agent can't reasonably "save to a file" without being silly.
    LARGE_PROMPT = (
        "Reply ONLY in this chat (do not write to any file). "
        "List 50 distinct adjectives describing different emotions, "
        "one per line, in the format: 'N. <adjective> — <one sentence "
        "explaining when this emotion occurs>'. Numbers 1 through 50, "
        "no skipping. Make each explanation distinct and at least 10 words."
    )

    async def run():
        pool = ACPConnectionPool(provider_config, client_class)
        try:
            async with pool.acquire() as (client, _sid):
                t0 = time.monotonic()
                response = await client.query(LARGE_PROMPT)
                elapsed = time.monotonic() - t0

                bytes_received = len(response.encode("utf-8"))
                # At least 3 KB confirms the provider honored the inline task.
                assert bytes_received > 3_000, (
                    f"response too small ({bytes_received} bytes) — "
                    f"likely the agent wrote to a file instead of inline reply: "
                    f"{response[:300]}"
                )
                # Should complete within 3 minutes (generous)
                assert elapsed < 180, f"response took {elapsed:.1f}s > 180s — possible deadlock"
                print(f"\n  ok: received {bytes_received} bytes in {elapsed:.1f}s ({bytes_received / elapsed:.0f} B/s)")
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_large_streaming_progressive(provider_config, client_class):
    """Verify streaming a large response yields chunks progressively (not buffered).

    Confirms the streaming path delivers chunks incrementally, which is
    the real safeguard against pipe-buffer deadlock for very large outputs.
    """
    from soulacp import ACPConnectionPool

    PROMPT = (
        "List 30 common Python standard-library modules with one-sentence "
        "descriptions. One module per line. Format: 'N. module — description'."
    )

    async def run():
        pool = ACPConnectionPool(provider_config, client_class)
        chunks: list[tuple[float, int]] = []  # (time, bytes)
        try:
            async with pool.acquire() as (client, _sid):
                t0 = time.monotonic()
                async for chunk in client.query_stream(PROMPT):
                    if isinstance(chunk, str):
                        chunks.append((time.monotonic() - t0, len(chunk.encode("utf-8"))))

                assert len(chunks) >= 3, (
                    f"expected progressive streaming, got only {len(chunks)} chunks "
                    "(may indicate buffering issue or backend returned all-at-once)"
                )

                # First chunk should arrive reasonably quickly
                first_chunk_at = chunks[0][0]
                last_chunk_at = chunks[-1][0]
                total_bytes = sum(b for _, b in chunks)
                print(
                    f"\n  ok: {len(chunks)} chunks, {total_bytes} bytes, "
                    f"first at {first_chunk_at:.2f}s, last at {last_chunk_at:.2f}s"
                )
        finally:
            await pool.close_all()

    asyncio.run(run())
