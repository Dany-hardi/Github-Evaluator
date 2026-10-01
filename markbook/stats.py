"""Review-effort statistics derived from the audit log (and, optionally, view events).

Pure functions: no I/O here. Every figure is a *measurement of what the log shows*,
and the time figures are LOWER bounds: nothing is recorded about time spent before
a reviewer's first decision (or first page view) of a stretch of work, and gaps
longer than the idle cutoff are treated as breaks and excluded.

Nothing here extrapolates, and nothing is a significance test.
"""
from __future__ import annotations

import bisect
import csv
import io
from datetime import datetime, timezone
from statistics import median

from .store import StoreError

DEFAULT_IDLE_MINUTES = 5.0
MIN_BASELINE_N = 5


class StatsError(StoreError):
    pass


def parse_ts(s) -> float | None:
    """UTC ISO-8601 -> epoch seconds; None if unparseable."""
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_baseline(text: str) -> dict[str, float]:
    """CSV `submission_id,seconds` -> {id: seconds}. Strict: a bad row is an error, not a guess."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows or [c.strip().lower() for c in rows[0][:2]] != ["submission_id", "seconds"]:
        raise StatsError("baseline CSV must start with the header: submission_id,seconds")
    out: dict[str, float] = {}
    for n, r in enumerate(rows[1:], start=2):
        if len(r) < 2:
            raise StatsError(f"baseline CSV line {n}: expected submission_id,seconds")
        sid = r[0].strip()
        try:
            secs = float(r[1])
        except ValueError:
            raise StatsError(f"baseline CSV line {n}: seconds {r[1]!r} is not a number") from None
        if not sid or not 0 < secs < float("inf"):
            raise StatsError(f"baseline CSV line {n}: need a submission id and positive finite seconds")
        if sid in out:
            raise StatsError(f"baseline CSV line {n}: duplicate submission {sid!r}")
        out[sid] = secs
    if not out:
        raise StatsError("baseline CSV has no data rows")
    return out


def _chain(points: list[tuple[float, str]], cutoff: float):
    """points: (time, submission), sorted. Gaps <= cutoff are credited to the submission the gap ends on;
    longer gaps are breaks. Returns ([(gap, submission)], [break_gap])."""
    credited, breaks = [], []
    for (t0, _), (t1, sub) in zip(points, points[1:]):
        gap = t1 - t0
        if gap <= cutoff:
            credited.append((gap, sub))
        else:
            breaks.append(gap)
    return credited, breaks


def _reviewer_time(decs: list[tuple[float, str]], views: list[tuple[float, str]], cutoff: float):
    """Active-time pieces for one reviewer. decs/views: sorted (time, submission).

    A decision belongs to the latest view (by this reviewer) at or before it, if that view was of the
    same submission; the window is then view -> decisions. Other decisions fall back to gaps between
    consecutive decisions."""
    vt = [v[0] for v in views]
    windows: dict[int, list[tuple[float, str]]] = {}
    uncovered: list[tuple[float, str]] = []
    for t, sub in decs:
        i = bisect.bisect_right(vt, t) - 1
        if i >= 0 and views[i][1] == sub:
            windows.setdefault(i, []).append((t, sub))
        else:
            uncovered.append((t, sub))
    credited, breaks = [], []
    for i, ds in windows.items():
        c, b = _chain([views[i]] + ds, cutoff)
        credited += c
        breaks += b
    c, b = _chain(uncovered, cutoff)
    return credited + c, breaks + b, len(uncovered), len(windows)


def compute(run: dict, decisions: list[dict], views: list[dict] | None = None, *,
            idle_minutes: float = DEFAULT_IDLE_MINUTES, baseline: dict[str, float] | None = None) -> dict:
    if not idle_minutes > 0:
        raise StatsError("--idle-minutes must be positive")
    cutoff = idle_minutes * 60
    views = views or []
    notes: list[str] = []

    by_rev: dict[str, dict] = {}

    def rev(name: str) -> dict:
        return by_rev.setdefault(name, {"decs": [], "views": [], "subs": set(), "crits": set(),
                                        "set_points": 0, "ai": 0, "ai_unchanged": 0, "ai_button": 0})

    skipped = 0
    for d in decisions:
        t = parse_ts(d.get("at"))
        if t is None:
            skipped += 1
            continue
        r = rev(str(d.get("reviewer") or "unknown"))
        sub = str(d.get("submission"))
        r["decs"].append((t, sub))
        r["subs"].add(sub)
        if d.get("action") == "set_points":
            r["crits"].add((sub, d.get("criterion")))
            r["set_points"] += 1
            ai = d.get("ai")
            if isinstance(ai, dict):
                r["ai"] += 1
                r["ai_unchanged"] += 1 if ai.get("unchanged") else 0
                r["ai_button"] += 1 if ai.get("accepted") else 0
    if skipped:
        notes.append(f"{skipped} decision(s) with an unreadable timestamp were ignored.")
    bad_views = 0
    for v in views:
        t = parse_ts(v.get("at"))
        if t is None:
            bad_views += 1
            continue
        rev(str(v.get("reviewer") or "unknown"))["views"].append((t, str(v.get("submission"))))
    if bad_views:
        notes.append(f"{bad_views} view event(s) with an unreadable timestamp were ignored.")

    per_sub: dict[str, float] = {}
    reviewers = []
    all_gaps: list[float] = []
    for name in sorted(by_rev):
        r = by_rev[name]
        decs, vws = sorted(r["decs"]), sorted(r["views"])
        credited, breaks, n_unc, n_win = _reviewer_time(decs, vws, cutoff)
        gaps = [g for g, _ in credited]
        all_gaps += gaps
        for g, sub in credited:
            per_sub[sub] = per_sub.get(sub, 0.0) + g
        if n_win and not n_unc:
            basis = "views + decisions"
        elif not n_win:
            basis = "decision gaps only"
        else:
            basis = "mixed (views for some decisions, gaps for the rest)"
        reviewers.append({
            "reviewer": name,
            "decisions": len(decs),
            "submissions_reviewed": len(r["subs"]),
            "criteria_reviewed": len(r["crits"]),
            "active_seconds": round(sum(gaps), 1),
            "median_seconds_per_decision": round(median(gaps), 1) if gaps else None,
            "measured_decisions": len(gaps),
            "breaks": len(breaks),
            "break_seconds": round(sum(breaks), 1),
            "views": len(vws),
            "timing_basis": basis,
            "set_points_decisions": r["set_points"],
            "ai_assisted_decisions": r["ai"],
            "ai_assisted_share": round(r["ai"] / r["set_points"], 3) if r["set_points"] else None,
            "ai_accepted_unchanged": r["ai_unchanged"],
            "ai_accept_unchanged_rate": round(r["ai_unchanged"] / r["ai"], 3) if r["ai"] else None,
            "ai_button_accepts": r["ai_button"],
        })

    total_sp = sum(x["set_points_decisions"] for x in reviewers)
    total_ai = sum(x["ai_assisted_decisions"] for x in reviewers)
    total_unch = sum(x["ai_accepted_unchanged"] for x in reviewers)
    run_ids = {s["id"] for s in run["submissions"]}
    queue = [s["id"] for s in run["submissions"] if s["triage"]["state"] == "review"]
    if queue:
        notes.append(f"{len(queue)} item(s) are still in the queue: review is not finished, so totals are incomplete.")
    out = {
        "run": run.get("run_id"),
        "idle_minutes": idle_minutes,
        "lower_bound": True,
        "lower_bound_note": ("Active time is a LOWER bound: the first decision of each stretch of work has no "
                             "measurable lead-in, gaps over the idle cutoff are excluded as breaks, and time "
                             "spent before a page view is not recorded."),
        "has_views": bool(views),
        "totals": {
            "decisions": sum(x["decisions"] for x in reviewers),
            "active_seconds": round(sum(x["active_seconds"] for x in reviewers), 1),
            "median_seconds_per_decision": round(median(all_gaps), 1) if all_gaps else None,
            "set_points_decisions": total_sp,
            "ai_assisted_share": round(total_ai / total_sp, 3) if total_sp else None,
            "ai_accept_unchanged_rate": round(total_unch / total_ai, 3) if total_ai else None,
        },
        "queue_remaining": len(queue),
        "queue_remaining_ids": queue,
        "reviewers": reviewers,
        "notes": notes,
        "modelled_review_load": run["summary"]["review_load"],
        "baseline": None,
    }
    if baseline is not None:
        out["baseline"] = _compare(baseline, per_sub, run_ids, queue, out["modelled_review_load"])
    return out


def _compare(baseline: dict[str, float], per_sub: dict[str, float], run_ids: set[str],
             queue: list[str], modelled: dict) -> dict:
    ids = sorted(i for i in baseline if i in run_ids)
    unknown = sorted(i for i in baseline if i not in run_ids)
    cautions = []
    if unknown:
        cautions.append(f"{len(unknown)} baseline submission(s) are not in this run and were ignored: "
                        + ", ".join(unknown[:5]) + (" ..." if len(unknown) > 5 else ""))
    if len(ids) != len(run_ids):
        cautions.append(f"The baseline covers {len(ids)} of the run's {len(run_ids)} submissions; the comparison "
                        "is restricted to those and is not extrapolated to the rest.")
    if len(ids) < MIN_BASELINE_N:
        cautions.append(f"Only {len(ids)} submission(s) in the comparison (fewer than {MIN_BASELINE_N}): "
                        "statistically weak; treat it as an anecdote, not an estimate.")
    if any(i in queue for i in ids):
        cautions.append("Some compared submissions are still in the review queue, so the with-queue time is incomplete.")
    if not ids:
        return {"submissions": 0, "cautions": cautions + ["No overlapping submissions: nothing to compare."]}
    base = [baseline[i] for i in ids]
    tool = [per_sub.get(i, 0.0) for i in ids]
    bt, tt = sum(base), sum(tool)
    return {
        "label": f"measured, {len(ids)} submissions, 1 grader",
        "submissions": len(ids),
        "baseline_total_seconds": round(bt, 1),
        "baseline_median_seconds": round(median(base), 1),
        "queue_total_seconds": round(tt, 1),
        "queue_median_seconds": round(median(tool), 1),
        "queue_submissions_touched": sum(1 for x in tool if x > 0),
        "ratio_queue_over_baseline": round(tt / bt, 3),
        "measured_time_reduction_pct": round(100 * (1 - tt / bt), 1),
        "modelled_review_load_reduction_pct": modelled["reduction_pct"],
        "cautions": cautions,
    }


def _dur(sec) -> str:
    if sec is None:
        return "n/a"
    sec = float(sec)
    if sec < 90:
        return f"{sec:.0f}s"
    m, s = divmod(round(sec), 60)
    return f"{m}m{s:02d}s" if m < 60 else f"{m // 60}h{m % 60:02d}m"


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def render(res: dict) -> str:
    t, L = res["totals"], []
    L.append(f"Review effort for run {res['run']}  (idle cutoff {res['idle_minutes']:g} min)")
    L.append("")
    if not res["reviewers"]:
        L.append("  No human decisions recorded yet.")
    for r in res["reviewers"]:
        L.append(f"  {r['reviewer']}")
        L.append(f"    decisions {r['decisions']}  |  submissions {r['submissions_reviewed']}  |  criteria {r['criteria_reviewed']}")
        L.append(f"    active review time >= {_dur(r['active_seconds'])}  (basis: {r['timing_basis']}; "
                 f"{r['measured_decisions']} measurable decision(s))")
        L.append(f"    median per decision {_dur(r['median_seconds_per_decision'])}  |  "
                 f"breaks excluded: {r['breaks']} ({_dur(r['break_seconds'])})")
        if r["set_points_decisions"]:
            L.append(f"    AI-assisted {_pct(r['ai_assisted_share'])} of {r['set_points_decisions']} scoring decision(s); "
                     f"accepted unchanged {_pct(r['ai_accept_unchanged_rate'])} of {r['ai_assisted_decisions']}")
    if len(res["reviewers"]) > 1:
        L.append(f"  All reviewers: {t['decisions']} decisions, active time >= {_dur(t['active_seconds'])}, "
                 f"median {_dur(t['median_seconds_per_decision'])}")
    L.append("")
    L.append(f"  Still in the queue: {res['queue_remaining']}")
    L.append("")
    L.append("  " + res["lower_bound_note"])
    if not res["has_views"]:
        L.append("  No view events (views.jsonl): timing uses gaps between decisions only.")
    for n in res["notes"]:
        L.append(f"  Note: {n}")
    b = res["baseline"]
    if b:
        L.append("")
        L.append(f"Manual baseline vs queue  [{b.get('label', 'no overlap')}]")
        if b["submissions"]:
            L.append(f"  by hand, no tool : total {_dur(b['baseline_total_seconds'])}, "
                     f"median {_dur(b['baseline_median_seconds'])} per submission")
            L.append(f"  with the queue   : total >= {_dur(b['queue_total_seconds'])}, "
                     f"median {_dur(b['queue_median_seconds'])} per submission "
                     f"({b['queue_submissions_touched']} of {b['submissions']} needed any human time)")
            L.append(f"  ratio queue/by-hand {b['ratio_queue_over_baseline']:.2f}  "
                     f"(measured time reduction {b['measured_time_reduction_pct']:g}%; queue time is a lower bound, "
                     "so this reduction is an upper bound)")
            L.append(f"  modelled review-load reduction (whole run; a workload model, not time): "
                     f"{b['modelled_review_load_reduction_pct']:g}%")
        for c in b["cautions"]:
            L.append(f"  CAUTION: {c}")
    return "\n".join(L)
