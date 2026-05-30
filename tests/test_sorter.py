from cubby.app.sorter import Sorter
from cubby.domain.category import Category, Config, Settings


def _config(tmp_path, **settings):
    base = dict(source=tmp_path, delay=0, content_scan=False)
    base.update(settings)
    categories = (
        Category(name="Invoices", name_patterns=("invoice",)),
        Category(name="Images", extensions=frozenset({"png"}), strong_ext=True),
        Category(name="Documents", extensions=frozenset({"pdf", "txt"})),
    )
    return Config(settings=Settings(**base), categories=categories)


def test_plan_does_not_move(tmp_path):
    (tmp_path / "Invoice-1.pdf").write_text("x")
    config = _config(tmp_path)
    outcomes = Sorter(config).sort_once(apply=False)
    assert (tmp_path / "Invoice-1.pdf").exists()
    assert outcomes[0].category == "Invoices"
    assert outcomes[0].moved_to is None


def test_apply_moves_files(tmp_path):
    (tmp_path / "Invoice-1.pdf").write_text("x")
    (tmp_path / "photo.png").write_text("x")
    config = _config(tmp_path)
    Sorter(config).sort_once(apply=True)
    assert (tmp_path / "Invoices" / "Invoice-1.pdf").exists()
    assert (tmp_path / "Images" / "photo.png").exists()
    assert not (tmp_path / "Invoice-1.pdf").exists()


def test_apply_respects_age(tmp_path):
    fresh = tmp_path / "new.txt"
    fresh.write_text("x")
    config = _config(tmp_path, delay=60)
    moved = Sorter(config).sort_once(apply=True)
    assert moved == []  # too fresh
    assert fresh.exists()


def test_already_sorted_files_are_not_repicked(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    config = _config(tmp_path)
    sorter = Sorter(config)
    sorter.sort_once(apply=True)
    second = sorter.sort_once(apply=True)
    assert second == []  # the Documents/ folder is managed and skipped
