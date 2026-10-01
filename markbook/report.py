"""Reports: JSON (schema-validated), CSV (generic, Canvas, Moodle), JUnit XML,
per-student Markdown feedback and a cohort summary.

All reports are pure functions of the *current* run (raw result + overrides).
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr


# ── feedback ──────────────────────────────────────────────────────────────────

_ICON = {"passed": "✅", "partial": "🟡", "failed": "❌", "manual": "📝", "error": "⚠️"}


def feedback_md(sub: dict, a: dict) -> str:
    """Markdown feedback for one student. Hidden-test details are never included
    because the evidence recorded for hidden cases already omits them."""
    lines = [f"# {a['name']}: feedback for {sub['name']}", ""]
    if sub["status"] == "error":
        lines += ["We could not grade this submission.", "",
                  f"> **{sub['error']['code']}**: {sub['error']['message']}", ""]
        return "\n".join(lines)
    sc = sub["score"]
    grade = f"**{sc['scaled']:g} / {sc['scale']:g}**" if sc["complete"] else \
        f"**provisional {sc['scaled']:g} / {sc['scale']:g}** (manual review pending)"
    lines += [f"Grade: {grade}  ", f"Commit graded: `{(sub['commit'] or '')[:10]}`", ""]
    if sub["late"] and sub["late"]["is_late"]:
        w = " (waived)" if sub["late"].get("waived") else f" (−{sub['late']['penalty_pct']:g}%)"
        lines += [f"⏰ Submitted {sub['late']['hours_late']:g} h after the deadline{w}.", ""]
    lines += ["| Criterion | Points | |", "|---|---:|---|"]
    for c in sub["criteria"]:
        o = c.get("override")
        pts = o["points"] if o else c["points"]
        shown = "pending" if (c["status"] in ("manual", "error") and not o) else f"{pts:g} / {c['max_points']:g}"
        lines.append(f"| {c['title']} | {shown} | {_ICON.get(c['status'], '')} |")
    lines.append("")
    for c in sub["criteria"]:
        o = c.get("override")
        problems = [e for e in c["evidence"]
                    if (c["status"] in ("failed", "partial", "error")
                        and e["label"] in ("problem", "missing", "summary"))
                    or e["label"].startswith(("FAIL", "✗"))]
        if o and o.get("comment"):
            lines += [f"### {c['title']}", f"{o['comment']}", ""]
        elif problems:
            lines += [f"### {c['title']}"] + [f"- {e['label']}: {e['text']}".rstrip(": ") for e in problems] + [""]
    if sub.get("reviewer_note"):
        lines += ["### Reviewer note", sub["reviewer_note"], ""]
    return "\n".join(lines)


# ── CSV ───────────────────────────────────────────────────────────────────────

def _status(sub: dict) -> str:
    if sub["status"] == "error":
        return "error"
    return "graded" if sub["score"]["complete"] else "pending_review"


def _grade(sub: dict, include_pending: bool) -> float | str:
    sc = sub["score"]
    if sub["status"] == "error" or (not sc["complete"] and not include_pending):
        return ""
    return sc["scaled"]


def _cell(value):
    """Neutralise spreadsheet formula injection (OWASP "CSV injection").

    A student name like `=HYPERLINK("http://evil","x")` becomes a live formula when a
    grader opens the export in Excel or LibreOffice. Free-text cells that start with
    = + - @ (or a tab/CR) get a leading apostrophe, which spreadsheets render as text.
    Identifiers and numbers are not touched, so LMS matching and grades are unaffected.
    """
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def to_csv(run: dict, fmt: str = "generic", include_pending: bool = False) -> str:
    a, subs = run["assignment"], run["submissions"]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    if fmt == "generic":
        crit_ids = [c["id"] for c in a["criteria"]]
        w.writerow(["id", "name", "email", "repo", "commit", "status", "grade", "scale", "late", "penalty_pct",
                    "review", "review_reasons", *[f"pts_{c}" for c in crit_ids]])
        for s in subs:
            pts = {c["id"]: (c["override"]["points"] if c.get("override") else c["points"]) for c in s["criteria"]}
            w.writerow([s["id"], _cell(s["name"]), _cell(s["email"]), _cell(s["repo"]), s["commit"] or "", _status(s),
                        _grade(s, include_pending), a["scale"],
                        bool(s["late"] and s["late"]["is_late"]),
                        (s["late"] or {}).get("penalty_pct", 0) if not (s["late"] or {}).get("waived") else 0,
                        s["triage"]["state"], "; ".join(r["code"] + (f":{r['criterion']}" if "criterion" in r else "")
                                                         for r in s["triage"]["reasons"]),
                        *[pts.get(c, "") for c in crit_ids]])
    elif fmt == "canvas":
        # Canvas gradebook import: match on "SIS Login ID" (use the roster `id` column for this),
        # then a column named after the assignment.
        col = a["name"]
        w.writerow(["Student", "ID", "SIS User ID", "SIS Login ID", "Section", col])
        w.writerow(["    Points Possible", "", "", "", "", a["scale"]])
        for s in subs:
            w.writerow([_cell(s["name"]), "", "", s["id"], "", _grade(s, include_pending)])
    elif fmt == "moodle":
        # Moodle "offline grading worksheet" upload. `Identifier` must equal Moodle's
        # exported identifier: put it in the roster `id` column.
        w.writerow(["Identifier", "Full name", "Email address", "Status", "Grade", "Maximum grade",
                    "Grade can be changed", "Feedback comments"])
        for s in subs:
            w.writerow([s["id"], _cell(s["name"]), _cell(s["email"]), "", _grade(s, include_pending), a["scale"], "Yes",
                        _cell(_one_line(_short_feedback(s)))])
    else:
        raise ValueError(f"unknown CSV format {fmt!r} (generic, canvas, moodle)")
    return out.getvalue()


def _short_feedback(sub: dict) -> str:
    if sub["status"] == "error":
        return f"Could not grade: {sub['error']['message']}"
    bits = []
    for c in sub["criteria"]:
        o = c.get("override")
        if o and o.get("comment"):
            bits.append(f"{c['title']}: {o['comment']}")
        elif c["status"] in ("failed", "partial"):
            bits.append(f"{c['title']}: {c['points']:g}/{c['max_points']:g}")
    if sub["late"] and sub["late"]["is_late"] and not sub["late"].get("waived"):
        bits.append(f"late penalty {sub['late']['penalty_pct']:g}%")
    if not sub["score"]["complete"]:
        bits.append("manual review pending")
    return "; ".join(bits) or "All automated checks passed."


# ── JUnit ─────────────────────────────────────────────────────────────────────

def to_junit(run: dict) -> str:
    """One <testsuite> per submission, one <testcase> per criterion, so any CI
    system can display the cohort natively."""
    suites = []
    for s in run["submissions"]:
        cases = []
        for c in s["criteria"]:
            body = escape("\n".join(f"{e['label']}: {e['text']}" for e in c["evidence"]))
            name = quoteattr(c["title"])
            cid = quoteattr(c["id"])
            if c["status"] == "passed":
                cases.append(f"    <testcase classname={quoteattr(s['id'])} name={name} id={cid}/>")
            elif c["status"] in ("manual",):
                cases.append(f"    <testcase classname={quoteattr(s['id'])} name={name}><skipped message=\"manual review\"/></testcase>")
            else:
                tag = "error" if c["status"] == "error" else "failure"
                msg = quoteattr("%s %g/%g" % (c["status"], c["points"], c["max_points"]))
                cases.append(f"    <testcase classname={quoteattr(s['id'])} name={name}>"
                             f"<{tag} message={msg}>{body}</{tag}></testcase>")
        if s["status"] == "error":
            cases.append(f"    <testcase classname={quoteattr(s['id'])} name=\"fetch\"><error message={quoteattr(s['error']['message'])}/></testcase>")
        fails = sum(1 for c in s["criteria"] if c["status"] in ("failed", "partial"))
        errs = sum(1 for c in s["criteria"] if c["status"] == "error") + (1 if s["status"] == "error" else 0)
        suites.append(f"  <testsuite name={quoteattr(s['id'])} tests=\"{len(cases)}\" failures=\"{fails}\" errors=\"{errs}\">\n"
                      + "\n".join(cases) + "\n  </testsuite>")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<testsuites>\n' + "\n".join(suites) + "\n</testsuites>\n"


# ── summary ───────────────────────────────────────────────────────────────────

def summary_md(run: dict) -> str:
    a, sm, subs = run["assignment"], run["summary"], run["submissions"]
    rl = sm["review_load"]
    fmt = lambda v: "n/a" if v is None else f"{v:g}"
    pass_rate = "n/a" if sm["pass_rate"] is None else "{:.0%}".format(sm["pass_rate"])
    lines = [f"# {a['name']}", "",
             f"Run `{run['run_id']}` · {sm['submissions']} submissions · sandbox `{a['sandbox']['runtime']}` · "
             f"spec `{a['spec_sha256'][:10]}`", "",
             "## Review load", "",
             f"**{rl['reduction_pct']:g}% less manual review** — {rl['review_items']} items need a human, "
             f"against {rl['baseline_items']} if every criterion of every submission were checked by hand. "
             f"{rl['fully_automatic_submissions']} submission(s) need no review at all.", "",
             "## Grades", "",
             f"Complete: {sm['complete']}/{sm['submissions']} · mean {fmt(sm['mean'])} · median {fmt(sm['median'])} · "
             f"range {fmt(sm['min'])}–{fmt(sm['max'])} · pass rate {pass_rate} · "
             f"provisional mean {fmt(sm['provisional_mean'])}", "",
             "## Review queue", ""]
    queue = [s for s in subs if s["triage"]["state"] == "review"]
    if not queue:
        lines.append("_Nothing needs review._")
    for s in queue:
        why = "; ".join(r["detail"] if r["code"] != "criterion" else f"criterion '{r['criterion']}': {r['detail']}"
                        for r in s["triage"]["reasons"])
        lines.append(f"- **{s['name']}** (`{s['id']}`): {why}")
    if run["similarity"]:
        lines += ["", "## Similarity", ""]
        for p in run["similarity"]:
            f = p["files"][0] if p["files"] else None
            lines.append(f"- `{p['a']}` ↔ `{p['b']}`: {p['score']:.0%}" + (f" ({f['a_file']} ↔ {f['b_file']})" if f else ""))
    return "\n".join(lines) + "\n"


# ── write everything ──────────────────────────────────────────────────────────

def write_reports(run: dict, out: Path, *, lms: list[str] | None = None, include_pending: bool = False) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []

    def put(name: str, text: str) -> None:
        p = out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        written.append(p)

    put("report.json", json.dumps(run, indent=2, ensure_ascii=False))
    put("grades.csv", to_csv(run, "generic", include_pending))
    for fmt in lms or []:
        put(f"grades.{fmt}.csv", to_csv(run, fmt, include_pending))
    put("junit.xml", to_junit(run))
    put("summary.md", summary_md(run))
    for s in run["submissions"]:
        put(f"feedback/{s['id']}.md", feedback_md(s, run["assignment"]))
    return written
