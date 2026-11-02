"""ACP connection configuration and provider routing."""

from __future__ import annotations

import copy
import json
import math
import os
import re
from dataclasses import dataclass, field
from ipaddress import IPv4Address, ip_address
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from soulacp.privacy import _raise_private_cause

if TYPE_CHECKING:
    from soulacp.adapters.protocol import ACPClient

_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _is_finite_number(value: int | float) -> bool:
    """Return whether a numeric policy value fits a finite runtime float."""
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _is_absolute_path(value: str) -> bool:
    """Recognize native, POSIX, and Windows absolute paths lexically."""
    return Path(value).is_absolute() or PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _require_valid_unicode(value: str, label: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must contain valid Unicode") from exc


def _require_non_empty_string(value: Any, label: str, *, forbid_nul: bool = True) -> str:
    if not isinstance(value, str) or not value or (forbid_nul and "\x00" in value):
        suffix = " without NUL bytes" if forbid_nul else ""
        raise ValueError(f"{label} must be a non-empty string{suffix}")
    _require_valid_unicode(value, label)
    return value


def _validate_name_value_entries(
    value: Any,
    label: str,
    *,
    environment: bool = False,
    http_headers: bool = False,
) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValueError(f"{label}[{index}] must be an object")
        name = _require_non_empty_string(entry.get("name"), f"{label}[{index}].name")
        if environment and "=" in name:
            raise ValueError(f"{label}[{index}].name must not contain '='")
        if http_headers and _HTTP_HEADER_NAME.fullmatch(name) is None:
            raise ValueError(f"{label}[{index}].name must be a valid HTTP field name")
        entry_value = entry.get("value")
        if not isinstance(entry_value, str) or "\x00" in entry_value:
            raise ValueError(f"{label}[{index}].value must be a string without NUL bytes")
        _require_valid_unicode(entry_value, f"{label}[{index}].value")
        if http_headers and ("\r" in entry_value or "\n" in entry_value):
            raise ValueError(f"{label}[{index}].value must not contain CR or LF")


def _validate_bracketed_url_host(netloc: str, hostname: str | None) -> None:
    """Require IP literals even on Python versions with permissive URL parsing."""
    if "[" not in netloc:
        return
    if not hostname:
        raise ValueError("Invalid bracketed URL host")
    if hostname.startswith("v"):
        if re.fullmatch(r"v[a-fA-F0-9]+\..+", hostname) is None:
            raise ValueError("Invalid IPvFuture URL host")
    elif isinstance(ip_address(hostname), IPv4Address):
        raise ValueError("IPv4 URL hosts must not use brackets")


def _validate_remote_mcp_url(value: Any, label: str) -> str:
    url = _require_non_empty_string(value, label)
    if url != url.strip():
        raise ValueError(f"{label} must not contain surrounding whitespace")
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in url) or "\\" in url:
        raise ValueError(f"{label} must not contain whitespace, control characters, or backslashes")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        _validate_bracketed_url_host(parsed.netloc, parsed.hostname)
    except ValueError:
        raise ValueError(f"{label} must be a valid HTTP(S) URL") from None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or (port is not None and port < 1):
        raise ValueError(f"{label} must be an absolute HTTP(S) URL with a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain embedded credentials; use headers")
    return url


def _validate_strict_json_value(value: Any, label: str) -> None:
    """Require the in-memory shape to use JSON types without coercion."""
    pending = [(value, False)]
    ancestors: set[int] = set()
    while pending:
        item, exiting = pending.pop()
        if exiting:
            ancestors.remove(id(item))
            continue
        if item is None or isinstance(item, (bool, int)):
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"{label} numbers must be finite")
            continue
        if isinstance(item, str):
            _require_valid_unicode(item, label)
            continue
        if isinstance(item, (list, dict)):
            identity = id(item)
            if identity in ancestors:
                raise ValueError(f"{label} must not contain cycles")
            ancestors.add(identity)
            pending.append((item, True))
            if isinstance(item, list):
                pending.extend((nested, False) for nested in item)
                continue
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise ValueError(f"{label} object keys must be strings")
                _require_valid_unicode(key, label)
                pending.append((nested, False))
            continue
        raise ValueError(f"{label} must contain only JSON values")


