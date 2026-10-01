"""A file-based job queue for grading across processes or machines.

A queue is a plain directory on a filesystem every worker can reach:

    <queue>/meta.json            run id, spec hash, roster order, runtime, ...
    <queue>/spec/                frozen copy of the spec (and its inputs, if it has any)
    <queue>/pending/<seq>-<id>.json     one job per submission (Entry fields only, never secrets)
    <queue>/claimed/<worker>/<seq>-<id>.json   a job owned by exactly one worker
    <queue>/workers/<worker>.hb  heartbeat (mtime = last sign of life) + declared lease
    <queue>/attempts/<seq>-<id>  one byte per time the job's worker was lost
    <queue>/done/<id>.json       result: submission record + similarity corpus (first write wins)
    <queue>/failed/<seq>-<id>.json   job files that could not even be read

Correctness rests on two filesystem primitives only:

* `os.rename` within one filesystem is atomic: of several workers renaming the same pending
  job into their own claim directory, exactly one succeeds. The winner owns the job.
* `os.link` fails if the target exists: the first finished result for an id wins, and a late
  result from a worker that was presumed dead can never overwrite or duplicate it.

The grading itself is the existing `grade_submission`; the final run is built by the
existing `assemble_run`. This module only moves work around.

The queue directory is a trust boundary (see README): whoever can write to it can make
workers run arbitrary repositories and can forge results.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

import yaml

from . import SCHEMA_VERSION, __version__
from .grader import grade_submission, now_iso, preflight
from .roster import Entry, safe_id
from .spec import Spec, SpecError, load_spec

QUEUE_SCHEMA = 1
MAX_ATTEMPTS = 3          # a job whose worker is lost this many times becomes an error record
_SUBDIRS = ("pending", "claimed", "done", "failed", "workers", "attempts", "spec")


class QueueError(ValueError):
    pass


# ── small helpers ─────────────────────────────────────────────────────────────

def _write_atomic(path: Path, data: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _tree_sha256(root: Path) -> str:
    """Hash of every file (relative path + bytes) under `root`: pins overlay/starter/cases files."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            h.update(p.relative_to(root).as_posix().encode() + b"\0")
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def _references_inputs(doc: dict) -> bool:
    if doc.get("overlay") or doc.get("starter"):
        return True
    for c in doc.get("criteria") or []:
        check = c.get("check") if isinstance(c, dict) else None
        if isinstance(check, dict) and check.get("cases_file"):
            return True
    return False


def _jobs(path: Path) -> list[Path]:
    try:
        return sorted(p for p in path.iterdir() if p.name.endswith(".json") and not p.name.startswith("."))
    except FileNotFoundError:
        return []


def _job_id(name: str) -> str:
    m = re.match(r"^\d+-(.+)\.json$", name)
    return m.group(1) if m else name.removesuffix(".json")


# ── the queue ─────────────────────────────────────────────────────────────────

