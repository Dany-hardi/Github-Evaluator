import csv
import io
import json
import xml.dom.minidom

import jsonschema
import pytest

from markbook import grader, report
from markbook.grader import rescore
from markbook.roster import Entry
from markbook.spec import load_spec
from conftest import crit_of, make_repo, sub_of


# ── demo cohort: end-to-end expectations ──────────────────────────────────────

EXPECTED_AUTO = {   # points awarded by automation, quality (manual) still pending
    "alice": 17.0, "bob": 17.0, "carol": 17.0, "erin": 13.6, "frank": 2.0,
}


def test_demo_scores(demo_run):
    run = demo_run["run"]
    for sid, scaled in EXPECTED_AUTO.items():
        assert sub_of(run, sid)["score"]["scaled"] == pytest.approx(scaled), sid
    dave = sub_of(run, "dave")
    assert crit_of(dave, "behaviour")["points"] == pytest.approx(2.0)   # 1 of 4 cases (the off-by-one bug)
    assert crit_of(dave, "readme")["status"] == "partial"               # thin README, no usage section


def test_crashing_program_earns_nothing_for_behaviour(demo_run):
    frank = sub_of(demo_run["run"], "frank")
    assert crit_of(frank, "runs")["status"] == "failed"
    assert crit_of(frank, "behaviour")["points"] == 0.0, "syntax error must not earn any case"


def test_late_penalty_applied_from_last_commit(demo_run):
    erin = sub_of(demo_run["run"], "erin")
    assert erin["late"]["is_late"] and erin["late"]["penalty_pct"] == 20.0
    assert erin["score"]["penalty"] == pytest.approx(0.2 * (2 + 2 + 8 + 3 + 2))


def test_copied_pair_flagged_both_ways_with_evidence(demo_run):
    run = demo_run["run"]
    assert [(p["a"], p["b"]) for p in run["similarity"]] == [("bob", "carol")]
    assert sub_of(run, "bob")["similarity"][0]["with"] == "carol"
    reasons = {r["code"] for r in sub_of(run, "carol")["triage"]["reasons"]}
    assert "similarity" in reasons
    assert not sub_of(run, "alice")["similarity"], "an independent solution must not be flagged"


def test_fetch_failure_goes_to_review_not_zero(demo_run):
    ghost = sub_of(demo_run["run"], "ghost")
    assert ghost["status"] == "error" and ghost["score"]["scaled"] is None
    assert ghost["triage"]["reasons"][0]["code"] == "fetch_failed"


def test_manual_criterion_is_pending_not_zero(demo_run):
    alice = sub_of(demo_run["run"], "alice")
    q = crit_of(alice, "quality")
    assert q["status"] == "manual" and q["needs_review"]
    assert alice["score"]["pending_points"] == 3 and not alice["score"]["complete"]


def test_review_load_numbers(demo_run):
    rl = demo_run["run"]["summary"]["review_load"]
    # 7 submissions x 6 criteria = 42. Review: 6 manual + 2 similarity + 1 fetch failure = 9.
    assert (rl["baseline_items"], rl["review_items"]) == (42, 9)
    assert rl["reduction_pct"] == pytest.approx(78.6)


def test_dependency_blocks_dependent_criteria(demo_run):
    # frank has no README but does have fizzbuzz.py; build a repo missing the file instead
    spec = demo_run["spec"]
    sub, _ = grader.grade_submission(spec, Entry("x", "X", "", str(demo_run["dir"] / "repos" / "frank")), "none",
                                     allow_local=True)
    # structure passes for frank (file exists) so dependents ran; now remove the dependency
    repo = make_repo(demo_run["dir"] / "noprog", {"README.md": "# hi"})
    sub, _ = grader.grade_submission(spec, Entry("n", "N", "", str(repo)), "none", allow_local=True)
    assert crit_of(sub, "structure")["status"] == "failed"
    assert crit_of(sub, "runs")["status"] == "failed"
    assert crit_of(sub, "runs")["evidence"][0]["label"] == "skipped"
    assert crit_of(sub, "behaviour")["points"] == 0


