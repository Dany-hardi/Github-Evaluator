"""Re-grade only what failed for infrastructure reasons.

Fetch failures (private repo, network blip, bad ref fixed since) and criteria that
errored (sandbox fault) are the review items automation can resolve by itself once the
cause is fixed. Everything that graded cleanly is left exactly as it was, and
similarity is recomputed over the whole cohort from the stored fingerprints.
"""
from __future__ import annotations

from pathlib import Path

from .grader import assemble_run, grade_entries, preflight
from .roster import Entry
from .spec import load_spec
from .store import StoreError, load_corpora, load_run


def failed_ids(run: dict) -> list[str]:
    return [s["id"] for s in run["submissions"]
            if s["status"] == "error" or any(c["status"] == "error" for c in s["criteria"])]


def retry_failed(run_dir: Path, runtime: str, *, jobs: int = 4, token: str | None = None,
                 progress=None, log=None, reviewer=None) -> tuple[dict, list[str]]:
    run = load_run(run_dir)
    ids = failed_ids(run)
    if not ids:
        return run, []
    inputs = run.get("inputs") or {}
    spec_path = inputs.get("spec_path")
    if not spec_path or not Path(spec_path).is_file():
        raise StoreError(f"cannot retry: the spec this run used is no longer at {spec_path!r}")
    spec = load_spec(spec_path)
    if spec.sha256 != run["assignment"]["spec_sha256"]:
        raise StoreError("cannot retry: the spec has changed since this run (hash differs). "
                         "Re-grading part of a cohort against a different rubric would make grades incomparable; "
                         "run the whole cohort again instead.")
    spec = preflight(spec, runtime, log)

    targets = {s["id"]: s for s in run["submissions"] if s["id"] in ids}
    entries = [Entry(t["id"], t["name"], t["email"], t["repo"], t["ref"]) for t in targets.values()]
    new_subs, new_corpora = grade_entries(spec, entries, runtime, jobs=jobs, token=token,
                                          allow_local=bool(inputs.get("allow_local")), progress=progress,
                                          reviewer=reviewer)
    fresh = {s["id"]: s for s in new_subs}
    subs = [fresh.get(s["id"], s) for s in run["submissions"]]
    corpora = load_corpora(run_dir)
    for sid in ids:
        corpora.pop(sid, None)
    corpora.update(new_corpora)
    out = assemble_run(spec, runtime, subs, corpora, run_id=run["run_id"], created=run["created_at"],
                       inputs=inputs)
    return out, ids
