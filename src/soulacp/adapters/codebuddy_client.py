"""Codebuddy Code ACP client — Tencent Cloud's intelligent coding tool."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_codebuddy_binary
from soulacp.meta import CLIENT_NAME, __version__

logger: logging.Logger = logging.getLogger(__name__)


class CodebuddyACPClient(ACPClientBase):
    """ACP client for Codebuddy Code CLI.

    Codebuddy Code uses standard ACP protocol with the same handshake
    as Claude Code and Qwen Code. No authentication required.

    Requires:
        - codebuddy-code CLI (npm install -g @tencent-ai/codebuddy-code)
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Codebuddy Code in ACP mode."""
        binary = find_codebuddy_binary()
        if not binary:
            raise FileNotFoundError(
                "Codebuddy Code CLI not found. Install with: npm install -g @tencent-ai/codebuddy-code"
            )
        return [binary, "--acp"]

    async def _initialize(self) -> None:
        """Perform Codebuddy protocol handshake and create session."""
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
        logger.info("Codebuddy session established")

        # 3. set model (optional, graceful degradation)
        model_id = self._configured_model_id()
        if model_id and model_id != "default":
            await self._set_model_if_supported(model_id)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Codebuddy session."""
        return await self._resume_if_available("session/resume", session_id, agent_name="Codebuddy")
