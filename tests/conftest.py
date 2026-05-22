"""Shared fixtures. The engine fixtures build everything in memory, which is
only possible because the domain layer has no IO dependencies."""
from __future__ import annotations

import pytest

from cubby.domain.category import Category, Config, Settings
from cubby.domain.file_ref import FileRef


@pytest.fixture(autouse=True)
def _isolate_user_config(monkeypatch):
    """Keep tests hermetic: never read the developer's real ~/.config file."""
    monkeypatch.setattr("cubby.adapters.config.find_user_config", lambda: None)
    monkeypatch.delenv("CUBBY_CONFIG", raising=False)


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
