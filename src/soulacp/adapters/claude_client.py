"""Claude-specific ACP client."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase, RPCError
from soulacp.binary import find_claude_binary
from soulacp.meta import CLIENT_NAME, __version__
from soulacp.privacy import exception_summary

logger: logging.Logger = logging.getLogger(__name__)


class ClaudeACPClient(ACPClientBase):
    """ACP client for Claude Code CLI.

    Handles Claude-specific initialization, session creation, and resume.

    **Effort level**: defaults to ``auto`` (adaptive thinking — Claude
    Opus 4.7's recommended behavior; model decides depth per step).
    Override via ``ACPConfig.extra_env``::

        ACPConfig(provider="claude", model="claude-acp/opus",
                  extra_env={"CLAUDE_CODE_EFFORT_LEVEL": "high"})

    Levels: ``low`` | ``medium`` | ``high`` | ``xhigh`` | ``max`` | ``auto``.
    ACP bridges do not consistently forward ``--effort`` to the underlying
    Claude runtime, so the environment variable is the reliable control surface.
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Claude CLI in ACP mode."""
        self._binary = find_claude_binary()
        if not self._binary:
            raise FileNotFoundError(
                "Claude ACP bridge not found. Install with: npm install -g @agentclientprotocol/claude-agent-acp"
            )
        return [self._binary]

    async def _initialize(self) -> None:
        """Perform Claude-specific protocol handshake and create a session."""
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

        # Create session
        session_params = {
            "cwd": self.config.cwd or os.getcwd(),
            "mcpServers": [],
        }
        if self.config.max_turns is not None:
            session_params["_meta"] = {"claudeCode": {"options": {"maxTurns": self.config.max_turns}}}
        res = await self._rpc("session/new", session_params, timeout=self.config.timeout_connect)

        self.session_id = res.get("sessionId") or res.get("session_id")
        if not self.session_id:
            raise ConnectionError("session/new did not return sessionId")
        logger.info("Claude session established")

        # Select the model (e.g. "claude-acp/sonnet" → "sonnet").
        # ACP has no session/set_model; the bridge exposes the model as a
        # config option (configId="model") validated against advertised values.
        model_id = self._configured_model_id()
        if model_id:
            await self._select_model_config_option(model_id)

    async def _select_model_config_option(self, model_id: str) -> bool:
        """Best-effort model selection via ``session/set_config_option``.

        Missing configOptions, no "model" option, no unambiguous value match,
        or a provider RPC rejection degrades to the provider default. Transport,
        timeout, and protocol failures remain visible to initialization callers.
        """
        model_opt = next(
            (
                option
                for option in self.session_config_options
                if option.get("type") == "select" and option.get("category") == "model"
            ),
            None,
        )
        if model_opt is None:
            model_opt = next(
                (
                    option
                    for option in self.session_config_options
                    if option.get("type") == "select" and option.get("id") in {"model", "models"}
                ),
                None,
            )
        if model_opt is None:
            logger.debug("No 'model' config option advertised; using provider default")
            return False

        config_id = model_opt["id"]
        current = model_opt["currentValue"]
        if current == model_id:
            logger.debug("Configured Claude model is already selected")
            return True

        values = self._select_option_values(model_opt["options"], config_id=config_id)
        chosen = model_id if model_id in values else None
        if chosen is None:
            matches = sorted(value for value in values if model_id in value or value in model_id)
            chosen = matches[0] if len(matches) == 1 else None
        if chosen is None:
            logger.warning("Configured Claude model was not unambiguously advertised; using the current provider model")
            return False
        if chosen == current:
            logger.debug("Configured Claude model alias resolves to the current model")
            return True
        try:
            await self.set_config_option(config_id, chosen)
        except RPCError as exc:
            logger.warning(
                "set_config_option(model=%s) rejected; using provider default: %s",
                chosen,
                exception_summary(exc),
            )
            return False
        logger.info("ACP model selected via config option: %s", chosen)
        return True

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Claude session via session/resume."""
        extra_params = None
        if self.config.max_turns is not None:
            extra_params = {"_meta": {"claudeCode": {"options": {"maxTurns": self.config.max_turns}}}}
        return await self._resume_if_available(
            "session/resume",
            session_id,
            agent_name="Claude",
            extra_params=extra_params,
        )
