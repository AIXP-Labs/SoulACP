"""Test host services."""

import asyncio
import os
import stat
import sys
import tempfile
import threading
from types import SimpleNamespace

import pytest


def test_resolve_path_relative():
    from soulacp.services.path_utils import resolve_path

    cwd = "/home/user/project"
    result = resolve_path("src/main.py", cwd)
    assert "src" in str(result)
    assert "main.py" in str(result)


def test_resolve_path_rejects_traversal():
    from soulacp.services.path_utils import resolve_path

    cwd = "/home/user/project"
    try:
        result = resolve_path("../../etc/passwd", cwd)
        # Should either raise or resolve within cwd
        assert str(cwd) in str(result) or result is None
    except (ValueError, PermissionError):
        pass  # Expected — traversal blocked


def test_resolve_path_allows_only_configured_additional_roots(tmp_path):
    from soulacp.services.path_utils import resolve_path

    primary = tmp_path / "primary"
    additional = tmp_path / "additional"
    outside = tmp_path / "outside"
    for directory in (primary, additional, outside):
        directory.mkdir()

    assert resolve_path(str(additional / "file.txt"), str(primary), [str(additional)]) == additional / "file.txt"
    with pytest.raises(PermissionError):
        resolve_path(str(outside / "file.txt"), str(primary), [str(additional)])


def test_fs_service_read_write():
    from soulacp.services.fs_service import FSService

    with tempfile.TemporaryDirectory() as tmpdir:
        fs = FSService(cwd=tmpdir)

        # Write
        test_file = os.path.join(tmpdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("hello soulacp")

        # Read (async method)
        import asyncio

        result = asyncio.run(fs.read_text_file("test.txt"))
        assert "hello soulacp" in str(result)


@pytest.mark.asyncio
async def test_fs_service_honors_acp_line_and_returns_empty_write_result(tmp_path):
    from soulacp.services.fs_service import FSService

    fs = FSService(str(tmp_path))
    target = tmp_path / "lines.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = await fs.read_text_file(path=str(target), line=2, limit=1)
    write_result = await fs.write_text_file(path=str(tmp_path / "written.txt"), content="value")

    assert result == {"content": "two\n"}
    assert write_result == {}
    assert (tmp_path / "written.txt").read_text(encoding="utf-8") == "value"


@pytest.mark.asyncio
async def test_fs_write_finishes_physical_operation_before_propagating_cancellation(monkeypatch, tmp_path):
    from soulacp.services.fs_service import FSService

    fs = FSService(str(tmp_path))
    started = threading.Event()
    release = threading.Event()

    def blocked_write(_resolved, _content):
        started.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(fs, "_write_resolved", blocked_write)
    writing = asyncio.create_task(fs.write_text_file(path=str(tmp_path / "owned.txt"), content="value"))
    assert await asyncio.to_thread(started.wait, 5)

    writing.cancel()
    await asyncio.sleep(0)
    assert not writing.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await writing


@pytest.mark.asyncio
async def test_fs_owned_thread_retrieves_worker_failure_when_cancellation_wins_race(monkeypatch):
    import traceback

    from soulacp.services import fs_service as fs_service_module

    original_shield = asyncio.shield
    first_wait = True
    write_error = OSError("synthetic private worker diagnostic")

    async def cancellation_after_worker_finishes(task):
        nonlocal first_wait
        if first_wait:
            first_wait = False
            while not task.done():
                await asyncio.sleep(0)
            raise asyncio.CancelledError
        return await original_shield(task)

    def fail_write():
        raise write_error

    monkeypatch.setattr(fs_service_module.asyncio, "shield", cancellation_after_worker_finishes)

    with pytest.raises(asyncio.CancelledError) as caught:
        await fs_service_module._await_owned_thread(fail_write)

    assert isinstance(getattr(caught.value, "cause", None), OSError)
    assert caught.value.cause is write_error
    assert caught.value.__cause__ is not write_error
    assert "synthetic private worker diagnostic" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
async def test_fs_service_supports_configured_additional_roots(tmp_path):
    from soulacp.services.fs_service import FSService

    primary = tmp_path / "primary"
    additional = tmp_path / "additional"
    outside = tmp_path / "outside"
    for directory in (primary, additional, outside):
        directory.mkdir()
    target = additional / "shared.txt"
    target.write_text("shared", encoding="utf-8")
    fs = FSService(str(primary), [str(additional)])

    assert await fs.read_text_file(path=str(target)) == {"content": "shared"}
    with pytest.raises(PermissionError):
        await fs.read_text_file(path=str(outside / "blocked.txt"))


@pytest.mark.asyncio
async def test_fs_service_accepts_acp_zero_line_and_limit_boundaries(tmp_path):
    from soulacp.services.fs_service import FSService

    fs = FSService(str(tmp_path))
    target = tmp_path / "lines.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")

    assert await fs.read_text_file(path=str(target), line=0, limit=1) == {"content": "one\n"}
    assert await fs.read_text_file(path=str(target), line=1, limit=0) == {"content": ""}
    assert await fs.read_text_file(path=str(target), line=2**32 - 1, limit=1) == {"content": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["line", "offset"])
