"""Shared fixtures. The engine fixtures build everything in memory, which is
only possible because the domain layer has no IO dependencies."""

from __future__ import annotations

import socket

import pytest

from cubby.domain.category import Category, Config, Settings
from cubby.domain.file_ref import FileRef


@pytest.fixture(autouse=True)
def _isolate_user_config(monkeypatch):
    """Keep tests hermetic: never read the developer's real ~/.config file."""
    monkeypatch.setattr("cubby.adapters.config.find_user_config", lambda: None)
    monkeypatch.delenv("CUBBY_CONFIG", raising=False)


@pytest.fixture(autouse=True)
def _isolate_user_state(monkeypatch, tmp_path_factory):
    """Point every piece of cubby state (journal, ledger, heartbeat, log, lock)
    at a fresh folder for each test.

    Without this, running the suite appended test moves to the user's own
    ~/.local/state/cubby/journal.jsonl, so a real `cubby undo` would replay a
    test's temp directories instead of their last real sort.
    """
    monkeypatch.setenv("CUBBY_STATE_DIR", str(tmp_path_factory.mktemp("cubby-state")))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Cubby makes no network calls; a test that tries one is a bug either way."""

    def refuse(*args, **kwargs):
        raise AssertionError("the test suite must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


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
