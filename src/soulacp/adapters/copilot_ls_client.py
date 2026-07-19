"""GitHub Copilot Language Server ACP client."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_copilot_ls_binary
from soulacp.meta import CLIENT_NAME, __version__

logger: logging.Logger = logging.getLogger(__name__)


class CopilotLSACPClient(ACPClientBase):
    """ACP client for GitHub Copilot Language Server.

    GitHub Copilot Language Server provides ACP access to Copilot.

    Requires:
        - copilot-language-server (npm install -g @github/copilot-language-server)
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start GitHub Copilot Language Server in ACP mode."""
        binary = find_copilot_ls_binary()
        if not binary:
            raise FileNotFoundError(
                "GitHub Copilot Language Server not found. Install with: npm install -g @github/copilot-language-server"
            )
        return [binary, "--acp"]

    async def _initialize(self) -> None:
        """Perform GitHub Copilot Language Server protocol handshake and create session."""
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
        await self._authenticate_if_advertised(result, agent_name="GitHub Copilot Language Server")

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
        logger.info("GitHub Copilot Language Server session established")

        # 4. set model (optional, graceful degradation)
        model_id = self._configured_model_id()
        if model_id and model_id != "default":
            await self._set_model_if_supported(model_id)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous GitHub Copilot Language Server session."""
        return await self._resume_if_available(
            "session/resume",
            session_id,
            agent_name="GitHub Copilot Language Server",
        )