def test_check_exception_becomes_error_and_review(demo_run, monkeypatch):
    def boom(opts, ctx):
        raise RuntimeError("kaboom")
    monkeypatch.setitem(grader.checks.REGISTRY, "readme", boom)
    repo = make_repo(demo_run["dir"] / "boomrepo", {"fizzbuzz.py": "print(1)\n"})
    sub, _ = grader.grade_submission(demo_run["spec"], Entry("b", "B", "", str(repo)), "none", allow_local=True)
    c = crit_of(sub, "readme")
    assert c["status"] == "error" and c["needs_review"] and "kaboom" in c["evidence"][0]["text"]
    run = {"assignment": grader.assignment_block(demo_run["spec"], "none"), "submissions": [sub]}
    assert rescore(run)["submissions"][0]["score"]["pending_points"] >= 3, "an infra error must not cost marks silently"


def test_git_dir_is_not_visible_to_student_code(demo_run):
    spec_yaml = (demo_run["dir"] / "spec.yaml").read_text().split("criteria:")[0]
    p = demo_run["dir"] / "ls_spec.yaml"
    p.write_text(spec_yaml + """criteria:
  - id: nogit
    points: 1
    check: {type: command, run: "test ! -e .git", expect_exit: 0}
""")
    sub, _ = grader.grade_submission(load_spec(p), Entry("a", "A", "", str(demo_run["dir"] / "repos/alice")),
                                     "none", allow_local=True)
    assert crit_of(sub, "nogit")["status"] == "passed"


def test_symlinks_stripped_and_overlay_wins(demo_run, tmp_path):
    overlay = tmp_path / "ov"
    overlay.mkdir()
    (overlay / "check.sh").write_text("echo teacher")
    (tmp_path / "s.yaml").write_text(f"""
name: T
sandbox: {{image: x}}
overlay: ov
criteria:
  - id: a
    points: 1
    check: {{type: command, run: "sh check.sh", stdout_contains: teacher}}
""")
    import os
    from conftest import git
    repo = make_repo(tmp_path / "stu", {"check.sh": "echo student"})
    os.symlink("/etc/passwd", repo / "link")   # committed, so it really is in the submission
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add link")
    sub, _ = grader.grade_submission(load_spec(tmp_path / "s.yaml"), Entry("s", "S", "", str(repo)), "none",
                                     allow_local=True)
    assert crit_of(sub, "a")["status"] == "passed", "teacher overlay must override student files"
    assert any("symlink" in n for n in sub["notes"])


# ── overrides ─────────────────────────────────────────────────────────────────

def test_overrides_recompute_without_mutating_raw(demo_run):
    raw = demo_run["run"]
    before = json.dumps(raw, sort_keys=True)
    ov = {"alice": {"criteria": {"quality": {"points": 3, "comment": "great", "reviewer": "t", "at": "x"}}},
          "bob": {"cleared": ["similarity"]}}
    new = rescore(raw, ov)
    assert json.dumps(raw, sort_keys=True) == before, "rescore must not mutate its input"
    alice = sub_of(new, "alice")
    assert alice["score"]["scaled"] == 20.0 and alice["score"]["complete"] and alice["triage"]["state"] == "auto"
    assert "similarity" not in {r["code"] for r in sub_of(new, "bob")["triage"]["reasons"]}
    assert "similarity" in {r["code"] for r in sub_of(new, "carol")["triage"]["reasons"]}


def test_late_waiver_and_borderline(demo_run):
    ov = {"erin": {"late_waived": True, "criteria": {"quality": {"points": 2, "reviewer": "t", "at": "x"}}}}
    erin = sub_of(rescore(demo_run["run"], ov), "erin")
    assert erin["score"]["penalty"] == 0 and erin["score"]["scaled"] == 19.0
    # dave: auto 8.08 + quality 2 = 10.08 -> within margin 0.4 of pass mark 10 -> borderline review
    ov = {"dave": {"criteria": {"quality": {"points": 2, "reviewer": "t", "at": "x"}}}}
    dave = sub_of(rescore(demo_run["run"], ov), "dave")
    assert [r["code"] for r in dave["triage"]["reasons"]] == ["borderline"]
    ov["dave"]["cleared"] = ["borderline"]
    assert sub_of(rescore(demo_run["run"], ov), "dave")["triage"]["state"] == "auto"


# ── reports ───────────────────────────────────────────────────────────────────

def test_report_json_validates_against_schema(demo_run, schema):
    jsonschema.Draft202012Validator(schema).validate(demo_run["run"])


