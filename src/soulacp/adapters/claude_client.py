"""Claude-specific ACP client."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_claude_binary
from soulacp.config import ACPConfig
from soulacp.meta import CLIENT_NAME, __version__

logger = logging.getLogger(__name__)


class ClaudeACPClient(ACPClientBase):
    """ACP client for Claude Code CLI.

    Handles Claude-specific initialization, session creation, and resume.

    **Effort level**: defaults to ``auto`` (adaptive thinking — Claude
    Opus 4.7's recommended behavior; model decides depth per step).
    Override via ``ACPConfig.extra_env``::

        ACPConfig(provider="claude", model="claude-acp/opus",
                  extra_env={"CLAUDE_CODE_EFFORT_LEVEL": "high"})

    Levels: ``low`` | ``medium`` | ``high`` | ``xhigh`` | ``max`` | ``auto``.
    (``--effort`` CLI flag is NOT honored — ``claude-code-acp`` does not
    forward process.argv to the underlying claude binary; env var is the
    only reliable control surface.)
    """

    def __init__(self, config: ACPConfig) -> None:
        super().__init__(config)
        self._binary: str | None = None
        self._is_acp: bool = False
        # v0.1.6 (heap retained through 0.1.7): give the Claude Code (Node) subprocess a larger V8 heap by
        # default to prevent long-session OOM crashes (exit code 3221226505 /
        # 0xC0000409 STATUS_STACK_BUFFER_OVERRUN). Only set when the caller
        # hasn't specified NODE_OPTIONS — caller override always wins.
        if self.config.extra_env is None:
            self.config.extra_env = {}
        self.config.extra_env.setdefault("NODE_OPTIONS", "--max-old-space-size=8192")

    def _get_acp_command(self) -> list[str]:
        """Return command to start Claude CLI in ACP mode."""
        self._binary = find_claude_binary()
        if not self._binary:
            raise FileNotFoundError(
                "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code\n"
                "Then authenticate with: claude login"
            )
        self._is_acp = "claude-code-acp" in self._binary.lower()
        return [self._binary] if self._is_acp else [self._binary, "mcp", "serve"]

    async def _initialize(self) -> None:
        """Perform Claude-specific protocol handshake and create a session."""
        protocol_version = 1 if self._is_acp else "1"

        await self._rpc(
            "initialize",
            {
                "clientInfo": {"name": CLIENT_NAME, "version": __version__},
                "protocolVersion": protocol_version,
                "clientCapabilities": {},
            },
            timeout=self.config.timeout_connect,
        )

        # Create session
        res = await self._rpc(
            "session/new",
            {
                "cwd": self.config.cwd or os.getcwd(),
                "mcpServers": [],
                # Raise the Claude Agent SDK per-session turn cap (default ~100,
                # which long per-node AIAP pipelines exceed mid-run). claude-code-acp
                # forwards params._meta.claudeCode.options into the SDK query options.
                "_meta": {"claudeCode": {"options": {"maxTurns": self.config.max_turns}}},
            },
            timeout=self.config.timeout_connect,
        )

        self.session_id = res.get("sessionId") or res.get("session_id")
        if not self.session_id:
            raise ConnectionError("session/new did not return sessionId")
        logger.info("Claude session created: %s", self.session_id)

        # Set the model (e.g. "claude-acp/sonnet" → "sonnet")
        model_id = self.config.model.split("/")[-1] if self.config.model else None
        if model_id:
            try:
                await self._rpc(
                    "session/set_model",
                    {
                        "sessionId": self.session_id,
                        "modelId": model_id,
                    },
                    timeout=self.config.timeout_connect,
                )
                logger.info("Claude model set to: %s", model_id)
            except Exception as e:
                logger.warning("session/set_model not supported, skipping: %s", e)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Claude session via session/resume."""
        try:
            await self._rpc(
                "session/resume",
                {
                    "sessionId": session_id,
                    "cwd": self.config.cwd or os.getcwd(),
                    # Same turn-cap raise on resume — resumed sessions otherwise
                    # fall back to the SDK default (~100).
                    "_meta": {"claudeCode": {"options": {"maxTurns": self.config.max_turns}}},
                },
                timeout=self.config.timeout_connect,
            )
            self.session_id = session_id
            logger.info("Claude session resumed: %s", session_id)
            return True
        except Exception as e:
            logger.warning("Claude session resume failed: %s", e)
            return False
