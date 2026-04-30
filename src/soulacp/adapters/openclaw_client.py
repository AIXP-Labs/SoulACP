"""OpenClaw ACP client — Gateway-backed AI assistant."""

from __future__ import annotations

import asyncio
import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_openclaw_binary
from soulacp.meta import CLIENT_NAME, __version__

logger = logging.getLogger(__name__)

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
        url = os.environ.get("OPENCLAW_URL")
        if url:
            cmd.extend(["--url", url])

        # Authentication (token-file > token > password-file > password)
        token_file = os.environ.get("OPENCLAW_TOKEN_FILE")
        token = os.environ.get("OPENCLAW_TOKEN")
        password_file = os.environ.get("OPENCLAW_PASSWORD_FILE")
        password = os.environ.get("OPENCLAW_PASSWORD")

        if token_file:
            cmd.extend(["--token-file", token_file])
        elif token:
            cmd.extend(["--token", token])
        elif password_file:
            cmd.extend(["--password-file", password_file])
        elif password:
            cmd.extend(["--password", password])

        # Session key — required to route prompts to an agent.
        # Defaults to "agent:main:main" (the default OpenClaw agent).
        session_key = os.environ.get("OPENCLAW_SESSION_KEY", "agent:main:main")
        cmd.extend(["--session", session_key])

        # Verbose logging
        if os.environ.get("OPENCLAW_VERBOSE", "").lower() in ("true", "1", "yes"):
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
                "clientCapabilities": {},
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
        logger.info("OpenClaw session created: %s", self.session_id)

        # 2b. Wait for Gateway WebSocket to fully connect.
        # The ACP bridge connects to Gateway asynchronously after startup;
        # session/new succeeds locally but prompt will fail with
        # "gateway not connected" if we send it too early.
        # Use a ping-based readiness check with exponential backoff
        # instead of a fixed sleep.
        for attempt in range(6):  # 0.5 + 1 + 1.5 + 2 + 2.5 + 3 = 10.5s max
            await asyncio.sleep(0.5 + attempt * 0.5)
            if await self.ping():
                logger.info("OpenClaw Gateway ready after %.1fs", 0.5 + attempt * 0.5)
                break
        else:
            logger.warning("OpenClaw Gateway readiness check timed out, proceeding anyway")

        # 3. Set thinking level (optional)
        thinking = _parse_thinking_level(self.config.model)
        if thinking and thinking != "off":
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
            except Exception as e:
                logger.warning("session/set_mode failed: %s", e)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous OpenClaw session via ``session/load``."""
        try:
            await self._rpc(
                "session/load",
                {
                    "sessionId": session_id,
                    "cwd": self.config.cwd or os.getcwd(),
                },
                timeout=self.config.timeout_connect,
            )
            self.session_id = session_id
            logger.info("OpenClaw session loaded: %s", session_id)
            return True
        except Exception as e:
            logger.warning("OpenClaw session load failed: %s", e)
            return False