def _validate_mcp_servers(value: Any) -> list[dict[str, Any]]:
    """Validate and detach ACP wire-shaped MCP server definitions."""
    if not isinstance(value, list):
        raise ValueError("mcp_servers must be a list")

    try:
        validated = copy.deepcopy(value)
        _validate_strict_json_value(validated, "mcp_servers")
        json.dumps(validated, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except Exception as exc:
        raise ValueError("mcp_servers must contain finite JSON values and valid Unicode") from exc
    for index, server in enumerate(validated):
        label = f"mcp_servers[{index}]"
        if not isinstance(server, dict):
            raise ValueError(f"{label} must be an object")
        _require_non_empty_string(server.get("name"), f"{label}.name")
        meta = server.get("_meta")
        if meta is not None and not isinstance(meta, dict):
            raise ValueError(f"{label}._meta must be an object or null")

        transport = server.get("type")
        if transport in (None, "stdio"):
            command = _require_non_empty_string(server.get("command"), f"{label}.command")
            if not _is_absolute_path(command):
                raise ValueError(f"{label}.command must be an absolute path")
            args = server.get("args")
            if not isinstance(args, list) or not all(isinstance(arg, str) and "\x00" not in arg for arg in args):
                raise ValueError(f"{label}.args must be a list of strings without NUL bytes")
            for arg in args:
                _require_valid_unicode(arg, f"{label}.args entry")
            _validate_name_value_entries(server.get("env"), f"{label}.env", environment=True)
            # ACP treats an absent discriminator as stdio. Emit the canonical
            # wire shape even when callers use the explicit ergonomic spelling.
            server.pop("type", None)
            continue

        if transport not in {"http", "sse"}:
            raise ValueError(f"{label}.type must be 'http', 'sse', 'stdio', or omitted")
        _validate_remote_mcp_url(server.get("url"), f"{label}.url")
        _validate_name_value_entries(server.get("headers"), f"{label}.headers", http_headers=True)

    return validated


def _model_id_for_provider(model: str, provider: str) -> str:
    """Remove only soulacp's leading provider namespace from a model ID."""
    lowered = model.lower()
    for prefix in (f"{provider}-acp/", f"{provider}/"):
        if lowered.startswith(prefix.lower()):
            return model[len(prefix) :]
    return model


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------


def resolve_provider(model: str) -> str:
    """Extract provider name from a model identifier.

    Examples:
        ``claude-acp/sonnet`` → ``claude``
        ``gemini-acp/pro`` → ``gemini``
        ``opencode-acp/default`` → ``opencode``
        ``cursor-acp/default`` → ``cursor``
        ``cursor-cli/gpt-4`` → ``cursor-cli``
        ``codex-acp/gpt-5`` → ``codex``
        ``qwen-acp/qwen-coder`` → ``qwen``
        ``kimi-acp/default`` → ``kimi``
        ``codebuddy-acp/default`` → ``codebuddy``
        ``cline-acp/default`` → ``cline``
        ``copilot-acp/default`` → ``copilot``
        ``minion-acp/default`` → ``minion``
        ``vibe-acp/default`` → ``vibe``
        ``nova-acp/default`` → ``nova``
        ``crow-acp/default`` → ``crow``
    """
    if not isinstance(model, str) or not model or model != model.strip() or "\x00" in model:
        raise ValueError("Cannot resolve provider from model")
    _require_valid_unicode(model, "model")
    model_lower = model.lower()
    providers = (
        "cursor-cli",
        "copilot-ls",
        "claude",
        "gemini",
        "opencode",
        "openclaw",
        "cursor",
        "codex",
        "qwen",
        "kimi",
        "codebuddy",
        "cline",
        "copilot",
        "minion",
        "vibe",
        "nova",
        "crow",
        "amp",
        "auggie",
        "autohand",
        "corust",
        "deepagents",
        "droid",
        "fastagent",
        "goose",
        "junie",
        "kilo",
        "pi",
        "qoder",
        "stakpak",
    )
    for provider in providers:
        if model_lower == provider or model_lower.startswith((f"{provider}-", f"{provider}/")):
            return provider
    raise ValueError("Cannot resolve provider from model")


def resolve_client_class(provider: str) -> type[ACPClient]:
    """Return the client class for a given provider name.

    Lazy imports to avoid circular dependencies.
    """
    if provider == "claude":
        from soulacp.adapters.claude_client import ClaudeACPClient

        return ClaudeACPClient
    if provider == "gemini":
        from soulacp.adapters.gemini_client import GeminiACPClient

        return GeminiACPClient
    if provider == "opencode":
        from soulacp.adapters.opencode_client import OpenCodeACPClient

        return OpenCodeACPClient
    if provider == "openclaw":
        from soulacp.adapters.openclaw_client import OpenClawACPClient

        return OpenClawACPClient
    if provider == "cursor":
        from soulacp.adapters.cursor_acp_client import CursorACPClient

        return CursorACPClient
    if provider == "cursor-cli":
        from soulacp.adapters.cursor_client import CursorCLIClient

        return CursorCLIClient
    if provider == "codex":
        from soulacp.adapters.codex_client import CodexACPClient

        return CodexACPClient
    if provider == "qwen":
        from soulacp.adapters.qwen_client import QwenACPClient

        return QwenACPClient
    if provider == "kimi":
        from soulacp.adapters.kimi_client import KimiACPClient

        return KimiACPClient
    if provider == "codebuddy":
        from soulacp.adapters.codebuddy_client import CodebuddyACPClient

        return CodebuddyACPClient
    if provider == "cline":
        from soulacp.adapters.cline_client import ClineACPClient

        return ClineACPClient
    if provider == "copilot":
        from soulacp.adapters.copilot_client import CopilotACPClient

        return CopilotACPClient
    if provider == "minion":
        from soulacp.adapters.minion_client import MinionACPClient

        return MinionACPClient
    if provider == "vibe":
        from soulacp.adapters.vibe_client import VibeACPClient

        return VibeACPClient
    if provider == "nova":
        from soulacp.adapters.nova_client import NovaACPClient

        return NovaACPClient
    if provider == "crow":
        from soulacp.adapters.crow_client import CrowACPClient

        return CrowACPClient
    if provider == "amp":
        from soulacp.adapters.amp_client import AmpACPClient

        return AmpACPClient
    if provider == "auggie":
        from soulacp.adapters.auggie_client import AuggieACPClient

        return AuggieACPClient
    if provider == "autohand":
        from soulacp.adapters.autohand_client import AutohandACPClient

        return AutohandACPClient
    if provider == "corust":
        from soulacp.adapters.corust_client import CorustACPClient

        return CorustACPClient
    if provider == "deepagents":
        from soulacp.adapters.deepagents_client import DeepAgentsACPClient

        return DeepAgentsACPClient
    if provider == "droid":
        from soulacp.adapters.droid_client import DroidACPClient

        return DroidACPClient
    if provider == "fastagent":
        from soulacp.adapters.fastagent_client import FastAgentACPClient

        return FastAgentACPClient
    if provider == "copilot-ls":
        from soulacp.adapters.copilot_ls_client import CopilotLSACPClient

        return CopilotLSACPClient
    if provider == "goose":
        from soulacp.adapters.goose_client import GooseACPClient

        return GooseACPClient
    if provider == "junie":
        from soulacp.adapters.junie_client import JunieACPClient

        return JunieACPClient
    if provider == "kilo":
        from soulacp.adapters.kilo_client import KiloACPClient

        return KiloACPClient
    if provider == "pi":
        from soulacp.adapters.pi_client import PiACPClient

        return PiACPClient
    if provider == "qoder":
        from soulacp.adapters.qoder_client import QoderACPClient

        return QoderACPClient
    if provider == "stakpak":
        from soulacp.adapters.stakpak_client import StakpakACPClient

        return StakpakACPClient
    raise ValueError("Unknown provider")


# ---------------------------------------------------------------------------
# Fallback configuration
# ---------------------------------------------------------------------------

# Format: provider → "fallback_provider-acp/fallback_model"
# Parsed by resolve_provider() to extract provider name.
FALLBACK_MAP: dict[str, str] = {
    "claude": "gemini-acp/gemini-2.5-flash",
    "gemini": "claude-acp/sonnet",
    "opencode": "claude-acp/sonnet",
    "openclaw": "claude-acp/sonnet",
    "cursor": "claude-acp/sonnet",
    "cursor-cli": "claude-acp/sonnet",
    "codex": "claude-acp/sonnet",
    "qwen": "claude-acp/sonnet",
    "kimi": "claude-acp/sonnet",
    "codebuddy": "claude-acp/sonnet",
    "cline": "claude-acp/sonnet",
    "copilot": "claude-acp/sonnet",
    "minion": "claude-acp/sonnet",
    "vibe": "claude-acp/sonnet",
    "nova": "claude-acp/sonnet",
    "crow": "claude-acp/sonnet",
    "amp": "claude-acp/sonnet",
    "auggie": "claude-acp/sonnet",
    "autohand": "claude-acp/sonnet",
    "corust": "claude-acp/sonnet",
    "deepagents": "claude-acp/sonnet",
    "droid": "claude-acp/sonnet",
    "fastagent": "claude-acp/sonnet",
    "copilot-ls": "claude-acp/sonnet",
    "goose": "claude-acp/sonnet",
    "junie": "claude-acp/sonnet",
    "kilo": "claude-acp/sonnet",
    "pi": "claude-acp/sonnet",
    "qoder": "claude-acp/sonnet",
    "stakpak": "claude-acp/sonnet",
}


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class ACPConfig:
    """Configuration for an ACP CLI subprocess connection.

    ``ACPConfig()`` uses explicit arguments and dataclass defaults.
    ``ACPConfig.from_env()`` reads process variables with the ``ACP_`` prefix
    (e.g. ``ACP_POOL_SIZE=5``); explicit keyword overrides take precedence.
    Neither constructor loads ``.env`` files.
    """

    provider: str = "claude"
    """Provider name — see README for full list. 30 providers including cursor-cli (legacy)."""

    model: str = "claude-acp/sonnet"
    """Namespaced model identifier forwarded to the selected provider adapter.

    It requests model selection, not independent attestation of the model used.
    """

    pool_size: int = 5
    """Maximum number of idle connections kept in the pool."""

    pool_max_connections: int = 8
    """Hard limit for open plus currently-creating connections in this pool instance."""

    pool_acquire_timeout: float = 30
    """Maximum seconds spent waiting for capacity before acquisition fails."""

    pool_idle_timeout: float = 600
    """Seconds since release before an idle pooled connection is retired."""

    pool_keepalive_interval: float = 300
    """Keepalive check interval in seconds (5min). 0 = disabled."""

    timeout_connect: float = 30
    """Timeout in seconds for subprocess startup + initialize."""

    timeout_prompt: float = 21600
    """End-to-end timeout in seconds for a single prompt/response cycle (6 hours)."""

    timeout_stream: float = 1800
    """Maximum seconds without any ACP stream activity (30 minutes)."""

    pool_max_age: float = 7200
    """Maximum connection age in seconds before soft retirement on a lease boundary."""

    pool_max_uses: int = 50
    """Maximum successful leases before soft retirement."""

    pool_cross_session_reuse: bool = False
    """Allow an idle transport bound to another session to be resumed explicitly."""

    cwd: str = field(default_factory=os.getcwd, repr=False)
    """Working directory for the CLI subprocess."""

    inherit_parent_env: bool = True
    """Inherit the full parent environment in provider and terminal children.

    Set to ``False`` to retain only a bounded runtime/path/locale allowlist;
    provider credentials must then come from ``extra_env`` or external stores.
    This limits environment propagation but is not an OS sandbox.
    """

    additional_directories: list[str] = field(default_factory=list, repr=False)
    """Ordered additional absolute workspace roots for ACP session setup.

    Non-empty values require the agent to advertise
    ``sessionCapabilities.additionalDirectories``.
    """

    mcp_servers: list[dict[str, Any]] = field(default_factory=list, repr=False)
    """ACP wire-shaped MCP server definitions supplied to session lifecycle calls.

    Stdio definitions require ``name``, an absolute ``command``, ``args``, and
    ``env``. HTTP/SSE definitions require ``type``, ``name``, ``url``, and
    ``headers``. Structured fields intentionally have no environment-variable
    parser; construct them explicitly so quoting and secrets remain unambiguous.
    """

    auto_approve_permissions: bool = True
    """Select allow or reject options for ACP permission requests.

    This is not a global host-service deny switch or human business authorization.
    """

    enable_fallback: bool = False
    """Allow configured fallback before prompt execution, not after unknown outcomes."""

    max_retries: int = 3
    """Total pre-prompt attempts for transient errors (minimum one)."""

    retry_base_delay: float = 1.0
    """Base delay in seconds for safe pre-prompt exponential backoff."""

    max_turns: int | None = None
    """Optional Claude-specific per-session turn cap.

    When set, the Claude adapter forwards it through
    ``_meta.claudeCode.options.maxTurns``. ``None`` preserves the provider's
    own default. Other adapters ignore this field.
    """

    extra_args: list[str] = field(default_factory=list, repr=False)
    """Extra arguments appended verbatim to the CLI subprocess command.

    Useful for adapter-specific tunables not yet exposed as typed fields.
    Examples:
      - Codex reasoning effort:
          extra_args=["-c", 'model_reasoning_effort="high"']
      - Codex sandbox policy:
          extra_args=["-c", 'sandbox_mode="workspace-write"']
      - Any other ``-c key=value`` overrides supported by the target CLI.

    Empty list = no extra args (default). Effective sandbox and approval policy
    belong to the installed runtime; arguments do not establish human consent.
    """

    extra_env: dict[str, str] = field(default_factory=dict, repr=False)
    """Extra environment variables passed to the CLI subprocess.

    Use only variables supported by the installed bridge/runtime. Passing an
    environment variable does not prove that a provider applied it.

    Examples:
      - Claude Code effort level:
          extra_env={"CLAUDE_CODE_EFFORT_LEVEL": "high"}
      - Codex reasoning (env-var route, alternative to -c flag):
          extra_env={"CODEX_REASONING_EFFORT": "high"}
      - Any custom env vars your CLI reads.

    Merged into the selected subprocess environment at launch — user-supplied
    keys override inherited and adapter-provided values, including
    ``ANTHROPIC_MODEL``. Empty dict = no overrides (default). Do not log this
    mapping or copy real credentials into examples or issue reports.
    """

    auth_method_id: str | None = field(default=None, repr=False)
    """Opt-in exact advertised authentication method, bound to this provider.

    None preserves adapter defaults. Explicit failures never authorize another
    method or provider. Authentication may still change native credential state.
    This field is deliberately not loaded from process environment variables.
    """

    _auth_provider: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        integer_fields = {
            "pool_size": self.pool_size,
            "pool_max_connections": self.pool_max_connections,
            "pool_max_uses": self.pool_max_uses,
            "max_retries": self.max_retries,
        }
        if self.max_turns is not None:
            integer_fields["max_turns"] = self.max_turns
        for name, value in integer_fields.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")

        number_fields = {
            "pool_acquire_timeout": self.pool_acquire_timeout,
            "pool_idle_timeout": self.pool_idle_timeout,
            "pool_keepalive_interval": self.pool_keepalive_interval,
            "pool_max_age": self.pool_max_age,
            "timeout_connect": self.timeout_connect,
            "timeout_prompt": self.timeout_prompt,
            "timeout_stream": self.timeout_stream,
            "retry_base_delay": self.retry_base_delay,
        }
        for name, value in number_fields.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number")

        for name in (
            "pool_cross_session_reuse",
            "inherit_parent_env",
            "auto_approve_permissions",
            "enable_fallback",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        if (
            not isinstance(self.provider, str)
            or not self.provider
            or self.provider != self.provider.strip()
            or "\x00" in self.provider
        ):
            raise ValueError("provider must be a non-empty string")
        _require_valid_unicode(self.provider, "provider")
        if self.auth_method_id is not None:
            try:
                _require_non_empty_string(self.auth_method_id, "auth_method_id")
            except ValueError as exc:
                _raise_private_cause(
                    ValueError("auth_method_id must be a non-empty valid Unicode string without NUL"), exc
                )
            if self._auth_provider is not None and self._auth_provider != self.provider:
                raise ValueError("auth_method_id is bound to its original provider")
            self._auth_provider = self.provider
        else:
            self._auth_provider = None
        if (
            not isinstance(self.model, str)
            or not self.model
            or self.model != self.model.strip()
            or "\x00" in self.model
        ):
            raise ValueError("model must be a non-empty string")
        _require_valid_unicode(self.model, "model")
        if not isinstance(self.cwd, str) or not self.cwd or not self.cwd.strip() or "\x00" in self.cwd:
            raise ValueError("cwd must be a non-empty string")
        _require_valid_unicode(self.cwd, "cwd")
        if not _is_absolute_path(self.cwd):
            self.cwd = str(Path(self.cwd).expanduser().resolve())
        if not isinstance(self.additional_directories, list):
            raise ValueError("additional_directories must be a list of absolute paths")
        for index, path in enumerate(self.additional_directories):
            if not isinstance(path, str) or not path or "\x00" in path or not _is_absolute_path(path):
                raise ValueError(f"additional_directories[{index}] must be a non-empty absolute path")
            _require_valid_unicode(path, f"additional_directories[{index}]")
        if not isinstance(self.extra_args, list) or not all(
            isinstance(arg, str) and "\x00" not in arg for arg in self.extra_args
        ):
            raise ValueError("extra_args must be a list of strings without NUL bytes")
        if not isinstance(self.extra_env, dict) or not all(
            isinstance(key, str)
            and bool(key)
            and "=" not in key
            and "\x00" not in key
            and isinstance(value, str)
            and "\x00" not in value
            for key, value in self.extra_env.items()
        ):
            raise ValueError("extra_env must contain valid environment string names and values")
        for arg in self.extra_args:
            _require_valid_unicode(arg, "extra_args entry")
        for key, value in self.extra_env.items():
            _require_valid_unicode(key, "extra_env key")
            _require_valid_unicode(value, "extra_env value")

        # ``dataclasses.replace`` otherwise aliases caller-owned mutable
        # values across per-provider pool configurations.
        self.additional_directories = list(self.additional_directories)
        self.mcp_servers = _validate_mcp_servers(self.mcp_servers)
        self.extra_args = list(self.extra_args)
        self.extra_env = dict(self.extra_env)

        if self.pool_size < 1:
            raise ValueError("pool_size must be >= 1")
        if self.pool_max_connections < 1:
            raise ValueError("pool_max_connections must be >= 1")
        if self.pool_size > self.pool_max_connections:
            raise ValueError("pool_size must be <= pool_max_connections")
        if not _is_finite_number(self.pool_acquire_timeout) or self.pool_acquire_timeout <= 0:
            raise ValueError("pool_acquire_timeout must be > 0")
        if not _is_finite_number(self.pool_idle_timeout) or self.pool_idle_timeout <= 0:
            raise ValueError("pool_idle_timeout must be > 0")
        if not _is_finite_number(self.pool_keepalive_interval) or self.pool_keepalive_interval < 0:
            raise ValueError("pool_keepalive_interval must be >= 0")
        if not _is_finite_number(self.pool_max_age) or self.pool_max_age <= 0:
            raise ValueError("pool_max_age must be > 0")
        if self.pool_max_uses < 1:
            raise ValueError("pool_max_uses must be >= 1")
        if not _is_finite_number(self.timeout_connect) or self.timeout_connect <= 0:
            raise ValueError("timeout_connect must be > 0")
        if not _is_finite_number(self.timeout_prompt) or self.timeout_prompt <= 0:
            raise ValueError("timeout_prompt must be > 0")
        if not _is_finite_number(self.timeout_stream) or self.timeout_stream <= 0:
            raise ValueError("timeout_stream must be > 0")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.max_turns is not None and self.max_turns < 1:
            raise ValueError("max_turns must be >= 1 when set")
        if not _is_finite_number(self.retry_base_delay) or self.retry_base_delay < 0:
            raise ValueError("retry_base_delay must be >= 0")

    @classmethod
    def from_env(cls, **overrides: Any) -> ACPConfig:
        """Create config from environment variables.

        Environment variables (all optional):
            ACP_PROVIDER          — provider name
            ACP_MODEL             — model identifier
            ACP_POOL_SIZE                — max idle connections
            ACP_POOL_MAX_CONNECTIONS     — hard per-pool connection limit
            ACP_POOL_ACQUIRE_TIMEOUT     — capacity wait timeout seconds
            ACP_POOL_IDLE_TIMEOUT        — idle timeout seconds
            ACP_POOL_KEEPALIVE_INTERVAL  — keepalive interval seconds (0 disables)
            ACP_POOL_MAX_AGE             — soft maximum connection age seconds
            ACP_POOL_MAX_USES            — soft maximum successful leases
            ACP_POOL_CROSS_SESSION_REUSE — explicitly allow cross-session resume
            ACP_TIMEOUT_CONNECT   — connect timeout
            ACP_TIMEOUT_PROMPT    — prompt timeout
            ACP_TIMEOUT_STREAM    — stream timeout
            ACP_CWD               — working directory
            ACP_INHERIT_PARENT_ENV — inherit all parent environment variables
            ACP_AUTO_APPROVE      — auto-approve permissions (true/false)
            ACP_ENABLE_FALLBACK   — pre-prompt provider fallback (true/false)
            ACP_MAX_RETRIES       — total pre-prompt attempt budget
            ACP_RETRY_BASE_DELAY  — exponential backoff base seconds
            ACP_MAX_TURNS         — optional Claude-specific turn cap
        """

        def _env(key: str, default: str | None = None) -> str | None:
            return os.environ.get(f"ACP_{key}", default)

        def _bool_env(key: str) -> bool | None:
            value = _env(key)
            if value is None or value == "":
                return None
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"ACP_{key} must be one of true/false, 1/0, yes/no, on/off")

        def _number_env(value: str, key: str, *, integer: bool = False) -> int | float:
            try:
                return int(value) if integer else float(value)
            except ValueError:
                kind = "integer" if integer else "number"
                raise ValueError(f"ACP_{key} must be a valid {kind}") from None

        kwargs: dict[str, Any] = {}

        v = _env("PROVIDER")
        if v:
            kwargs["provider"] = v

        v = _env("MODEL")
        if v:
            kwargs["model"] = v

        v = _env("POOL_SIZE")
        if v:
            kwargs["pool_size"] = _number_env(v, "POOL_SIZE", integer=True)

        v = _env("POOL_MAX_CONNECTIONS")
        if v:
            kwargs["pool_max_connections"] = _number_env(v, "POOL_MAX_CONNECTIONS", integer=True)

        v = _env("POOL_ACQUIRE_TIMEOUT")
        if v:
            kwargs["pool_acquire_timeout"] = _number_env(v, "POOL_ACQUIRE_TIMEOUT")

        v = _env("POOL_IDLE_TIMEOUT")
        if v:
            kwargs["pool_idle_timeout"] = _number_env(v, "POOL_IDLE_TIMEOUT")

        v = _env("POOL_KEEPALIVE_INTERVAL")
        if v:
            kwargs["pool_keepalive_interval"] = _number_env(v, "POOL_KEEPALIVE_INTERVAL")

        v = _env("POOL_MAX_AGE")
        if v:
            kwargs["pool_max_age"] = _number_env(v, "POOL_MAX_AGE")

        v = _env("POOL_MAX_USES")
        if v:
            kwargs["pool_max_uses"] = _number_env(v, "POOL_MAX_USES", integer=True)

        v = _bool_env("POOL_CROSS_SESSION_REUSE")
        if v is not None:
            kwargs["pool_cross_session_reuse"] = v

        v = _env("TIMEOUT_CONNECT")
        if v:
            kwargs["timeout_connect"] = _number_env(v, "TIMEOUT_CONNECT")

        v = _env("TIMEOUT_PROMPT")
        if v:
            kwargs["timeout_prompt"] = _number_env(v, "TIMEOUT_PROMPT")

        v = _env("TIMEOUT_STREAM")
        if v:
            kwargs["timeout_stream"] = _number_env(v, "TIMEOUT_STREAM")

        v = _env("CWD")
        if v:
            kwargs["cwd"] = v

        v = _bool_env("INHERIT_PARENT_ENV")
        if v is not None:
            kwargs["inherit_parent_env"] = v

        v = _bool_env("AUTO_APPROVE")
        if v is not None:
            kwargs["auto_approve_permissions"] = v

        v = _bool_env("ENABLE_FALLBACK")
        if v is not None:
            kwargs["enable_fallback"] = v

        v = _env("MAX_RETRIES")
        if v:
            kwargs["max_retries"] = _number_env(v, "MAX_RETRIES", integer=True)

        v = _env("RETRY_BASE_DELAY")
        if v:
            kwargs["retry_base_delay"] = _number_env(v, "RETRY_BASE_DELAY")

        v = _env("MAX_TURNS")
        if v:
            kwargs["max_turns"] = _number_env(v, "MAX_TURNS", integer=True)

        kwargs.update(overrides)
        return cls(**kwargs)
