"""Exact reads and permission ownership across completed prompt turns."""

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest

from soulacp.adapters.base_client import ACPClientBase
from soulacp.adapters.gemini_client import GeminiACPClient
from soulacp.config import ACPConfig
from soulacp.services.fs_service import FSService


class BoundaryClient(ACPClientBase):
    def __init__(self, cwd, auto_approve=True):
        super().__init__(ACPConfig(cwd=str(cwd), auto_approve_permissions=auto_approve))
        self._connected = True
        self.session_id = "synthetic-session"
        self.results = []
        self.errors = []
        self.release_request = None
        self.request_started = asyncio.Event()

    def _get_acp_command(self):
        raise AssertionError("This fixture does not launch a provider")

    async def _initialize(self):
        raise AssertionError("This fixture does not initialize a provider")

    async def _send_result(self, mid, result):
        self.results.append((mid, result))

    async def _send_error(self, mid, code, message):
        self.errors.append((mid, code, message))

    async def _handle_request(self, message):
        self.request_started.set()
        if self.release_request is not None:
            await self.release_request.wait()
        await super()._handle_request(message)


def permission(client, mid=100):
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "method": "session/request_permission",
        "params": {
            "sessionId": client.session_id,
            "toolCall": {"toolCallId": "synthetic-tool"},
            "options": [
                {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
            ],
        },
    }


async def drain(client):
    if client._request_tasks:
        await asyncio.gather(*list(client._request_tasks))
    await asyncio.sleep(0)


@pytest.mark.parametrize("limit", [None, 3000])
async def test_read_many_short_lines_returns_exact_requested_content(tmp_path, limit):
    text = "x\n" * 2500 + "TAIL\n"
    path = tmp_path / "many.txt"
    path.write_bytes(text.encode("utf-8"))
    assert await FSService(str(tmp_path)).read_text_file(str(path), limit=limit) == {"content": text}


@pytest.mark.parametrize("limit", [None, 1])
async def test_oversized_read_fails_without_publishing_partial_content(tmp_path, limit):
    fs = FSService(str(tmp_path))
    text = "x" * (fs.MAX_READ_CHARS + 1)
    path = tmp_path / "long.txt"
    path.write_bytes(text.encode("utf-8"))
    with pytest.raises(ValueError, match="read limit") as caught:
        await fs.read_text_file(str(path), limit=limit)
    assert str(path) not in str(caught.value)
    assert text not in str(caught.value)
    assert path.read_text(encoding="utf-8") == text


async def test_explicit_read_slice_and_roundtrip_preserve_tail(tmp_path):
    path = tmp_path / "roundtrip.txt"
    text = "x\n" * 2100 + "TAIL\n"
    path.write_bytes(text.encode("utf-8"))
    fs = FSService(str(tmp_path))
    assert await fs.read_text_file(str(path), line=2101, limit=1) == {"content": "TAIL\n"}
    original = (await fs.read_text_file(str(path)))["content"]
    await fs.write_text_file(str(path), original.replace("x\n", "y\n", 1))
    assert path.read_bytes() == text.replace("x\n", "y\n", 1).encode("utf-8")


async def test_read_escaped_text_stays_exact_and_bounded(tmp_path):
    fs = FSService(str(tmp_path))
    text = '"\\\t\x01\u4e2d\n' * 100
    path = tmp_path / "escaped.txt"
    path.write_bytes(text.encode("utf-8"))
    result = await fs.read_text_file(str(path))
    assert result == {"content": text}
    assert len(json.dumps(result).encode()) <= 6 * fs.MAX_READ_CHARS + 32


async def test_read_limits_remain_fixed_and_slices_of_large_text_are_legal(tmp_path):
    fs = FSService(str(tmp_path))
    assert fs.MAX_READ_CHARS == 10_000
    assert fs.MAX_FILE_BYTES == 10 * 1_048_576
    assert fs.MAX_LINE_LIMIT == 10_000
    path = tmp_path / "slices.txt"
    path.write_bytes(("a" * 6000 + "\n" + "b" * 6000 + "\n").encode("utf-8"))
    assert (await fs.read_text_file(str(path), line=1, limit=1))["content"] == "a" * 6000 + "\n"
    assert (await fs.read_text_file(str(path), line=2, limit=1))["content"] == "b" * 6000 + "\n"
    with pytest.raises(ValueError, match="read limit"):
        await fs.read_text_file(str(path))
    with pytest.raises(ValueError, match="limit must be <="):
        await fs.read_text_file(str(path), limit=10_001)