def test_report_with_overrides_still_validates(demo_run, schema):
    ov = {"alice": {"criteria": {"quality": {"points": 3, "comment": "great", "reviewer": "t", "at": "2026-01-01"}}}}
    jsonschema.Draft202012Validator(schema).validate(rescore(demo_run["run"], ov))


def rows(text):
    return list(csv.reader(io.StringIO(text)))


def test_generic_csv_blank_grade_until_complete(demo_run):
    r = rows(report.to_csv(demo_run["run"]))
    header = r[0]
    body = {row[0]: dict(zip(header, row)) for row in r[1:]}
    assert body["alice"]["grade"] == "" and body["alice"]["status"] == "pending_review"
    assert body["ghost"]["status"] == "error"
    prov = {row[0]: dict(zip(header, row)) for row in rows(report.to_csv(demo_run["run"], include_pending=True))[1:]}
    assert prov["alice"]["grade"] == "17.0" and prov["ghost"]["grade"] == ""
    assert "pts_behaviour" in header


def test_csv_neutralises_formula_injection_and_keeps_data_intact(demo_run):
    run = rescore(demo_run["run"])
    run["submissions"][0]["name"] = '=HYPERLINK("http://evil","click")'
    run["submissions"][1]["name"] = 'Eve, "The" Hacker\nline2'
    run["submissions"][2]["email"] = "+cmd|' /C calc'!A0"
    for fmt in ("generic", "canvas", "moodle"):
        for row in rows(report.to_csv(run, fmt))[1:]:
            for cell in row:
                assert cell[:1] not in ("=", "+", "@"), (fmt, cell)
    generic = rows(report.to_csv(run))
    assert generic[1][1] == "'=HYPERLINK(\"http://evil\",\"click\")"      # defused, still readable
    assert generic[2][1] == 'Eve, "The" Hacker\nline2'                        # quoting/newlines round-trip
    assert generic[1][0] == "alice" and generic[1][3].startswith("/")          # ids and paths untouched
    assert report._cell("-5") == "'-5" and report._cell(17.0) == 17.0 and report._cell("alice") == "alice"


def test_canvas_and_moodle_formats(demo_run):
    ov = {"alice": {"criteria": {"quality": {"points": 3, "comment": "Nice, clean work.", "reviewer": "t", "at": "x"}}}}
    run = rescore(demo_run["run"], ov)
    canvas = rows(report.to_csv(run, "canvas"))
    assert canvas[0][:5] == ["Student", "ID", "SIS User ID", "SIS Login ID", "Section"]
    assert canvas[1][0].strip() == "Points Possible" and canvas[1][-1] == "20.0"
    assert [r for r in canvas if r[3] == "alice"][0][-1] == "20.0"
    moodle = rows(report.to_csv(run, "moodle"))
    assert moodle[0][0] == "Identifier" and "Maximum grade" in moodle[0]
    alice = [r for r in moodle if r[0] == "alice"][0]
    assert alice[moodle[0].index("Grade")] == "20.0" and "Nice, clean work." in alice[-1]
    with pytest.raises(ValueError):
        report.to_csv(run, "blackboard")


def test_junit_is_well_formed(demo_run):
    xml_text = report.to_junit(demo_run["run"])
    doc = xml.dom.minidom.parseString(xml_text)
    assert len(doc.getElementsByTagName("testsuite")) == 7
    assert doc.getElementsByTagName("skipped"), "manual criteria appear as skipped"
    assert doc.getElementsByTagName("failure"), "failed criteria appear as failures"


def test_feedback_never_leaks_hidden_expectations(demo_run):
    for s in demo_run["run"]["submissions"]:
        text = report.feedback_md(s, demo_run["run"]["assignment"])
        assert "FizzBuzz\\n16" not in text and "stdout_regex" not in text
    dave = report.feedback_md(sub_of(demo_run["run"], "dave"), demo_run["run"]["assignment"])
    assert "FAIL first five" in dave and "hidden" in dave


def test_write_reports_creates_expected_files(demo_run, tmp_path):
    files = report.write_reports(rescore(demo_run["run"]), tmp_path, lms=["canvas", "moodle"])
    names = {p.relative_to(tmp_path).as_posix() for p in files}
    assert {"report.json", "grades.csv", "grades.canvas.csv", "grades.moodle.csv", "junit.xml", "summary.md",
            "feedback/alice.md", "feedback/ghost.md"} <= names
    assert "Review load" in (tmp_path / "summary.md").read_text()
