"""Optional OpenTelemetry instruments for connection-pool decisions."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, ClassVar

from soulacp.config import FALLBACK_MAP


def telemetry_provider_name(provider: object) -> str:
    """Return a bounded provider attribute for metrics and traces."""
    return provider if isinstance(provider, str) and provider in FALLBACK_MAP else "custom"


@lru_cache(maxsize=1)
def _pool_instruments() -> dict[str, Any]:
    """Create process-wide instruments once, as required by their identity."""
    from opentelemetry import metrics  # pyright: ignore[reportMissingImports]

    meter = metrics.get_meter("soulacp.pool")
    return {
        "acquisitions": meter.create_counter("soulacp.pool.acquisitions", unit="{lease}"),
        "acquire_duration": meter.create_histogram("soulacp.pool.acquire.duration", unit="s"),
        "wait_duration": meter.create_histogram("soulacp.pool.wait.duration", unit="s"),
        "created": meter.create_counter("soulacp.pool.connections.created", unit="{connection}"),
        "connect_duration": meter.create_histogram("soulacp.pool.connect.duration", unit="s"),
        "evictions": meter.create_counter("soulacp.pool.evictions", unit="{connection}"),
        "disconnect_failures": meter.create_counter("soulacp.pool.disconnect.failures", unit="{failure}"),
        "cleanup_retries": meter.create_counter("soulacp.pool.cleanup.retries", unit="{retry}"),
        "resume": meter.create_counter("soulacp.pool.resume", unit="{attempt}"),
        "health": meter.create_counter("soulacp.pool.health_checks", unit="{check}"),
        "keepalive": meter.create_counter("soulacp.pool.keepalive.runs", unit="{run}"),
        "acquire_timeouts": meter.create_counter("soulacp.pool.acquire.timeouts", unit="{timeout}"),
    }


class PoolTelemetry:
    """Small OTel API-only wrapper that becomes a no-op when OTel is absent."""

    _ALLOWED_ATTRIBUTE_VALUES: ClassVar[dict[str, frozenset[str]]] = {
        "source": frozenset(
            {
                "new",
                "fresh",
                "exact_session",
                "idle_unbound",
                "cross_session",
                "tracked",
                "failed_startup",
                "other",
            }
        ),
        "result": frozenset({"success", "failure", "capacity_timeout", "other"}),
        "reason": frozenset(
            {
                "fresh",
                "session_resume",
                "new_session",
                "capacity_rebalance",
                "health_replacement",
                "lease_error",
                "prepare_failed",
                "health_check_failed",
                "creation_abandoned",
                "pool_closed",
                "client_invalidated",
                "session_changed",
                "disconnected",
                "idle_timeout",
                "max_age",
                "max_uses",
                "pool_full_lru",
                "unknown",
                "other",
            }
        ),
        "trigger": frozenset({"acquire", "keepalive", "other"}),
    }
    _BOOLEAN_ATTRIBUTES: ClassVar[frozenset[str]] = frozenset({"fresh"})

    def __init__(self, provider: str) -> None:
        self._provider = telemetry_provider_name(provider)
        self._instruments: dict[str, Any] = {}
        try:
            self._instruments = _pool_instruments()
        except Exception:
            self._instruments = {}

    def _attrs(self, **attributes: str | bool) -> dict[str, str | bool]:
        safe: dict[str, str | bool] = {}
        for key, value in attributes.items():
            allowed_values = self._ALLOWED_ATTRIBUTE_VALUES.get(key)
            if allowed_values is not None and isinstance(value, str):
                safe[key] = value if value in allowed_values else "other"
            elif key in self._BOOLEAN_ATTRIBUTES and isinstance(value, bool):
                safe[key] = value
        return {"provider": self._provider, **safe}

    def add(self, name: str, **attributes: str | bool) -> None:
        """Increment a named counter when its OTel instrument is available."""
        instrument = self._instruments.get(name)
        if instrument is not None:
            try:
                instrument.add(1, self._attrs(**attributes))
            except Exception:
                return

    def record(self, name: str, value: float, **attributes: str | bool) -> None:
        """Record a value on a named histogram when OTel is available."""
        instrument = self._instruments.get(name)
        if instrument is not None:
            try:
                instrument.record(value, self._attrs(**attributes))
            except Exception:
                return
