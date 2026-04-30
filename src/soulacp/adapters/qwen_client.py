"""ACP client for Qwen Code CLI."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_qwen_binary
from soulacp.meta import CLIENT_NAME, __version__

logger = logging.getLogger(__name__)


class QwenACPClient(ACPClientBase):
    """ACP client for Qwen Code CLI.

    Qwen Code uses standard ACP protocol with the same handshake as Claude Code.
    No authentication or initialized notification required.
    Optional session/set_model for model selection.

    Requires:
        - qwen-code CLI (``npm install -g @qwen-code/qwen-code``)
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Qwen Code in ACP mode."""
        binary = find_qwen_binary()
        if not binary:
            raise FileNotFoundError(
                "Qwen Code CLI not found. Install with: npm install -g @qwen-code/qwen-code"
            )
        return [binary, "--acp", "--experimental-skills"]

    async def _initialize(self) -> None:
        """Perform Qwen Code protocol handshake and create session."""
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
        logger.info("Qwen Code connected: session=%s", self.session_id)

        # 3. set model (optional, graceful degradation)
        model_id = self.config.model.split("/")[-1] if self.config.model else None
        if model_id:
            try:
                await self._rpc(
                    "session/set_model",
                    {"sessionId": self.session_id, "modelId": model_id},
                    timeout=self.config.timeout_connect,
                )
                logger.info("Qwen model set to: %s", model_id)
            except Exception as e:
                logger.warning("session/set_model not supported, skipping: %s", e)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Qwen Code session."""
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
            logger.info("Qwen session resumed: %s", session_id)
            return True
        except Exception as e:
            logger.debug("Qwen session resume failed for %s: %s", session_id, e)
            return False
