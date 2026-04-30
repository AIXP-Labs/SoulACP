"""Test binary discovery."""


def test_find_binary_existing():
    from soulacp.binary import find_binary

    # python should exist on any system
    result = find_binary(["python3", "python"])
    assert result is not None


def test_find_binary_nonexistent():
    from soulacp.binary import find_binary

    result = find_binary(["nonexistent_binary_xyz_123"])
    assert result is None


def test_find_claude_binary():
    from soulacp.binary import find_claude_binary

    # May or may not be installed, just verify it doesn't crash
    result = find_claude_binary()
    # result is str or None, both are valid
    assert result is None or isinstance(result, str)


def test_find_gemini_binary():
    from soulacp.binary import find_gemini_binary

    result = find_gemini_binary()
    assert result is None or isinstance(result, str)
