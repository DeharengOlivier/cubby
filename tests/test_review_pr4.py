"""Reproducers and pins for the independent review of pull request 4.

The review record is on the pull request.
"""

from __future__ import annotations

import faulthandler
import importlib.metadata
import os
import plistlib
import signal
import threading
import time
from pathlib import Path

import pytest

import cubby
from cubby.adapters.service import ServiceSpec
from cubby.adapters.service import launchd as launchd_mod
from cubby.adapters.service import systemd as systemd_mod
from cubby.adapters.service.base import STOP_TIMEOUT
from cubby.adapters.service.launchd import LaunchdService
from cubby.adapters.service.systemd import SystemdService
from cubby.app.sorter import Sorter
from cubby.app.watcher import StopRequest
from cubby.cli import main
from cubby.domain.category import Category, Config, Settings

# --- 2. the stop request takes no lock and ends a sleep promptly ---------------


def test_a_stop_request_is_a_plain_flag():
    stop = StopRequest()
    assert stop() is False
    stop.request(signal.SIGTERM, None)
    assert stop() is True
    assert not any(isinstance(v, type(threading.Lock())) for v in vars(stop).values())


def test_the_sleep_wakes_within_a_tick_of_a_stop():
    naps: list[float] = []
    stop = StopRequest(tick=0.5, nap=lambda s: (naps.append(s), stop.request()))
    stop.sleep(3600)
    assert naps == [0.5]


def test_the_sleep_lasts_its_time_without_a_stop():
    now = [0.0]
    naps: list[float] = []

    def nap(seconds: float) -> None:
        naps.append(seconds)
        now[0] += seconds

    StopRequest(tick=0.5, nap=nap).sleep(1.2, clock=lambda: now[0])
    assert naps == pytest.approx([0.5, 0.5, 0.2])


def test_sigterm_during_the_sleep_ends_the_agent(tmp_path, monkeypatch):
    # A regression here would hang; fail loudly instead.
    faulthandler.dump_traceback_later(30, exit=True)
    try:
        monkeypatch.setattr("cubby.app.watcher.Watcher._cycle", lambda self: 0)
        timer = threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM))
        timer.start()
        started = time.monotonic()
        assert main(["watch", "--source", str(tmp_path), "--interval", "1h"]) == 0
        assert time.monotonic() - started < 5
    finally:
        faulthandler.cancel_dump_traceback_later()


def test_watch_off_the_main_thread_does_not_touch_signals(tmp_path, monkeypatch):
    monkeypatch.setattr("cubby.app.watcher.Watcher.run", lambda self, **_: 0)
    before = signal.getsignal(signal.SIGTERM)
    result: list[int] = []
    worker = threading.Thread(
        target=lambda: result.append(main(["watch", "--source", str(tmp_path)]))
    )
    worker.start()
    worker.join(10)
    assert result == [0]
    assert signal.getsignal(signal.SIGTERM) is before


# --- 3. a stop ends a pass between two files; the units allow for it ------------


def test_a_stop_ends_the_pass_between_two_files(tmp_path):
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name)
    config = Config(
        settings=Settings(source=tmp_path, delay=0, content_scan=False),
        categories=(Category(name="Documents", extensions=frozenset({"txt"})),),
    )
    checks = iter([False, True])

    outcomes = Sorter(config).sort_once(apply=True, stop=lambda: next(checks, True))

    assert [o.name for o in outcomes] == ["a.txt"]
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["b.txt", "c.txt"]


def _spec(tmp_path: Path) -> ServiceSpec:
    return ServiceSpec(program_args=["/usr/bin/cubby", "watch"], log_path=tmp_path / "log")


def test_systemd_gives_the_agent_time_to_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(systemd_mod, "_UNIT_DIR", tmp_path)
    monkeypatch.setattr(systemd_mod, "require_success", lambda cmd, **_: None)
    monkeypatch.setattr(systemd_mod, "run_manager", lambda cmd, **_: None)
    monkeypatch.setattr(SystemdService, "is_running", lambda self, label=None: True)

    unit = SystemdService().install(_spec(tmp_path)).read_text()

    assert f"TimeoutStopSec={STOP_TIMEOUT}\n" in unit


def test_launchd_gives_the_agent_time_to_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(launchd_mod, "_AGENTS_DIR", tmp_path)
    monkeypatch.setattr(launchd_mod, "require_success", lambda cmd, **_: None)
    monkeypatch.setattr(launchd_mod, "run_manager", lambda cmd, **_: None)
    monkeypatch.setattr(LaunchdService, "is_running", lambda self, label=None: True)

    path = LaunchdService().install(_spec(tmp_path))

    assert plistlib.loads(path.read_bytes())["ExitTimeOut"] == STOP_TIMEOUT


# --- 4. the version the release tag is checked against is the wheel's ----------


def test_the_code_version_is_the_package_version():
    assert cubby.__version__ == importlib.metadata.version("cubby-sort")
