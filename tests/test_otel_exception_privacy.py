"""Optional telemetry never receives raw application exceptions."""

from __future__ import annotations

import asyncio
import json

import pytest

from soulacp import ACPConfig
from soulacp.adapters.base_client import ACPClientBase
from soulacp.errors import ACPRequestOutcomeUnknownError


class Client(ACPClientBase):
    def _get_acp_command(self):
        raise AssertionError("No provider may start")

    async def _initialize(self):
        raise AssertionError("No provider may initialize")


@pytest.mark.parametrize("error", [RuntimeError("synthetic-private-error"), asyncio.CancelledError()])
def test_span_exit_gets_no_exception_and_cannot_suppress_business_error(monkeypatch, error):
    from opentelemetry import trace

    captured = []

    class Span:
        def __init__(self):
            self.status = None
            self.attributes = {}

        def set_status(self, status):
            self.status = status

        def set_attribute(self, key, value):
            self.attributes[key] = value

    span = Span()

    class Context:
        def __enter__(self):
            return span

        def __exit__(self, *args):
            captured.append(args)
            return True

    class Tracer:
        def start_as_current_span(self, name, *, attributes):
            return Context()

    monkeypatch.setattr(trace, "get_tracer", lambda name: Tracer())
    with pytest.raises(type(error)) as caught, Client(ACPConfig())._otel_rpc_span("initialize", 1):
        raise error
    assert caught.value is error
    assert captured == [(None, None, None)]
    assert span.status.status_code == trace.StatusCode.ERROR
    assert span.status.description is None
    assert span.attributes["error.type"] in {"error", "cancelled"}


@pytest.mark.parametrize("sensitive_opt_in", ["0", "1"])
@pytest.mark.parametrize("kind", ["error", "unknown", "cancelled", "success"])
def test_real_sdk_keeps_events_and_status_content_free(monkeypatch, sensitive_opt_in, kind):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    marker = "synthetic-original-provider-diagnostic"
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setenv("ACP_LOG_SENSITIVE_DATA", sensitive_opt_in)
    monkeypatch.setattr(trace, "get_tracer", lambda name: provider.get_tracer(name))
    error = {
        "error": RuntimeError(marker),
        "unknown": ACPRequestOutcomeUnknownError(OSError(marker)),
        "cancelled": asyncio.CancelledError(marker),
        "success": None,
    }[kind]
    try:
        if error is None:
            with Client(ACPConfig())._otel_rpc_span("initialize", 1):
                pass
        else:
            with pytest.raises(type(error)) as caught, Client(ACPConfig())._otel_rpc_span("initialize", 1):
                raise error
            assert caught.value is error
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert not span.events
        assert marker not in json.dumps(dict(span.attributes))
        assert span.status.description is None
        assert span.status.status_code == (trace.StatusCode.UNSET if error is None else trace.StatusCode.ERROR)
    finally:
        provider.shutdown()