async def test_missing_file_is_resource_not_found_and_does_not_leak_path(tmp_path):
    client = BoundaryClient(tmp_path)
    path = tmp_path / "synthetic-private-marker.txt"
    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "id": 90,
            "method": "fs/read_text_file",
            "params": {"sessionId": client.session_id, "path": str(path)},
        }
    )
    await drain(client)
    assert not client.results
    assert client.errors == [(90, -32002, "Resource not found")]
    assert "synthetic-private-marker" not in str(client.errors)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("auto_approve", [False, True])
async def test_completed_prompt_cannot_authorize_late_permission(tmp_path, stream, auto_approve):
    client = BoundaryClient(tmp_path, auto_approve)

    async def rpc(*_args, **_kwargs):
        return {"stopReason": "end_turn"}

    client._rpc = rpc
    if stream:
        assert [part async for part in client.query_stream("synthetic")] == []
    else:
        assert await client.query("synthetic") == ""
    await client._dispatch(permission(client))
    await drain(client)
    assert client.results == [(100, {"outcome": {"outcome": "cancelled"}})]


async def test_permission_before_first_prompt_is_not_authorized(tmp_path):
    client = BoundaryClient(tmp_path)
    await client._dispatch(permission(client))
    await drain(client)
    assert client.results == [(100, {"outcome": {"outcome": "cancelled"}})]


async def test_queued_completed_turn_does_not_borrow_next_turn(tmp_path):
    client = BoundaryClient(tmp_path)
    client.release_request = asyncio.Event()

    async def first_rpc(*_args, **_kwargs):
        await client._dispatch(permission(client, 101))
        await client.request_started.wait()
        return {"stopReason": "end_turn"}

    client._rpc = first_rpc
    await client.query("first")

    async def next_rpc(*_args, **_kwargs):
        client.release_request.set()
        await drain(client)
        await client._dispatch(permission(client, 102))
        await drain(client)
        return {"stopReason": "end_turn"}

    client._rpc = next_rpc
    await client.query("next")
    assert dict(client.results) == {
        101: {"outcome": {"outcome": "cancelled"}},
        102: {"outcome": {"outcome": "selected", "optionId": "allow"}},
    }


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("auto_approve", [False, True])
async def test_active_turn_keeps_policy_across_successive_prompts(tmp_path, stream, auto_approve):
    client = BoundaryClient(tmp_path, auto_approve)
    mid = 110

    async def rpc(*_args, **_kwargs):
        nonlocal mid
        mid += 1
        await client._dispatch(permission(client, mid))
        await drain(client)
        return {"stopReason": "end_turn"}

    client._rpc = rpc
    for _ in range(2):
        if stream:
            assert [chunk async for chunk in client.query_stream("test")] == []
        else:
            assert await client.query("test") == ""
    assert [r[1]["outcome"]["optionId"] for r in client.results] == ["allow" if auto_approve else "reject"] * 2
    assert not client._permission_turn.active


@pytest.mark.parametrize(
    "reply", [{"result": {"stopReason": "end_turn"}}, {"error": {"code": -32603, "message": "synthetic"}}]
)
async def test_correlated_response_closes_permission_before_query_resumes(tmp_path, reply):
    client = BoundaryClient(tmp_path)
    client._begin_permission_turn()
    future = asyncio.get_running_loop().create_future()
    client._pending[50] = future
    client._inflight[50] = ("session/prompt", 0.0)
    await client._dispatch({"jsonrpc": "2.0", "id": 50, **reply})
    await client._dispatch(permission(client))
    await drain(client)
    assert client.results == [(100, {"outcome": {"outcome": "cancelled"}})]
    if "error" in reply:
        assert future.exception() is not None
    else:
        assert future.result() == reply["result"]


@pytest.mark.parametrize("stream", [False, True])
async def test_failed_prompt_leaves_no_permission_window(tmp_path, stream):
    client = BoundaryClient(tmp_path)

    async def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic-failure")

    client._rpc = fail
    with pytest.raises(RuntimeError, match="synthetic-failure"):
        if stream:
            _ = [part async for part in client.query_stream("test")]
        else:
            await client.query("test")
    assert not client._permission_turn.active
    if not client._connected:
        return
    await client._dispatch(permission(client))
    await drain(client)
    assert client.results == [(100, {"outcome": {"outcome": "cancelled"}})]


