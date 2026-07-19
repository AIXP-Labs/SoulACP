"""Cursor ACP client — standard ACP protocol via cursor-agent acp."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_cursor_binary
from soulacp.meta import CLIENT_NAME, __version__

logger: logging.Logger = logging.getLogger(__name__)


class CursorACPClient(ACPClientBase):
    """ACP client for Cursor via standard ACP protocol.

    Uses ``cursor-agent acp`` subcommand for JSON-RPC 2.0 over stdio.
    Supports connection pooling, session management, and streaming
    via standard ACP mechanisms.

    Requires:
        - cursor-agent binary with ACP support (v2026.03.30+)
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Cursor in ACP mode."""
        binary = find_cursor_binary()
        if not binary:
            raise FileNotFoundError("Cursor CLI not found. Download from: https://cursor.com")
        return [binary, "acp"]

    async def _initialize(self) -> None:
        """Perform Cursor ACP handshake and create session."""
        # 1. initialize
        result = await self._rpc(
            "initialize",
            {
                "clientInfo": {"name": CLIENT_NAME, "version": __version__},
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": True,
                },
            },
            timeout=self.config.timeout_connect,
        )

        # 2. authenticate (if required — Cursor may require login)
        # NOTE: Using Gemini pattern {"methodId": ...} rather than Codex
        # pattern {"authMethod": ..., "credentials": {...}}, because Cursor
        # is more likely to use OAuth login (like Gemini) than API keys.
        await self._authenticate_if_advertised(result, agent_name="Cursor")

        # 3. session/new
        res = await self._rpc(
            "session/new",
            {
                "cwd": self.config.cwd or os.getcwd(),
                "mcpServers": [],
            },
            timeout=self.config.timeout_connect,
        )
        self.session_id = res.get("sessionId") or res.get("session_id")
        if not self.session_id:
            raise ConnectionError("session/new did not return sessionId")
        logger.info("Cursor ACP session established")

        # 4. set model (optional)
        model_id = self._configured_model_id()
        if model_id and model_id != "default":
            await self._set_model_if_supported(model_id)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Cursor ACP session."""
        return await self._resume_if_available("session/resume", session_id, agent_name="Cursor")
