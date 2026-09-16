"""The two rules that make the dme transport find Project Rio at all.

Both are Windows-only in effect and silent when broken: the overlay simply
sits on "Waiting for controller data..." forever with Rio running in front of
it, which is indistinguishable from "no controller plugged in".

Run from the submodule root with gc-overlay's own venv:

    ./venv/bin/python -m pytest tests -q

Not part of PRSH's suite (`testpaths = ["tests"]` at the PRSH root does not
reach a submodule), the same way pyrio carries its own.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dolphin_process as dp  # noqa: E402


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(dp.platform, "system", lambda: "Windows")


@pytest.fixture
def processes(monkeypatch):
    def _set(names):
        monkeypatch.setattr(dp, "_running_process_names", lambda: list(names))
    return _set


# ── 1. which name discovery commits to ──────────────────────────────────────

def test_project_rio_is_found(on_windows, processes):
    """The whole bug. DME's own list is Dolphin.exe / DolphinQt2.exe /
    DolphinWx.exe, and Project Rio renames its executable."""
    processes(["chrome.exe", "obs64.exe", "Project Rio.exe"])
    assert dp.find_dolphin_process_name() == "Project Rio.exe"


def test_stock_dolphin_still_works(on_windows, processes):
    """DME_DOLPHIN_PROCESS_NAME REPLACES the default list rather than adding
    to it, so discovery has to cover the names DME would have matched itself
    — otherwise fixing Rio breaks plain Dolphin."""
    processes(["chrome.exe", "Dolphin.exe"])
    assert dp.find_dolphin_process_name() == "Dolphin.exe"


def test_rio_wins_when_both_are_running(on_windows, processes):
    """PRSH is a Mario Superstar Baseball app; a producer with both open
    means the fork."""
    processes(["Dolphin.exe", "Project Rio.exe"])
    assert dp.find_dolphin_process_name() == "Project Rio.exe"


def test_an_unlisted_fork_is_still_found(on_windows, processes):
    processes(["chrome.exe", "RioDolphinNightly.exe"])
    assert dp.find_dolphin_process_name() == "RioDolphinNightly.exe"


@pytest.mark.parametrize("name", ["Trio.exe", "Rioja.exe", "MarioRio.exe", "obs64.exe"])
def test_the_fuzzy_match_does_not_grab_a_bystander(on_windows, processes, name):
    """Hooking the wrong process reads garbage memory and draws noise, which
    is worse than drawing nothing."""
    processes(["chrome.exe", name])
    assert dp.find_dolphin_process_name() is None


def test_nothing_running_answers_none(on_windows, processes):
    processes(["chrome.exe", "obs64.exe"])
    assert dp.find_dolphin_process_name() is None


def test_an_unreadable_process_list_answers_none(on_windows, processes):
    """Which the adapter reads as 'don't know' and lets DME try its own
    names — the behaviour this replaced."""
    processes([])
    assert dp.find_dolphin_process_name() is None


def test_macos_is_not_a_platform_this_answers_for():
    """macOS uses the memorywatcher transport, which connects to a socket and
    never asks what the process is called."""
    assert dp.process_list_is_readable() is (sys.platform != "darwin")
