"""F6: the resume cache followed the row number, not the organization.

work/orgs/O001.jsonl, with ids assigned by row position and renumbered
contiguously on every roster merge. Insert a row in input/organizations.xlsx and
every organization after it loaded a different organization's cached result. The
archived app runs were safe only because each gets its own folder; the CLI path
documented in the README shares one work/ and was exposed.
"""
from __future__ import annotations

import json

import pytest

from scan import config, pipeline as P

AERC = {"id": "O001", "name": "African Economic Research Consortium (AERC)"}
UNCTAD = {"id": "O002", "name": "UN Trade and Development (UNCTAD)"}


@pytest.fixture
def orgs_dir(tmp_path, monkeypatch):
    d = tmp_path / "orgs"
    d.mkdir()
    monkeypatch.setattr(config, "ORGS_WORK", d)
    return d


# --- the key follows the organization ------------------------------------------------
def test_key_is_stable_across_a_row_move(orgs_dir):
    """The bug, directly: the same organization at a different row number must hit
    the same cache entry."""
    a = P._org_path(dict(AERC, id="O001"))
    b = P._org_path(dict(AERC, id="O007"))
    assert a == b


def test_different_organizations_never_share_a_key(orgs_dir):
    assert P._org_path(AERC) != P._org_path(dict(UNCTAD, id="O001"))


def test_key_survives_a_spelling_variant(orgs_dir):
    """_norm_org drops the parenthetical, so the bare name and the declared form
    are one organization and one cache entry."""
    assert P._org_path(AERC) == P._org_path({"id": "O009", "name": "African Economic Research Consortium"})


def test_filename_stays_readable(orgs_dir):
    name = P._org_path(UNCTAD).name
    assert name.endswith(".jsonl") and "trade" in name


def test_round_trip(orgs_dir):
    P._write_org(AERC, {"org": AERC["name"], "rows": [{"name": "A program"}]})
    got = P._read_org(dict(AERC, id="O042"))          # different row number
    assert got and got["rows"][0]["name"] == "A program"


def test_a_never_scanned_org_is_a_miss(orgs_dir):
    assert P._read_org(UNCTAD) is None


# --- the legacy cache: migrate what matches, ignore what does not ---------------------
def test_matching_legacy_entry_is_migrated_not_discarded(orgs_dir):
    (orgs_dir / "O001.jsonl").write_text(
        json.dumps({"org": AERC["name"], "rows": [{"name": "Old work"}]}), encoding="utf-8")
    got = P._read_org(AERC)
    assert got and got["rows"][0]["name"] == "Old work", "correct cached work must not be thrown away"
    assert P._org_path(AERC).exists(), "the migrated entry should be rewritten under the new key"


def test_mismatching_legacy_entry_is_ignored(orgs_dir):
    """The old bug presenting itself. O001 holds UNCTAD's result, but row one is now
    AERC. Inheriting it is exactly the failure, so it is ignored and AERC re-scans."""
    (orgs_dir / "O001.jsonl").write_text(
        json.dumps({"org": UNCTAD["name"], "rows": [{"name": "Someone else's work"}]}), encoding="utf-8")
    assert P._read_org(AERC) is None


def test_corrupt_legacy_entry_does_not_crash(orgs_dir):
    (orgs_dir / "O001.jsonl").write_text("{not json", encoding="utf-8")
    assert P._read_org(AERC) is None


# --- the scenario end to end ----------------------------------------------------------
def test_inserting_a_row_does_not_cross_the_wires(orgs_dir):
    before = [dict(AERC, id="O001"), dict(UNCTAD, id="O002")]
    for o in before:
        P._write_org(o, {"org": o["name"], "rows": [{"name": f"{o['name']} program"}]})
    # the analyst inserts a new organization at the top, everything renumbers
    after = [{"id": "O001", "name": "Policy Center for the New South"},
             dict(AERC, id="O002"), dict(UNCTAD, id="O003")]
    assert P._read_org(after[0]) is None                       # genuinely new, must scan
    for o in after[1:]:
        got = P._read_org(o)
        assert got and got["org"] == o["name"], "cached result served under the wrong organization"
