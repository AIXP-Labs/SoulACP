"""pi ACP - coding agent adapter ACP client."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_pi_binary
from soulacp.meta import CLIENT_NAME, __version__

logger = logging.getLogger(__name__)


class PiACPClient(ACPClientBase):
    """ACP client for pi ACP.

    pi ACP is a coding agent adapter with ACP support.

    Requires:
        - pi (npm install -g @anthropic/pi-acp)
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start pi ACP in ACP mode."""
        binary = find_pi_binary()
        if not binary:
            raise FileNotFoundError(
                "pi ACP not found. Install with: npm install -g @anthropic/pi-acp"
            )
        return [binary]

    async def _initialize(self) -> None:
        """Perform pi ACP protocol handshake and create session."""
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
                logger.warning("pi ACP authenticate failed: %s", e)

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
        logger.info("pi ACP connected: session=%s", self.session_id)

        # 4. set model (optional, graceful degradation)
        model_id = self.config.model.split("/")[-1] if self.config.model else None
        if model_id and model_id != "default":
            try:
                await self._rpc(
                    "session/set_model",
                    {"sessionId": self.session_id, "modelId": model_id},
                    timeout=self.config.timeout_connect,
                )
                logger.info("pi ACP model set to: %s", model_id)
            except Exception as e:
                logger.warning("session/set_model not supported, skipping: %s", e)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous pi ACP session."""
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
            logger.info("pi ACP session resumed: %s", session_id)
            return True
        except Exception as e:
            logger.debug("pi ACP session resume failed for %s: %s", session_id, e)
            return False
