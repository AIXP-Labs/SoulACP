"""Raw text, safe resource errors, and nonblocking ownership boundaries."""

import asyncio
import io
import os
import stat
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

from soulacp.adapters.base_client import ACPClientBase
from soulacp.config import ACPConfig
from soulacp.services import fs_service
from soulacp.services.fs_service import FSService


class ErrorClient(ACPClientBase):
    def __init__(self, cwd):
        super().__init__(ACPConfig(cwd=str(cwd)))
        self._connected = True
        self.session_id = "synthetic-session"
        self.errors = []
        self.results = []

    def _get_acp_command(self):
        raise AssertionError("No provider in this fixture")

    async def _initialize(self):
        raise AssertionError("No provider in this fixture")

    async def _send_result(self, mid, result):
        self.results.append((mid, result))

    async def _send_error(self, mid, code, message):
        self.errors.append((mid, code, message))


async def request(client, method, **fields):
    await client._dispatch(
        {"jsonrpc": "2.0", "id": 11, "method": method, "params": {"sessionId": client.session_id, **fields}}
    )
    if client._request_tasks:
        await asyncio.gather(*list(client._request_tasks))
    await asyncio.sleep(0)


@pytest.mark.parametrize("text", ["a\r\nb\r\n", "a\rb\n", "a\n", "\ufeff\u4e2d\U0001f600\r\nTAIL", "a\x1ab"])
async def test_utf8_roundtrip_keeps_original_bytes(tmp_path, text):
    source = tmp_path / "source.txt"
    source.write_bytes(text.encode("utf-8"))
    service = FSService(str(tmp_path))
    assert await service.read_text_file(str(source)) == {"content": text}
    await service.write_text_file(str(source), text)
    assert source.read_bytes() == text.encode("utf-8")


async def test_d1_a_crlf_expansion_is_rejected_without_partial_result(tmp_path):
    source = tmp_path / "crlf.txt"
    source.write_bytes(b"a\r\n" * 3334)
    with pytest.raises(ValueError, match="read limit"):
        await FSService(str(tmp_path)).read_text_file(str(source))


async def test_invalid_utf8_keeps_raw_diagnostic_private(tmp_path):
    source = tmp_path / "invalid.txt"
    marker = "SYNTHETIC_ENCODING_SECRET"
    source.write_bytes(marker.encode() + b"\xff")
    with pytest.raises(ValueError) as caught:
        await FSService(str(tmp_path)).read_text_file(str(source))
    error = caught.value
    assert not isinstance(error, UnicodeDecodeError)
    assert isinstance(error.cause, UnicodeDecodeError)
    assert marker in repr(error.cause.object)
    assert marker not in str(error) + repr(error) + "".join(traceback.format_exception(error))


async def test_zero_limit_does_not_construct_text_decoder(tmp_path, monkeypatch):
    source = tmp_path / "zero.txt"
    source.write_bytes(b"\xff")

    def forbidden(*args, **kwargs):
        raise AssertionError("zero-line read must not decode content")

    monkeypatch.setattr(fs_service.io, "TextIOWrapper", forbidden)
    assert await FSService(str(tmp_path)).read_text_file(str(source), line=999, limit=0) == {"content": ""}


async def test_line_limit_checked_before_fetching_next_logical_line(tmp_path, monkeypatch):
    source = tmp_path / "range.txt"
    source.write_bytes(b"first\n" + b"x" * 30000)
    calls = []
    original = io.TextIOWrapper

    class TrackingText(original):
        def __next__(self):
            calls.append("next")
            return super().__next__()

    monkeypatch.setattr(fs_service.io, "TextIOWrapper", TrackingText)
    assert await FSService(str(tmp_path)).read_text_file(str(source), limit=1) == {"content": "first\n"}
    assert calls == ["next"]


@pytest.mark.parametrize("operation", ["read", "write"])
async def test_path_resolution_does_not_run_on_event_loop(tmp_path, monkeypatch, operation):
    path = tmp_path / "thread.txt"
    path.write_bytes(b"before")
    owner = threading.get_ident()
    original = fs_service.resolve_path

    def resolve(*args):
        assert threading.get_ident() != owner
        return original(*args)

    monkeypatch.setattr(fs_service, "resolve_path", resolve)
    service = FSService(str(tmp_path))
    if operation == "read":
        assert await service.read_text_file(str(path)) == {"content": "before"}
    else:
        assert await service.write_text_file(str(path), "after") == {}


