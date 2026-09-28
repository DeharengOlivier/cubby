"""Tests for the commands that manage the background agent.

These install a launchd or systemd unit on the user's machine and bake the
flags given to `cubby install` into the command the agent will run forever
afterwards. A mistake there is silent: the agent watches the wrong folder, or
with the wrong delay, and nobody finds out until files are in the wrong place.

The backends themselves are covered by test_service.py; what is exercised here
is the CLI around them, with a fake service so nothing is ever installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cubby.adapters import state
from cubby.cli import EXIT_OK, build_parser, main
from cubby.cli import agent as cli_agent


class FakeService:
    """Records what the CLI asked of it, installs nothing."""

    name = "fake"

    def __init__(self, *, installed: bool = False) -> None:
        self.installed = installed
        self.spec = None
        self.uninstalled = False

    def install(self, spec):
        self.spec = spec
        self.installed = True
        return Path("/tmp/fake.agent")

    def uninstall(self, label: str = "com.cubby.agent") -> bool:
        was_installed, self.installed = self.installed, False
        self.uninstalled = True
        return was_installed

    def unit_path(self, label: str) -> Path:
        return Path("/tmp/fake.agent")

    def program_args(self, label: str = "com.cubby.agent") -> list[str] | None:
        return None  # no unit to read: readiness uses the default config

    def is_installed(self, label: str = "com.cubby.agent") -> bool:
        return self.installed

    def is_running(self, label: str = "com.cubby.agent") -> bool:
        return self.installed


@pytest.fixture(autouse=True)
def _home_with_downloads(tmp_path, monkeypatch):
    """A home whose default ~/Downloads exists, so flag-less commands have a source."""
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def fake_service(monkeypatch):
    service = FakeService()
    monkeypatch.setattr(cli_agent, "get_service", lambda: service)
    monkeypatch.setattr(cli_agent, "detect_service", lambda: service)
    return service


@pytest.fixture
def config_file(tmp_path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text('[[category]]\nname = "Documents"\nextensions = ["pdf"]\n', "utf-8")
    return path


# --- what gets baked into the agent -----------------------------------------


def test_install_bakes_the_requested_settings_into_the_agent(
    tmp_path, config_file, fake_service, capsys
):
    source = tmp_path / "downloads"
    source.mkdir()

    assert (
        main(
            [
                "install",
                "--config",
                str(config_file),
                "--source",
                str(source),
                "--delay",
                "120",
                "--interval",
                "45",
            ]
        )
        == EXIT_OK
    )

    baked = fake_service.spec.program_args
    assert baked[-len(baked) + 1 :][0] == "watch" or "watch" in baked
    assert str(source.resolve()) in baked
    assert str(config_file.resolve()) in baked
    assert "120" in baked
    assert "45" in baked


def test_install_without_flags_bakes_no_flags(tmp_path, fake_service):
    assert main(["install"]) == EXIT_OK
    baked = fake_service.spec.program_args
    assert "watch" in baked
    assert "--source" not in baked
    assert "--delay" not in baked


def test_install_bakes_no_content_into_the_agent(fake_service):
    assert main(["install", "--no-content"]) == EXIT_OK

    baked = fake_service.spec.program_args
    watch_args = build_parser().parse_args(baked[baked.index("watch") :])
    assert watch_args.no_content is True


def test_install_without_no_content_leaves_content_scanning_on(fake_service):
    assert main(["install"]) == EXIT_OK

    assert "--no-content" not in fake_service.spec.program_args


def test_the_agent_command_points_at_a_real_executable(fake_service):
    main(["install"])
    baked = fake_service.spec.program_args
    # Either the installed console script, or this interpreter running the
    # module. Never a bare name that depends on the agent's PATH.
    assert baked[0].startswith("/") or baked[0] == "python"


def test_install_reports_where_the_agent_went(fake_service, capsys):
    main(["install"])
    assert "fake" in capsys.readouterr().out.lower()


# --- uninstalling -----------------------------------------------------------


def test_uninstall_removes_an_installed_agent(fake_service, capsys):
    fake_service.installed = True
    assert main(["uninstall"]) == EXIT_OK
    assert fake_service.uninstalled
    assert "Removed" in capsys.readouterr().out


def test_uninstall_says_so_when_nothing_was_installed(fake_service, capsys):
    fake_service.installed = False
    assert main(["uninstall"]) == EXIT_OK
    assert "No cubby agent" in capsys.readouterr().out


def test_uninstall_without_a_service_manager_is_not_an_error(monkeypatch, capsys):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    assert main(["uninstall"]) == EXIT_OK
    assert "No cubby agent" in capsys.readouterr().out


# --- status and doctor ------------------------------------------------------


def test_status_reports_a_running_agent(fake_service, capsys):
    fake_service.installed = True
    assert main(["status"]) == EXIT_OK
    assert "running" in capsys.readouterr().out


def test_status_reports_a_missing_agent(fake_service, capsys):
    assert main(["status"]) == EXIT_OK
    assert "not installed" in capsys.readouterr().out


def test_status_works_with_no_service_manager_at_all(monkeypatch, capsys):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    assert main(["status"]) == EXIT_OK
    assert "not installed" in capsys.readouterr().out


def test_doctor_reports_the_environment(tmp_path, config_file, fake_service, capsys):
    source = tmp_path / "downloads"
    source.mkdir()

    assert main(["doctor", "--config", str(config_file), "--source", str(source)]) == EXIT_OK

    out = capsys.readouterr().out
    assert "cubby" in out
    assert str(source) in out
    assert "fake" in out


def test_doctor_without_a_service_manager_says_so(monkeypatch, config_file, capsys):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    assert main(["doctor", "--config", str(config_file)]) == EXIT_OK
    assert "manual watch" in capsys.readouterr().out


# --- watch ------------------------------------------------------------------


def test_watch_stops_cleanly_on_an_interrupt(monkeypatch, tmp_path, config_file, capsys):
    source = tmp_path / "downloads"
    source.mkdir()

    def interrupted(self, **_):
        raise KeyboardInterrupt

    monkeypatch.setattr("cubby.app.watcher.Watcher.run", interrupted)

    assert main(["watch", "--config", str(config_file), "--source", str(source)]) == EXIT_OK
    log = state.log_path().read_text("utf-8")
    assert "cubby watching" in log
    assert "cubby stopped" in log


def test_doctor_reports_a_library_that_fails_to_import(tmp_path, monkeypatch, config_file, capsys):
    # The libraries are imported in a child process (a planted module in the
    # working folder must not run): a broken package first on its path.
    broken = tmp_path / "broken" / "openpyxl"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text('raise ImportError("broken wheel")\n', "utf-8")
    monkeypatch.setenv("PYTHONPATH", str(broken.parent))
    assert main(["doctor", "--config", str(config_file)]) == EXIT_OK
    assert "openpyxl -" in capsys.readouterr().out


def test_watch_stops_after_the_pass_in_progress_on_sigterm(tmp_path, monkeypatch):
    # launchd and systemd send SIGTERM; the pass in progress completes, then
    # the loop ends, and the handler the process had before is put back.
    import signal

    source = tmp_path / "downloads"
    source.mkdir()
    passes = []

    def one_pass(self):
        passes.append(1)
        signal.raise_signal(signal.SIGTERM)
        return 0

    monkeypatch.setattr("cubby.app.watcher.Watcher._cycle", one_pass)
    before = signal.getsignal(signal.SIGTERM)

    assert main(["watch", "--source", str(source), "--interval", "1h"]) == 0

    assert passes == [1]
    assert signal.getsignal(signal.SIGTERM) is before


def test_doctor_lists_every_converter_extraction_uses(monkeypatch, capsys):
    monkeypatch.setattr(cli_agent.shutil, "which", lambda name: None)

    assert main(["doctor"]) == EXIT_OK

    out = capsys.readouterr().out
    for tool in ("pdftotext", "textutil", "antiword", "catdoc"):
        assert tool in out