async def test_transport_reset_retires_old_permission_owner(tmp_path):
    client = BoundaryClient(tmp_path)
    client._begin_permission_turn()
    previous = client._permission_turn
    client._reset_protocol_state()
    assert not previous.active
    assert not client._permission_turn.active


async def test_buffered_stream_content_does_not_extend_permission_window(tmp_path):
    client = BoundaryClient(tmp_path)

    async def rpc(*_args, **_kwargs):
        client._append_chunk("part")
        return {"stopReason": "end_turn"}

    client._rpc = rpc
    stream = client.query_stream("test")
    assert await anext(stream) == "part"
    await client._dispatch(permission(client))
    await drain(client)
    assert client.results == [(100, {"outcome": {"outcome": "cancelled"}})]
    await stream.aclose()


@pytest.mark.parametrize(
    "kind,expected",
    [(PermissionError, -32603), (IsADirectoryError, 10003), (RuntimeError, -32603), (ValueError, -32602)],
)
async def test_read_failures_are_not_misclassified_or_leaked(tmp_path, caplog, kind, expected):
    client = BoundaryClient(tmp_path)

    async def fail(**_kwargs):
        raise kind("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    client._fs_service.read_text_file = fail
    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "id": 91,
            "method": "fs/read_text_file",
            "params": {"sessionId": client.session_id, "path": str(tmp_path / "private.txt")},
        }
    )
    await drain(client)
    assert client.errors[0][1] == expected
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in str(client.errors) + caplog.text


async def test_missing_outside_root_is_still_denied(tmp_path):
    client = BoundaryClient(tmp_path)
    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "id": 92,
            "method": "fs/read_text_file",
            "params": {"sessionId": client.session_id, "path": str(tmp_path.parent / "outside-missing.txt")},
        }
    )
    await drain(client)
    assert client.errors == [(92, -32603, "Host service request failed")]


async def test_read_cancellation_waits_for_owned_worker(tmp_path, monkeypatch):
    fs = FSService(str(tmp_path))
    started = threading.Event()
    release = threading.Event()

    def blocked(*_args):
        started.set()
        assert release.wait(5)
        return {"content": "ok"}

    monkeypatch.setattr(fs, "_read_resolved", blocked)
    reading = asyncio.create_task(fs.read_text_file(str(tmp_path / "synthetic.txt")))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        reading.cancel()
        await asyncio.sleep(0)
        assert not reading.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await reading


class StdioClient(GeminiACPClient):
    """Use the normal ACP transport with a deterministic local agent."""

    def __init__(self, config):
        super().__init__(config)
        self.late_reply = asyncio.Event()
        self.observed = []

    def _get_acp_command(self):
        return [sys.executable, "-u", str(Path(__file__).parent / "fixtures" / "boundary_stdio_agent.py")]

    async def _send_result(self, mid, result):
        await super()._send_result(mid, result)
        self.observed.append((mid, result))
        if mid == "late":
            self.late_reply.set()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("auto_approve", [False, True])
async def test_real_stdio_file_and_permission_roundtrip(tmp_path, stream, auto_approve):
    text = "x\n" * 2500 + "TAIL\n"
    path = tmp_path / "source.txt"
    path.write_bytes(text.encode("utf-8"))
    client = StdioClient(
        ACPConfig(
            cwd=str(tmp_path),
            auto_approve_permissions=auto_approve,
            timeout_connect=10,
            timeout_prompt=10,
            timeout_stream=5,
        )
    )
    try:
        await client.connect()
        prompt = json.dumps({"path": str(path)})
        if stream:
            output = "".join([chunk async for chunk in client.query_stream(prompt)])
        else:
            output = await client.query(prompt)
        await asyncio.wait_for(client.late_reply.wait(), 5)
        report = json.loads(output)
        assert report["read"] == {"content": text}
        assert report["missing"]["code"] == -32002
        assert report["permission"]["outcome"] == {
            "outcome": "selected",
            "optionId": "allow" if auto_approve else "reject",
        }
        assert dict(client.observed)["late"] == {"outcome": {"outcome": "cancelled"}}
    finally:
        await client.disconnect()
    assert client.process is None
    assert not client._request_tasks
