"""Shared fixtures. The engine fixtures build everything in memory, which is
only possible because the domain layer has no IO dependencies."""

from __future__ import annotations

import socket

import pytest

from cubby.domain.category import Category, Config, Settings
from cubby.domain.file_ref import FileRef

# HOME and the XDG folders point at a session folder for the whole run, and the
# session fails if the real home's cubby state changed: isolation that holds
# even when the code under test ignores CUBBY_STATE_DIR (tests/test_state_isolation.py).
from tests.real_state_guard import (  # noqa: F401 (pytest hooks, found by name)
    pytest_configure,
    pytest_sessionfinish,
    pytest_unconfigure,
)

# The isolation below uses its own MonkeyPatch, not the shared `monkeypatch`
# fixture: a test calling `monkeypatch.undo()` would otherwise undo it too, and
# the rest of that test would write to the developer's real state folder (it
# happened once: tests/test_isolation.py pins it).


@pytest.fixture(autouse=True)
def _isolate_user_config():
    """Keep tests hermetic: never read the developer's real ~/.config file."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("cubby.adapters.config.find_user_config", lambda: None)
        mp.delenv("CUBBY_CONFIG", raising=False)
        # Else `cubby init` in a test writes into the developer's real folder.
        mp.delenv("XDG_CONFIG_HOME", raising=False)
        yield


@pytest.fixture(autouse=True)
def _isolate_user_state(tmp_path_factory):
    """Point every piece of cubby state (journal, ledger, heartbeat, log, lock)
    at a fresh folder for each test.

    Without this, running the suite appended test moves to the user's own
    ~/.local/state/cubby/journal.jsonl, so a real `cubby undo` would replay a
    test's temp directories instead of their last real sort.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CUBBY_STATE_DIR", str(tmp_path_factory.mktemp("cubby-state")))
        yield


@pytest.fixture(autouse=True)
def _no_network():
    """Cubby makes no network calls; a test that tries one is a bug either way."""

    def refuse(*args, **kwargs):
        raise AssertionError("the test suite must not open network connections")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket.socket, "connect", refuse)
        mp.setattr(socket, "create_connection", refuse)
        yield


@pytest.fixture
def sample_config() -> Config:
    categories = (
        Category(
            name="Invoices",
            name_patterns=("invoice", "receipt", "facture"),
            content_patterns=("numero de facture", "payment confirmation"),
        ),
        Category(
            name="Legal",
            name_patterns=("contract", "convention", "statuts"),
            content_patterns=("association sans but lucratif", "hereby agree"),
        ),
        Category(name="Installers", extensions=frozenset({"dmg", "pkg", "exe"}), strong_ext=True),
        Category(name="Images", extensions=frozenset({"png", "jpg", "svg"}), strong_ext=True),
        Category(name="Documents", extensions=frozenset({"pdf", "docx", "txt"})),
    )
    return Config(settings=Settings(), categories=categories)


@pytest.fixture
def make_ref():
    """Factory building a FileRef from a filename and optional fake content."""

    def _make(name: str, *, text: str = "", is_file: bool = True) -> FileRef:
        stem, _, ext = name.rpartition(".")
        return FileRef(
            name=name,
            stem=stem or name,
            ext=ext.lower() if stem else "",
            is_file=is_file,
            read_text=lambda: text,
        )

    return _make
