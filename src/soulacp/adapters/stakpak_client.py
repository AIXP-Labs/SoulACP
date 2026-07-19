"""Stakpak - DevOps agent ACP client."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_stakpak_binary
from soulacp.meta import CLIENT_NAME, __version__

logger: logging.Logger = logging.getLogger(__name__)


class StakpakACPClient(ACPClientBase):
    """ACP client for Stakpak.

    Stakpak is a DevOps agent with ACP support.

    Requires:
        - stakpak (https://github.com/stakpak/agent/releases)
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Stakpak in ACP mode."""
        binary = find_stakpak_binary()
        if not binary:
            raise FileNotFoundError("Stakpak not found. Install from: https://github.com/stakpak/agent/releases")
        return [binary, "acp"]

    async def _initialize(self) -> None:
        """Perform Stakpak protocol handshake and create session."""
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

        # 2. authenticate (if required)
        await self._authenticate_if_advertised(result, agent_name="Stakpak")

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
        logger.info("Stakpak session established")

        # 4. set model (optional, graceful degradation)
        model_id = self._configured_model_id()
        if model_id and model_id != "default":
            await self._set_model_if_supported(model_id)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Stakpak session."""
        return await self._resume_if_available("session/resume", session_id, agent_name="Stakpak")
