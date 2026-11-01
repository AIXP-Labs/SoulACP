"""Physical transport exit must precede release of an owned file worker."""

import asyncio
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import pytest

from soulacp.adapters.base_client import ACPClientBase
from soulacp.adapters.opencode_client import OpenCodeACPClient
from soulacp.config import ACPConfig


class LocalClient(ACPClientBase):
    def _get_acp_command(self):
        return [sys.executable, "-u", "-c", "import time; time.sleep(60)"]

    async def _initialize(self):
        self.session_id = "synthetic-local-session"


async def eventually(condition, timeout=4.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert condition()


@pytest.mark.parametrize("backend", ["base", "opencode"])
async def test_real_parent_exits_before_owned_fs_worker_is_released(tmp_path, monkeypatch, backend):
    config = ACPConfig(cwd=str(tmp_path))
    executor = None
    if backend == "base":
        client = LocalClient(config)
        await client.connect()
        process = client.process

        def exited():
            return process.returncode is not None
    else:
        client = OpenCodeACPClient(config)
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", "import time; time.sleep(60)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        client._popen = process
        client._connected = True
        client.session_id = "synthetic-popen-session"
        executor = ThreadPoolExecutor(max_workers=1)
        asyncio.get_running_loop().set_default_executor(executor)

        def exited():
            return process.poll() is not None

    async def cancel():
        pass

    monkeypatch.setattr(client, "cancel", cancel)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocked(*_args):
        started.set()
        assert release.wait(10)
        finished.set()
        return {"content": "once"}

    monkeypatch.setattr(client._fs_service, "_read_resolved", blocked)
    disconnect = None
    try:
        await client._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "fs/read_text_file",
                "params": {"sessionId": client.session_id, "path": str(tmp_path / "file")},
            }
        )
        await eventually(started.is_set)
        disconnect = asyncio.create_task(client.disconnect())
        await eventually(exited)
        assert not release.is_set()
        assert not finished.is_set()
        assert not disconnect.done()
    finally:
        release.set()
        if disconnect is not None:
            await asyncio.wait_for(asyncio.shield(disconnect), 10)
        else:
            await client.disconnect()
        if executor is not None:
            executor.shutdown(wait=True)
        with suppress(AttributeError):
            assert client.process is None
    assert finished.is_set()
    assert not client._request_tasks
