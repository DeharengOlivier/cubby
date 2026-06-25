from cubby.adapters.ui import Palette, banner


def test_palette_disabled_is_noop():
    p = Palette(False)
    assert p.bold("x") == "x"
    assert p.accent("x") == "x"


def test_palette_enabled_wraps_with_ansi():
    p = Palette(True)
    out = p.bold("x")
    assert out.startswith("\033[") and out.endswith("\033[0m") and "x" in out


def test_banner_contains_name_and_version():
    text = banner(Palette(False))
    assert "cubby" in text
    assert "v" in text  # version line


def test_banner_has_no_ansi_when_disabled():
    assert "\033[" not in banner(Palette(False))


def test_banner_has_ansi_when_enabled():
    assert "\033[" in banner(Palette(True))
