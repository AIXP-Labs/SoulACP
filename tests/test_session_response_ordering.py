"""Wire-order regressions using real StreamReader buffers and no native process."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress

import pytest

from soulacp.adapters.base_client import ACPClientBase, RPCError
from soulacp.config import ACPConfig
from soulacp.errors import ACPProtocolError


def options(value: str) -> list[dict]:
    return [
        {
            "id": "effort",
            "name": "Effort",
            "type": "select",
            "currentValue": value,
            "options": [{"value": name, "name": name} for name in ("balanced", "fast", "quality")],
        }
    ]


def modes(current: str = "plan") -> dict:
    return {"currentModeId": current, "availableModes": [{"id": name, "name": name} for name in ("plan", "code")]}


def notification(update: dict, sid: str = "session-new") -> dict:
    return {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": update}}


def response(mid: int, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def wire_updates(sid: str = "session-new") -> list[dict]:
    return [
        notification({"sessionUpdate": "current_mode_update", "currentModeId": "code"}, sid),
        notification({"sessionUpdate": "config_option_update", "configOptions": options("quality")}, sid),
        notification(
            {
                "sessionUpdate": "available_commands_update",
                "availableCommands": [{"name": "latest", "description": "Latest wire command"}],
            },
            sid,
        ),
    ]


class MemoryStdin:
    def __init__(self, process) -> None:
        self.process = process
        self.closed = False
        self.request = None
        self.written = asyncio.Event()
        self.on_request = None
        self.block_drain = False
        self.release_drain = asyncio.Event()
        self._last_was_request = False

    def write(self, data: bytes) -> None:
        frame = json.loads(data)
        self._last_was_request = "id" in frame
        if self._last_was_request:
            self.request = frame
            self.written.set()
            if self.on_request is not None:
                self.on_request(frame)

    async def drain(self) -> None:
        if self.block_drain and self._last_was_request:
            await self.release_drain.wait()

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True
        self.process.finish()

    async def wait_closed(self) -> None:
        return None


class MemoryProcess:
    """The SDK sees real buffered readers; no process can ever be spawned/killed."""

    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = MemoryStdin(self)
        self.returncode = None
        self.exited = asyncio.Event()

    def feed(self, *frames: dict) -> None:
        # One feed_data call is essential: response and update are already in
        # the same buffer when the reader publishes the RPC result.
        self.stdout.feed_data(b"".join(json.dumps(frame).encode() + b"\n" for frame in frames))

    def finish(self) -> None:
        if self.returncode is None:
            self.returncode = 0
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.exited.set()

    terminate = finish
    kill = finish

    async def wait(self) -> int:
        await self.exited.wait()
        return self.returncode


class MemoryClient(ACPClientBase):
    def __init__(self) -> None:
        super().__init__(ACPConfig(inherit_parent_env=False, timeout_connect=2, pool_keepalive_interval=0))
        self.completions = []
        self.dispatch_errors = []
        self.hold_result = False
        self.result_observed = asyncio.Event()
        self.release_result = asyncio.Event()

    def _get_acp_command(self) -> list[str]:
        raise AssertionError("native startup must not run")

    async def _initialize(self) -> None:
        raise AssertionError("only the in-memory RPC transport is used")

    def _complete_rpc_lifecycle(self, method, result, transition_id, *args, **kwargs):
        self.completions.append(method)
        return super()._complete_rpc_lifecycle(method, result, transition_id, *args, **kwargs)

    async def _await_rpc_future(self, future, deadline):
        result = await super()._await_rpc_future(future, deadline)
        if self.hold_result:
            self.result_observed.set()
            await self.release_result.wait()
        return result

    async def _dispatch(self, message):
        try:
            return await super()._dispatch(message)
        except BaseException as exc:
            self.dispatch_errors.append(exc)
            raise

    # Tests explicitly own final cleanup. Suppress only autonomous fake-process
    # reaping, so assertions can inspect the actual reader/lifecycle outcome.
    def _schedule_host_cleanup(self) -> None:
        return None

    def _schedule_process_cleanup(self) -> None:
        return None


@asynccontextmanager
async def transport():
    client, process = MemoryClient(), MemoryProcess()
    client.process, client._connected = process, True
    reader = asyncio.create_task(client._read_stdout())
    client._reader_tasks = [reader]
    try:
        yield client, process, reader
    finally:
        await client.disconnect()


def install_old_state(client) -> None:
    client.session_id = "session-old"
    client.session_mode_state = modes()
    client.session_config_options = options("balanced")
    client.session_info = {"title": "Old"}
    client.session_usage = {"used": 1, "size": 100}
    client.available_commands = [{"name": "old", "description": "Old command"}]


@pytest.mark.parametrize("delivery", ["coalesced", "delayed"])
async def test_new_response_and_same_session_update_keep_real_reader_connected(delivery):
    async with transport() as (client, process, reader):
        updated = asyncio.Event()
        client.set_update_callback(lambda update: updated.set())
        result = {"sessionId": "session-new"}
        update = notification({"sessionUpdate": "available_commands_update", "availableCommands": []})
        process.stdin.on_request = lambda request: process.feed(
            response(request["id"], result), *([update] if delivery == "coalesced" else [])
        )

        assert await client._rpc("session/new", {}, timeout=2) == result
        if delivery == "delayed":
            process.feed(update)
        await asyncio.wait_for(updated.wait(), 2)

        assert client.is_connected and not reader.done()
        assert client.session_id == "session-new"
        assert client.dispatch_errors == []
        assert client.completions == ["session/new"]


@pytest.mark.parametrize("method", ["session/new", "session/load", "session/resume"])
async def test_response_lifecycle_applies_once_and_later_metadata_is_not_overwritten(method):
    async with transport() as (client, process, _reader):
        install_old_state(client)
        updated = asyncio.Event()
        client.set_update_callback(
            lambda update: updated.set() if update["sessionUpdate"] == "available_commands_update" else None
        )
        result = {"configOptions": options("fast"), "modes": modes()}
        if method == "session/new":
            result["sessionId"] = "session-new"
        params = {} if method == "session/new" else {"sessionId": "session-new"}
        process.stdin.on_request = lambda request: process.feed(
            response(request["id"], result),
            *wire_updates(),
            response(request["id"], {"sessionId": "duplicate-must-not-commit"}),
        )

        await client._rpc(method, params, timeout=2)
        assert client.is_connected
        await asyncio.wait_for(updated.wait(), 2)
        assert client.session_id == "session-new"
        assert client.session_mode_state["currentModeId"] == "code"
        assert client.session_config_options == options("quality")
        assert client.available_commands == [{"name": "latest", "description": "Latest wire command"}]
        assert client.completions == [method]
        assert client._session_transition_active is False


async def test_public_set_mode_preserves_update_returning_to_the_initial_mode():
    async with transport() as (client, process, _reader):
        install_old_state(client)
        process.stdin.on_request = lambda request: process.feed(
            response(request["id"], {}),
            notification({"sessionUpdate": "current_mode_update", "currentModeId": "plan"}, "session-old"),
        )

        state = await client.set_mode("code")

        assert state["currentModeId"] == client.session_mode_state["currentModeId"] == "plan"
        assert client.completions == ["session/set_mode"]


async def test_public_set_config_option_preserves_later_wire_snapshot():
    async with transport() as (client, process, _reader):
        install_old_state(client)
        process.stdin.on_request = lambda request: process.feed(
            response(request["id"], {"configOptions": options("fast")}),
            notification({"sessionUpdate": "config_option_update", "configOptions": options("quality")}, "session-old"),
        )

        assert await client.set_config_option("effort", "fast") == options("quality")
        assert client.session_config_options == options("quality")
        assert client.completions == ["session/set_config_option"]


@pytest.mark.parametrize("method", ["session/new", "session/load", "session/resume"])
@pytest.mark.parametrize("same_session_id", [False, True])
@pytest.mark.parametrize("old_response_first", [False, True])
async def test_old_config_response_cannot_apply_to_a_rebound_session(method, same_session_id, old_response_first):
    async with transport() as (client, process, reader):
        install_old_state(client)
        config_request = asyncio.create_task(client.set_config_option("effort", "fast"))
        await process.stdin.written.wait()
        config_mid = process.stdin.request["id"]
        process.stdin.written.clear()

        target = "session-old" if same_session_id else "session-new"
        params = {} if method == "session/new" else {"sessionId": target}
        transition = asyncio.create_task(client._rpc(method, params, timeout=2))
        await process.stdin.written.wait()
        transition_mid = process.stdin.request["id"]
        # An omitted new config snapshot must remain empty, even if the old
        # response arrives while the session transition is still pending.
        transition_result = {} if old_response_first else {"configOptions": options("quality")}
        if method == "session/new":
            transition_result["sessionId"] = target
        frames = [
            response(transition_mid, transition_result),
            response(config_mid, {"configOptions": options("fast")}),
        ]
        process.feed(*(reversed(frames) if old_response_first else frames))

        try:
            with pytest.raises(ACPProtocolError, match="Session changed during config request"):
                await config_request
            assert await transition == transition_result
            assert client.session_id == target
            assert client.session_config_options == ([] if old_response_first else options("quality"))
            assert client.is_connected and not reader.done()
            assert not client._rpc_lifecycles
        finally:
            for request in (config_request, transition):
                if not request.done():
                    request.cancel()
                with suppress(asyncio.CancelledError, ACPProtocolError):
                    await request


async def test_raw_old_config_response_is_returned_without_polluting_rebound_session():
    async with transport() as (client, process, reader):
        install_old_state(client)
        request = asyncio.create_task(
            client._rpc(
                "session/set_config_option", {"sessionId": "session-old", "configId": "effort", "value": "fast"}
            )
        )
        await process.stdin.written.wait()
        config_mid = process.stdin.request["id"]
        process.stdin.written.clear()
        transition = asyncio.create_task(client._rpc("session/load", {"sessionId": "session-old"}, timeout=2))
        await process.stdin.written.wait()
        result = {"configOptions": options("fast")}
        process.feed(
            response(process.stdin.request["id"], {"configOptions": options("quality")}),
            response(config_mid, result),
        )

        assert await request == result
        await transition
        assert client.session_config_options == options("quality")
        assert client.is_connected and not reader.done()
        assert not client._rpc_lifecycles


async def test_config_return_rejects_session_rebound_after_response_was_applied():
    async with transport() as (client, process, reader):
        install_old_state(client)
        client.hold_result = True
        request = asyncio.create_task(client.set_config_option("effort", "fast"))
        await process.stdin.written.wait()
        process.feed(response(process.stdin.request["id"], {"configOptions": options("fast")}))
        await asyncio.wait_for(client.result_observed.wait(), 2)
        process.stdin.written.clear()
        updated = asyncio.Event()
        client.set_update_callback(lambda update: updated.set())
        transition = asyncio.create_task(client._rpc("session/new", {}, timeout=2))
        await process.stdin.written.wait()
        process.feed(
            response(process.stdin.request["id"], {"sessionId": "session-new", "configOptions": options("quality")}),
            notification({"sessionUpdate": "available_commands_update", "availableCommands": []}),
        )
        await asyncio.wait_for(updated.wait(), 2)
        client.release_result.set()

        try:
            with pytest.raises(ACPProtocolError, match="Session changed during config request"):
                await request
            await transition
            assert client.session_config_options == options("quality")
            assert client.is_connected and not reader.done()
        finally:
            client.release_result.set()
            for task in (request, transition):
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError, ACPProtocolError):
                    await task


async def test_same_session_concurrent_config_responses_keep_wire_order():
    async with transport() as (client, process, reader):
        install_old_state(client)
        first = asyncio.create_task(client.set_config_option("effort", "fast"))
        await process.stdin.written.wait()
        first_mid = process.stdin.request["id"]
        process.stdin.written.clear()
        second = asyncio.create_task(client.set_config_option("effort", "quality"))
        await process.stdin.written.wait()
        process.feed(
            response(first_mid, {"configOptions": options("fast")}),
            response(process.stdin.request["id"], {"configOptions": options("quality")}),
        )

        assert await first == options("quality")
        assert await second == options("quality")
        assert client.session_config_options == options("quality")
        assert client.is_connected and not reader.done()


async def test_config_response_after_failed_transition_cannot_overwrite_restored_snapshot():
    async with transport() as (client, process, reader):
        install_old_state(client)
        previous = client._snapshot_session_state()
        request = asyncio.create_task(client.set_config_option("effort", "fast"))
        await process.stdin.written.wait()
        config_mid = process.stdin.request["id"]
        process.stdin.written.clear()
        transition = asyncio.create_task(client._rpc("session/new", {}, timeout=2))
        await process.stdin.written.wait()
        process.feed(
            {"jsonrpc": "2.0", "id": process.stdin.request["id"], "error": {"code": -32603, "message": "refused"}}
        )
        with pytest.raises(RPCError):
            await transition
        process.feed(response(config_mid, {"configOptions": options("fast")}))

        with pytest.raises(ACPProtocolError, match="Session changed during config request"):
            await request
        assert client._snapshot_session_state() == previous
        assert client.is_connected and not reader.done()


async def test_stale_config_response_still_requires_valid_schema():
    async with transport() as (client, process, _reader):
        install_old_state(client)
        request = asyncio.create_task(client.set_config_option("effort", "fast"))
        await process.stdin.written.wait()
        config_mid = process.stdin.request["id"]
        process.stdin.written.clear()
        transition = asyncio.create_task(client._rpc("session/new", {}, timeout=2))
        await process.stdin.written.wait()
        process.feed(
            response(process.stdin.request["id"], {"sessionId": "session-new", "configOptions": options("quality")}),
            response(config_mid, {"configOptions": "malformed"}),
        )
        with pytest.raises(ACPProtocolError, match="configOptions must be an array"):
            await request
        await transition
        assert client.session_id == "session-new"
        assert client.session_config_options == options("quality")
        assert not client.is_connected


@pytest.mark.parametrize("method", ["session/new", "session/load", "session/resume"])
@pytest.mark.parametrize("same_session_id", [False, True])
@pytest.mark.parametrize("request_kind", ["public", "raw"])
async def test_active_transition_rejects_config_requests_before_writing(method, same_session_id, request_kind):
    async with transport() as (client, process, reader):
        install_old_state(client)
        target = "session-old" if same_session_id else "session-new"
        params = {} if method == "session/new" else {"sessionId": target}
        transition = asyncio.create_task(client._rpc(method, params, timeout=2))
        await process.stdin.written.wait()
        transition_mid = process.stdin.request["id"]
        transition_result = {"configOptions": options("quality")}
        if method == "session/new":
            transition_result["sessionId"] = target
        updated = asyncio.Event()
        client.set_update_callback(lambda update: updated.set())
        # Legal in-transition metadata can repopulate options before the
        # transition response; its presence must not enable a config request.
        update_sid = "session-old" if method == "session/new" else target
        process.feed(
            notification({"sessionUpdate": "config_option_update", "configOptions": options("balanced")}, update_sid)
        )
        await asyncio.wait_for(updated.wait(), 2)
        before_mid = client._msg_id
        process.stdin.on_request = lambda request: process.feed(
            response(transition_mid, transition_result),
            response(request["id"], {"configOptions": options("fast")}),
        )
        try:
            with pytest.raises(ACPProtocolError, match="Cannot set config during session transition"):
                if request_kind == "public":
                    await client.set_config_option("effort", "fast")
                else:
                    await client._rpc(
                        "session/set_config_option", {"sessionId": target, "configId": "effort", "value": "fast"}
                    )
            # Raw RPCs may allocate an id, but registration must reject before
            # any bytes are written; public calls reject before invoking _rpc.
            assert process.stdin.request["id"] == transition_mid
            assert client._msg_id == before_mid + (request_kind == "raw")
            assert client.is_connected and not reader.done()
            process.feed(response(transition_mid, transition_result))
            await transition
            assert client.session_config_options == options("quality")
            process.stdin.on_request = lambda request: process.feed(
                response(request["id"], {"configOptions": options("fast")})
            )
            assert await client.set_config_option("effort", "fast") == options("fast")
            assert client.is_connected and not reader.done()
            assert client._pending == client._inflight == client._rpc_lifecycles == {}
        finally:
            if not transition.done():
                transition.cancel()
            with suppress(asyncio.CancelledError):
                await transition


@pytest.mark.parametrize("request_kind", ["public", "raw"])
async def test_committed_transition_keeps_config_blocked_until_its_waiter_finishes(request_kind):
    async with transport() as (client, process, reader):
        install_old_state(client)
        client.hold_result = True
        transition = asyncio.create_task(client._rpc("session/load", {"sessionId": "session-old"}, timeout=2))
        await process.stdin.written.wait()
        transition_mid = process.stdin.request["id"]
        process.feed(response(transition_mid, {"configOptions": options("quality")}))
        await asyncio.wait_for(client.result_observed.wait(), 2)
        assert client._session_transition_active
        process.stdin.on_request = lambda request: process.feed(
            response(request["id"], {"configOptions": options("fast")})
        )
        # Suppress only the fake waiter's hold for a subsequent config request.
        # The transition already passed its hold check and remains blocked.
        client.hold_result = False
        try:
            with pytest.raises(ACPProtocolError, match="Cannot set config during session transition"):
                if request_kind == "public":
                    await client.set_config_option("effort", "fast")
                else:
                    await client._rpc(
                        "session/set_config_option", {"sessionId": "session-old", "configId": "effort", "value": "fast"}
                    )
            assert process.stdin.request["id"] == transition_mid
            assert client.session_config_options == options("quality")
            assert client.is_connected and not reader.done()
        finally:
            client.release_result.set()
            await transition
        assert not client._session_transition_active
        assert await client.set_config_option("effort", "fast") == options("fast")
        assert client.is_connected and not reader.done()


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"sessionId": "session-new", "configOptions": "invalid"},
        {"sessionId": "session-new", "modes": {"currentModeId": "missing"}},
    ],
)
async def test_invalid_session_response_restores_entire_previous_state(result):
    async with transport() as (client, process, _reader):
        install_old_state(client)
        previous = client._snapshot_session_state()
        process.stdin.on_request = lambda request: process.feed(response(request["id"], result))

        with pytest.raises(ACPProtocolError):
            await client._rpc("session/new", {}, timeout=2)

        assert client._snapshot_session_state() == previous
        assert client._session_transition_active is False
        assert client._session_transition_id is None


async def test_explicit_rpc_error_rolls_back_without_committing_response_state():
    async with transport() as (client, process, _reader):
        install_old_state(client)
        previous = client._snapshot_session_state()
        process.stdin.on_request = lambda request: process.feed(
            {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32603, "message": "synthetic refusal"}}
        )

        with pytest.raises(RPCError):
            await client._rpc("session/new", {}, timeout=2)

        assert client._snapshot_session_state() == previous
        assert client.completions == []


@pytest.mark.parametrize("future_state", ["cancelled", "already_done"])
async def test_response_for_finished_pending_future_cannot_commit(future_state):
    async with transport() as (client, process, _reader):
        install_old_state(client)
        process.stdin.block_drain = True
        request = asyncio.create_task(client._rpc("session/new", {}, timeout=2))
        await process.stdin.written.wait()
        mid = process.stdin.request["id"]
        future = client._pending[mid]
        if future_state == "cancelled":
            future.cancel()
        else:
            future.set_result({"sessionId": "already-published"})
        before = client._snapshot_session_state()
        try:
            await client._dispatch(response(mid, {"sessionId": "late-must-not-commit"}))
            assert client._snapshot_session_state() == before
            assert client.completions == []
        finally:
            request.cancel()
            with suppress(asyncio.CancelledError):
                await request


@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
async def test_interruption_before_apply_ignores_response_while_cleanup_waits(interruption):
    async with transport() as (client, process, _reader):
        install_old_state(client)
        previous = client._snapshot_session_state()
        cancellation_entered, release_cancellation = asyncio.Event(), asyncio.Event()

        async def pause_cancellation(_mid):
            cancellation_entered.set()
            await release_cancellation.wait()

        client._notify_request_cancel = pause_cancellation
        if interruption == "timeout":

            async def expire_waiter(_future, _deadline):
                raise asyncio.TimeoutError

            client._await_rpc_future = expire_waiter
        request = asyncio.create_task(client._rpc("session/new", {}, timeout=2))
        await process.stdin.written.wait()
        mid = process.stdin.request["id"]
        if interruption == "cancel":
            request.cancel()
        await asyncio.wait_for(cancellation_entered.wait(), 2)
        during_cancel = client._snapshot_session_state()
        try:
            await client._dispatch(response(mid, {"sessionId": "late-must-not-commit"}))
            assert client._snapshot_session_state() == during_cancel
            assert client.completions == []
        finally:
            release_cancellation.set()
            with pytest.raises(asyncio.CancelledError if interruption == "cancel" else asyncio.TimeoutError):
                await request
        assert client._snapshot_session_state() == previous
        await client._dispatch(response(mid, {"sessionId": "after-cleanup-must-not-commit"}))
        assert client._snapshot_session_state() == previous


async def test_cancel_after_apply_keeps_wire_confirmed_session_and_following_metadata():
    async with transport() as (client, process, _reader):
        install_old_state(client)
        client.hold_result = True
        process.stdin.on_request = lambda request: process.feed(
            response(request["id"], {"sessionId": "session-new", "configOptions": options("fast"), "modes": modes()}),
            *wire_updates(),
        )
        request = asyncio.create_task(client._rpc("session/new", {}, timeout=2))
        try:
            await asyncio.wait_for(client.result_observed.wait(), 2)
            assert client.session_id == "session-new"
            assert client.session_mode_state["currentModeId"] == "code"
        finally:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request

        assert client.session_id == "session-new"
        assert client.session_mode_state["currentModeId"] == "code"
        assert client.session_config_options == options("quality")
        assert client.available_commands[0]["name"] == "latest"
        assert client.completions == ["session/new"]
        assert not client.is_connected


async def test_unknown_response_cannot_change_existing_session_state():
    async with transport() as (client, _process, _reader):
        install_old_state(client)
        previous = client._snapshot_session_state()
        await client._dispatch(response(999, {"sessionId": "unknown-must-not-commit", "modes": modes("code")}))
        assert client._snapshot_session_state() == previous
        assert client.completions == []


async def test_truly_foreign_update_still_ends_reader_after_valid_new_response():
    async with transport() as (client, process, reader):
        observed = []
        client.set_update_callback(observed.append)
        process.stdin.on_request = lambda request: process.feed(
            response(request["id"], {"sessionId": "session-new"}),
            notification({"sessionUpdate": "available_commands_update", "availableCommands": []}, "foreign-session"),
        )

        await client._rpc("session/new", {}, timeout=2)
        await asyncio.wait_for(asyncio.shield(reader), 2)

        assert not client.is_connected
        assert observed == []
        assert len(client.dispatch_errors) == 1 and isinstance(client.dispatch_errors[0], ACPProtocolError)
