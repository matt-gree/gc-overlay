"""The ordering rule the dme transport is built around.

dolphin-memory-engine reads DME_DOLPHIN_PROCESS_NAME into a **static** on the
first findPID() of the process:

    static const char* const s_dolphinProcessName{std::getenv(...)};

So the value is captured once, on the FIRST hook attempt, and every later
attempt reuses it. Hooking before a process exists captures it as unset and
Project Rio can never be found for the rest of the run — which is exactly the
shape of the bug this replaced, because the old loop called hook() every
second from startup.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dme_adapter  # noqa: E402


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.delenv("DME_DOLPHIN_PROCESS_NAME", raising=False)
    monkeypatch.setattr(dme_adapter, "process_list_is_readable", lambda: True)
    a = dme_adapter.DmeAdapter.__new__(dme_adapter.DmeAdapter)
    a._hook_name = None
    a._announced_wait = False
    return a


def test_no_process_means_no_hook_attempt(adapter, monkeypatch):
    """The refusal IS the fix: a hook attempt here burns the static."""
    monkeypatch.setattr(dme_adapter, "find_dolphin_process_name", lambda: None)
    assert adapter._prepare_hook() is False
    assert "DME_DOLPHIN_PROCESS_NAME" not in os.environ


def test_the_name_is_set_before_hooking_is_allowed(adapter, monkeypatch):
    monkeypatch.setattr(dme_adapter, "find_dolphin_process_name", lambda: "Project Rio.exe")
    assert adapter._prepare_hook() is True
    assert os.environ["DME_DOLPHIN_PROCESS_NAME"] == "Project Rio.exe"
    assert adapter._hook_name == "Project Rio.exe"


def test_the_name_is_committed_to_for_the_run(adapter, monkeypatch):
    """Re-deciding per retry would be pure noise — DME has already cached it."""
    monkeypatch.setattr(dme_adapter, "find_dolphin_process_name", lambda: "Project Rio.exe")
    adapter._prepare_hook()

    def _never_called():
        raise AssertionError("discovery re-ran after the name was committed")

    monkeypatch.setattr(dme_adapter, "find_dolphin_process_name", _never_called)
    assert adapter._prepare_hook() is True
    assert os.environ["DME_DOLPHIN_PROCESS_NAME"] == "Project Rio.exe"


def test_an_unreadable_process_list_falls_back_to_dmes_own_names(adapter, monkeypatch):
    """macOS, or any platform we cannot enumerate: hook blind rather than
    never hooking at all."""
    monkeypatch.setattr(dme_adapter, "process_list_is_readable", lambda: False)
    assert adapter._prepare_hook() is True
    assert "DME_DOLPHIN_PROCESS_NAME" not in os.environ
    assert adapter._hook_name is None
