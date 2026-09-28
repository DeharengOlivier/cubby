"""Reproducers for the independent review of pull request 5.

The review record is on the pull request.
"""

from __future__ import annotations

import pytest

from cubby.adapters.pause import current_pause, pause_path
from cubby.app.report import SortOutcome
from cubby.app.sorter import Sorter
from cubby.app.watcher import Watcher
from cubby.cli import EXIT_OK, main
from cubby.domain.category import Category, Config, Settings

# --- 1. no pause file can crash the agent; every odd one pauses --------------


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xfe not utf-8",
        b'{"since": "x", "until": NaN}',
        b'{"since": "x", "until": Infinity}',
        b'{"since": "x", "until": 1e300}',
        b'{"since": null, "until": null}',
    ],
)
def test_an_odd_pause_file_pauses_and_is_described(content, capsys, monkeypatch):
    pause_path().parent.mkdir(parents=True, exist_ok=True)
    pause_path().write_bytes(content)

    pause = current_pause()

    assert pause is not None
    assert pause.damaged
    assert "cubby resume" in pause.describe()
    monkeypatch.setattr("cubby.cli.detect_service", lambda: None)
    assert main(["status"]) == EXIT_OK
    assert "no file is moved" in capsys.readouterr().out


def test_a_pause_check_that_raises_counts_as_paused(tmp_path):
    class Sorter_:
        source = tmp_path
        passes = 0

        def sort_once(self, **_):
            self.passes += 1
            return []

    def broken() -> str | None:
        raise ValueError("boom")

    sorter = Sorter_()
    Watcher(sorter, 1.0, sleep=lambda _: None, paused=broken).run(max_cycles=2)
    assert sorter.passes == 0


def test_pause_for_is_capped(capsys):
    with pytest.raises(SystemExit) as info:
        main(["pause", "--for", "99999999999d"])
    assert info.value.code == 2
    assert not pause_path().exists()


# --- 3. a pause stops the pass in progress between two files -----------------


def test_a_pause_stops_a_pass_between_two_files(tmp_path):
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name)
    config = Config(
        settings=Settings(source=tmp_path, delay=0, content_scan=False),
        categories=(Category(name="Documents", extensions=frozenset({"txt"})),),
    )
    checks = iter([None, None, "paused since now"])  # before the pass, file a, file b

    moved = Watcher(
        Sorter(config), 1.0, sleep=lambda _: None, paused=lambda: next(checks, "paused")
    ).run(max_cycles=1)

    assert moved == 1
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["b.txt", "c.txt"]


# --- 5. a file that recovers and fails again is announced again --------------


def test_a_file_failing_again_after_recovering_is_announced_again(tmp_path):
    failing = [SortOutcome.failed(tmp_path / "x.pdf", "Permission denied")]
    rounds = iter([failing, [], failing])

    class Sorter_:
        source = tmp_path

        def sort_once(self, **_):
            return next(rounds)

    alerts: list[str] = []
    Watcher(Sorter_(), 1.0, sleep=lambda _: None, alert=alerts.append).run(max_cycles=3)
    assert len(alerts) == 2
