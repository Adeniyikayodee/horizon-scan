"""F9: the small bucket, plus the joins that had no tests at all.

(a) cost in currency, (b) top2 respects posture, (c) title style notes,
(d) covered in evaluate.py, (e) the untested joins, (f) run-folder pruning.
"""
from __future__ import annotations

import pytest

from scan import client, config, guardrail
from scan import pipeline as P


# --- (a) cost in currency, without inventing a price ---------------------------------
@pytest.fixture(autouse=True)
def clean_account():
    client.USAGE.update({k: 0 for k in client.USAGE})
    client.BY_MODEL.clear()
    yield
    client.USAGE.update({k: 0 for k in client.USAGE})
    client.BY_MODEL.clear()


class _U:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _R:
    def __init__(self, usage): self.usage = usage


def test_cost_is_priced_per_model():
    client._account(_R(_U(prompt_tokens=1_000_000, completion_tokens=1_000_000)), "openai/gpt-4o-mini")
    priced, unpriced = client.cost_usd()
    assert priced == pytest.approx(0.75)      # 0.15 in + 0.60 out
    assert not unpriced


def test_unpriced_model_is_named_never_guessed():
    client._account(_R(_U(prompt_tokens=1_000_000, completion_tokens=1_000)), "some/unknown-model")
    priced, unpriced = client.cost_usd()
    assert priced == 0 and unpriced == ["some/unknown-model"]
    assert "no price on file for some/unknown-model" in client.usage_line()


def test_cache_tokens_are_counted_and_discounted():
    client._account(_R(_U(input_tokens=1_000_000, output_tokens=0,
                          cache_read_input_tokens=900_000,
                          cache_creation_input_tokens=100_000)),
                    "anthropic/claude-sonnet-4")
    # 0 billed at full rate, 900k at 0.1x, 100k at 1.25x, all against $3/M
    priced, _ = client.cost_usd()
    assert priced == pytest.approx((900_000 * 0.1 + 100_000 * 1.25) * 3 / 1_000_000)
    assert client.USAGE["cache_write"] == 100_000, "cache creation was previously never counted"


def test_two_models_in_one_run_are_priced_separately():
    client._account(_R(_U(prompt_tokens=1_000_000, completion_tokens=0)), "openai/gpt-4o-mini")
    client._account(_R(_U(prompt_tokens=1_000_000, completion_tokens=0)), "anthropic/claude-sonnet-4")
    priced, _ = client.cost_usd()
    assert priced == pytest.approx(0.15 + 3.00)


# --- (b) top2 must respect the posture -------------------------------------------------
def _theme(name, tag, posture, mark="strong"):
    t = {"name": name, "tag": tag, "posture": posture, "top2": False}
    for k in ("mandate_fit", "research_to_policy", "african_traction", "white_space"):
        t[k] = mark
    return t


def test_top2_picks_the_two_strongest_entry_themes():
    themes = [_theme("A", "new", "enter", "strong"), _theme("B", "adjacent", "enter", "partial"),
              _theme("C", "new", "enter", "weak")]
    got = P._apply_top2(themes)
    assert got == ["A", "B"]
    assert [t["top2"] for t in themes] == [True, True, False]


def test_no_entry_theme_means_no_invented_pair():
    """A run where nothing is worth entering used to still name two 'cleanest new
    areas to enter'."""
    themes = [_theme("A", "new", "watch"), _theme("B", "adjacent", "watch")]
    assert P._apply_top2(themes) == []
    assert not any(t["top2"] for t in themes)


def test_one_entry_theme_names_one():
    themes = [_theme("A", "new", "enter"), _theme("B", "adjacent", "watch")]
    assert P._apply_top2(themes) == ["A"]


def test_existing_theme_is_never_a_top_area():
    themes = [_theme("A", "existing", "enter"), _theme("B", "new", "enter", "weak")]
    assert P._apply_top2(themes) == ["B"]


# --- (c) title style notes, warn only --------------------------------------------------
def test_colon_title_is_flagged():
    notes = guardrail.title_notes("# Market Shaping: New Frontiers\n\ntext")
    assert any("colon" in n for n in notes)


def test_title_case_is_flagged():
    notes = guardrail.title_notes(
        "# AI-Driven Policy Platforms and Market Shaping for African Transformation\n")
    assert any("title case" in n for n in notes)


def test_plain_title_passes():
    assert guardrail.title_notes("# Global scan, the new areas to enter\n\ntext") == []


def test_missing_title_is_reported():
    assert guardrail.title_notes("no heading here") == ["title note: the memo has no top-level title"]


def test_title_notes_never_rewrite():
    src = "# Market Shaping: New Frontiers\n\nbody"
    guardrail.title_notes(src)
    assert src == "# Market Shaping: New Frontiers\n\nbody"


# --- (e) the joins that had no tests ---------------------------------------------------
def test_best_report_matches_on_two_or_more_tokens():
    cand = {"name": "Blue Economy Program", "one_liner": "Coastal value addition in fisheries"}
    reports = [{"title": "Annual report 2024", "url": "https://x.org/annual.pdf"},
               {"title": "Blue Economy and coastal value addition", "url": "https://x.org/blue.pdf"}]
    assert P._best_report(cand, reports)["url"] == "https://x.org/blue.pdf"


def test_best_report_refuses_a_weak_match():
    cand = {"name": "Blue Economy Program", "one_liner": "Coastal value addition"}
    assert P._best_report(cand, [{"title": "Annual report 2024", "url": "https://x.org/a.pdf"}]) is None


def test_best_report_ignores_reports_with_no_url():
    cand = {"name": "Blue Economy Program", "one_liner": "Coastal value addition"}
    assert P._best_report(cand, [{"title": "Blue economy coastal value", "url": ""}]) is None


def test_best_report_handles_an_unnamed_candidate():
    assert P._best_report({"name": "", "one_liner": ""}, [{"title": "x", "url": "u"}]) is None


def test_dead_second_source_corroborates_nothing():
    corr = {"corroborated": True, "source": "Another body", "url": "https://x.org/gone",
            "quote": "q", "note": "Confirmed."}
    out = P._reject_dead_corroboration(dict(corr), dead=True)
    assert out["corroborated"] is False and out["url"] == ""
    assert "did not resolve" in out["note"]


def test_live_second_source_is_kept():
    corr = {"corroborated": True, "source": "Another body", "url": "https://x.org/ok",
            "quote": "q", "note": "Confirmed."}
    out = P._reject_dead_corroboration(dict(corr), dead=False)
    assert out["corroborated"] is True and out["url"] == "https://x.org/ok"


def test_uncorroborated_finding_is_left_alone():
    corr = {"corroborated": False, "url": "", "note": "None found."}
    assert P._reject_dead_corroboration(dict(corr), dead=True)["note"] == "None found."


# --- (f) run-folder pruning -------------------------------------------------------------
def test_prune_keeps_the_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    for i in range(5):
        d = runs / f"run{i}"
        d.mkdir()
        (d / "f.txt").write_text("x")
        import os
        os.utime(d, (1_000_000 + i, 1_000_000 + i))
    gone = P.prune_runs(keep=2)
    assert sorted(gone) == ["run0", "run1", "run2"]
    assert sorted(d.name for d in runs.iterdir()) == ["run3", "run4"]


def test_prune_dry_run_deletes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    for i in range(3):
        (runs / f"run{i}").mkdir()
    gone = P.prune_runs(keep=1, dry=True)
    assert len(gone) == 2
    assert len(list(runs.iterdir())) == 3


def test_prune_with_no_runs_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    assert P.prune_runs(keep=5) == []