async def test_fs_service_rejects_line_values_above_acp_uint32(field, tmp_path):
    from soulacp.services.fs_service import FSService

    fs = FSService(str(tmp_path))
    target = tmp_path / "lines.txt"
    target.write_text("one\n", encoding="utf-8")

    with pytest.raises(ValueError, match="uint32"):
        await fs.read_text_file(path=str(target), **{field: 2**32})


@pytest.mark.asyncio
async def test_fs_service_rejects_invalid_unicode_as_bad_input(tmp_path):
    from soulacp.services.fs_service import FSService

    fs = FSService(str(tmp_path))

    with pytest.raises(ValueError, match="valid Unicode"):
        await fs.write_text_file(path=str(tmp_path / "invalid.txt"), content="\ud800")


@pytest.mark.asyncio
async def test_fs_service_rejects_directories_and_oversized_reads(tmp_path):
    from soulacp.services.fs_service import FSService

    fs = FSService(str(tmp_path))
    fs.MAX_FILE_BYTES = 3
    target = tmp_path / "large.txt"
    target.write_text("four", encoding="utf-8")

    with pytest.raises(IsADirectoryError):
        await fs.read_text_file(path=str(tmp_path))
    with pytest.raises(ValueError, match="read limit"):
        await fs.read_text_file(path=str(target))


def test_fs_service_enforces_limit_when_file_grows_after_open(monkeypatch, tmp_path):
    from soulacp.services import fs_service as fs_service_module
    from soulacp.services.fs_service import FSService

    fs = FSService(str(tmp_path))
    fs.MAX_FILE_BYTES = 3
    target = tmp_path / "growing.txt"
    target.write_text("four", encoding="utf-8")

    class ChangedFile:
        def is_dir(self):
            return False

        def stat(self):
            return SimpleNamespace(st_mode=target.stat().st_mode, st_size=0)

        def open(self, *args, **kwargs):
            return target.open(*args, **kwargs)

    monkeypatch.setattr(
        fs_service_module.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=target.stat().st_mode, st_size=0),
    )
    with pytest.raises(ValueError, match="read limit"):
        fs._read_resolved(ChangedFile(), 1, 1)


def test_fs_service_rejects_non_regular_files_before_open(tmp_path):
    from soulacp.services.fs_service import FSService

    fs = FSService(str(tmp_path))

    class SpecialFile:
        def is_dir(self):
            return False

        def stat(self):
            return SimpleNamespace(st_mode=stat.S_IFIFO, st_size=0)

        def open(self, *args, **kwargs):
            raise AssertionError("non-regular file must not be opened")

    with pytest.raises(ValueError, match="regular text file"):
        fs._read_resolved(SpecialFile(), 1, 1)


