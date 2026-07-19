"""Gemini-specific ACP client."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import aclosing

from soulacp.adapters.base_client import ACPClientBase, RPCError
from soulacp.binary import find_gemini_binary
from soulacp.errors import ACPProtocolError
from soulacp.meta import CLIENT_NAME, __version__
from soulacp.privacy import exception_summary

logger: logging.Logger = logging.getLogger(__name__)


class GeminiACPClient(ACPClientBase):
    """ACP client for Gemini CLI.

    Key differences from Claude:
    - Startup: ``gemini --acp [--model MODEL]``
    - Authentication: ``authenticate`` RPC after ``initialize``
    - Establishes a session during ``_initialize()``
    - Resume uses ``session/load`` (not ``session/resume``)
    - Uses ``session/list`` as a first-page existence hint before loading
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Gemini CLI in ACP mode."""
        binary = find_gemini_binary()
        if not binary:
            raise FileNotFoundError(
                "Gemini CLI not found. Install with: npm install -g @google/gemini-cli\n"
                "Then run gemini once to configure a supported authentication method"
            )
        cmd = [binary, "--acp"]
        # Pass model ID if specified
        model_id = self._configured_model_id()
        if model_id:
            cmd.extend(["--model", model_id])
        return cmd

    async def _initialize(self) -> None:
        """Perform the Gemini handshake, authenticate, and create a session."""
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
        if not isinstance(auth_methods, list):
            raise ACPProtocolError("Gemini initialize authMethods must be an array")
        if auth_methods:
            first_method = auth_methods[0]
            if not isinstance(first_method, dict):
                raise ACPProtocolError("Gemini initialize authMethods entries must be objects")
            method_id = first_method.get("id")
            if not isinstance(method_id, str) or not method_id:
                raise ACPProtocolError("Gemini initialize auth method requires a non-empty id")
            await self._rpc(
                "authenticate",
                {
                    "methodId": method_id,
                },
                timeout=self.config.timeout_connect,
            )

        await self.ensure_session()
        logger.info("Gemini session initialized")

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
        logger.info("Gemini session established")
        return self.session_id

    async def query(self, prompt: str) -> str:
        """Override to ensure session exists before querying."""
        await self.ensure_session()
        return await super().query(prompt)

    async def query_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """Override to ensure session exists before streaming."""
        await self.ensure_session()
        async with aclosing(super().query_stream(prompt)) as stream:
            async for chunk in stream:
                yield chunk

    async def resume(self, session_id: str) -> bool:
        """Resume a Gemini session via session/load.

        Validates the session via session/list when the agent advertises it.
        """
        # session/list is optional. Do not probe a method the agent omitted.
        if self.supports_session_capability("list"):
            try:
                result = await self._rpc("session/list", {}, timeout=self.config.timeout_connect)
                page = self._validate_session_list_result(result)
                exists = any(session["sessionId"] == session_id for session in page["sessions"])
                if not exists and page.get("nextCursor") is None:
                    logger.warning("Gemini session was not found in the provider session list")
                    return False
                if not exists:
                    # The requested session may be on a later page. session/load
                    # is authoritative and avoids unbounded list pagination.
                    logger.debug("Gemini session list is paginated; trying session/load directly")
            except RPCError as exc:
                logger.debug(
                    "Gemini session/list rejected; trying session/load directly: %s",
                    exception_summary(exc),
                )

        return await self._resume_if_available("session/load", session_id, agent_name="Gemini")
