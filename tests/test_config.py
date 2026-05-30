import textwrap

from cubby.adapters.config import load_config


def test_load_default_config():
    config = load_config(user_path=None)
    names = [c.name for c in config.categories]
    assert "Invoices" in names
    assert "Documents" in names
    # Documents is the catch-all and must come after specific categories.
    assert names.index("Invoices") < names.index("Documents")
    assert config.settings.delay == 60.0
    assert config.settings.interval == 30.0


def test_strong_ext_flag_loaded():
    config = load_config(user_path=None)
    installers = next(c for c in config.categories if c.name == "Installers")
    assert installers.strong_ext is True
    assert "dmg" in installers.extensions


def test_user_file_overrides_settings(tmp_path):
    user = tmp_path / "config.toml"
    user.write_text(
        textwrap.dedent("""
        [settings]
        delay = "5m"
        source = "~/Inbox"
    """)
    )
    config = load_config(user_path=user)
    assert config.settings.delay == 300.0
    assert config.settings.source.name == "Inbox"
    # Categories still come from defaults since the user file defined none.
    assert any(c.name == "Images" for c in config.categories)


def test_user_categories_replace_defaults(tmp_path):
    user = tmp_path / "config.toml"
    user.write_text(
        textwrap.dedent("""
        [[category]]
        name = "OnlyThis"
        extensions = ["pdf"]
    """)
    )
    config = load_config(user_path=user)
    assert [c.name for c in config.categories] == ["OnlyThis"]


def test_cli_overrides_win(tmp_path):
    config = load_config(user_path=None, overrides={"settings": {"delay": 0}})
    assert config.settings.delay == 0.0
