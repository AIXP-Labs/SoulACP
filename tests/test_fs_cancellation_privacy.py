"""Keep owned worker failures private without weakening cancellation ownership."""

import asyncio
import errno
import threading
import traceback

import pytest

from soulacp.services import fs_service

MARKER = "SYNTHETIC_PRIVATE_WORKER_VALUE"


def worker_error(kind):
    if kind is OSError:
        return OSError(errno.ENOENT, MARKER, "/synthetic/private-worker.txt")
    return kind(MARKER)


def assert_private_cancellation(cancelled, original):
    assert isinstance(cancelled, asyncio.CancelledError)
    assert getattr(cancelled, "cause", None) is original
    assert cancelled.__cause__ is not original
    assert cancelled.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(cancelled))
    assert MARKER not in rendered
    assert "/synthetic/private-worker.txt" not in rendered
    assert MARKER in str(cancelled.cause)


async def capture_cancellation(awaitable, captured):
    try:
        return await awaitable
    except asyncio.CancelledError as error:
        captured.append(error)
        raise


@pytest.mark.parametrize("kind", [OSError, RuntimeError])
@pytest.mark.parametrize("sensitive", ["0", "1"])
async def test_finished_worker_failure_and_cancellation_keep_private_cause(monkeypatch, kind, sensitive):
    monkeypatch.setenv("ACP_LOG_SENSITIVE_DATA", sensitive)
    original_shield = asyncio.shield
    first = True
    error = worker_error(kind)
    cancellation = asyncio.CancelledError("synthetic-stop")

    async def cancelled_after_finish(task):
        nonlocal first
        if first:
            first = False
            while not task.done():
                await asyncio.sleep(0)
            raise cancellation
        return await original_shield(task)

    def fail():
        raise error

    monkeypatch.setattr(fs_service.asyncio, "shield", cancelled_after_finish)
    with pytest.raises(asyncio.CancelledError) as caught:
        await fs_service._await_owned_thread(fail)
    assert caught.value.args == ("synthetic-stop",)
    assert caught.value is cancellation
    assert_private_cancellation(caught.value, error)


@pytest.mark.parametrize("kind", [OSError, RuntimeError])
@pytest.mark.parametrize("sensitive", ["0", "1"])
async def test_worker_failure_after_repeated_cancellation_stays_private(monkeypatch, kind, sensitive):
    monkeypatch.setenv("ACP_LOG_SENSITIVE_DATA", sensitive)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    error = worker_error(kind)

    def fail_after_release():
        started.set()
        assert release.wait(5)
        finished.set()
        raise error

    captured = []
    task = asyncio.create_task(capture_cancellation(fs_service._await_owned_thread(fail_after_release), captured))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel("first-stop")
        await asyncio.sleep(0)
        task.cancel("second-stop")
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert finished.is_set()
    assert captured[0].args == ("first-stop",)
    assert_private_cancellation(captured[0], error)
    assert MARKER not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("kind", [OSError, RuntimeError])
async def test_uncancelled_worker_failure_retains_original_type_and_identity(kind):
    error = worker_error(kind)

    def fail():
        raise error

    with pytest.raises(kind) as caught:
        await fs_service._await_owned_thread(fail)
    assert caught.value is error


@pytest.mark.parametrize("operation", ["read", "write"])
async def test_successful_physical_io_finishes_before_repeated_cancel_returns(tmp_path, monkeypatch, operation):
    service = fs_service.FSService(str(tmp_path))
    path = tmp_path / "synthetic.txt"
    path.write_text("before", encoding="utf-8")
    method = "_read_resolved" if operation == "read" else "_write_resolved"
    original = getattr(service, method)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocked(*args):
        started.set()
        assert release.wait(5)
        result = original(*args)
        finished.set()
        return result

    monkeypatch.setattr(service, method, blocked)
    coroutine = (
        service.read_text_file(str(path)) if operation == "read" else service.write_text_file(str(path), "after")
    )
    captured = []
    task = asyncio.create_task(capture_cancellation(coroutine, captured))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel("first-stop")
        await asyncio.sleep(0)
        task.cancel("second-stop")
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert captured[0].args == ("first-stop",)
    assert finished.is_set()
    assert getattr(captured[0], "cause", None) is None
    assert MARKER not in "".join(traceback.format_exception(caught.value))
    assert path.read_text(encoding="utf-8") == ("before" if operation == "read" else "after")


async def test_cancelled_read_missing_file_preserves_private_os_error(tmp_path, monkeypatch):
    service = fs_service.FSService(str(tmp_path))
    path = tmp_path / "synthetic.txt"
    path.write_text("synthetic", encoding="utf-8")
    original = service._read_resolved
    started = threading.Event()
    release = threading.Event()

    def blocked(*args):
        started.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(service, "_read_resolved", blocked)
    captured = []
    task = asyncio.create_task(capture_cancellation(service.read_text_file(str(path)), captured))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        path.unlink()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    cause = getattr(captured[0], "cause", None)
    assert isinstance(cause, FileNotFoundError)
    assert cause.filename == str(path)
    assert str(path) not in "".join(traceback.format_exception(caught.value))
