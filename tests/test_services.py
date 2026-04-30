"""Test host services."""

import os
import tempfile


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
