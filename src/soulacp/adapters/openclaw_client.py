"""OpenClaw ACP client — Gateway-backed AI assistant."""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

from soulacp.adapters.base_client import ACPClientBase, RPCError
from soulacp.binary import find_openclaw_binary
from soulacp.config import _validate_bracketed_url_host
from soulacp.meta import CLIENT_NAME, __version__
from soulacp.privacy import exception_summary

logger: logging.Logger = logging.getLogger(__name__)

# Valid OpenClaw thinking levels (from openclaw/src/auto-reply/thinking.ts).
VALID_THINKING_LEVELS = frozenset(
    {
        "off",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    }
)


def _validate_gateway_url(url: str) -> str:
    """Validate the WebSocket URL before exposing it in child argv."""
    try:
        url.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("OPENCLAW_URL must contain valid Unicode") from exc
    if url != url.strip():
        raise ValueError("OPENCLAW_URL must not contain surrounding whitespace")
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in url) or "\\" in url:
        raise ValueError("OPENCLAW_URL must not contain whitespace, control characters, or backslashes")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        username = parsed.username
        password = parsed.password
        _validate_bracketed_url_host(parsed.netloc, parsed.hostname)
    except ValueError:
        raise ValueError("OPENCLAW_URL must be a valid WebSocket URL") from None
    if username is not None or password is not None:
        raise ValueError("OPENCLAW_URL must not contain embedded credentials")
    if parsed.scheme.lower() not in {"ws", "wss"} or not parsed.hostname or (port is not None and port < 1):
        raise ValueError("OPENCLAW_URL must be an absolute ws:// or wss:// URL with a host")
    return url


def _parse_thinking_level(model: str) -> str | None:
    """Extract thinking level from a model identifier.

    Format: ``openclaw/<model>/<thinking_level>``

    Examples::

        openclaw/default/high  → "high"
        openclaw/default       → None
        openclaw/high          → "high"  (shorthand — valid level as 2nd segment)
    """
    parts = model.split("/")
    # openclaw/<model>/<thinking_level>
    if len(parts) == 3:
        level = parts[2].lower()
        return level if level in VALID_THINKING_LEVELS else None
    # openclaw/<thinking_level> (shorthand)
    if len(parts) == 2:
        level = parts[1].lower()
        return level if level in VALID_THINKING_LEVELS else None
    return None


class OpenClawACPClient(ACPClientBase):
    """ACP client for the ``openclaw acp`` subprocess.

    Communication chain::

        soulacp → stdin/stdout (ACP JSON-RPC) → openclaw acp → WebSocket → Gateway
    """

    def _get_acp_command(self) -> list[str]:
        """Build the ``openclaw acp`` launch command."""
        binary = find_openclaw_binary()
        if not binary:
            raise FileNotFoundError("OpenClaw CLI not found. Install with: npm install -g openclaw")

        cmd = [binary, "acp", "--no-prefix-cwd"]

        # Gateway URL
        url = self._child_env_value("OPENCLAW_URL")
        if url:
            cmd.extend(["--url", _validate_gateway_url(url)])

        # Authentication overrides use files only. Plain secrets on argv are
        # visible to local process-inspection tools on common operating systems.
        token_file = self._child_env_value("OPENCLAW_TOKEN_FILE")
        token = self._child_env_value("OPENCLAW_TOKEN")
        password_file = self._child_env_value("OPENCLAW_PASSWORD_FILE")
        password = self._child_env_value("OPENCLAW_PASSWORD")

        if token:
            raise ValueError("OPENCLAW_TOKEN is not accepted; use OPENCLAW_TOKEN_FILE or configured Gateway auth")
        if password:
            raise ValueError("OPENCLAW_PASSWORD is not accepted; use OPENCLAW_PASSWORD_FILE or configured Gateway auth")

        if token_file:
            cmd.extend(["--token-file", token_file])
        elif password_file:
            cmd.extend(["--password-file", password_file])

        # Leave session routing to OpenClaw by default.  Its ACP bridge creates
        # an isolated acp-bridge:<uuid> key; --session deliberately opts into a
        # shared/attached Gateway session and must therefore be explicit.
        session_key = self._child_env_value("OPENCLAW_SESSION_KEY")
        if session_key:
            cmd.extend(["--session", session_key])

        # Verbose logging
        if (self._child_env_value("OPENCLAW_VERBOSE", "") or "").lower() in ("true", "1", "yes"):
            cmd.append("--verbose")

        return cmd

    async def _initialize(self) -> None:
        """ACP handshake + session creation + thinking level."""
        # 1. Handshake
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

        # 2. Create session
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
        logger.info("OpenClaw session established")

        # 3. Set thinking level (optional)
        thinking = _parse_thinking_level(self.config.model)
        if thinking:
            try:
                await self._rpc(
                    "session/set_mode",
                    {
                        "sessionId": self.session_id,
                        "modeId": thinking,
                    },
                    timeout=self.config.timeout_connect,
                )
                logger.info("OpenClaw thinking level: %s", thinking)
            except RPCError as e:
                logger.warning("session/set_mode failed: %s", exception_summary(e))

    async def resume(self, session_id: str) -> bool:
        """Resume a previous OpenClaw session via ``session/load``."""
        return await self._resume_if_available("session/load", session_id, agent_name="OpenClaw")
