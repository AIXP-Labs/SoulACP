"""Gemini-specific ACP client."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_gemini_binary
from soulacp.meta import CLIENT_NAME, __version__

logger = logging.getLogger(__name__)


class GeminiACPClient(ACPClientBase):
    """ACP client for Gemini CLI.

    Key differences from Claude:
    - Startup: ``gemini --acp [--model MODEL]``
    - Authentication: ``authenticate`` RPC after ``initialize``
    - Delayed session: ``_initialize()`` does NOT create a session
    - Resume uses ``session/load`` (not ``session/resume``)
    - Validates session existence via ``session/list`` before loading
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Gemini CLI in ACP mode."""
        binary = find_gemini_binary()
        if not binary:
            raise FileNotFoundError(
                "Gemini CLI not found. Install with: npm install -g @google/gemini-cli\n"
                "Then authenticate with: gemini auth"
            )
        cmd = [binary, "--acp"]
        # Pass model ID if specified
        model_id = self.config.model.replace("gemini-acp/", "")
        if model_id:
            cmd.extend(["--model", model_id])
        return cmd

    async def _initialize(self) -> None:
        """Perform Gemini protocol handshake (delayed session creation).

        Unlike Claude, Gemini does NOT create a session during initialization.
        The session is created lazily when ``ensure_session()`` is called
        (typically by the pool's ``acquire()``).
        """
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

        # Handle authentication
        auth_methods = result.get("authMethods", [])
        if auth_methods:
            method_id = auth_methods[0].get("id", "oauth-personal")
            try:
                await self._rpc(
                    "authenticate",
                    {
                        "methodId": method_id,
                    },
                    timeout=self.config.timeout_connect,
                )
            except Exception as e:
                logger.warning("Gemini authenticate RPC failed: %s", e)

        # Delayed: session_id stays None until ensure_session()
        self.session_id = None
        logger.info("Gemini initialized (session deferred)")

    async def ensure_session(self) -> str:
        """Create a new session if one doesn't exist yet.

        Returns:
            The session ID.
        """
        if self.session_id is not None:
            return self.session_id

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
        logger.info("Gemini session created: %s", self.session_id)
        return self.session_id

    async def query(self, prompt: str) -> str:
        """Override to ensure session exists before querying."""
        await self.ensure_session()
        return await super().query(prompt)

    async def query_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """Override to ensure session exists before streaming."""
        await self.ensure_session()
        async for chunk in super().query_stream(prompt):
            yield chunk

    async def resume(self, session_id: str) -> bool:
        """Resume a Gemini session via session/load.

        Validates the session exists via session/list before loading.
        """
        # 1. Validate session exists
        try:
            result = await self._rpc("session/list", {}, timeout=self.config.timeout_connect)
            sessions = result.get("sessions", [])
            exists = any(s.get("id") == session_id for s in sessions)
            if not exists:
                logger.warning("Gemini session not found in list: %s", session_id)
                return False
        except Exception as e:
            logger.debug("session/list check failed: %s", e)

        # 2. Load session
        try:
            await self._rpc(
                "session/load",
                {
                    "sessionId": session_id,
                    "cwd": self.config.cwd or os.getcwd(),
                    "mcpServers": [],
                },
                timeout=self.config.timeout_connect,
            )
            self.session_id = session_id
            logger.info("Gemini session loaded: %s", session_id)
            return True
        except Exception as e:
            logger.warning("Gemini session load failed: %s", e)
            return False
