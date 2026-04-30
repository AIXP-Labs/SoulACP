"""Minimal interface contract for all ACP client implementations.

``ACPClient`` is a :class:`~typing.Protocol` (structural subtype) so that
both :class:`ACPClientBase`-derived clients **and** standalone clients like
:class:`CursorCLIClient` satisfy the same interface without requiring
inheritance.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Protocol, runtime_checkable

from soulacp.config import ACPConfig


@runtime_checkable
class ACPClient(Protocol):
    """Structural interface every ACP client must satisfy.

    Both ``ACPClientBase`` subclasses (Claude, Gemini, OpenCode, OpenClaw,
    Codex, Qwen, Kimi, Cursor, Codebuddy, Cline, Copilot, Minion, Vibe, Nova, Crow) and the legacy ``CursorCLIClient`` conform to this protocol.
    """

    config: ACPConfig

    @property
    def is_connected(self) -> bool:
        """Whether the underlying CLI process is alive."""
        ...

    @property
    def is_idle_timeout(self) -> bool:
        """Whether the connection has exceeded the idle timeout."""
        ...

    @property
    def session_id(self) -> str | None:
        """Current CLI session identifier, or ``None``."""
        ...

    async def connect(self) -> None:
        """Start the CLI subprocess and perform protocol handshake."""
        ...

    async def disconnect(self) -> None:
        """Terminate the CLI subprocess and release resources."""
        ...

    async def ping(self) -> bool:
        """Lightweight health check — verify the client is usable."""
        ...

    async def resume(self, session_id: str) -> bool:
        """Resume a previous session. Returns ``True`` on success."""
        ...

    async def query(self, prompt: str) -> str:
        """Send a prompt and return the complete response."""
        ...

    def query_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """Send a prompt and yield response chunks as they arrive."""
        ...
