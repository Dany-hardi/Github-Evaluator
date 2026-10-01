import json
import shutil
from pathlib import Path

import jsonschema
import pytest

from markbook import cli
from markbook.sandbox import SandboxUnavailable
from markbook.store import StoreError, current, load_overrides, record_decision
from conftest import make_repo


@pytest.fixture
def graded(tmp_path, demo_run, capsys):
    """A graded demo run in a temp dir, via the real CLI."""
    out = tmp_path / "run"
    rc = cli.main(["grade", str(demo_run["spec_path"]), "--roster", str(demo_run["roster_path"]),
                   "--sandbox", "none", "--out", str(out), "--quiet"])
    assert rc == 0
    capsys.readouterr()
    return out


def test_grade_writes_everything(graded):
    assert {p.name for p in graded.iterdir()} >= {"run.json", "report.json", "grades.csv", "junit.xml",
                                                   "summary.md", "feedback"}


def test_validate_ok_and_error(demo_run, tmp_path, capsys):
    assert cli.main(["validate", str(demo_run["spec_path"])]) == 0
    assert "6 criteria" in capsys.readouterr().out
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\ncriteria: []\n")
    assert cli.main(["validate", str(bad)]) == 1
    assert "at least one criterion" in capsys.readouterr().err


def test_init_creates_valid_spec_for_each_template(tmp_path, capsys):
    for tpl in ("python", "c"):
        d = tmp_path / tpl
        assert cli.main(["init", str(d), "--template", tpl]) == 0
        assert cli.main(["validate", str(d / "spec.yaml")]) == 0
        assert cli.main(["init", str(d), "--template", tpl]) == 1, "refuses to overwrite"
    capsys.readouterr()


