import json

import pytest

from markbook import cli
from markbook.stats import StatsError, compute, parse_baseline, render
from markbook.views import load_views, record_view


def ts(minute, second=0):
    return f"2026-03-01T10:{minute:02d}:{second:02d}+00:00"


def run_of(ids, queue=()):
    return {"run_id": "r", "summary": {"review_load": {"reduction_pct": 80.0}},
            "submissions": [{"id": i, "triage": {"state": "review" if i in queue else "auto"}} for i in ids]}


def dec(at, sub, reviewer="ann", crit="q", ai=None, action="set_points"):
    d = {"at": at, "reviewer": reviewer, "submission": sub, "action": action, "criterion": crit, "points": 1}
    if ai is not None:
        d["ai"] = ai
    return d


def test_decision_gaps_and_breaks():
    # gaps: 60s, 120s, 20 min (break), 30s
    decs = [dec(ts(0), "a"), dec(ts(1), "a", crit="r"), dec(ts(3), "b"), dec(ts(23), "c"), dec(ts(23, 30), "c", crit="r")]
    r = compute(run_of("abc"), decs, idle_minutes=5)
    rv = r["reviewers"][0]
    assert rv["active_seconds"] == 210 and rv["breaks"] == 1 and rv["break_seconds"] == 1200
    assert rv["median_seconds_per_decision"] == 60 and rv["measured_decisions"] == 3
    assert rv["decisions"] == 5 and rv["submissions_reviewed"] == 3 and rv["criteria_reviewed"] == 5
    assert rv["timing_basis"] == "decision gaps only" and r["lower_bound"] is True
    assert "LOWER bound" in render(r)


def test_idle_cutoff_is_configurable():
    decs = [dec(ts(0), "a"), dec(ts(8), "a", crit="r")]
    assert compute(run_of("a"), decs, idle_minutes=5)["reviewers"][0]["active_seconds"] == 0
    assert compute(run_of("a"), decs, idle_minutes=10)["reviewers"][0]["active_seconds"] == 480
    with pytest.raises(StatsError):
        compute(run_of("a"), decs, idle_minutes=0)


def test_reviewers_are_separate():
    decs = [dec(ts(0), "a", "ann"), dec(ts(1), "b", "bob"), dec(ts(2), "a", "ann", crit="r"), dec(ts(2, 10), "b", "bob", crit="r")]
    r = {x["reviewer"]: x for x in compute(run_of("ab"), decs)["reviewers"]}
    assert r["ann"]["active_seconds"] == 120 and r["bob"]["active_seconds"] == 70


def test_views_give_lead_in():
    views = [{"submission": "a", "reviewer": "ann", "at": ts(0)}, {"submission": "b", "reviewer": "ann", "at": ts(4)}]
    decs = [dec(ts(2), "a"), dec(ts(5), "b")]
    rv = compute(run_of("ab"), decs, views)["reviewers"][0]
    assert rv["active_seconds"] == 120 + 60 and rv["timing_basis"] == "views + decisions"
    assert rv["measured_decisions"] == 2
    # same decisions without views: only the 3-minute gap between them is measurable
    assert compute(run_of("ab"), decs)["reviewers"][0]["active_seconds"] == 180


def test_view_window_does_not_span_a_later_view_of_another_submission():
    views = [{"submission": "a", "reviewer": "ann", "at": ts(0)}, {"submission": "b", "reviewer": "ann", "at": ts(1)}]
    decs = [dec(ts(2), "a")]   # decided on a after having moved to b: not inside a's window
    rv = compute(run_of("ab"), decs, views)["reviewers"][0]
    assert rv["active_seconds"] == 0 and rv["timing_basis"] == "decision gaps only"


def test_view_to_decision_over_cutoff_is_a_break():
    views = [{"submission": "a", "reviewer": "ann", "at": ts(0)}]
    rv = compute(run_of("a"), [dec(ts(30), "a")], views, idle_minutes=5)["reviewers"][0]
    assert rv["active_seconds"] == 0 and rv["breaks"] == 1


def test_ai_share_and_accept_unchanged():
    ai_same = {"suggested_points": 1, "accepted": True, "unchanged": True}
    ai_diff = {"suggested_points": 2, "accepted": False, "unchanged": False}
    decs = [dec(ts(0), "a", ai=ai_same), dec(ts(1), "b", ai=ai_diff), dec(ts(2), "c"), dec(ts(3), "d", action="clear_flag", crit=None)]
    t = compute(run_of("abcd"), decs)
    rv = t["reviewers"][0]
    assert rv["set_points_decisions"] == 3 and rv["ai_assisted_decisions"] == 2
    assert rv["ai_assisted_share"] == pytest.approx(0.667, abs=1e-3)
    assert rv["ai_accept_unchanged_rate"] == 0.5 and rv["ai_button_accepts"] == 1
    assert t["totals"]["ai_assisted_share"] == pytest.approx(0.667, abs=1e-3)


