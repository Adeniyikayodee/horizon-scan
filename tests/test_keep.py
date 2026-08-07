"""F7: the reading gate was one unrubricked boolean and nobody could see it work.

Across the archived runs the drop rate swung from 0 to 79 percent on identical
code, two runs returned zero rows, and the drops carried no reason. The pipeline
even shipped a printed apology telling the user to try a steadier model.
"""
from __future__ import annotations

import glob
import json

import pytest

from scan import agents, config, io_xlsx, mock, schemas
from scan import pipeline as P


# --- keep_reason travels the whole way ------------------------------------------------
def test_schema_requires_a_reason_either_way():
    assert "keep_reason" in schemas.READER_SCHEMA["required"]
    assert "keep_reason" in schemas.READER_SCHEMA["properties"]


def test_reading_model_carries_the_reason():
    r = schemas.Reading(keep=False, keep_reason="No named program on the page.")
    assert r.keep_reason == "No named program on the page."


def test_coercion_keeps_the_reason_and_survives_a_missing_one():
    assert agents._coerce_reading({"keep": True, "keep_reason": "Names a pilot."})["keep_reason"] \
        == "Names a pilot."
    assert agents._coerce_reading({"keep": False})["keep_reason"] == ""


def test_mock_supplies_a_reason_both_ways():
    for h in range(10):
        assert mock._read("Candidate: x", h)["keep_reason"], "the dry run must model the reason too"


# --- the rubric is actually in the frame ------------------------------------------------
def test_reader_frame_states_the_rubric_and_the_non_reasons():
    text = agents.READER_I.lower()
    assert "named program" in text and "concrete piece of evidence" in text
    # the failure mode was dropping for earliness, which the band already records
    assert "earliness" in text or "being early" in text
    assert "keep_reason" in text


# --- drops carry a stage and a reason ---------------------------------------------------
def test_open_questions_lists_every_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REVIEW_DIR", tmp_path)
    io_xlsx.write_open_questions(
        [],
        [{"org": "AfDB", "name": "A page", "stage": "not kept at reading",
          "reason": "No named program and no result."},
         {"org": "UNCTAD", "name": "Old brief", "stage": "outside the recency window",
          "reason": "source dated 2019, outside 2023-2026"}])
    text = (tmp_path / "open_questions.md").read_text(encoding="utf-8")
    assert "No named program and no result." in text
    assert "outside the recency window" in text
    assert "—" not in text, "house style: no long dashes in the review files either"


# --- the drop-rate report ----------------------------------------------------------------
def test_no_report_when_the_rate_is_ordinary():
    rows = [{"name": f"r{i}"} for i in range(9)]
    dropped = [{"stage": "not kept at reading"}]
    assert P.drop_report(rows, dropped) == ""


def test_report_names_the_rate_and_the_stages():
    rows = [{"name": "r1"}]
    dropped = ([{"stage": "not kept at reading"}] * 7) + [{"stage": "maturing"}] * 2
    out = P.drop_report(rows, dropped)
    assert "9 of 10" in out and "90%" in out
    assert "7 not kept at reading" in out and "2 maturing" in out


def test_report_handles_a_drop_with_no_stage():
    assert "not kept at reading" in P.drop_report([], [{}, {}])


def test_empty_run_does_not_divide_by_zero():
    assert P.drop_report([], []) == ""


# --- replay: the runs that motivated this must trip the report ----------------------------
def test_replay_flags_the_worst_archived_runs():
    tripped = []
    for d in sorted(glob.glob("runs/*/work/orgs")):
        rows, dropped = [], []
        for f in glob.glob(d + "/*.jsonl"):
            try:
                p = json.loads(open(f, encoding="utf-8").read())
            except Exception:
                continue
            rows += p.get("rows", [])
            dropped += p.get("dropped", [])
        if P.drop_report(rows, dropped):
            tripped.append(d.split("/")[1])
    if tripped:
        # 3983b64d16 dropped 19 of 31, e1199a6e1b 28 of 36, both should be visible
        assert "3983b64d16" in tripped or "e1199a6e1b" in tripped
