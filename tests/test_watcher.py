from cubby.app.sorter import Sorter
from cubby.app.watcher import Watcher
from cubby.domain.category import Category, Config, Settings


def _config(tmp_path):
    return Config(
        settings=Settings(source=tmp_path, delay=0, content_scan=False),
        categories=(Category(name="Documents", extensions=frozenset({"txt"})),),
    )


def test_watcher_sorts_each_cycle(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    sorter = Sorter(_config(tmp_path))
    slept: list[float] = []
    watcher = Watcher(sorter, interval=5, sleep=slept.append)

    total = watcher.run(max_cycles=1)

    assert total == 1
    assert (tmp_path / "Documents" / "a.txt").exists()
    # max_cycles stops us before any sleep happens.
    assert slept == []


def test_watcher_stop_predicate_breaks_loop(tmp_path):
    sorter = Sorter(_config(tmp_path))
    cycles = {"n": 0}

    def stop() -> bool:
        cycles["n"] += 1
        return cycles["n"] >= 3

    watcher = Watcher(sorter, interval=0, sleep=lambda _: None)
    watcher.run(stop=stop)
    assert cycles["n"] == 3


def test_watcher_picks_up_files_appearing_between_cycles(tmp_path):
    config = _config(tmp_path)
    sorter = Sorter(config)
    state = {"cycle": 0}

    def sleep(_: float) -> None:
        # A new file lands while the watcher is "sleeping".
        (tmp_path / "late.txt").write_text("x")

    def stop() -> bool:
        state["cycle"] += 1
        return state["cycle"] >= 2

    Watcher(sorter, interval=0, sleep=sleep).run(stop=stop)
    assert (tmp_path / "Documents" / "late.txt").exists()
