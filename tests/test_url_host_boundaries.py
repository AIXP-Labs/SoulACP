"""Reject malformed IP literals independently of urlsplit patch-version behavior."""

import traceback
from urllib.parse import SplitResult

import pytest

from soulacp import ACPConfig
from soulacp import config as config_module
from soulacp.adapters import openclaw_client

ENDPOINTS = [("http", "http"), ("sse", "https"), ("gateway", "ws"), ("gateway", "wss")]


def validate(endpoint, url):
    if endpoint == "gateway":
        return openclaw_client._validate_gateway_url(url)
    config = ACPConfig(mcp_servers=[dict(type=endpoint, name="synthetic", url=url, headers=[])])
    return config.mcp_servers[0]["url"]


@pytest.mark.parametrize("endpoint,scheme", ENDPOINTS)
@pytest.mark.parametrize("host", ["SYNTHETIC_URL_SECRET", "192.0.2.1", "v.invalid", "v1.", "invalid-ip"])
def test_legacy_parser_cannot_admit_malformed_bracketed_hosts(monkeypatch, endpoint, scheme, host):
    url = f"{scheme}://[{host}]:443/synthetic"
    parsed = SplitResult(scheme, f"[{host}]:443", "/synthetic", "", "")
    module = openclaw_client if endpoint == "gateway" else config_module
    monkeypatch.setattr(module, "urlsplit", lambda value: parsed)

    with pytest.raises(ValueError) as caught:
        validate(endpoint, url)
    assert host not in str(caught.value)
    assert host not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("endpoint,scheme", ENDPOINTS)
@pytest.mark.parametrize(
    "host",
    [
        "[::1]",
        "[2001:db8::1234]",
        "[::ffff:192.0.2.1]",
        "[fe80::1%25synthetic-zone]",
        "[v1.example]",
        "192.0.2.1",
        "example.invalid",
    ],
)
def test_valid_host_forms_keep_the_exact_url(endpoint, scheme, host):
    url = f"{scheme}://{host}:443/synthetic?value=1#fragment"
    assert validate(endpoint, url) == url
