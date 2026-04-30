"""Kilo - open source coding agent ACP client."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_kilo_binary
from soulacp.meta import CLIENT_NAME, __version__

logger = logging.getLogger(__name__)


class KiloACPClient(ACPClientBase):
    """ACP client for Kilo.

    Kilo is an open source coding agent with ACP support.

    Requires:
        - kilo (npm install -g @anthropic/kilo)
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Kilo in ACP mode."""
        binary = find_kilo_binary()
        if not binary:
            raise FileNotFoundError(
                "Kilo not found. Install with: npm install -g @anthropic/kilo"
            )
        return [binary, "acp"]

    async def _initialize(self) -> None:
        """Perform Kilo protocol handshake and create session."""
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
        auth_methods = result.get("authMethods", [])
        if auth_methods:
            method_id = auth_methods[0].get("id", "api_key")
            try:
                await self._rpc(
                    "authenticate",
                    {"methodId": method_id},
                    timeout=self.config.timeout_connect,
                )
            except Exception as e:
                logger.warning("Kilo authenticate failed: %s", e)

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
        logger.info("Kilo connected: session=%s", self.session_id)

        # 4. set model (optional, graceful degradation)
        model_id = self.config.model.split("/")[-1] if self.config.model else None
        if model_id and model_id != "default":
            try:
                await self._rpc(
                    "session/set_model",
                    {"sessionId": self.session_id, "modelId": model_id},
                    timeout=self.config.timeout_connect,
                )
                logger.info("Kilo model set to: %s", model_id)
            except Exception as e:
                logger.warning("session/set_model not supported, skipping: %s", e)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Kilo session."""
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
            logger.info("Kilo session resumed: %s", session_id)
            return True
        except Exception as e:
            logger.debug("Kilo session resume failed for %s: %s", session_id, e)
            return False
