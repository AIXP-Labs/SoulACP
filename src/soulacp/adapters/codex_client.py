"""ACP client for OpenAI Codex via codex-acp adapter."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_codex_binary
from soulacp.meta import CLIENT_NAME, __version__

logger = logging.getLogger(__name__)


class CodexACPClient(ACPClientBase):
    """ACP client for OpenAI Codex via codex-acp adapter.

    Requires:
        - codex-acp binary (``npx @zed-industries/codex-acp`` or pre-built)
        - ``OPENAI_API_KEY`` or ``CODEX_API_KEY`` environment variable
    """

    def _get_acp_command(self) -> list[str]:
        binary = find_codex_binary()
        if not binary:
            raise FileNotFoundError("codex-acp not found. Install with: npm install -g @zed-industries/codex-acp")
        return [binary]

    async def _initialize(self) -> None:
        """Perform Codex ACP handshake: initialize → initialized → authenticate → session/new."""
        # 1. initialize
        await self._rpc(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": CLIENT_NAME, "version": __version__},
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": True,
                },
            },
            timeout=self.config.timeout_connect,
        )

        # 2. initialized notification (no ID, no response — standard ACP)
        await self._send_notification("initialized")

        # 3. authenticate
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY or CODEX_API_KEY environment variable required for Codex")
        await self._rpc(
            "authenticate",
            {
                "authMethod": "api_key",
                "credentials": {"api_key": api_key},
            },
            timeout=self.config.timeout_connect,
        )

        # 4. session/new
        result = await self._rpc(
            "session/new",
            {"cwd": self.config.cwd},
            timeout=self.config.timeout_connect,
        )
        self.session_id = result.get("sessionId") or result.get("session_id")
        if not self.session_id:
            raise ConnectionError("session/new did not return sessionId")
        logger.info("Codex connected: session=%s", self.session_id)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Codex session via session/load."""
        try:
            await self._rpc(
                "session/load",
                {"sessionId": session_id},
                timeout=self.config.timeout_connect,
            )
            self.session_id = session_id
            return True
        except Exception as e:
            logger.debug("Codex session resume failed for %s: %s", session_id, e)
            return False
