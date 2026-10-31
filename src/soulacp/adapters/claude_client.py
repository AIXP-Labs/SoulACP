"""Claude-specific ACP client."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from soulacp.adapters.base_client import ACPClientBase, RPCError
from soulacp.binary import find_claude_binary
from soulacp.meta import CLIENT_NAME, __version__
from soulacp.privacy import exception_summary, sensitive_logging_enabled

logger: logging.Logger = logging.getLogger(__name__)

_MODEL_CONTEXT_HINT_RE = re.compile(r"(?:\[\d+m\]|-\d+m\b)", re.IGNORECASE)
_MODEL_FAMILY_VERSION_RE = re.compile(
    r"\b(opus|sonnet|haiku|fable)[^a-z0-9]+(\d+)(?:[-.\s](\d+))?",
    re.IGNORECASE,
)


def _canonical_model_id(value: str) -> str:
    """Normalize model labels while retaining the model generation."""
    without_context = _MODEL_CONTEXT_HINT_RE.sub("", value.strip().lower())
    return re.sub(r"[^a-z0-9]+", "", without_context)


def _model_family_version(value: str) -> tuple[str | None, str | None]:
    """Extract a Claude family and optional generation from an ACP label."""
    without_context = _MODEL_CONTEXT_HINT_RE.sub("", value.lower())
    family = next(
        (candidate for candidate in ("opus", "sonnet", "haiku", "fable") if candidate in without_context),
        None,
    )
    match = _MODEL_FAMILY_VERSION_RE.search(without_context)
    if match is None:
        return family, None
    major, minor = match.group(2), match.group(3)
    return match.group(1).lower(), f"{major}.{minor}" if minor else major


class ClaudeACPClient(ACPClientBase):
    """ACP client for Claude Code CLI.

    Handles Claude-specific initialization, session creation, and resume.

    **Effort level**: this adapter does not inject a default. The installed
    bridge/runtime and its configuration determine the effective value.
    Request a provider-supported level via ``ACPConfig.extra_env``::

        ACPConfig(provider="claude", model="claude-acp/opus",
                  extra_env={"CLAUDE_CODE_EFFORT_LEVEL": "high"})

    Accepted values depend on the bridge and model; SoulACP does not validate
    this provider-specific enum. The variable is passed in the child environment,
    not by assuming the bridge forwards a provider CLI's ``--effort`` flag.
    Do not include credentials or raw environment values in shared diagnostics.
    """

    def _get_acp_command(self) -> list[str]:
        """Return command to start Claude CLI in ACP mode."""
        self._binary = find_claude_binary()
        if not self._binary:
            raise FileNotFoundError(
                'Claude ACP bridge not found. Install with: npm install -g "@agentclientprotocol/claude-agent-acp@>=0.86.0"'
            )
        return [self._binary]

    def _get_acp_env(self) -> dict[str, str]:
        """Pin the configured model before Claude creates or resumes a session.

        Provide ``ANTHROPIC_MODEL`` before new/resume so the bridge can use it
        when constructing a session. ``ACPConfig.extra_env`` is applied later
        and can override it. Picker matching remains best effort, so this is
        not a strict guarantee of the active model or its underlying identity.
        """
        model_id = self._configured_model_id()
        return {"ANTHROPIC_MODEL": model_id} if model_id else {}

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
        session_params: dict[str, Any] = {
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
        if self._model_option_is_selected(model_id, model_opt):
            if sensitive_logging_enabled():
                logger.info("Claude model active: requested=%r selected=%r", model_id, current)
            else:
                logger.info("Configured Claude model is already selected")
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
            if sensitive_logging_enabled():
                logger.warning(
                    "set_config_option(model=%r) rejected; using provider default: %s", chosen, exception_summary(exc)
                )
            else:
                logger.warning("set_config_option(model) rejected; using provider default: %s", exception_summary(exc))
            return False
        if sensitive_logging_enabled():
            logger.info("ACP model selected via config option: %r", chosen)
        else:
            logger.info("ACP model selected via config option")
        return True

    @staticmethod
    def _model_option_is_selected(model_id: str, model_opt: dict[str, Any]) -> bool:
        """Return whether an advertised picker value represents ``model_id``.

        Claude's picker may expose ``opus[1m]`` while the caller pins
        ``claude-opus-4-8``. The selected option's description carries the
        resolved generation, so compare family and generation without treating
        the context-window hint as a model version.
        """
        current = model_opt.get("currentValue")
        if not isinstance(current, str):
            return False
        if _canonical_model_id(current) == _canonical_model_id(model_id):
            return True

        selected = next(
            (
                option
                for option in model_opt.get("options", [])
                if isinstance(option, dict) and option.get("value") == current
            ),
            None,
        )
        labels = [current]
        if selected is not None:
            labels.extend(value for key in ("name", "description") if isinstance((value := selected.get(key)), str))
        candidate = " ".join(labels)
        requested_family, requested_version = _model_family_version(model_id)
        selected_family, selected_version = _model_family_version(candidate)
        if requested_family is None or requested_family != selected_family:
            return False
        if requested_version is None:
            return True
        return requested_version == selected_version

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Claude session via session/resume."""
        extra_params = None
        if self.config.max_turns is not None:
            extra_params = {"_meta": {"claudeCode": {"options": {"maxTurns": self.config.max_turns}}}}
        resumed = await self._resume_if_available(
            "session/resume",
            session_id,
            agent_name="Claude",
            extra_params=extra_params,
        )
        if not resumed:
            return False

        # Current claude-agent-acp versions reassert ANTHROPIC_MODEL during
        # resume. Verify the returned state and retain config-option fallback
        # for older bridges instead of silently keeping the transcript model.
        model_id = self._configured_model_id()
        if model_id:
            await self._select_model_config_option(model_id)
        return True