async def test_write_rejects_nonregular_target_before_side_effect(tmp_path, monkeypatch):
    path = tmp_path / "special.txt"
    path.write_bytes(b"before")
    original_stat = Path.lstat

    def fake_lstat(value, *args, **kwargs):
        if value == path:
            return SimpleNamespace(st_mode=stat.S_IFIFO, st_size=0)
        return original_stat(value, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    with pytest.raises(ValueError, match="regular text file"):
        await FSService(str(tmp_path)).write_text_file(str(path), "after")
    assert path.read_bytes() == b"before"


@pytest.mark.parametrize("data,code", [(b"x" * 10001, 10001), (b"\xff", 10002)])
async def test_rpc_resource_and_encoding_errors_are_not_missing(tmp_path, data, code):
    path = tmp_path / "file.txt"
    path.write_bytes(data)
    client = ErrorClient(tmp_path)
    await request(client, "fs/read_text_file", path=str(path))
    assert not client.results
    assert len(client.errors) == 1
    _, actual, message = client.errors[0]
    assert actual == code
    assert all(token not in message for token in ("Resource not found", "ENOENT", "does not exist", "No such file"))
    assert str(path) not in message


async def test_missing_terminal_keeps_direct_keyerror_and_safe_rpc(tmp_path):
    client = ErrorClient(tmp_path)
    with pytest.raises(KeyError):
        await client._terminal_service.get_output(terminalId="unknown")
    await request(client, "terminal/output", terminalId="unknown")
    assert client.errors == [(11, -32002, "Resource not found")]


@pytest.mark.parametrize("terminal_id", [12, [], {}])
async def test_terminal_invalid_type_is_not_resource_missing(tmp_path, terminal_id):
    client = ErrorClient(tmp_path)
    await request(client, "terminal/output", terminalId=terminal_id)
    assert client.errors == [(11, -32602, "Invalid host request parameters")]


async def test_short_writes_are_completed_and_zero_progress_is_failure(tmp_path, monkeypatch):
    path = tmp_path / "short.txt"
    service = FSService(str(tmp_path))
    original = fs_service.os.write
    writes = []

    def short(fd, data):
        writes.append(len(data))
        return original(fd, data[:2])

    monkeypatch.setattr(fs_service.os, "write", short)
    assert await service.write_text_file(str(path), "abcdefgh") == {}
    assert path.read_bytes() == b"abcdefgh"
    assert writes == [8, 6, 4, 2]
    monkeypatch.setattr(fs_service.os, "write", lambda *_args: 0)
    with pytest.raises(OSError, match="no progress"):
        await service.write_text_file(str(path), "new")
    assert path.read_bytes() == b""


async def test_explicit_overwrite_does_not_decode_old_bytes(tmp_path):
    path = tmp_path / "replace.txt"
    path.write_bytes(b"\xff")
    assert await FSService(str(tmp_path)).write_text_file(str(path), "valid") == {}
    assert path.read_bytes() == b"valid"


async def test_replaced_target_is_not_truncated_before_identity_check(tmp_path, monkeypatch):
    path = tmp_path / "target.txt"
    path.write_bytes(b"original")
    original = fs_service.os.open
    switched = False

    def changed(target, flags, *args, **kwargs):
        nonlocal switched
        if Path(target) == path and not switched:
            switched = True
            assert not flags & os.O_TRUNC
            path.rename(tmp_path / "original.txt")
            path.write_bytes(b"replacement")
        return original(target, flags, *args, **kwargs)

    monkeypatch.setattr(fs_service.os, "open", changed)
    with pytest.raises(OSError, match="changed during safe open"):
        await FSService(str(tmp_path)).write_text_file(str(path), "after")
    assert path.read_bytes() == b"replacement"
    assert (tmp_path / "original.txt").read_bytes() == b"original"


async def test_new_file_race_does_not_overwrite_winner(tmp_path, monkeypatch):
    path = tmp_path / "race.txt"
    original = fs_service.os.open

    def raced(target, flags, *args, **kwargs):
        if Path(target) == path:
            assert flags & os.O_EXCL
            path.write_bytes(b"winner")
        return original(target, flags, *args, **kwargs)

    monkeypatch.setattr(fs_service.os, "open", raced)
    with pytest.raises(FileExistsError):
        await FSService(str(tmp_path)).write_text_file(str(path), "after")
    assert path.read_bytes() == b"winner"


@pytest.mark.parametrize("data", [b"\xef\xbb\xbf\r\n\r", b"a\v\f\xc2\x85\xe2\x80\xa8b\r\n"])
async def test_universal_newlines_do_not_split_other_unicode_separators(tmp_path, data):
    path = tmp_path / "separators.txt"
    path.write_bytes(data)
    assert await FSService(str(tmp_path)).read_text_file(str(path), limit=1) == {
        "content": data.decode("utf-8").partition("\r")[0] + "\r\n"
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows DOS devices; POSIX covered by isolated FIFO probe")
@pytest.mark.parametrize("name", ["NUL", "con.txt", "COM1", "LPT1.log"])
async def test_windows_device_names_are_rejected_without_open(tmp_path, monkeypatch, name):
    def forbidden(*args):
        raise AssertionError("device must not be opened")

    monkeypatch.setattr(fs_service, "_open_regular", forbidden)
    with pytest.raises(ValueError, match="regular text file"):
        await FSService(str(tmp_path)).write_text_file(str(tmp_path / name), "secret")


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO; Windows has separate device-name validation")
@pytest.mark.parametrize("operation", ["read", "write"])
def test_real_fifo_is_rejected_in_owned_subprocess(tmp_path, operation):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    script = (
        "import asyncio,sys;from soulacp.services.fs_service import FSService;"
        "fs=FSService(sys.argv[1]);"
        "operation=fs.read_text_file(sys.argv[2]) if sys.argv[3]=='read' else fs.write_text_file(sys.argv[2],'x');"
        "\ntry: asyncio.run(operation)"
        "\nexcept ValueError: print('SAFE_REJECTION')"
        "\nelse: raise AssertionError('special file accepted')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(fifo), operation],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SAFE_REJECTION"
