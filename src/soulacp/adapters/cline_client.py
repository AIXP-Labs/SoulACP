"""Cline ACP client — autonomous coding agent CLI."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_cline_binary
from soulacp.meta import CLIENT_NAME, __version__

logger: logging.Logger = logging.getLogger(__name__)


class ClineACPClient(ACPClientBase):
    """ACP client for Cline CLI.

    Cline is an autonomous coding agent capable of creating/editing files,
    running commands, and using the browser. Uses standard ACP protocol
    with the same handshake as Claude Code. No authentication required.

    Requires:
        - cline CLI (npm install -g cline)
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Cline in ACP mode."""
        binary = find_cline_binary()
        if not binary:
            raise FileNotFoundError("Cline CLI not found. Install with: npm install -g cline")
        return [binary, "--acp"]

    async def _initialize(self) -> None:
        """Perform Cline protocol handshake and create session."""
        # 1. initialize
        await self._rpc(
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

        # 2. session/new
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
        logger.info("Cline session established")

        # 3. set model (optional, graceful degradation)
        model_id = self._configured_model_id()
        if model_id and model_id != "default":
            await self._set_model_if_supported(model_id)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Cline session."""
        return await self._resume_if_available("session/resume", session_id, agent_name="Cline")