@pytest.mark.asyncio
async def test_fs_service_bounds_directory_listing(tmp_path):
    from soulacp.services.fs_service import FSService

    fs = FSService(str(tmp_path))
    fs.MAX_DIRECTORY_ENTRIES = 2
    for name in ("a", "b", "c"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    with pytest.raises(ValueError, match="entry listing limit"):
        await fs.list_directory(str(tmp_path))


@pytest.mark.asyncio
async def test_fs_service_bounds_total_directory_scan(tmp_path):
    from soulacp.services.fs_service import FSService

    fs = FSService(str(tmp_path))
    fs.MAX_DIRECTORY_SCAN_ENTRIES = 2
    for name in (".a", ".b", ".c"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    with pytest.raises(ValueError, match="entry scan limit"):
        await fs.list_directory(str(tmp_path))


@pytest.mark.asyncio
async def test_terminal_service_supports_acp_fields_live_output_and_exit(tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    created = await service.create(
        command=sys.executable,
        args=[
            "-u",
            "-c",
            "import os; print(os.environ['SOULACP_TEST']); print(os.getcwd())",
        ],
        env=[{"name": "SOULACP_TEST", "value": "visible"}],
        cwd=str(tmp_path),
        outputByteLimit=1024,
    )
    terminal_id = created["terminalId"]

    status = await service.wait_for_exit(terminalId=terminal_id)
    output = await service.get_output(terminalId=terminal_id)

    assert status == {"exitCode": 0, "signal": None}
    assert "visible" in output["output"]
    assert str(tmp_path) in output["output"]
    assert output["truncated"] is False
    assert "_meta" not in output
    assert output["exitStatus"] == status
    await service.release(terminalId=terminal_id)


def test_terminal_bare_command_is_resolved_before_spawn(monkeypatch, tmp_path):
    from soulacp.services import terminal_service as terminal_module
    from soulacp.services.terminal_service import TerminalService

    expected = str(tmp_path / "trusted" / "tool.exe")
    calls = []
    monkeypatch.setattr(
        terminal_module,
        "find_binary_in_environment",
        lambda names, environment: calls.append((names, environment)) or expected,
    )
    service = TerminalService(str(tmp_path))
    environment = {"PATH": str(tmp_path / "trusted")}

    assert service._resolve_command("tool", environment) == expected
    assert calls == [(["tool"], environment)]


def test_terminal_explicit_relative_command_is_not_path_searched(monkeypatch, tmp_path):
    from soulacp.services import terminal_service as terminal_module
    from soulacp.services.terminal_service import TerminalService

    monkeypatch.setattr(
        terminal_module,
        "find_binary_in_environment",
        lambda *_args: pytest.fail("explicit paths must not be searched"),
    )
    service = TerminalService(str(tmp_path))

    assert service._resolve_command(".\\tool.exe", {}) == ".\\tool.exe"


@pytest.mark.asyncio
async def test_terminal_service_enforces_utf8_byte_limit(tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    created = await service.create(
        command=sys.executable,
        args=["-u", "-c", "print('é' * 100)"],
        outputByteLimit=9,
    )
    terminal_id = created["terminalId"]
    await service.wait_for_exit(terminalId=terminal_id)
    output = await service.get_output(terminalId=terminal_id)

    assert output["truncated"] is True
    assert output["_meta"]["soulacp"]["truncated"] is True
    assert len(output["output"].encode()) <= 9
    await service.close()


@pytest.mark.asyncio
async def test_terminal_service_accepts_zero_output_byte_limit(tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    created = await service.create(
        command=sys.executable,
        args=["-u", "-c", "print('discarded')"],
        outputByteLimit=0,
    )
    terminal_id = created["terminalId"]
    await service.wait_for_exit(terminalId=terminal_id)
    output = await service.get_output(terminalId=terminal_id)

    assert output["output"] == ""
    assert output["truncated"] is True
    assert output["_meta"]["soulacp"]["truncated"] is True
    await service.close()


@pytest.mark.asyncio
async def test_terminal_service_accepts_explicit_null_output_limit(tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    created = await service.create(
        command=sys.executable,
        args=["-u", "-c", "print('default limit')"],
        outputByteLimit=None,
    )
    terminal_id = created["terminalId"]
    await service.wait_for_exit(terminalId=terminal_id)
    output = await service.get_output(terminalId=terminal_id)

    assert output["output"].strip() == "default limit"
    assert output["truncated"] is False
    await service.close()


@pytest.mark.asyncio
async def test_terminal_release_kills_running_process_and_invalidates_id(tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    created = await service.create(
        command=sys.executable,
        args=["-u", "-c", "import time; time.sleep(60)"],
    )
    terminal_id = created["terminalId"]
    process = service._terminals[terminal_id].process

    await service.release(terminalId=terminal_id)

    assert process.returncode is not None
    with pytest.raises(KeyError):
        await service.get_output(terminalId=terminal_id)


@pytest.mark.asyncio
async def test_terminal_service_rejects_cwd_outside_workspace(tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    with pytest.raises(PermissionError):
        await service.create(command=sys.executable, cwd=str(tmp_path.parent))


@pytest.mark.asyncio
async def test_host_services_reject_invalid_unicode_paths_before_os_io(tmp_path):
    from soulacp.services.fs_service import FSService
    from soulacp.services.terminal_service import TerminalService

    fs = FSService(str(tmp_path))
    terminal = TerminalService(str(tmp_path))

    with pytest.raises(ValueError, match="path must contain valid Unicode"):
        await fs.exists("\ud800")
    with pytest.raises(ValueError, match="terminal cwd must contain valid Unicode"):
        await terminal.create(command=sys.executable, cwd="\ud800")


@pytest.mark.asyncio
async def test_terminal_service_supports_configured_additional_roots(tmp_path):
    from soulacp.services.terminal_service import TerminalService

    primary = tmp_path / "primary"
    additional = tmp_path / "additional"
    primary.mkdir()
    additional.mkdir()
    service = TerminalService(str(primary), [str(additional)])

    created = await service.create(
        command=sys.executable,
        args=["-c", "import os; print(os.getcwd())"],
        cwd=str(additional),
    )
    terminal_id = created["terminalId"]
    try:
        await service.wait_for_exit(terminalId=terminal_id)
        output = await service.get_output(terminalId=terminal_id)
        assert str(additional.resolve()).lower() in output["output"].strip().lower()
    finally:
        await service.release(terminalId=terminal_id)


@pytest.mark.asyncio
async def test_terminal_service_rejects_non_list_args(tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    with pytest.raises(ValueError, match="args"):
        await service.create(command=sys.executable, args="")


@pytest.mark.asyncio
async def test_terminal_service_rejects_nul_in_args(tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    with pytest.raises(ValueError, match="NUL"):
        await service.create(command=sys.executable, args=["bad\x00arg"])


@pytest.mark.asyncio
async def test_terminal_service_bounds_command_args_and_env(monkeypatch, tmp_path):
    from soulacp.services import terminal_service as terminal_module
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))

    monkeypatch.setattr(terminal_module, "MAX_COMMAND_BYTES", 3)
    with pytest.raises(ValueError, match="terminal command exceeds"):
        await service.create(command="abcd")

    monkeypatch.setattr(terminal_module, "MAX_ARGUMENTS", 1)
    with pytest.raises(ValueError, match="argument limit"):
        await service.create(command="x", args=["a", "b"])

    monkeypatch.setattr(terminal_module, "MAX_ARGUMENTS", 10)
    monkeypatch.setattr(terminal_module, "MAX_ARGV_BYTES", 4)
    with pytest.raises(ValueError, match="command and args"):
        await service.create(command="x", args=["abc"])

    monkeypatch.setattr(terminal_module, "MAX_ENV_ENTRIES", 1)
    with pytest.raises(ValueError, match=r"env exceeds.*entry"):
        await service.create(
            command="x",
            env=[{"name": "A", "value": "1"}, {"name": "B", "value": "2"}],
        )

    monkeypatch.setattr(terminal_module, "MAX_ENV_ENTRIES", 10)
    monkeypatch.setattr(terminal_module, "MAX_ENV_BYTES", 5)
    with pytest.raises(ValueError, match=r"env exceeds.*byte"):
        await service.create(command="x", env=[{"name": "A", "value": "1234"}])


@pytest.mark.asyncio
async def test_terminal_service_bounds_unreleased_process_records(monkeypatch, tmp_path):
    from soulacp.services import terminal_service as terminal_module
    from soulacp.services.terminal_service import TerminalService

    monkeypatch.setattr(terminal_module, "MAX_TERMINALS", 1)
    service = TerminalService(str(tmp_path))
    created = await service.create(
        command=sys.executable,
        args=["-u", "-c", "import time; time.sleep(60)"],
    )
    try:
        with pytest.raises(RuntimeError, match="unreleased terminals"):
            await service.create(command=sys.executable, args=["-c", "pass"])
    finally:
        await service.release(terminalId=created["terminalId"])


class ControlledProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.wait_started = asyncio.Event()
        self.exited = asyncio.Event()
        self.terminate_calls = 0
        self.kill_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self.exited.set()

    async def wait(self):
        self.wait_started.set()
        await self.exited.wait()
        return self.returncode


class EmptyProcessOutput:
    async def read(self, _size: int = -1) -> bytes:
        return b""


class InheritedPipeOutput:
    def __init__(self) -> None:
        self.read_started = asyncio.Event()
        self.close_pipe = asyncio.Event()

    async def read(self, _size: int = -1) -> bytes:
        self.read_started.set()
        await self.close_pipe.wait()
        return b""


class ExitedWithInheritedPipeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.stdout = InheritedPipeOutput()

    async def wait(self):
        self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


class SpawnRaceProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.stdout = EmptyProcessOutput()
        self.exited = asyncio.Event()
        self.terminate_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0
        self.exited.set()

    def kill(self) -> None:
        self.returncode = -9
        self.exited.set()

    async def wait(self):
        await self.exited.wait()
        return self.returncode


class UnkillableProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.stdout = EmptyProcessOutput()
        self.can_exit = False

    def terminate(self) -> None:
        if self.can_exit:
            self.returncode = 0
            return
        raise PermissionError("terminate denied")

    def kill(self) -> None:
        if self.can_exit:
            self.returncode = -9
            return
        raise PermissionError("kill denied")

    async def wait(self):
        if self.returncode is not None:
            return self.returncode
        raise PermissionError("wait denied")


@pytest.mark.asyncio
async def test_terminal_wait_uses_direct_child_exit_when_descendant_holds_stdout(monkeypatch, tmp_path):
    from soulacp.services import terminal_service as terminal_module
    from soulacp.services.terminal_service import TerminalService

    monkeypatch.setattr(terminal_module, "CAPTURE_JOIN_TIMEOUT", 0.01)
    service = TerminalService(str(tmp_path))
    process = ExitedWithInheritedPipeProcess()

    async def fake_spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    created = await service.create(command=sys.executable)
    terminal_id = created["terminalId"]
    terminal = service._terminals[terminal_id]
    await process.stdout.read_started.wait()

    result = await asyncio.wait_for(service.wait_for_exit(terminalId=terminal_id), timeout=0.2)

    assert result == {"exitCode": 0, "signal": None}
    assert terminal.task is not None and terminal.task.done() is False

    process.stdout.close_pipe.set()
    await asyncio.wait_for(terminal.task, timeout=0.2)
    await service.release(terminalId=terminal_id)


@pytest.mark.asyncio
async def test_terminal_close_waits_for_inflight_create_cleanup(monkeypatch, tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    process = SpawnRaceProcess()
    spawn_started = asyncio.Event()
    allow_spawn = asyncio.Event()

    async def delayed_spawn(*_args, **_kwargs):
        spawn_started.set()
        await allow_spawn.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    creating = asyncio.create_task(service.create(command=sys.executable))
    await spawn_started.wait()
    closing = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    assert not closing.done()

    allow_spawn.set()
    with pytest.raises(RuntimeError, match="closed while creating"):
        await creating
    await closing

    assert process.terminate_calls == 1
    assert process.returncode == 0


@pytest.mark.asyncio
async def test_terminal_cancelled_spawn_is_claimed_and_reaped(monkeypatch, tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    process = SpawnRaceProcess()
    spawn_started = asyncio.Event()
    allow_spawn = asyncio.Event()

    async def delayed_spawn(*_args, **_kwargs):
        spawn_started.set()
        await allow_spawn.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    creating = asyncio.create_task(service.create(command=sys.executable))
    await spawn_started.wait()
    creating.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creating

    closing = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    assert not closing.done()
    allow_spawn.set()
    await closing

    assert process.terminate_calls == 1
    assert process.returncode == 0
    assert service._terminals == {}


@pytest.mark.asyncio
async def test_terminal_late_spawn_keeps_its_capacity_reserved(monkeypatch, tmp_path):
    from soulacp.services import terminal_service as terminal_module
    from soulacp.services.terminal_service import TerminalService

    monkeypatch.setattr(terminal_module, "MAX_TERMINALS", 1)
    service = TerminalService(str(tmp_path))
    late_process = SpawnRaceProcess()
    second_process = SpawnRaceProcess()
    spawn_started = asyncio.Event()
    allow_late_spawn = asyncio.Event()
    spawn_calls = 0

    async def controlled_spawn(*_args, **_kwargs):
        nonlocal spawn_calls
        spawn_calls += 1
        if spawn_calls == 1:
            spawn_started.set()
            await allow_late_spawn.wait()
            return late_process
        return second_process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", controlled_spawn)
    creating = asyncio.create_task(service.create(command=sys.executable))
    await spawn_started.wait()
    creating.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creating

    assert service._reserved_terminal_slots == 1
    try:
        with pytest.raises(RuntimeError, match="unreleased terminals"):
            await service.create(command=sys.executable)
        assert spawn_calls == 1
    finally:
        allow_late_spawn.set()
        await service.close()

    assert service._reserved_terminal_slots == 0
    assert late_process.terminate_calls == 1
    assert second_process.terminate_calls == 0


@pytest.mark.asyncio
async def test_terminal_close_reports_unreaped_late_spawn(monkeypatch, tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    process = UnkillableProcess()
    spawn_started = asyncio.Event()
    allow_spawn = asyncio.Event()

    async def delayed_spawn(*_args, **_kwargs):
        spawn_started.set()
        await allow_spawn.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    creating = asyncio.create_task(service.create(command=sys.executable))
    await spawn_started.wait()
    creating.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creating

    closing = asyncio.create_task(service.close())
    allow_spawn.set()
    with pytest.raises(RuntimeError, match="failed to terminate 1 terminal process"):
        await closing

    assert len(service._terminals) == 1
    process.can_exit = True
    await service.close()
    assert service._terminals == {}


@pytest.mark.asyncio
async def test_terminal_create_retains_unreaped_process_for_close_retry(monkeypatch, tmp_path):
    from soulacp.services.terminal_service import TerminalService

    service = TerminalService(str(tmp_path))
    process = UnkillableProcess()
    spawn_started = asyncio.Event()
    allow_spawn = asyncio.Event()

    async def delayed_spawn(*_args, **_kwargs):
        spawn_started.set()
        await allow_spawn.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    creating = asyncio.create_task(service.create(command=sys.executable))
    await spawn_started.wait()
    closing = asyncio.create_task(service.close())
    allow_spawn.set()

    with pytest.raises(RuntimeError, match="did not exit"):
        await creating
    with pytest.raises(RuntimeError, match="failed to terminate 1 terminal process"):
        await closing

    assert len(service._terminals) == 1
    assert next(iter(service._terminals.values())).process is process

    process.can_exit = True
    await service.close()
    assert service._terminals == {}


@pytest.mark.asyncio
async def test_terminal_close_finishes_physical_cleanup_before_cancellation():
    from soulacp.services.terminal_service import TerminalService, _Terminal

    service = TerminalService("C:/workspace")
    process = ControlledProcess()
    service._terminals["term"] = _Terminal(process=process, output_byte_limit=1024)

    closing = asyncio.create_task(service.close())
    await process.wait_started.wait()
    closing.cancel()
    await asyncio.sleep(0)

    assert not closing.done()
    process.returncode = 0
    process.exited.set()
    with pytest.raises(asyncio.CancelledError):
        await closing

    assert service._terminals == {}
    assert process.terminate_calls == 1


@pytest.mark.asyncio
async def test_terminal_release_finishes_after_caller_cancellation(tmp_path):
    from soulacp.services.terminal_service import TerminalService, _Terminal

    service = TerminalService(str(tmp_path))
    process = ControlledProcess()
    service._terminals["term"] = _Terminal(process=process, output_byte_limit=1024)

    releasing = asyncio.create_task(service.release(terminalId="term"))
    await process.wait_started.wait()
    releasing.cancel()
    await asyncio.sleep(0)
    releasing.cancel()
    await asyncio.sleep(0)

    assert not releasing.done()

    process.returncode = 0
    process.exited.set()
    with pytest.raises(asyncio.CancelledError):
        await releasing

    assert "term" not in service._terminals
    assert process.terminate_calls == 1


@pytest.mark.asyncio
async def test_terminal_release_and_close_share_one_physical_termination(tmp_path):
    from soulacp.services.terminal_service import TerminalService, _Terminal

    service = TerminalService(str(tmp_path))
    process = ControlledProcess()
    service._terminals["term"] = _Terminal(process=process, output_byte_limit=1024)

    releasing = asyncio.create_task(service.release(terminalId="term"))
    await process.wait_started.wait()
    closing = asyncio.create_task(service.close())
    await asyncio.sleep(0)

    process.returncode = 0
    process.exited.set()
    await asyncio.gather(releasing, closing)

    assert service._terminals == {}
    assert process.terminate_calls == 1


@pytest.mark.asyncio
async def test_terminal_release_preserves_ownership_when_process_cannot_be_reaped(tmp_path):
    from soulacp.services.terminal_service import TerminalService, _Terminal

    service = TerminalService(str(tmp_path))
    terminal = _Terminal(process=UnkillableProcess(), output_byte_limit=1024)
    service._terminals["term"] = terminal

    with pytest.raises(RuntimeError, match="did not exit"):
        await service.release(terminalId="term")

    assert service._terminals == {"term": terminal}
    assert terminal.exit_event.is_set() is False


@pytest.mark.asyncio
async def test_terminal_close_reports_and_retains_unreaped_process(tmp_path):
    from soulacp.services.terminal_service import TerminalService, _Terminal

    service = TerminalService(str(tmp_path))
    terminal = _Terminal(process=UnkillableProcess(), output_byte_limit=1024)
    service._terminals["term"] = terminal

    with pytest.raises(RuntimeError, match="failed to terminate 1 terminal process"):
        await service.close()

    assert service._terminals == {"term": terminal}
    assert terminal.exit_event.is_set() is False

    terminal.process.can_exit = True
    await service.close()

    assert service._terminals == {}
    assert terminal.exit_event.is_set() is True


@pytest.mark.asyncio
async def test_terminal_capture_does_not_publish_unknown_exit_status(tmp_path):
    from soulacp.services.terminal_service import TerminalService, _Terminal

    service = TerminalService(str(tmp_path))
    terminal = _Terminal(process=UnkillableProcess(), output_byte_limit=1024)

    await service._capture_output(terminal)

    assert terminal.exit_event.is_set() is False


@pytest.mark.asyncio
async def test_terminal_internal_terminate_propagates_cancellation(tmp_path):
    from soulacp.services.terminal_service import TerminalService, _Terminal

    service = TerminalService(str(tmp_path))
    process = ControlledProcess()
    process.returncode = 0
    terminal = _Terminal(process=process, output_byte_limit=1024)
    capture_started = asyncio.Event()

    async def blocked_capture():
        capture_started.set()
        await asyncio.Event().wait()

    terminal.task = asyncio.create_task(blocked_capture())
    await capture_started.wait()
    terminating = asyncio.create_task(service._terminate(terminal))
    await asyncio.sleep(0)

    terminating.cancel()
    with pytest.raises(asyncio.CancelledError):
        await terminating

    assert terminal.task.cancelled()
    assert terminal.exit_event.is_set()
