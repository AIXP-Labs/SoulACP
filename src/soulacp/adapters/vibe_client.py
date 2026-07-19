"""Mistral Vibe ACP client — Mistral's open-source coding assistant."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_vibe_binary
from soulacp.meta import CLIENT_NAME, __version__

logger: logging.Logger = logging.getLogger(__name__)


class VibeACPClient(ACPClientBase):
    """ACP client for Mistral Vibe.

    The ``vibe-acp`` binary is itself the ACP entry point — no subcommand
    or flag needed. Same pattern as Codex (``codex-acp``).

    Requires:
        - vibe-acp binary in PATH
          (download from github.com/mistralai/mistral-vibe/releases)
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Mistral Vibe in ACP mode."""
        binary = find_vibe_binary()
        if not binary:
            raise FileNotFoundError(
                "Mistral Vibe CLI not found. Download from: https://github.com/mistralai/mistral-vibe/releases"
            )
        return [binary]

    async def _initialize(self) -> None:
        """Perform Mistral Vibe protocol handshake and create session."""
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

        # Vibe advertises available browser/terminal setup methods even when
        # already authenticated. Headless clients must not trigger one merely
        # because it appears in initialize; its child env/config is authoritative.

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
        logger.info("Vibe session established")

        # 3. set model (optional, graceful degradation)
        model_id = self._configured_model_id()
        if model_id and model_id != "default":
            await self._set_model_if_supported(model_id)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Mistral Vibe session."""
        return await self._resume_if_available("session/resume", session_id, agent_name="Vibe")
