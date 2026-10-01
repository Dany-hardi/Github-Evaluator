"""File-based job queue: layout, exactly-once results, crash recovery, equivalence with local grading.

Multi-*process* on one machine only: multi-machine (NFS) operation is not verified here.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import make_repo
from markbook import cli
from markbook import queue as jq
from markbook.demo import build_demo
from markbook.grader import grade_cohort
from markbook.report import write_reports
from markbook.roster import RosterError, load_roster, parse_roster
from markbook.spec import load_spec
from markbook.store import current, save_run

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONPATH": str(ROOT), "NO_COLOR": "1"}
ENV.pop("GITHUB_TOKEN", None)
ENV.pop("GH_TOKEN", None)

FAST_SPEC = """\
name: "queue test"
scale: 10
criteria:
  - {id: has_main, title: main.py exists, points: 5, check: {type: file_exists, paths: [main.py]}}
  - {id: has_readme, title: README exists, points: 5, check: {type: file_exists, paths: [README.md]}}
"""

SLOW_SPEC = """\
name: "slow queue test"
scale: 10
sandbox: {image: "python:3.12-slim", timeout: 30}
criteria:
  - {id: slow, title: slow command, points: 10, check: {type: command, run: "sleep 2", expect_exit: 0}}
"""


def mk_cohort(tmp_path, n, spec_text=FAST_SPEC, spec_name="spec.yaml"):
    repo = make_repo(tmp_path / "repo", {"main.py": "print('hi')\n", "README.md": "# hi\n"})
    spec = tmp_path / spec_name
    spec.write_text(spec_text)
    roster = tmp_path / "roster.csv"
    roster.write_text("id,name,email,repo\n" + "".join(f"s{i:03d},Student {i},s{i}@x.edu,{repo}\n" for i in range(n)))
    return spec, roster


def enqueue(spec, roster, qdir, sandbox="none"):
    rc = cli.main(["enqueue", str(spec), "--roster", str(roster), "--queue", str(qdir), "--sandbox", sandbox])
    assert rc == 0
    return jq.Queue(qdir)


def spawn_worker(qdir, *extra, **kw):
    return subprocess.Popen([sys.executable, "-m", "markbook", "worker", str(qdir), "--poll", "0.1", *extra],
                            env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def done_ids(qdir):
    return sorted(p.name.removesuffix(".json") for p in (Path(qdir) / "done").glob("*.json"))


def wait_for(cond, timeout=60.0, what="condition"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


# ── enqueue ───────────────────────────────────────────────────────────────────

def test_enqueue_layout_and_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_SECRETSECRET")
    spec, roster = mk_cohort(tmp_path, 3)
    q = enqueue(spec, roster, tmp_path / "q")
    meta = json.loads((tmp_path / "q" / "meta.json").read_text())
    assert meta["roster_ids"] == ["s000", "s001", "s002"]
    assert meta["spec_sha256"] == q.load_spec().sha256
    assert meta["runtime"] == "none" and meta["queue_schema"] == 1 and meta["allow_local"] is True
    assert meta["run_id"] and meta["created_at"]
    jobs = sorted(p.name for p in (tmp_path / "q" / "pending").iterdir())
    assert jobs == ["0000-s000.json", "0001-s001.json", "0002-s002.json"]
    job = json.loads((tmp_path / "q" / "pending" / jobs[1]).read_text())
    assert set(job) == {"seq", "id", "name", "email", "repo", "ref"} and job["id"] == "s001"
    # only the YAML is frozen when the spec references no other files
    assert [p.name for p in (tmp_path / "q" / "spec").iterdir()] == ["spec.yaml"]
    for p in (tmp_path / "q").rglob("*"):
        if p.is_file():
            assert "SECRET" not in p.read_text()


def test_enqueue_refusals(tmp_path):
    spec, roster = mk_cohort(tmp_path, 1)
    with pytest.raises(jq.QueueError, match="empty roster"):
        jq.enqueue(spec, [], tmp_path / "q0", "none", run_id="x")
    with pytest.raises(RosterError):
        parse_roster("id,name,email,repo\n")
    enqueue(spec, roster, tmp_path / "q")
    with pytest.raises(jq.QueueError, match="already holds a queue"):
        jq.enqueue(spec, load_roster(roster), tmp_path / "q", "none", run_id="y")
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\ncriteria: []\n")
    from markbook.spec import SpecError
    with pytest.raises(SpecError):
        jq.enqueue(bad, load_roster(roster), tmp_path / "q2", "none", run_id="z")
    assert not (tmp_path / "q2").exists()   # nothing half-created


def test_spec_hash_tamper_refused(tmp_path):
    spec, roster = mk_cohort(tmp_path, 2)
    q = enqueue(spec, roster, tmp_path / "q")
    frozen = tmp_path / "q" / "spec" / "spec.yaml"
    frozen.write_text(frozen.read_text().replace("points: 5", "points: 6", 1))
    with pytest.raises(jq.QueueError, match="hash differs"):
        q.load_spec()
    r = subprocess.run([sys.executable, "-m", "markbook", "worker", str(tmp_path / "q"), "--idle-exit"],
                       env=ENV, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "hash differs" in r.stderr
    assert done_ids(tmp_path / "q") == []
    assert cli.main(["collect", str(tmp_path / "q"), "--partial", "--out", str(tmp_path / "o")]) == 1


def test_spec_with_overlay_freezes_directory_and_detects_tamper(tmp_path):
    spec_dir = tmp_path / "specdir"
    (spec_dir / "overlay").mkdir(parents=True)
    (spec_dir / "overlay" / "t.txt").write_text("teacher file\n")
    (spec_dir / "spec.yaml").write_text(FAST_SPEC + "overlay: overlay\n")
    _, roster = mk_cohort(tmp_path, 1)
    q = enqueue(spec_dir / "spec.yaml", roster, tmp_path / "q")
    assert (tmp_path / "q" / "spec" / "overlay" / "t.txt").read_text() == "teacher file\n"
    assert q.load_spec().overlay == (tmp_path / "q" / "spec" / "overlay").resolve()
    (tmp_path / "q" / "spec" / "overlay" / "t.txt").write_text("tampered\n")
    with pytest.raises(jq.QueueError, match="overlay/starter/cases"):
        q.load_spec()


# ── exactly-once ──────────────────────────────────────────────────────────────

def test_two_competing_workers_never_grade_a_job_twice(tmp_path):
    n = 40
    spec, roster = mk_cohort(tmp_path, n)
    enqueue(spec, roster, tmp_path / "q")
    procs = [spawn_worker(tmp_path / "q", "--idle-exit", "--jobs", "2") for _ in range(2)]
    graded = 0
    for p in procs:
        _, err = p.communicate(timeout=180)
        assert p.returncode == 0, err
        line = [l for l in err.splitlines() if "exiting: graded" in l][-1]
        graded += int(line.split("graded ")[1].split(",")[0])
        assert "discarded 0" in line
    assert done_ids(tmp_path / "q") == [f"s{i:03d}" for i in range(n)]
    assert graded == n          # each job graded exactly once across both workers
    q = jq.Queue(tmp_path / "q")
    assert q.counts() == {"total": n, "pending": 0, "claimed": 0, "done": n, "failed": 0}
    assert list((tmp_path / "q" / "claimed").iterdir()) == []


def test_late_duplicate_result_is_discarded(tmp_path):
    spec, roster = mk_cohort(tmp_path, 1)
    q = enqueue(spec, roster, tmp_path / "q")
    entry = load_roster(roster)[0]
    first = jq.error_record(entry, "first", "first result")
    late = jq.error_record(entry, "late", "late result from a presumed-dead worker")
    assert q.write_result("s000", first, {"a.py": {3, 1, 2}}, worker="w1") is True
    assert q.write_result("s000", late, {}, worker="w2") is False
    res = q.read_result("s000")
    assert res["submission"]["error"]["code"] == "first" and res["worker"] == "w1"
    assert res["corpus"] == {"a.py": [1, 2, 3]}
    assert done_ids(tmp_path / "q") == ["s000"]
    assert [p.name for p in (tmp_path / "q" / "done").iterdir()] == ["s000.json"]   # no temp litter


def test_reclaimed_job_already_done_is_dropped_not_regraded(tmp_path):
    spec, roster = mk_cohort(tmp_path, 1)
    q = enqueue(spec, roster, tmp_path / "q")
    w = jq.Worker(q, worker_id="w-late")
    claimed, name = q.claim(w.id)
    q.write_result("s000", jq.error_record(load_roster(roster)[0], "first", "x"), {}, worker="other")
    w._process(claimed, name, q.load_spec())
    assert w.graded == 0 and w.discarded == 1
    assert q.read_result("s000")["submission"]["error"]["code"] == "first"
    assert not claimed.exists()


def test_unexpected_exception_becomes_error_record(tmp_path, monkeypatch):
    spec, roster = mk_cohort(tmp_path, 1)
    q = enqueue(spec, roster, tmp_path / "q")

    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(jq, "grade_submission", boom)
    w = jq.Worker(q, worker_id="w1")
    claimed, name = q.claim(w.id)
    w._process(claimed, name, q.load_spec())
    sub = q.read_result("s000")["submission"]
    assert sub["status"] == "error" and sub["error"]["code"] == "internal_error" and "kaboom" in sub["error"]["message"]
    assert q.counts()["done"] == 1


def test_unreadable_job_goes_to_failed(tmp_path):
    spec, roster = mk_cohort(tmp_path, 2)
    q = enqueue(spec, roster, tmp_path / "q")
    (tmp_path / "q" / "pending" / "0000-s000.json").write_text("{not json")
    w = jq.Worker(q, worker_id="w1")
    claimed, name = q.claim(w.id)
    w._process(claimed, name, q.load_spec())
    assert q.counts()["failed"] == 1
    assert cli.main(["collect", str(tmp_path / "q"), "--partial", "--out", str(tmp_path / "o")]) == 0
    run = json.loads((tmp_path / "o" / "run.json").read_text())
    assert run["submissions"][0]["error"]["code"] == "not_graded"


# ── crash recovery ────────────────────────────────────────────────────────────

def test_sigkilled_worker_job_is_reclaimed_and_graded_once(tmp_path):
    spec, roster = mk_cohort(tmp_path, 1, SLOW_SPEC)
    q = enqueue(spec, roster, tmp_path / "q")
    claimed_dir = tmp_path / "q" / "claimed"
    w1 = spawn_worker(tmp_path / "q", "--lease-seconds", "2")
    try:
        wait_for(lambda: any(p.is_dir() and list(p.glob("*.json")) for p in claimed_dir.iterdir()),
                 what="worker 1 to claim its job")
        w1.kill()              # SIGKILL mid-job: no cleanup, heartbeat stops
        w1.communicate(timeout=30)
        assert done_ids(tmp_path / "q") == []
        w2 = spawn_worker(tmp_path / "q", "--lease-seconds", "2", "--idle-exit")
        _, err = w2.communicate(timeout=120)
        assert w2.returncode == 0, err
    finally:
        for p in (w1,):
            if p.poll() is None:
                p.kill()
    assert done_ids(tmp_path / "q") == ["s000"]
    res = q.read_result("s000")
    assert res["submission"]["status"] == "graded" and res["submission"]["criteria"][0]["status"] == "passed"
    assert (tmp_path / "q" / "attempts" / "0000-s000.json").stat().st_size == 1   # lost exactly once
    assert q.counts() == {"total": 1, "pending": 0, "claimed": 0, "done": 1, "failed": 0}


def test_job_lost_repeatedly_becomes_error_result(tmp_path):
    spec, roster = mk_cohort(tmp_path, 1)
    q = enqueue(spec, roster, tmp_path / "q")
    for _ in range(jq.MAX_ATTEMPTS):
        assert q.claim("ghost")            # ghost has no heartbeat: presumed dead at once
        q.reclaim_stale(5.0)
    assert q.counts()["pending"] == 0 and q.counts()["done"] == 1
    assert q.read_result("s000")["submission"]["error"]["code"] == "worker_lost"


def test_reclaim_leaves_live_workers_alone(tmp_path):
    spec, roster = mk_cohort(tmp_path, 1)
    q = enqueue(spec, roster, tmp_path / "q")
    w = jq.Worker(q, worker_id="alive", lease_seconds=60)
    w._write_hb()
    assert q.claim("alive")
    assert q.reclaim_stale(60) == 0
    assert q.counts()["claimed"] == 1
    old = time.time() - 3600
    os.utime(tmp_path / "q" / "workers" / "alive.hb", (old, old))
    assert q.reclaim_stale(60) == 1 and q.counts()["pending"] == 1


# ── graceful stop ─────────────────────────────────────────────────────────────

def test_sigterm_finishes_current_job_and_claims_no_more(tmp_path):
    spec, roster = mk_cohort(tmp_path, 4, SLOW_SPEC)
    enqueue(spec, roster, tmp_path / "q")
    w = spawn_worker(tmp_path / "q")
    try:
        wait_for(lambda: any(p.is_dir() and list(p.glob("*.json")) for p in (tmp_path / "q" / "claimed").iterdir()),
                 what="the worker to claim a job")
        w.send_signal(signal.SIGTERM)
        _, err = w.communicate(timeout=60)
    finally:
        if w.poll() is None:
            w.kill()
    assert w.returncode == 0, err
    assert "claiming no more" in err
    q = jq.Queue(tmp_path / "q")
    c = q.counts()
    assert c["done"] == 1 and c["claimed"] == 0 and c["pending"] == 3     # finished one, left the rest
    assert list((tmp_path / "q" / "workers").iterdir()) == []             # heartbeat removed
    assert q.read_result(done_ids(tmp_path / "q")[0])["submission"]["status"] == "graded"


# ── collect & status ──────────────────────────────────────────────────────────

def test_collect_partial_and_queue_status(tmp_path, capsys):
    spec, roster = mk_cohort(tmp_path, 3)
    q = enqueue(spec, roster, tmp_path / "q")
    capsys.readouterr()
    cli.main(["queue-status", str(tmp_path / "q"), "--json"])
    st = json.loads(capsys.readouterr().out)
    assert (st["pending"], st["claimed"], st["done"], st["failed"], st["total"]) == (3, 0, 0, 0, 3)
    assert st["complete"] is False

    w = jq.Worker(q, worker_id="w-one", lease_seconds=60)
    w._write_hb()
    sp = q.load_spec()
    for _ in range(2):
        c, n = q.claim(w.id)
        w._process(c, n, sp)
    held, _ = q.claim(w.id)                     # a third job is claimed but unfinished
    cli.main(["queue-status", str(tmp_path / "q"), "--json"])
    st = json.loads(capsys.readouterr().out)
    assert (st["pending"], st["claimed"], st["done"]) == (0, 1, 2)
    assert st["workers"][0]["worker"] == "w-one" and st["workers"][0]["claimed"] == ["s002"]
    assert st["workers"][0]["stale"] is False and st["stale_leases"] == []
    old = time.time() - 3600
    os.utime(tmp_path / "q" / "workers" / "w-one.hb", (old, old))
    cli.main(["queue-status", str(tmp_path / "q"), "--json"])
    st = json.loads(capsys.readouterr().out)
    assert st["stale_leases"] == [{"worker": "w-one", "jobs": ["s002"]}]

    out = tmp_path / "out"
    assert cli.main(["collect", str(tmp_path / "q"), "--out", str(out)]) == 1     # refuses: not complete
    assert not out.exists()
    assert cli.main(["collect", str(tmp_path / "q"), "--partial", "--out", str(out)]) == 0
    run = json.loads((out / "run.json").read_text())
    assert [s["id"] for s in run["submissions"]] == ["s000", "s001", "s002"]
    missing = run["submissions"][2]
    assert missing["status"] == "error" and missing["error"]["code"] == "not_graded"
    assert missing["name"] == "Student 2"            # identity kept from the job file
    assert run["submissions"][0]["status"] == "graded"
    assert run["run_id"] == q.meta["run_id"] and run["inputs"]["allow_local"] is True
    assert (out / "report.json").is_file() and (out / "fingerprints.json").is_file()


# ── equivalence with local grading ────────────────────────────────────────────

def _essence(run):
    return [{"id": s["id"], "status": s["status"], "error": s["error"], "score": s["score"], "triage": s["triage"],
             "commit": s["commit"], "late": s["late"],
             "criteria": [(c["id"], c["status"], c["points"], c["needs_review"]) for c in s["criteria"]],
             "similarity": s["similarity"]} for s in run["submissions"]]


def test_distributed_equals_local_on_demo_cohort(tmp_path):
    spec_p, roster_p = build_demo(tmp_path / "demo")
    run = grade_cohort(load_spec(spec_p), load_roster(roster_p), "none", allow_local=True, jobs=4, run_id="local")
    local_dir = tmp_path / "local"
    save_run(local_dir, run)
    local = current(local_dir)
    write_reports(local, local_dir)

    qdir, out = tmp_path / "q", tmp_path / "dist"
    enqueue(spec_p, roster_p, qdir)
    procs = [spawn_worker(qdir, "--idle-exit") for _ in range(3)]
    for p in procs:
        _, err = p.communicate(timeout=180)
        assert p.returncode == 0, err
    assert cli.main(["collect", str(qdir), "--out", str(out)]) == 0
    dist = json.loads((out / "report.json").read_text())

    assert [s["id"] for s in dist["submissions"]] == [s["id"] for s in local["submissions"]]   # roster order
    assert _essence(dist) == _essence(local)
    assert dist["summary"]["review_load"] == local["summary"]["review_load"]
    assert dist["similarity"] == local["similarity"]
    assert any(s["similarity"] for s in dist["submissions"])       # the copied pair really was flagged
    assert dist["assignment"]["spec_sha256"] == local["assignment"]["spec_sha256"]
    assert (out / "grades.csv").read_text() == (local_dir / "grades.csv").read_text()
