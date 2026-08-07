"""F8: "reads the actual report" was really "reads the front of it".

60,000 characters for the Reader, 40,000 for the Verifier, 80 pages out of a PDF,
three magic numbers across two files. That is roughly the opening 15 to 20 pages of
a flagship report. The design is fine; implying full coverage was not.
"""
from __future__ import annotations

import asyncio

import pytest

from scan import agents, config, sources


@pytest.fixture(autouse=True)
def clear_cache():
    sources._TEXT_CACHE.clear()
    yield
    sources._TEXT_CACHE.clear()


# --- the caps are named and read from one place ---------------------------------------
def test_caps_are_config_not_magic_numbers():
    for name in ("READ_MAX_CHARS", "VERIFY_MAX_CHARS", "EXTRACT_MAX_CHARS", "PDF_MAX_PAGES"):
        assert isinstance(getattr(config, name), int)


def test_changing_the_cap_changes_what_is_read(monkeypatch):
    monkeypatch.setattr(sources, "_extract_full", lambda url: "x" * 5000)
    monkeypatch.setattr(config, "READ_MAX_CHARS", 100)
    assert len(sources.fetch_text("https://site.org/a")) == 100
    sources._TEXT_CACHE.clear()
    monkeypatch.setattr(config, "READ_MAX_CHARS", 250)
    assert len(sources.fetch_text("https://site.org/a")) == 250


# --- the meta says how much was missed --------------------------------------------------
def test_meta_reports_the_full_length(monkeypatch):
    monkeypatch.setattr(sources, "_extract_full", lambda url: "y" * 200_000)
    text, full = sources.fetch_text_with_meta("https://site.org/big.pdf", 60_000)
    assert len(text) == 60_000 and full == 200_000


def test_short_document_is_not_truncated(monkeypatch):
    monkeypatch.setattr(sources, "_extract_full", lambda url: "y" * 900)
    text, full = sources.fetch_text_with_meta("https://site.org/small", 60_000)
    assert len(text) == full == 900


def test_no_url_is_safe():
    assert sources.fetch_text_with_meta("") == ("", 0)


def test_fetch_text_still_works_for_existing_callers(monkeypatch):
    monkeypatch.setattr(sources, "_extract_full", lambda url: "z" * 1000)
    assert sources.fetch_text("https://site.org/a", 10) == "z" * 10


# --- the row records it, and the model is told ------------------------------------------
def _stub_reader(monkeypatch, captured):
    async def _call(**kw):
        captured["user"] = kw["user"]
        return {"keep": True, "keep_reason": "ok", "band": "emerging", "what": "A thing",
                "evidence": "A result", "uptake": "One country", "quotes": ["a line"],
                "locator": "p1", "verbatim": True, "access_note": "Published 2024."}
    monkeypatch.setattr(agents, "structured_call", _call)


def test_row_records_how_much_was_read(monkeypatch):
    captured: dict = {}
    _stub_reader(monkeypatch, captured)
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "READ_MAX_CHARS", 1000)
    monkeypatch.setattr(sources, "_extract_full", lambda url: "w" * 50_000)
    r = asyncio.run(agents.read({"mission": ""}, {"name": "A program", "url": "https://site.org/r.pdf"}))
    assert r["read_chars"] == 1000
    assert r["source_chars"] == 50_000
    assert r["source_truncated"] is True


def test_model_is_told_it_has_only_part_of_the_document(monkeypatch):
    captured: dict = {}
    _stub_reader(monkeypatch, captured)
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "READ_MAX_CHARS", 1000)
    monkeypatch.setattr(sources, "_extract_full", lambda url: "w" * 50_000)
    asyncio.run(agents.read({"mission": ""}, {"name": "A program", "url": "https://site.org/r.pdf"}))
    assert "percent of it" in captured["user"]
    assert "do not describe the document as a whole" in captured["user"]


def test_no_window_note_when_the_whole_document_fits(monkeypatch):
    captured: dict = {}
    _stub_reader(monkeypatch, captured)
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "READ_MAX_CHARS", 60_000)
    monkeypatch.setattr(sources, "_extract_full", lambda url: "w" * 900)
    r = asyncio.run(agents.read({"mission": ""}, {"name": "A program", "url": "https://site.org/r"}))
    assert r["source_truncated"] is False
    assert "percent of it" not in captured["user"]


# --- and the README no longer overclaims -------------------------------------------------
def test_readme_does_not_claim_the_whole_report():
    text = (config.ROOT / "README.md").read_text(encoding="utf-8")
    assert "reads the actual report" not in text
    assert "opening sections" in text
