"""Cursor ACP client — standard ACP protocol via cursor-agent acp."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_cursor_binary
from soulacp.meta import CLIENT_NAME, __version__

logger = logging.getLogger(__name__)


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
        auth_methods = result.get("authMethods", [])
        if auth_methods:
            method_id = auth_methods[0].get("id", "oauth-personal")
            try:
                await self._rpc(
                    "authenticate",
                    {"methodId": method_id},
                    timeout=self.config.timeout_connect,
                )
            except Exception as e:
                logger.warning("Cursor authenticate failed: %s", e)

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
        logger.info("Cursor ACP connected: session=%s", self.session_id)

        # 4. set model (optional)
        model_id = self.config.model.split("/")[-1] if self.config.model else None
        if model_id and model_id != "default":
            try:
                await self._rpc(
                    "session/set_model",
                    {"sessionId": self.session_id, "modelId": model_id},
                    timeout=self.config.timeout_connect,
                )
                logger.info("Cursor model set to: %s", model_id)
            except Exception as e:
                logger.warning("session/set_model not supported, skipping: %s", e)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Cursor ACP session."""
        try:
            await self._rpc(
                "session/resume",
                {
                    "sessionId": session_id,
                    "cwd": self.config.cwd or os.getcwd(),
                },
                timeout=self.config.timeout_connect,
            )
            self.session_id = session_id
            logger.info("Cursor session resumed: %s", session_id)
            return True
        except Exception as e:
            logger.debug("Cursor session resume failed for %s: %s", session_id, e)
            return False
