"""Pinning: record, per student, the exact commit that existed at the deadline.

Commit timestamps are author-controlled, and a student can keep pushing after
the deadline. The defence is to record the commit SHA once, at a moment the
student cannot influence, and then grade *that* ref (`grade` already checks out
the roster's `ref` column and records the SHA).

Two capture modes:

* ``now``      ask the remote for each repository's current tip (`git ls-remote`,
               no clone). Run it right when the deadline passes. The result
               reflects the remote's state at capture time, so commit-timestamp
               tricks cannot backdate it.
* ``deadline`` retroactive: clone and take the last commit whose *committer date*
               is <= the deadline. This still trusts commit timestamps, which a
               student can set freely, so it is a best-effort fallback, not
               evidence.

Optionally a `git bundle` of each pinned commit is stored so the evidence
survives a later force-push or repository deletion.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .fetch import FetchError, _classify, _git_env, normalize_repo
from .roster import _ALIASES, Entry, RosterError, load_roster, safe_id

MODES = ("now", "deadline")
MANIFEST_VERSION = 1
_FULL_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.IGNORECASE)
_SHORT_SHA = re.compile(r"^[0-9a-f]{4,39}$", re.IGNORECASE)


@dataclass
class Pin:
    id: str
    repo: str
    mode: str
    sha: str = ""
    ref_requested: str | None = None
    captured_at: str = ""
    verified: bool = False       # SHA seen as an advertised tip, or present in a clone
    error: str | None = None     # a fetch._classify code, or no_commit_before_deadline / bundle_failed
    message: str = ""
    bundle: str | None = None
    via: str = ""                # ls-remote | clone | given

    def as_dict(self) -> dict:
        return {"repo": self.repo, "sha": self.sha or None, "ref_requested": self.ref_requested,
                "mode": self.mode, "via": self.via, "captured_at": self.captured_at,
                "verified": self.verified, "bundle": self.bundle,
                "error": self.error, "message": self.message or None}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Git:
    """Runs git for one repository URL with the same hardening as fetch.checkout."""

    def __init__(self, repo: str, token: str | None):
        self.url, self.url_ref, _subdir = normalize_repo(repo)
        is_local = os.path.exists(self.url) or self.url.startswith("file://")
        if not is_local and not self.url.startswith(("https://", "http://")):
            raise FetchError("invalid_url", f"Unsupported repository location: {repo!r}")
        if self.url.startswith("-"):
            raise FetchError("invalid_url", "Repository and ref must not start with '-'.")
        self.env = _git_env(token, self.url)
        self.base = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never",
                     "-c", "protocol.file.allow=" + ("always" if is_local else "never")]

    def run(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([*self.base, *args], capture_output=True, text=True,
                                  timeout=timeout, env=self.env)
        except subprocess.TimeoutExpired as exc:
            raise FetchError("timeout", f"git timed out after {exc.timeout}s") from exc

    def ls_remote(self) -> dict[str, str]:
        r = self.run("ls-remote", "--", self.url)
        if r.returncode != 0:
            raise FetchError(*_classify(r.stderr))
        tips: dict[str, str] = {}
        for line in r.stdout.splitlines():
            sha, _, name = line.partition("\t")
            if sha and name:
                tips[name] = sha
        return tips

    def clone(self, dest: Path, full: bool) -> None:
        """Bare clone. Blobless unless a bundle is wanted (a bundle needs the objects)."""
        flt = [] if full else ["--filter=blob:none"]
        r = self.run("clone", "--quiet", "--bare", *flt, "--", self.url, str(dest), timeout=300)
        if r.returncode != 0:
            raise FetchError(*_classify(r.stderr))

    def in_clone(self, dest: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return self.run("-C", str(dest), *args, timeout=timeout)


def _pick_ref(tips: dict[str, str], ref: str | None) -> str:
    if not ref:
        if "HEAD" in tips:
            return tips["HEAD"]
        if not tips:
            raise FetchError("empty_repository", "The repository has no commits.")
        raise FetchError("ref_not_found", "The remote advertises no default branch (HEAD).")
    for name in (f"refs/heads/{ref}", f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", ref):
        if name in tips:
            return tips[name]
    raise FetchError("ref_not_found", f"The branch, tag or commit {ref!r} does not exist in the repository.")


def _make_bundle(g: _Git, clone: Path, sha: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if g.in_clone(clone, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        raise FetchError("ref_not_found", f"Commit {sha} is not present in a clone of the repository.")
    # A bundle needs a ref; a bare SHA would be refused as an empty bundle.
    if g.in_clone(clone, "update-ref", "refs/markbook/pin", sha).returncode != 0:
        raise FetchError("clone_failed", "Cannot reference the pinned commit.")
    tmp = path.with_name(path.name + ".part")
    r = g.in_clone(clone, "bundle", "create", "--quiet", str(tmp), "refs/markbook/pin", timeout=300)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise FetchError("clone_failed", "git bundle create failed: "
                         + (r.stderr.strip().splitlines() or ["?"])[-1][:200])
    heads = g.in_clone(clone, "bundle", "list-heads", str(tmp))
    if g.in_clone(clone, "bundle", "verify", str(tmp)).returncode != 0 or sha not in heads.stdout:
        tmp.unlink(missing_ok=True)
        raise FetchError("clone_failed", "The bundle does not contain the pinned commit.")
    os.replace(tmp, path)


def pin_one(entry: Entry, *, mode: str, deadline: datetime | None, token: str | None,
            bundle_dir: Path | None) -> Pin:
    pin = Pin(id=entry.id, repo=entry.repo, mode=mode, ref_requested=entry.ref)
    try:
        g = _Git(entry.repo, token)
        ref = entry.ref or g.url_ref
        pin.ref_requested = ref
        if ref and ref.startswith("-"):
            raise FetchError("invalid_url", "Repository and ref must not start with '-'.")
        with tempfile.TemporaryDirectory(prefix="markbook-pin-") as tmp:
            clone = Path(tmp) / "repo"
            cloned = False

            def ensure_clone() -> None:
                nonlocal cloned
                if not cloned:
                    g.clone(clone, full=bundle_dir is not None)
                    cloned = True

            def missing_ref() -> FetchError:
                if not ref:
                    return FetchError("empty_repository", "The repository has no commits.")
                return FetchError("ref_not_found", f"The branch, tag or commit {ref!r} "
                                                   "does not exist in the repository.")

            def clone_resolve(by_deadline: bool) -> str:
                ensure_clone()
                r = g.in_clone(clone, "rev-parse", "--verify", "--quiet", f"{ref or 'HEAD'}^{{commit}}")
                if r.returncode != 0:
                    raise missing_ref()
                tip = r.stdout.strip()
                if not by_deadline:
                    return tip
                assert deadline is not None
                r = g.in_clone(clone, "rev-list", "-1", f"--before=@{int(deadline.timestamp())}", tip)
                if r.returncode != 0 or not r.stdout.strip():
                    raise FetchError("no_commit_before_deadline",
                                     "The repository has no commit dated on or before the deadline.")
                return r.stdout.strip()

            pin.captured_at = _now()
            if ref and _FULL_SHA.match(ref):
                # Already a commit id: keep as is; ls-remote still proves the repo is reachable.
                pin.sha, pin.via = ref.lower(), "given"
                pin.verified = pin.sha in g.ls_remote().values()
            elif mode == "now":
                tips = g.ls_remote()
                try:
                    pin.sha, pin.via = _pick_ref(tips, ref), "ls-remote"
                except FetchError as exc:
                    if not (exc.code == "ref_not_found" and ref and _SHORT_SHA.match(ref)):
                        raise
                    pin.sha, pin.via = clone_resolve(False), "clone"   # abbreviated SHA
                pin.verified = True
            else:
                pin.sha, pin.via, pin.verified = clone_resolve(True), "clone", True
            if bundle_dir is not None:
                path = bundle_dir / f"{safe_id(entry.id)}-{pin.sha[:12]}.bundle"
                try:
                    ensure_clone()
                    _make_bundle(g, clone, pin.sha, path)
                except FetchError as exc:
                    # The pin itself stays valid; only the evidence copy is missing.
                    pin.error, pin.message, pin.verified = "bundle_failed", f"{exc.code}: {exc.message}", False
                else:
                    pin.bundle, pin.verified = path.name, True
    except FetchError as exc:
        pin.sha, pin.verified, pin.error, pin.message = "", False, exc.code, exc.message
    except OSError as exc:
        pin.sha, pin.verified, pin.error = "", False, "clone_failed"
        pin.message = f"{type(exc).__name__}: {exc}"
    return pin


@dataclass
class PinResult:
    pins: list[Pin]
    out_csv: Path
    manifest: Path
    failed: list[Pin] = field(default_factory=list)


def _roster_rows(text: str) -> tuple[list[str], list[dict]]:
    """Original header, and the rows that load_roster turns into entries (same order)."""
    reader = csv.DictReader(text.splitlines())
    fields = list(reader.fieldnames or [])
    cols = {h.strip().lower().replace(" ", "_"): h for h in fields if h}
    repo_cols = [cols[a] for a in _ALIASES["repo"] if a in cols]
    return fields, [r for r in reader if any((r.get(c) or "").strip() for c in repo_cols)]


def _find_col(header: list[str], name: str) -> str:
    for h in header:
        if h and h.strip().lower() == name:
            return h
    header.append(name)
    return name


def pin_roster(roster_path: str | Path, out_csv: str | Path, *, mode: str = "now",
               deadline: datetime | None = None, token: str | None = None,
               bundle_dir: str | Path | None = None, jobs: int = 4,
               spec_name: str | None = None, progress=None) -> PinResult:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mode == "deadline" and deadline is None:
        raise RosterError("--at deadline needs a `deadline` in the spec (none is set)")
    roster_path, out_csv = Path(roster_path), Path(out_csv)
    if roster_path.resolve() == out_csv.resolve():
        raise RosterError("--out must differ from the input roster (the original is never overwritten)")
    entries = load_roster(roster_path)
    fields, rows = _roster_rows(roster_path.read_text(encoding="utf-8-sig"))
    if len(rows) != len(entries):  # pragma: no cover - both use the same alias logic
        raise RosterError("could not align roster rows with entries")
    bdir = Path(bundle_dir) if bundle_dir else None
    if bdir:
        bdir.mkdir(parents=True, exist_ok=True)

    def work(e: Entry) -> Pin:
        p = pin_one(e, mode=mode, deadline=deadline, token=token, bundle_dir=bdir)
        if progress:
            progress(p)
        return p

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        pins = list(pool.map(work, entries))

    # Output CSV: every original column, plus `ref` and `pinned_at`.
    header = list(fields)
    ref_col, at_col = _find_col(header, "ref"), _find_col(header, "pinned_at")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for row, p in zip(rows, pins):
        row = {k: v for k, v in row.items() if k is not None}
        if p.sha:
            row[ref_col], row[at_col] = p.sha, p.captured_at
        else:
            # Unresolved: any original ref is left untouched; an empty pinned_at marks it as not pinned.
            row[at_col] = ""
            row.setdefault(ref_col, "")
        w.writerow(row)
    manifest = out_csv.with_name("pins.json")
    doc = {"markbook_pin": MANIFEST_VERSION, "spec": spec_name, "mode": mode,
           "deadline": deadline.isoformat() if deadline else None,
           "generated_at": _now(), "roster": str(roster_path), "pinned_roster": str(out_csv),
           "pins": {p.id: p.as_dict() for p in pins}}
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    for path, data in ((out_csv, buf.getvalue()),
                       (manifest, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")):
        tmp = path.with_name(path.name + ".part")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)
    return PinResult(pins, out_csv, manifest, [p for p in pins if p.error])