def test_sandbox_unavailable_exit_code_3(demo_run, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("markbook.grader.docker_status", lambda: (False, "daemon not running"))
    rc = cli.main(["grade", str(demo_run["spec_path"]), "--roster", str(demo_run["roster_path"]),
                   "--sandbox", "docker", "--out", str(tmp_path / "o")])
    assert rc == 3
    err = capsys.readouterr().err
    assert "daemon not running" in err and "--sandbox none" in err
    assert not (tmp_path / "o").exists(), "nothing is written when grading never started"


def test_no_silent_fallback_to_host(demo_run, monkeypatch):
    """With runtime=docker, a missing docker binary must raise, never run on the host."""
    from markbook.sandbox import make_sandbox
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(SandboxUnavailable):
        make_sandbox("docker", demo_run["spec"].sandbox)


def test_fail_on_review_exit_code_4(demo_run, tmp_path, capsys):
    rc = cli.main(["grade", str(demo_run["spec_path"]), "--roster", str(demo_run["roster_path"]),
                   "--sandbox", "none", "--out", str(tmp_path / "o"), "--quiet", "--fail-on-review"])
    assert rc == 4
    capsys.readouterr()


def test_single_repo_mode_and_json_stdout(tmp_path, demo_run, capsys, schema):
    rc = cli.main(["grade", str(demo_run["spec_path"]), "--repo", str(demo_run["dir"] / "repos/alice"),
                   "--id", "alice1", "--sandbox", "none", "--out", str(tmp_path / "o"), "--quiet",
                   "--format", "json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    jsonschema.Draft202012Validator(schema).validate(report)
    assert report["submissions"][0]["id"] == "alice1"


def test_review_override_report_loop(graded, capsys):
    assert cli.main(["review", str(graded), "--json"]) == 0
    queue = json.loads(capsys.readouterr().out)
    assert "alice" in {q["id"] for q in queue} and len(queue) == 7

    assert cli.main(["override", str(graded), "alice", "-c", "quality", "-p", "3", "-m", "good",
                     "--reviewer", "tester"]) == 0
    assert cli.main(["override", str(graded), "alice", "-c", "quality", "-p", "99"]) == 1   # out of range
    capsys.readouterr()

    rep = json.loads((graded / "report.json").read_text())
    alice = next(s for s in rep["submissions"] if s["id"] == "alice")
    assert alice["score"]["scaled"] == 20.0 and alice["triage"]["state"] == "auto"
    raw = json.loads((graded / "run.json").read_text())
    assert next(s for s in raw["submissions"] if s["id"] == "alice")["criteria"][-1]["override"] is None, \
        "raw result is never edited"

    assert cli.main(["review", str(graded), "--json"]) == 0
    assert "alice" not in {q["id"] for q in json.loads(capsys.readouterr().out)}

    decisions = load_overrides(graded)["decisions"]
    assert decisions[0]["reviewer"] == "tester" and decisions[0]["was_status"] == "manual"
    assert decisions[0]["at"]


def test_report_regenerates_with_lms(graded, tmp_path, capsys):
    assert cli.main(["report", str(graded), "--lms", "canvas", "--out", str(tmp_path / "r2")]) == 0
    assert (tmp_path / "r2" / "grades.canvas.csv").exists()
    capsys.readouterr()


def test_show_and_unknown_run(graded, capsys):
    assert cli.main(["show", str(graded), "dave"]) == 0
    assert "FAIL first five" in capsys.readouterr().out
    assert cli.main(["show", str(graded), "nobody"]) == 1
    assert cli.main(["review", "does-not-exist"]) == 1
    capsys.readouterr()


def test_schema_command_matches_packaged_file(capsys, schema):
    assert cli.main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out) == schema


def test_store_validation(graded):
    with pytest.raises(StoreError, match="no submission"):
        record_decision(graded, "zzz", reviewer="t", comment="x")
    with pytest.raises(StoreError, match="no criterion"):
        record_decision(graded, "alice", reviewer="t", criterion="nope", points=1)
    with pytest.raises(StoreError, match="nothing to record"):
        record_decision(graded, "alice", reviewer="t")
    with pytest.raises(StoreError, match="only clear"):
        record_decision(graded, "alice", reviewer="t", clear="late")


def test_grade_is_deterministic(tmp_path, demo_run):
    """Same spec + same commits → identical scores (reproducibility claim)."""
    from markbook.grader import grade_cohort
    from markbook.roster import load_roster
    again = grade_cohort(demo_run["spec"], load_roster(demo_run["roster_path"]), "none", allow_local=True)
    scores = lambda r: {s["id"]: (s["score"]["scaled"], [c["points"] for c in s["criteria"]]) for s in r["submissions"]}
    assert scores(again) == scores(demo_run["run"])


# ── retry, runs, locking, interrupt ───────────────────────────────────────────

def test_retry_recovers_fixed_repo_and_recomputes_similarity(tmp_path, demo_run, capsys):
    """A repo that failed to fetch (path didn't exist yet) is re-graded without touching anyone else."""
    import shutil
    from markbook import cli
    late_repo = tmp_path / "late-repo"
    roster = tmp_path / "roster.csv"
    roster.write_text("id,name,repo\nalice,Alice,%s\nghost2,Ghost,%s\n" % (demo_run["dir"] / "repos/alice", late_repo))
    out = tmp_path / "run"
    assert cli.main(["grade", str(demo_run["spec_path"]), "--roster", str(roster), "--sandbox", "none",
                     "--out", str(out), "--quiet"]) == 0
    before = json.loads((out / "run.json").read_text())
    assert next(s for s in before["submissions"] if s["id"] == "ghost2")["status"] == "error"
    alice_before = next(s for s in before["submissions"] if s["id"] == "alice")

    shutil.copytree(demo_run["dir"] / "repos/alice", late_repo)          # "the student fixed their repo": a clone of alice
    capsys.readouterr()
    assert cli.main(["retry", str(out), "--sandbox", "none"]) == 0
    after = json.loads((out / "run.json").read_text())
    ghost = next(s for s in after["submissions"] if s["id"] == "ghost2")
    assert ghost["status"] == "graded" and ghost["score"]["scaled"] == 17.0
    assert next(s for s in after["submissions"] if s["id"] == "alice")["criteria"] == alice_before["criteria"]
    assert [(p["a"], p["b"]) for p in after["similarity"]] == [("alice", "ghost2")], \
        "similarity must be recomputed against the unchanged students' stored fingerprints"
    assert after["run_id"] == before["run_id"]
    assert cli.main(["retry", str(out), "--sandbox", "none"]) == 0
    assert "Nothing to retry" in capsys.readouterr().out


def test_retry_refuses_when_spec_changed(tmp_path, demo_run, capsys):
    import shutil
    spec_dir = tmp_path / "s"
    shutil.copy(demo_run["spec_path"], spec_dir.mkdir() or spec_dir / "spec.yaml")
    out = tmp_path / "run"
    cli.main(["grade", str(spec_dir / "spec.yaml"), "--roster", str(demo_run["roster_path"]), "--sandbox", "none",
              "--out", str(out), "--quiet"])
    (spec_dir / "spec.yaml").write_text((spec_dir / "spec.yaml").read_text().replace("version: 1", "version: 2"))
    capsys.readouterr()
    assert cli.main(["retry", str(out), "--sandbox", "none"]) == 1
    assert "spec has changed" in capsys.readouterr().err


def test_runs_listing(graded, capsys):
    assert cli.main(["runs", "--dir", str(graded.parent), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["id"] == "run" and rows[0]["submissions"] == 7 and rows[0]["needs_review"] == 7


def test_concurrent_decisions_are_not_lost(graded):
    """CLI and web can write at the same time; a lost update would silently drop a human's grade."""
    import threading
    from markbook.store import load_overrides
    ids = ["alice", "bob", "carol", "dave", "erin", "frank"]
    barrier = threading.Barrier(len(ids))

    def decide(sid):
        barrier.wait()
        record_decision(graded, sid, reviewer="t", criterion="quality", points=2, comment="x")

    threads = [threading.Thread(target=decide, args=(i,)) for i in ids]
    [t.start() for t in threads]; [t.join() for t in threads]
    doc = load_overrides(graded)
    assert set(doc["effective"]) == set(ids) and len(doc["decisions"]) == len(ids)


def test_python_dash_m_entrypoint():
    import subprocess, sys
    r = subprocess.run([sys.executable, "-m", "markbook", "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("markbook ")


def test_ctrl_c_stops_scheduling_and_exits_130(tmp_path):
    """SIGINT mid-run: exits promptly (after the in-flight submission), grades nothing further, writes no partial run."""
    import signal, subprocess, sys, time
    from conftest import make_repo
    spec = tmp_path / "spec.yaml"
    spec.write_text("name: slow\nsandbox: {image: x, timeout: 20}\ncriteria:\n"
                    "  - {id: wait, points: 1, check: {type: command, run: 'sleep 3'}}\n")
    repos = [make_repo(tmp_path / f"r{i}", {"a": "1"}) for i in range(6)]
    roster = tmp_path / "roster.csv"
    roster.write_text("id,repo\n" + "".join(f"s{i},{r}\n" for i, r in enumerate(repos)))
    out = tmp_path / "out"
    p = subprocess.Popen([sys.executable, "-m", "markbook", "grade", str(spec), "--roster", str(roster), "--sandbox", "none",
                          "--jobs", "1", "--out", str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    t0 = time.time()
    p.send_signal(signal.SIGINT)
    _, err = p.communicate(timeout=30)
    assert p.returncode == 130, err
    assert time.time() - t0 < 8, "6 x 3 s of queued work must not keep running after Ctrl-C"
    assert not (out / "run.json").exists()