class Queue:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        mp = self.root / "meta.json"
        if not mp.is_file():
            raise QueueError(f"{self.root} is not a markbook queue (no meta.json); create one with `markbook enqueue`")
        try:
            self.meta: dict = json.loads(mp.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise QueueError(f"{mp} is not valid JSON: {exc}") from exc
        if self.meta.get("queue_schema") != QUEUE_SCHEMA:
            raise QueueError(f"unsupported queue schema {self.meta.get('queue_schema')!r} "
                             f"(this markbook understands {QUEUE_SCHEMA})")
        for d in _SUBDIRS:
            (self.root / d).mkdir(exist_ok=True)

    # paths
    def d(self, name: str) -> Path:
        return self.root / name

    @property
    def spec_path(self) -> Path:
        return self.root / "spec" / self.meta["spec_file"]

    def load_spec(self) -> Spec:
        """Load the frozen spec, refusing if it (or anything it references) differs from enqueue time."""
        try:
            spec = load_spec(self.spec_path)
        except SpecError as exc:
            raise QueueError(f"the frozen spec in {self.root / 'spec'} no longer loads:\n{exc}") from exc
        if spec.sha256 != self.meta["spec_sha256"]:
            raise QueueError("refusing to grade: the frozen spec's hash differs from the one recorded when the "
                             "queue was created (the queue directory was modified or is corrupt)")
        want = self.meta.get("bundle_sha256")
        if want and _tree_sha256(self.root / "spec") != want:
            raise QueueError("refusing to grade: files next to the frozen spec (overlay/starter/cases) differ "
                             "from those recorded when the queue was created")
        return spec

    # counts & status
    def counts(self) -> dict:
        claimed = sum(len(_jobs(p)) for p in self.d("claimed").iterdir() if p.is_dir())
        return {"total": len(self.meta["roster_ids"]), "pending": len(_jobs(self.d("pending"))),
                "claimed": claimed, "done": len(_jobs(self.d("done"))), "failed": len(_jobs(self.d("failed")))}

    def _lease_of(self, worker: str, default: float) -> tuple[float, float | None]:
        """(declared lease seconds, age of the heartbeat in seconds or None if it is gone)."""
        hb = self.d("workers") / f"{worker}.hb"
        try:
            age = time.time() - hb.stat().st_mtime
        except FileNotFoundError:
            return default, None
        try:
            lease = float(json.loads(hb.read_text(encoding="utf-8")).get("lease_seconds", default))
        except (ValueError, OSError, AttributeError):
            lease = default
        return lease, age

    @staticmethod
    def margin(lease: float) -> float:
        """Slack added to the lease before a worker is presumed dead, to absorb clock skew between machines."""
        return min(30.0, 0.25 * lease)

    def workers(self, default_lease: float = 300.0) -> list[dict]:
        out = []
        names = {p.name for p in self.d("claimed").iterdir() if p.is_dir()}
        names |= {p.name.removesuffix(".hb") for p in self.d("workers").glob("*.hb")}
        for w in sorted(names):
            lease, age = self._lease_of(w, default_lease)
            jobs = [_job_id(p.name) for p in _jobs(self.d("claimed") / w)]
            stale = age is None or age > lease + self.margin(lease)
            out.append({"worker": w, "claimed": jobs, "heartbeat_age_s": None if age is None else round(age, 1),
                        "lease_seconds": lease, "stale": stale})
        return out

    def status(self) -> dict:
        ws = self.workers()
        c = self.counts()
        return {"run_id": self.meta["run_id"], "assignment_spec_sha256": self.meta["spec_sha256"], **c,
                "complete": c["done"] >= c["total"], "workers": ws,
                "stale_leases": [{"worker": w["worker"], "jobs": w["claimed"]} for w in ws
                                 if w["stale"] and w["claimed"]]}

    # claiming
    def claim(self, worker: str) -> tuple[Path, str] | None:
        """Atomically take one pending job. Returns (claimed path, job file name) or None."""
        mine = self.d("claimed") / worker
        mine.mkdir(exist_ok=True)
        for p in _jobs(self.d("pending")):
            target = mine / p.name
            try:
                os.rename(p, target)
            except FileNotFoundError:
                continue            # another worker won this one
            return target, p.name
        return None

    def reclaim_stale(self, default_lease: float = 300.0, *, skip: str | None = None) -> int:
        """Return jobs of presumed-dead workers to pending/. Safe to call from any worker, any time."""
        moved = 0
        for wdir in sorted(p for p in self.d("claimed").iterdir() if p.is_dir()):
            w = wdir.name
            if w == skip:
                continue
            lease, age = self._lease_of(w, default_lease)
            if age is not None and age <= lease + self.margin(lease):
                continue
            for job in _jobs(wdir):
                if self._lost(job):
                    moved += 1
                    continue
                try:
                    os.rename(job, self.d("pending") / job.name)
                    moved += 1
                except FileNotFoundError:
                    pass            # someone else reclaimed it, or the owner just finished
            try:
                wdir.rmdir()
            except OSError:
                pass
            try:
                (self.d("workers") / f"{w}.hb").unlink()
            except FileNotFoundError:
                pass
        return moved

    def _lost(self, job: Path) -> bool:
        """Count a lost attempt. After MAX_ATTEMPTS, record an error result instead of retrying forever.
        Returns True if the job was turned into a result (and the claim removed)."""
        att = self.d("attempts") / job.name
        with open(att, "ab") as f:
            f.write(b"x")
        if att.stat().st_size < MAX_ATTEMPTS:
            return False
        try:
            entry = _entry_from_file(job)
        except QueueError:
            entry = Entry(_job_id(job.name), _job_id(job.name), "", "", None)
        sub = error_record(entry, "worker_lost",
                           f"the worker grading this submission disappeared {MAX_ATTEMPTS} times "
                           "(a repository that crashes or hangs the grading machine?)")
        self.write_result(entry.id, sub, {}, worker="queue")
        try:
            os.unlink(job)
        except FileNotFoundError:
            pass
        return True

    # results
    def result_path(self, sid: str) -> Path:
        return self.d("done") / f"{sid}.json"

    def write_result(self, sid: str, sub: dict, corpus: dict, *, worker: str) -> bool:
        """Publish a result, first writer wins. Returns False if one already exists (this one is discarded)."""
        payload = {"queue_schema": QUEUE_SCHEMA, "spec_sha256": self.meta["spec_sha256"], "worker": worker,
                   "finished_at": now_iso(), "submission": sub,
                   "corpus": {f: sorted(fp) for f, fp in corpus.items()}}
        fd, tmp = tempfile.mkstemp(dir=self.d("done"), prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, self.result_path(sid))   # fails if it exists: that is the first-wins guarantee
            except FileExistsError:
                return False
            return True
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def read_result(self, sid: str) -> dict | None:
        try:
            return json.loads(self.result_path(sid).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None


def error_record(entry: Entry, code: str, message: str) -> dict:
    return {"id": entry.id, "name": entry.name, "email": entry.email, "repo": entry.repo, "ref": entry.ref,
            "commit": None, "status": "error", "criteria": [], "history": None, "late": None, "notes": [],
            "similarity": [], "duration_s": 0.0, "error": {"code": code, "message": message}}


def _entry_from_file(path: Path) -> Entry:
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
        return Entry(str(j["id"]), str(j["name"]), str(j.get("email", "")), str(j["repo"]), j.get("ref"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QueueError(f"unreadable job file {path.name}: {exc}") from exc


# ── enqueue ───────────────────────────────────────────────────────────────────

def enqueue(spec_path: str | Path, entries: list[Entry], root: str | Path, runtime: str, *,
            allow_local: bool = True, run_id: str) -> Queue:
    if not entries:
        raise QueueError("refusing to enqueue an empty roster")
    spec_path, root = Path(spec_path), Path(root)
    spec = load_spec(spec_path)       # validate first: a typo must fail now, not on every worker
    if (root / "meta.json").exists():
        raise QueueError(f"{root} already holds a queue; refusing to overwrite it (pick a new directory)")
    if root.exists() and any(root.iterdir()):
        raise QueueError(f"{root} exists and is not empty")
    root.mkdir(parents=True, exist_ok=True)
    for d in _SUBDIRS:
        (root / d).mkdir(exist_ok=True)

    raw = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    frozen = root / "spec"
    bundle = None
    if _references_inputs(raw):
        # The spec reaches for files next to it: freeze the whole source directory so workers
        # on other machines grade with exactly these overlay/starter/cases files.
        shutil.copytree(spec.source_dir, frozen, dirs_exist_ok=True, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
        try:
            load_spec(frozen / spec_path.name)
        except SpecError as exc:
            shutil.rmtree(root)
            raise QueueError("the spec references overlay/starter files outside its own directory, which "
                             f"cannot be frozen into the queue:\n{exc}") from exc
        bundle = _tree_sha256(frozen)
    else:
        shutil.copy2(spec_path, frozen / spec_path.name)

    ids = [e.id for e in entries]
    width = max(4, len(str(len(entries))))
    for seq, e in enumerate(entries):
        job = {"seq": seq, "id": e.id, "name": e.name, "email": e.email, "repo": e.repo, "ref": e.ref}
        _write_atomic(root / "pending" / f"{seq:0{width}d}-{safe_id(e.id)}.json", json.dumps(job, ensure_ascii=False))
    meta = {"queue_schema": QUEUE_SCHEMA, "run_id": run_id, "created_at": now_iso(), "runtime": runtime,
            "spec_file": spec_path.name, "spec_sha256": spec.sha256, "bundle_sha256": bundle,
            "roster_ids": ids, "report_schema_version": SCHEMA_VERSION, "markbook_version": __version__,
            "allow_local": bool(allow_local)}
    _write_atomic(root / "meta.json", json.dumps(meta, indent=2))   # written last: its presence = a complete queue
    return Queue(root)


# ── worker ────────────────────────────────────────────────────────────────────

class Worker:
    def __init__(self, queue: Queue, *, jobs: int = 1, lease_seconds: float = 300.0, idle_exit: bool = False,
                 token: str | None = None, poll: float = 1.0, log: Callable[[str], None] | None = None,
                 worker_id: str | None = None):
        self.q = queue
        self.jobs = max(1, jobs)
        self.lease = float(lease_seconds)
        self.idle_exit = idle_exit
        self.token = token
        self.poll = poll
        self.log = log or (lambda m: None)
        self.id = worker_id or safe_id(f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(2)}")
        self.stop = threading.Event()
        self.graded = 0
        self.discarded = 0
        self._hb = self.q.d("workers") / f"{self.id}.hb"

    # heartbeat
    def _beat(self) -> None:
        try:
            os.utime(self._hb, None)
        except FileNotFoundError:
            self._write_hb()

    def _write_hb(self) -> None:
        _write_atomic(self._hb, json.dumps({"worker": self.id, "host": socket.gethostname(), "pid": os.getpid(),
                                            "lease_seconds": self.lease, "started": now_iso()}))

    def _heartbeat_loop(self) -> None:
        while not self._hb_stop.wait(max(0.05, self.lease / 4)):
            try:
                self._beat()
            except OSError:
                pass

    # one job
    def _process(self, claimed: Path, name: str, spec: Spec) -> None:
        sid = _job_id(name)
        try:
            entry = _entry_from_file(claimed)
        except QueueError as exc:
            self.log(f"{name}: {exc}; moving to failed/")
            try:
                os.rename(claimed, self.q.d("failed") / name)
            except FileNotFoundError:
                pass
            return
        if self.q.result_path(entry.id).exists():      # reclaimed after the original worker finished anyway
            self.discarded += 1
            self._drop(claimed)
            return
        self.log(f"start {entry.id}")
        try:
            sub, corpus = grade_submission(spec, entry, self.q.meta["runtime"], token=self.token,
                                           allow_local=bool(self.q.meta.get("allow_local")))
        except Exception as exc:   # last-resort guard, same as grade_entries: never a lost job
            sub, corpus = error_record(entry, "internal_error", f"{type(exc).__name__}: {exc}"), {}
        won = self.q.write_result(entry.id, sub, corpus, worker=self.id)
        if won:
            self.graded += 1
        else:
            self.discarded += 1
        self.log(f"{'done' if won else 'discarded duplicate for'} {entry.id}: {sub['status']}")
        self._drop(claimed)

    @staticmethod
    def _drop(claimed: Path) -> None:
        try:
            os.unlink(claimed)
        except FileNotFoundError:
            pass            # it was reclaimed while we graded; the first-wins result write covers us

    def _loop(self, spec: Spec) -> None:
        while not self.stop.is_set():
            got = self.q.claim(self.id)
            if got:
                self._process(got[0], got[1], spec)
                continue
            if self.q.reclaim_stale(self.lease, skip=self.id):
                continue
            if self.idle_exit:
                c = self.q.counts()
                if c["pending"] == 0 and c["claimed"] == 0:
                    return
            self.stop.wait(self.poll)

    def run(self, *, install_signals: bool = True) -> int:
        spec = self.q.load_spec()
        preflight(spec, self.q.meta["runtime"], self.log)
        self._write_hb()
        self._hb_stop = threading.Event()
        hb = threading.Thread(target=self._heartbeat_loop, daemon=True)
        hb.start()
        old = {}
        if install_signals:
            def handler(signum, frame):
                self.log("signal received: finishing current job(s), claiming no more")
                self.stop.set()
            for s in (signal.SIGINT, signal.SIGTERM):
                old[s] = signal.signal(s, handler)
        threads = [threading.Thread(target=self._loop, args=(spec,), name=f"w{i}") for i in range(self.jobs)]
        try:
            for t in threads:
                t.start()
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(0.2)
        finally:
            self.stop.set()
            self._hb_stop.set()
            for s, h in old.items():
                signal.signal(s, h)
            # Anything still claimed (a thread died unexpectedly) goes back to pending right away.
            mine = self.q.d("claimed") / self.id
            for job in _jobs(mine):
                try:
                    os.rename(job, self.q.d("pending") / job.name)
                except FileNotFoundError:
                    pass
            try:
                mine.rmdir()
            except OSError:
                pass
            try:
                self._hb.unlink()
            except FileNotFoundError:
                pass
        return 0


# ── collect ───────────────────────────────────────────────────────────────────

def collect(root: str | Path, *, partial: bool = False) -> tuple[Spec, dict, list[dict], dict, list[str]]:
    """Gather results in roster order. Returns (spec, meta, submissions, corpora, missing ids).

    Without `partial`, raises QueueError unless every job has a result. Missing jobs under
    `partial` become `status: error` records with code `not_graded`."""
    q = Queue(root)
    spec = q.load_spec()
    entries: dict[str, Entry] = {}
    for sub in ("pending", "failed"):
        for p in _jobs(q.d(sub)):
            try:
                e = _entry_from_file(p)
                entries[e.id] = e
            except QueueError:
                pass
    for w in q.d("claimed").iterdir():
        for p in _jobs(w) if w.is_dir() else []:
            try:
                e = _entry_from_file(p)
                entries[e.id] = e
            except QueueError:
                pass
    subs, corpora, missing = [], {}, []
    for sid in q.meta["roster_ids"]:
        res = q.read_result(sid)
        if res is None:
            missing.append(sid)
            continue
        if res.get("spec_sha256") != q.meta["spec_sha256"]:
            raise QueueError(f"result for {sid!r} was produced with a different spec (hash mismatch); "
                             "refusing to mix rubrics. Delete that file from done/ to have it re-graded.")
        if res["submission"]["id"] != sid:
            raise QueueError(f"result file for {sid!r} contains a submission for {res['submission']['id']!r}")
    if missing and not partial:
        c = q.counts()
        raise QueueError(f"{len(missing)} of {c['total']} job(s) are not done yet "
                         f"(pending {c['pending']}, claimed {c['claimed']}, failed {c['failed']}); "
                         "wait for the workers, or use --partial to collect what exists "
                         f"(first missing: {', '.join(missing[:5])})")
    for sid in q.meta["roster_ids"]:
        res = q.read_result(sid)
        if res is None:
            e = entries.get(sid) or Entry(sid, sid, "", "", None)
            subs.append(error_record(e, "not_graded", "this submission had not been graded when the results "
                                                      "were collected (--partial); run more workers and collect again"))
            continue
        subs.append(res["submission"])
        if res["corpus"]:
            corpora[sid] = {f: set(fp) for f, fp in res["corpus"].items()}
    return spec, q.meta, subs, corpora, missing
