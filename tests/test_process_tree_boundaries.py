"""Synthetic process graph and Win32-handle boundaries; never target real PIDs."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from soulacp.adapters import base_client


def test_snapshot_keeps_only_proven_transitive_descendants(monkeypatch):
    monkeypatch.setattr(base_client.sys, "platform", "win32")
    monkeypatch.setattr(base_client, "_win_parent_map", lambda: {101: 100, 102: 101, 201: 200})

    assert base_client._snapshot_descendants(100) == {101, 102}
    assert 100 not in base_client._snapshot_descendants(100)


def test_snapshot_does_not_guess_a_missing_intermediate(monkeypatch):
    monkeypatch.setattr(base_client.sys, "platform", "win32")
    monkeypatch.setattr(base_client, "_win_parent_map", lambda: {102: 101, 201: 200})

    assert base_client._snapshot_descendants(100) == set()


def test_snapshot_failure_does_not_invent_descendants(monkeypatch):
    monkeypatch.setattr(base_client.sys, "platform", "win32")

    def unavailable():
        raise OSError("synthetic snapshot failure")

    monkeypatch.setattr(base_client, "_win_parent_map", unavailable)
    assert base_client._snapshot_descendants(100) == set()


def test_missing_windows_api_fails_without_a_fallback(monkeypatch):
    monkeypatch.delattr(ctypes, "windll", raising=False)

    with pytest.raises(AttributeError, match="windll"):
        base_client._win_parent_map()
    with pytest.raises(AttributeError, match="windll"):
        base_client._win_terminate_descendants({101}, 100)


@pytest.fixture
def win_kernel(monkeypatch):
    kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=501), TerminateProcess=Mock(return_value=1), CloseHandle=Mock()
    )
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    return kernel


def test_windows_termination_targets_only_recorded_current_chain(monkeypatch, win_kernel):
    monkeypatch.setattr(base_client, "_win_parent_map", lambda: {101: 100, 201: 200})

    assert base_client._win_terminate_descendants({101, 201}, 100) == [101]
    win_kernel.OpenProcess.assert_called_once_with(0x0001, False, 101)
    win_kernel.TerminateProcess.assert_called_once_with(501, 1)
    win_kernel.CloseHandle.assert_called_once_with(501)


def test_windows_termination_rejects_changed_parent(monkeypatch, win_kernel):
    monkeypatch.setattr(base_client, "_win_parent_map", lambda: {101: 900})

    assert base_client._win_terminate_descendants({101}, 100) == []
    win_kernel.OpenProcess.assert_not_called()
    win_kernel.TerminateProcess.assert_not_called()


def test_windows_snapshot_error_does_not_open_a_process(monkeypatch, win_kernel):
    def unavailable():
        raise OSError("synthetic snapshot failure")

    monkeypatch.setattr(base_client, "_win_parent_map", unavailable)
    assert base_client._win_terminate_descendants({101}, 100) == []
    win_kernel.OpenProcess.assert_not_called()
    win_kernel.TerminateProcess.assert_not_called()


def test_windows_access_denied_does_not_terminate_or_close_invalid_handle(monkeypatch, win_kernel):
    monkeypatch.setattr(base_client, "_win_parent_map", lambda: {101: 100})
    win_kernel.OpenProcess.return_value = None

    assert base_client._win_terminate_descendants({101}, 100) == []
    win_kernel.TerminateProcess.assert_not_called()
    win_kernel.CloseHandle.assert_not_called()


def test_windows_failed_termination_closes_owned_handle(monkeypatch, win_kernel):
    monkeypatch.setattr(base_client, "_win_parent_map", lambda: {101: 100})
    win_kernel.TerminateProcess.return_value = 0

    assert base_client._win_terminate_descendants({101}, 100) == []
    win_kernel.CloseHandle.assert_called_once_with(501)


def test_windows_termination_exception_still_closes_owned_handle(monkeypatch, win_kernel):
    monkeypatch.setattr(base_client, "_win_parent_map", lambda: {101: 100})
    win_kernel.TerminateProcess.side_effect = OSError("synthetic termination failure")

    with pytest.raises(OSError, match="synthetic termination failure"):
        base_client._win_terminate_descendants({101}, 100)
    win_kernel.CloseHandle.assert_called_once_with(501)
