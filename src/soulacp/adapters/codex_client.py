"""ACP client for OpenAI Codex via codex-acp adapter."""

from __future__ import annotations

import logging

from soulacp.adapters.base_client import ACPClientBase
from soulacp.binary import find_codex_binary
from soulacp.meta import CLIENT_NAME, __version__

logger: logging.Logger = logging.getLogger(__name__)


class CodexACPClient(ACPClientBase):
    """ACP client for OpenAI Codex via codex-acp adapter.

    Requires:
        - codex-acp binary (``npm install -g "@agentclientprotocol/codex-acp@>=2.1.1"``)
        - One of:
            (a) ``codex login`` (ChatGPT account auth — recommended,
                exposes the models enabled for that account)
            (b) API-key authentication configured for the installed bridge.

    Design mirrors ``ClaudeACPClient``: no explicit ``authenticate`` RPC,
    relying on codex-acp to use whatever auth it has locally
    (the configured credential store or API-key authentication path).
    Model selection prefers an advertised model config option; legacy
    ``session/set_model`` is only the compatibility fallback. This does not
    independently attest which model served a request.

    **Reasoning effort**: this adapter injects ``"medium"`` when the setting
    is absent. Override precedence:

    1. ``ACPConfig.extra_args`` (Python code, highest priority)::

           ACPConfig(provider="codex", model="codex-acp/gpt-5.5",
                     extra_args=["-c", 'model_reasoning_effort="high"'])

    2. ``CODEX_REASONING_EFFORT`` in ``ACPConfig.extra_env`` or the process
       environment::

           # Set in the process environment (load any .env in the caller):
           CODEX_REASONING_EFFORT=high

    3. Built-in default ``"medium"`` if neither of the above is set.

    An explicitly empty value suppresses injection. Accepted levels depend on
    the installed bridge/model; SoulACP forwards, rather than validates, them.

    **Permission model**: Codex has TWO independent controls. Both are
    optional. When unset here, SoulACP injects no override; the installed
    runtime and its effective configuration determine the policy:

    - ``CODEX_SANDBOX_MODE`` env var: ``read-only`` | ``workspace-write`` |
      ``danger-full-access``. Controls WHAT Codex can touch.
    - ``CODEX_APPROVAL_POLICY`` env var: provider-supported approval policy,
      such as ``on-request`` or ``never``. Controls WHEN Codex pauses to ask.

    To request workspace writes without approval prompts, set::

        CODEX_SANDBOX_MODE=workspace-write
        CODEX_APPROVAL_POLICY=never

    Optional ``CODEX_NETWORK_ACCESS=true`` opt-in for network access under
    ``workspace-write``. This injects that sandbox's network configuration,
    not a cross-sandbox network grant. Effective enforcement belongs to the
    installed runtime; these settings are not human business authorization.

    These can also be passed via ``ACPConfig.extra_args`` (e.g.
    ``["-c", 'sandbox_mode="workspace-write"']``) which takes precedence.

    Review the runtime's effective policy locally; do not share credentials,
    authentication stores, or raw configuration dumps in diagnostics.
    """

    def _get_acp_command(self) -> list[str]:
        binary = find_codex_binary()
        if not binary:
            raise FileNotFoundError(
                'codex-acp not found. Install with: npm install -g "@agentclientprotocol/codex-acp@>=2.1.1"'
            )
        cmd = [binary]

        # Reasoning effort resolution (precedence high → low):
        #   1. user's extra_args (-c model_reasoning_effort=...)  — explicit Python override
        #   2. CODEX_REASONING_EFFORT child env                   — config / shell control
        #   3. adapter default "medium"; an explicit empty value injects nothing.
        # Accepted values are validated by the installed bridge/runtime.
        user_args_str = " ".join(self.config.extra_args or [])
        if "model_reasoning_effort" not in user_args_str:
            effort = (self._child_env_value("CODEX_REASONING_EFFORT", "medium") or "").strip()
            if effort:
                cmd.extend(["-c", f'model_reasoning_effort="{effort}"'])

        # Sandbox mode (optional — when set, controls what Codex can touch):
        #   read-only
        #   workspace-write (write within workspace, no network)
        #   danger-full-access (full system access, DANGEROUS)
        #
        # No default injection: leave the runtime's effective config in force.
        # Set CODEX_SANDBOX_MODE explicitly to request an override.
        if "sandbox_mode" not in user_args_str:
            sandbox = (self._child_env_value("CODEX_SANDBOX_MODE", "") or "").strip()
            if sandbox:
                cmd.extend(["-c", f'sandbox_mode="{sandbox}"'])

        # Approval policy (optional — when set, controls when Codex stops to ask):
        # Values and enforcement belong to the installed runtime; they are not
        # equivalent to another provider's permission policy or human consent.
        if "approval_policy" not in user_args_str:
            approval = (self._child_env_value("CODEX_APPROVAL_POLICY", "") or "").strip()
            if approval:
                cmd.extend(["-c", f'approval_policy="{approval}"'])

        # Network access in workspace-write sandbox (optional opt-in):
        # Inject only this sandbox's configuration key, not a general network
        # grant. Other sandbox policies remain the runtime's responsibility.
        if "sandbox_workspace_write.network_access" not in user_args_str:
            net = (self._child_env_value("CODEX_NETWORK_ACCESS", "") or "").strip().lower()
            if net in ("true", "1", "yes"):
                cmd.extend(["-c", "sandbox_workspace_write.network_access=true"])

        return cmd

    async def _initialize(self) -> None:
        """Initialize, create a session, then attempt supported model selection."""
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
        #    Auth is implicit in the bridge's effective credential configuration;
        #    an environment API key alone does not select its authentication mode.
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
        logger.info("Codex session established")

        # 4. Prefer an advertised model config option; use the legacy fallback
        #    only when no model option is advertised.
        #    Model id format: "codex-acp/gpt-5.5" → strip prefix → "gpt-5.5".
        #    Availability depends on the active ChatGPT or API credentials;
        #    gpt-5.5 is a compatibility example, not a hard-coded default.
        model_id = self._configured_model_id()
        if model_id:
            await self._set_model_if_supported(model_id)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous Codex session via session/load.

        Per ACP 1.0 spec, session/load requires cwd + mcpServers params
        (same shape as session/new).
        """
        return await self._resume_if_available("session/load", session_id, agent_name="Codex")
