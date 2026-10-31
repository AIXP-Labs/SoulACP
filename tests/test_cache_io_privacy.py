"""I/O privacy must preserve filesystem failure and durability semantics."""

from __future__ import annotations

import asyncio
import errno
import os
import traceback
from pathlib import Path

import pytest

from soulacp.cache import FileCache

MARKER = "synthetic-cache-location-should-not-appear"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["set", "flush", "close"])
async def test_parent_conflict_is_private_without_disguising_failure(tmp_path, operation):
    parent = tmp_path / MARKER
    parent.write_text("unchanged")
    cache = FileCache(parent / "sessions.json", debounce_seconds=60 if operation != "set" else 0)
    try:
        with pytest.raises(FileExistsError) as caught:
            await cache.set("synthetic-key", "synthetic-value")
            if operation != "set":
                await getattr(cache, operation)()
        error = caught.value
        assert error.errno == errno.EEXIST
        assert MARKER not in str(error) and MARKER not in repr(error)
        assert MARKER not in "".join(traceback.format_exception(error))
        assert error.cause.filename == str(parent)
        assert error.filename
        assert cache._dirty is True
        assert parent.read_text() == "unchanged"
    finally:
        task = cache._save_task
        if task is not None and not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["mkdir", "mkstemp", "write", "fsync", "replace"])
async def test_io_failures_keep_errno_raw_diagnostics_and_old_file(monkeypatch, tmp_path, stage):
    cache = FileCache(tmp_path / MARKER / "sessions.json")
    await cache.set("old", "value")
    before = cache._path.read_bytes()
    raw = PermissionError(errno.EACCES, MARKER, str(cache._path))

    def fail(*args, **kwargs):
        raise raw

    if stage == "mkdir":
        monkeypatch.setattr(Path, "mkdir", fail)
    elif stage == "mkstemp":
        monkeypatch.setattr("soulacp.cache.tempfile.mkstemp", fail)
    elif stage == "fsync":
        monkeypatch.setattr(os, "fsync", fail)
    elif stage == "replace":
        monkeypatch.setattr(Path, "replace", fail)
    else:
        original = os.fdopen

        class Writer:
            def __init__(self, fd, mode):
                self.file = original(fd, mode)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.file.close()

            def write(self, value):
                raise raw

        monkeypatch.setattr(os, "fdopen", Writer)

    with pytest.raises(PermissionError) as caught:
        await cache.set("new", "value")
    error = caught.value
    assert error is not raw
    assert error.cause is raw and raw.filename == str(cache._path)
    assert error.errno == raw.errno
    assert MARKER not in str(error) and MARKER not in repr(error)
    assert MARKER not in "".join(traceback.format_exception(error))
    assert cache._path.read_bytes() == before and cache._dirty is True
    assert not list(cache._path.parent.glob("*.tmp"))
    monkeypatch.undo()
    await cache.flush()
    assert await FileCache(cache._path).get("new") == "value"
    assert cache._dirty is False
