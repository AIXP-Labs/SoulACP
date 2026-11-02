"""Kimi CLI ACP client — Moonshot AI's coding assistant."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_kimi_binary
from soulacp.errors import ACPProtocolError
from soulacp.meta import CLIENT_NAME, __version__

logger: logging.Logger = logging.getLogger(__name__)


class KimiACPClient(ACPClientBase):
    """ACP client for Kimi CLI.

    Kimi CLI uses standard ACP protocol via the ``kimi acp`` subcommand.
    Distributed as a pre-built binary (not npm).

    Requires:
        - kimi binary in PATH (download from github.com/MoonshotAI/kimi-cli/releases)
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Kimi CLI in ACP mode."""
        binary = find_kimi_binary()
        if not binary:
            raise FileNotFoundError(
                "Kimi CLI not found. Download from: https://github.com/MoonshotAI/kimi-cli/releases"
            )
        return [binary, "acp"]

    async def _initialize(self) -> None:
        """Perform Kimi protocol handshake and create session."""
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

        # 2. Kimi advertises a login check even for persisted credentials.
        # Authentication itself is completed beforehand with `kimi login`.
        auth_methods = result.get("authMethods", [])
        if not isinstance(auth_methods, list):
            raise ACPProtocolError("Kimi initialize authMethods must be an array")
        if auth_methods and self._auth_method_id is None:
            first_method = auth_methods[0]
            if not isinstance(first_method, dict):
                raise ACPProtocolError("Kimi initialize authMethods entries must be objects")
            method_id = first_method.get("id")
            if not isinstance(method_id, str) or not method_id:
                raise ACPProtocolError("Kimi initialize auth method requires a non-empty id")
            await self._rpc(
                "authenticate",
                {"methodId": method_id},
                timeout=self.config.timeout_connect,
            )

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
        logger.info("Kimi session established")

        # 4. set model (optional, graceful degradation)
        model_id = self._configured_model_id()
        if model_id and model_id != "default":
            await self._set_model_if_supported(model_id)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Kimi session."""
        return await self._resume_if_available("session/resume", session_id, agent_name="Kimi")
