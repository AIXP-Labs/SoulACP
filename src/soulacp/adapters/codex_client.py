"""ACP client for OpenAI Codex via codex-acp adapter."""

from __future__ import annotations

import logging
import os

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_codex_binary
from soulacp.meta import CLIENT_NAME, __version__

logger = logging.getLogger(__name__)


class CodexACPClient(ACPClientBase):
    """ACP client for OpenAI Codex via codex-acp adapter.

    Requires:
        - codex-acp binary (``npm install -g @zed-industries/codex-acp``)
        - One of:
            (a) ``codex login`` (ChatGPT account auth — recommended,
                exposes gpt-5.5 and other ChatGPT-only models)
            (b) ``OPENAI_API_KEY`` env var (API-key auth — codex-acp
                picks it up automatically from environment)

    Design mirrors ``ClaudeACPClient``: no explicit ``authenticate`` RPC,
    relying on codex-acp to use whatever auth it has locally
    (``~/.codex/auth.json`` for ChatGPT or env var for API key).
    Model is set per-session via ``session/set_model`` RPC, so callers
    can switch models without editing ``~/.codex/config.toml``.

    **Reasoning effort**: defaults to ``"medium"`` (OpenAI's recommended
    balance). 3 ways to override, in priority order:

    1. ``ACPConfig.extra_args`` (Python code, highest priority)::

           ACPConfig(provider="codex", model="codex-acp/gpt-5.5",
                     extra_args=["-c", 'model_reasoning_effort="high"'])

    2. ``CODEX_REASONING_EFFORT`` env var (e.g. via ``.env``)::

           # In .env or shell:
           CODEX_REASONING_EFFORT=high

    3. Built-in default ``"medium"`` if neither of the above is set.

    Valid levels (5): ``minimal`` | ``low`` | ``medium`` | ``high`` | ``xhigh``
    (per https://developers.openai.com/codex/config-reference).

    **Permission model**: Codex has TWO independent controls. Both are
    optional — if unset, Codex uses its safer built-in defaults
    (read-only + untrusted prompts):

    - ``CODEX_SANDBOX_MODE`` env var: ``read-only`` | ``workspace-write`` |
      ``danger-full-access``. Controls WHAT Codex can touch.
    - ``CODEX_APPROVAL_POLICY`` env var: ``untrusted`` | ``on-failure`` |
      ``on-request`` | ``never``. Controls WHEN Codex pauses to ask.

    To replicate Claude's ``--permission-mode dontAsk`` for in-workspace
    flows, set in ``.env``::

        CODEX_SANDBOX_MODE=workspace-write
        CODEX_APPROVAL_POLICY=never

    Optional ``CODEX_NETWORK_ACCESS=true`` opt-in for network access under
    ``workspace-write`` (needed for AISOP tools like google_search,
    web_browser, or pip install). Silently no-op under read-only or
    danger-full-access (the latter has network on by default).

    These can also be passed via ``ACPConfig.extra_args`` (e.g.
    ``["-c", 'sandbox_mode="workspace-write"']``) which takes precedence.

    Source: https://developers.openai.com/codex/agent-approvals-security
    """

    def _get_acp_command(self) -> list[str]:
        binary = find_codex_binary()
        if not binary:
            raise FileNotFoundError(
                "codex-acp not found. Install with: "
                "npm install -g @zed-industries/codex-acp"
            )
        cmd = [binary]

        # Reasoning effort resolution (precedence high → low):
        #   1. user's extra_args (-c model_reasoning_effort=...)  — explicit Python override
        #   2. CODEX_REASONING_EFFORT env var                     — .env / shell control
        #   3. default "medium"                                    — OpenAI's recommended balance
        #
        # 5 levels per Codex config-reference (https://developers.openai.com/codex/config-reference):
        #   minimal / low / medium / high / xhigh
        user_args_str = " ".join(self.config.extra_args or [])
        if "model_reasoning_effort" not in user_args_str:
            effort = os.environ.get("CODEX_REASONING_EFFORT", "medium").strip()
            if effort:
                cmd.extend(["-c", f'model_reasoning_effort="{effort}"'])

        # Sandbox mode (optional — when set, controls what Codex can touch):
        #   read-only (Codex default, safe)
        #   workspace-write (write within workspace, no network)
        #   danger-full-access (full system access, DANGEROUS)
        #
        # NO default injection — Codex's own default ("read-only") is safer
        # than anything we'd inject. Set CODEX_SANDBOX_MODE explicitly to
        # opt into write permissions.
        if "sandbox_mode" not in user_args_str:
            sandbox = os.environ.get("CODEX_SANDBOX_MODE", "").strip()
            if sandbox:
                cmd.extend(["-c", f'sandbox_mode="{sandbox}"'])

        # Approval policy (optional — when set, controls when Codex stops to ask):
        #   untrusted (Codex default, prompts for risky ops)
        #   on-failure (only ask when sandbox blocks)
        #   on-request (model decides)
        #   never (NEVER ask — equivalent to Claude --permission-mode dontAsk)
        if "approval_policy" not in user_args_str:
            approval = os.environ.get("CODEX_APPROVAL_POLICY", "").strip()
            if approval:
                cmd.extend(["-c", f'approval_policy="{approval}"'])

        # Network access in workspace-write sandbox (optional opt-in):
        # workspace-write defaults to no network; needed for AISOP tools like
        # google_search / web_browser, or for pip install during AIAP runs.
        # Only meaningful when sandbox_mode == "workspace-write" (Codex
        # silently ignores when sandbox_mode == "read-only" or
        # "danger-full-access" — the latter has network on by default).
        if "sandbox_workspace_write.network_access" not in user_args_str:
            net = os.environ.get("CODEX_NETWORK_ACCESS", "").strip().lower()
            if net in ("true", "1", "yes"):
                cmd.extend(["-c", "sandbox_workspace_write.network_access=true"])

        return cmd

    async def _initialize(self) -> None:
        """Perform Codex ACP handshake: initialize → initialized → session/new → set_model."""
        # 1. initialize
        await self._rpc(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": CLIENT_NAME, "version": __version__},
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": True,
                },
            },
            timeout=self.config.timeout_connect,
        )

        # 2. initialized notification (no ID, no response — standard ACP)
        await self._send_notification("initialized")

        # 3. session/new — codex-acp 0.14+ requires mcpServers field.
        #    Auth is implicit: codex-acp uses ~/.codex/auth.json (ChatGPT)
        #    or OPENAI_API_KEY env var, whichever is available.
        result = await self._rpc(
            "session/new",
            {
                "cwd": self.config.cwd,
                "mcpServers": [],
            },
            timeout=self.config.timeout_connect,
        )
        self.session_id = result.get("sessionId") or result.get("session_id")
        if not self.session_id:
            raise ConnectionError("session/new did not return sessionId")
        logger.info("Codex connected: session=%s", self.session_id)

        # 4. session/set_model — set per-session model (mirrors Claude pattern).
        #    Model id format: "codex-acp/gpt-5.5" → strip prefix → "gpt-5.5".
        #    Available with ChatGPT auth: gpt-5.5 (recommended).
        #    Available with API key: gpt-5.5-codex / o3 / o4-mini / etc.
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
                logger.info("Codex model set to: %s", model_id)
            except Exception as e:
                logger.warning(
                    "session/set_model not supported, falling back to config.toml default: %s", e
                )

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Codex session via session/load.

        Per ACP 1.0 spec, session/load requires cwd + mcpServers params
        (same shape as session/new).
        """
        try:
            await self._rpc(
                "session/load",
                {
                    "sessionId": session_id,
                    "cwd": self.config.cwd,
                    "mcpServers": [],
                },
                timeout=self.config.timeout_connect,
            )
            self.session_id = session_id
            return True
        except Exception as e:
            logger.debug("Codex session resume failed for %s: %s", session_id, e)
            return False
