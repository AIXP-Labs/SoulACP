"""Pool telemetry stays optional and filters high-cardinality attributes."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from soulacp.telemetry import PoolTelemetry, _pool_instruments, telemetry_provider_name


@pytest.fixture(autouse=True)
def clear_instrument_cache():
    _pool_instruments.cache_clear()
    yield
    _pool_instruments.cache_clear()


class FakeInstrument:
    def __init__(self) -> None:
        self.measurements: list[tuple[float, dict]] = []

    def add(self, value, attributes):
        self.measurements.append((value, attributes))

    def record(self, value, attributes):
        self.measurements.append((value, attributes))


class FakeMeter:
    def __init__(self) -> None:
        self.instruments: dict[str, FakeInstrument] = {}

    def create_counter(self, name, **_kwargs):
        return self.instruments.setdefault(name, FakeInstrument())

    def create_histogram(self, name, **_kwargs):
        return self.instruments.setdefault(name, FakeInstrument())


def test_pool_telemetry_records_low_cardinality_attributes(monkeypatch):
    meter = FakeMeter()
    fake_metrics = SimpleNamespace(get_meter=lambda _name: meter)
    monkeypatch.setitem(sys.modules, "opentelemetry", SimpleNamespace(metrics=fake_metrics))

    telemetry = PoolTelemetry("claude")
    telemetry.add(
        "acquisitions",
        source="exact_session",
        result="success",
        session_id="secret",
        prompt="private",
    )

    measurement = meter.instruments["soulacp.pool.acquisitions"].measurements[0]
    assert measurement == (
        1,
        {"provider": "claude", "source": "exact_session", "result": "success"},
    )

    telemetry.add("cleanup_retries", source="failed_startup")
    retry_measurement = meter.instruments["soulacp.pool.cleanup.retries"].measurements[0]
    assert retry_measurement == (
        1,
        {"provider": "claude", "source": "failed_startup"},
    )


def test_pool_telemetry_collapses_unbounded_attribute_values():
    telemetry = PoolTelemetry.__new__(PoolTelemetry)
    telemetry._provider = "claude"
    telemetry._instruments = {}

    assert telemetry._attrs(
        source="private-user-id",
        result="private-result",
        reason="private prompt text",
        trigger="private/session/id",
        fresh="not-a-boolean",
        prompt="private prompt text",
    ) == {
        "provider": "claude",
        "source": "other",
        "result": "other",
        "reason": "other",
        "trigger": "other",
    }
    assert telemetry._attrs(
        source="exact_session",
        result="success",
        reason="session_resume",
        trigger="acquire",
        fresh=True,
    ) == {
        "provider": "claude",
        "source": "exact_session",
        "result": "success",
        "reason": "session_resume",
        "trigger": "acquire",
        "fresh": True,
    }


def test_custom_provider_is_collapsed_to_bounded_telemetry_value():
    assert telemetry_provider_name("claude") == "claude"
    assert telemetry_provider_name("tenant/private-user") == "custom"

    telemetry = PoolTelemetry.__new__(PoolTelemetry)
    telemetry._provider = telemetry_provider_name("tenant/private-user")
    telemetry._instruments = {}

    assert telemetry._attrs() == {"provider": "custom"}


def test_pool_telemetry_noops_when_otel_api_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "opentelemetry", SimpleNamespace())

    telemetry = PoolTelemetry("claude")
    telemetry.add("acquisitions", source="new", result="success")
    telemetry.record("acquire_duration", 0.1, source="new", result="success")


def test_pool_telemetry_reuses_process_wide_instruments(monkeypatch):
    meter = FakeMeter()
    calls = 0

    def get_meter(_name):
        nonlocal calls
        calls += 1
        return meter

    fake_metrics = SimpleNamespace(get_meter=get_meter)
    monkeypatch.setitem(sys.modules, "opentelemetry", SimpleNamespace(metrics=fake_metrics))

    first = PoolTelemetry("claude")
    second = PoolTelemetry("gemini")

    assert calls == 1
    assert first._instruments is second._instruments


def test_pool_telemetry_never_breaks_runtime_when_instrument_raises():
    class BrokenInstrument:
        def add(self, *_args, **_kwargs):
            raise RuntimeError("metrics backend failed")

        def record(self, *_args, **_kwargs):
            raise RuntimeError("metrics backend failed")

    telemetry = PoolTelemetry.__new__(PoolTelemetry)
    telemetry._provider = "claude"
    telemetry._instruments = {
        "acquisitions": BrokenInstrument(),
        "acquire_duration": BrokenInstrument(),
    }

    telemetry.add("acquisitions", source="new", result="success")
    telemetry.record("acquire_duration", 0.1, source="new", result="success")