def test_no_ai_means_none_not_zero():
    rv = compute(run_of("a"), [dec(ts(0), "a")])["reviewers"][0]
    assert rv["ai_assisted_share"] == 0.0 and rv["ai_accept_unchanged_rate"] is None


def test_queue_remaining_and_empty_log():
    r = compute(run_of("abc", queue={"b", "c"}), [])
    assert r["queue_remaining"] == 2 and r["reviewers"] == [] and r["totals"]["median_seconds_per_decision"] is None
    assert "No human decisions" in render(r) and "still in the queue" in " ".join(r["notes"])


def test_bad_timestamps_are_ignored_and_reported():
    r = compute(run_of("a"), [dec("garbage", "a"), dec(ts(0), "a"), dec(ts(1), "a", crit="r")])
    assert r["reviewers"][0]["active_seconds"] == 60 and any("unreadable" in n for n in r["notes"])


def test_parse_baseline():
    assert parse_baseline("submission_id,seconds\nalice,300\nbob,150.5\n") == {"alice": 300.0, "bob": 150.5}
    for bad in ("", "id,secs\na,1", "submission_id,seconds\n", "submission_id,seconds\na,x",
                "submission_id,seconds\na,0", "submission_id,seconds\na,1\na,2", "submission_id,seconds\na,nan"):
        with pytest.raises(StatsError):
            parse_baseline(bad)


def test_baseline_comparison_and_cautions():
    ids = list("abcdef")
    decs = [dec(ts(0), "a"), dec(ts(1), "a", crit="r"), dec(ts(2), "b")]   # a: 60s credited, b: 60s
    base = {i: 100.0 for i in ids}
    b = compute(run_of(ids), decs, baseline=base)["baseline"]
    assert b["label"] == "measured, 6 submissions, 1 grader"
    assert b["baseline_total_seconds"] == 600 and b["queue_total_seconds"] == 120
    assert b["ratio_queue_over_baseline"] == 0.2 and b["queue_submissions_touched"] == 2
    assert b["modelled_review_load_reduction_pct"] == 80.0 and b["cautions"] == []
    text = render(compute(run_of(ids), decs, baseline=base))
    assert "measured, 6 submissions, 1 grader" in text and "modelled" in text


def test_baseline_small_or_mismatched_sets_warn():
    ids = list("abcdef")
    b = compute(run_of(ids), [dec(ts(0), "a")], baseline={"a": 10.0, "b": 10.0, "zzz": 10.0})["baseline"]
    assert b["submissions"] == 2
    joined = " ".join(b["cautions"])
    assert "fewer than 5" in joined and "2 of the run's 6" in joined and "zzz" in joined
    none = compute(run_of(ids), [], baseline={"zzz": 5.0})["baseline"]
    assert none["submissions"] == 0 and any("nothing to compare" in c for c in none["cautions"])


def test_views_file_roundtrip_and_tolerance(tmp_path):
    assert load_views(tmp_path) == []
    record_view(tmp_path, "r", "a", "ann", at=ts(0))
    with open(tmp_path / "views.jsonl", "a") as f:
        f.write("not json\n{\"x\": 1}\n")
    record_view(tmp_path, "r", "b", "ann")
    v = load_views(tmp_path)
    assert [x["submission"] for x in v] == ["a", "b"]


def test_cli_stats_end_to_end(tmp_path, demo_run, capsys):
    out = tmp_path / "run"
    cli.main(["grade", str(demo_run["spec_path"]), "--roster", str(demo_run["roster_path"]),
              "--sandbox", "none", "--out", str(out), "--quiet"])
    capsys.readouterr()
    assert cli.main(["stats", str(out), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["lower_bound"] is True and data["reviewers"] == [] and data["queue_remaining"] >= 1
    assert cli.main(["override", str(out), "alice", "-c", "quality", "-p", "2", "--reviewer", "ann"]) == 0
    assert cli.main(["override", str(out), "bob", "-c", "quality", "-p", "2", "--reviewer", "ann"]) == 0
    capsys.readouterr()
    assert cli.main(["stats", str(out)]) == 0
    text = capsys.readouterr().out
    assert "LOWER bound" in text and "decisions 2" in text
    csv_p = tmp_path / "b.csv"
    csv_p.write_text("submission_id,seconds\nalice,120\n")
    assert cli.main(["stats", str(out), "--baseline", str(csv_p)]) == 0
    assert "CAUTION" in capsys.readouterr().out
    csv_p.write_text("oops\n")
    assert cli.main(["stats", str(out), "--baseline", str(csv_p)] ) == 1
