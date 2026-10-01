"""Assignment spec: the rubric as code.

A spec is a YAML file. It is loaded and validated eagerly so that a typo fails
at `markbook validate`, not halfway through grading forty repositories.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

CHECK_TYPES = {"file_exists", "command", "cases", "junit", "readme", "git", "manual"}

# Check types that execute student code and therefore need the sandbox.
CODE_CHECKS = ("command", "cases", "junit")

# Required keys per check type (beyond `type`).
_REQUIRED: dict[str, tuple[str, ...]] = {
    "file_exists": ("paths",),
    "command": ("run",),
    "cases": ("run",),
    "junit": ("run",),
    "readme": (),
    "git": (),
    "manual": (),
}
_ALLOWED: dict[str, set[str]] = {
    "file_exists": {"paths"},
    "command": {"run", "expect_exit", "stdout_contains", "stdin", "timeout"},
    "cases": {"run", "cases", "cases_file", "compare", "timeout"},
    "junit": {"run", "report", "min_tests", "hidden", "timeout"},
    "readme": {"sections", "min_words", "forbid"},
    "git": {"min_commits", "min_active_days", "max_single_commit_share", "min_authors"},
    "manual": {"guidance", "ai"},
}


class SpecError(ValueError):
    """Raised with a message listing every problem found in the spec."""


@dataclass(frozen=True)
class SandboxSpec:
    image: str | None = None
    timeout: int = 10          # default per-command timeout, seconds
    memory: str = "256m"
    cpus: float = 1.0
    pids: int = 128
    disk: str = "64m"          # size of the writable /work tmpfs


@dataclass(frozen=True)
class PrepareSpec:
    """Cohort-level dependency step: runs ONCE, with network, using only teacher-authored commands and files."""
    run: tuple[str, ...]
    files: tuple[str, ...] = ()      # relative to the spec directory
    timeout: int = 600


@dataclass(frozen=True)
class LatePolicy:
    percent_per_day: float = 0.0
    max_percent: float = 100.0
    grace_minutes: int = 0


@dataclass(frozen=True)
class SimilaritySpec:
    enabled: bool = True
    threshold: float = 0.6
    min_fingerprints: int = 20


@dataclass(frozen=True)
class Criterion:
    id: str
    title: str
    points: float
    check: dict[str, Any]
    requires: tuple[str, ...] = ()

    @property
    def type(self) -> str:
        return self.check["type"]

    @property
    def manual(self) -> bool:
        return self.type == "manual"


@dataclass(frozen=True)
class Spec:
    name: str
    version: int
    scale: float
    pass_mark: float | None
    deadline: datetime | None
    late: LatePolicy
    sandbox: SandboxSpec
    similarity: SimilaritySpec
    criteria: tuple[Criterion, ...]
    overlay: Path | None
    starter: Path | None
    source_dir: Path
    sha256: str
    borderline_margin: float = 0.0
    prepare: PrepareSpec | None = None
    prepared: dict | None = None     # filled in by prepare.ensure_prepared: the image actually used

    @property
    def max_points(self) -> float:
        return sum(c.points for c in self.criteria)

    @property
    def needs_sandbox(self) -> bool:
        return any(c.type in CODE_CHECKS for c in self.criteria)


def _parse_deadline(value: Any, errors: list[str]) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"deadline: {value!r} is not an ISO-8601 timestamp")
            return None
    if dt.tzinfo is None:
        errors.append("deadline: must include a timezone (e.g. 2026-03-01T23:59:00+00:00 or ...Z)")
        return None
    return dt.astimezone(timezone.utc)


def _load_cases_file(base: Path, rel: str, errors: list[str], where: str) -> list[dict]:
    p = (base / rel).resolve()
    if not p.is_relative_to(base.resolve()):
        errors.append(f"{where}: cases_file must stay inside the spec directory")
        return []
    if not p.is_file():
        errors.append(f"{where}: cases_file {rel!r} not found")
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("cases")
    if not isinstance(data, list):
        errors.append(f"{where}: cases_file must contain a list of cases")
        return []
    return data


_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@+-]*$")
MAX_PREPARE_FILE = 1 << 20


def _parse_prepare(raw, base: Path, errors: list[str], confine: bool) -> PrepareSpec | None:
    """`prepare:` runs with network access, so it is only accepted from a trusted spec."""
    if confine:
        errors.append("prepare: not allowed in uploaded specs, because it runs commands with network access; "
                      "put the spec in a saved-specs directory (`serve --specs DIR`) or use the CLI")
        return None
    if not isinstance(raw, dict):
        errors.append("prepare: must be a mapping with `run` (and optionally `files`, `timeout`)")
        return None
    bad = set(raw) - {"run", "files", "timeout"}
    if bad:
        errors.append(f"prepare: unknown option(s) {sorted(bad)}")
    run = raw.get("run")
    run = [run] if isinstance(run, str) else run
    if not isinstance(run, list) or not run or not all(isinstance(c, str) and c.strip() for c in run):
        errors.append("prepare.run: a command or a non-empty list of commands is required")
        run = []
    files = raw.get("files") or []
    if not isinstance(files, list):
        errors.append("prepare.files: must be a list of paths next to the spec")
        files = []
    ok_files = []
    for f in files:
        p = (base / str(f))
        rel = Path(str(f))
        if rel.is_absolute() or ".." in rel.parts or not str(f).strip():
            errors.append(f"prepare.files: {f!r} must be a relative path inside the spec directory")
        elif p.is_symlink() or not p.is_file():
            errors.append(f"prepare.files: {f!r} is not a regular file next to the spec")
        elif p.stat().st_size > MAX_PREPARE_FILE:
            errors.append(f"prepare.files: {f!r} is larger than {MAX_PREPARE_FILE // 1024} KiB")
        else:
            ok_files.append(rel.as_posix())
    timeout = raw.get("timeout", 600)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        errors.append("prepare.timeout: must be a positive number of seconds")
        timeout = 600
    return PrepareSpec(tuple(run), tuple(ok_files), timeout) if run else None


def load_spec(path: str | Path, *, confine: bool = False) -> Spec:
    """Load and validate a spec. With confine=True (untrusted uploads), `overlay` and
    `starter` must live inside the spec's own directory."""
    path = Path(path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"cannot read spec: {exc}") from exc
    try:
        doc = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise SpecError(f"invalid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise SpecError("spec must be a YAML mapping")

    base = path.parent
    errors: list[str] = []

    name = str(doc.get("name") or "").strip()
    if not name:
        errors.append("name: required")

    scale = doc.get("scale", 100)
    if not isinstance(scale, (int, float)) or scale <= 0:
        errors.append("scale: must be a positive number")
        scale = 100

    pass_mark = doc.get("pass_mark")
    if pass_mark is not None and (not isinstance(pass_mark, (int, float)) or not 0 <= pass_mark <= scale):
        errors.append("pass_mark: must be a number between 0 and scale")
        pass_mark = None

    deadline = _parse_deadline(doc.get("deadline"), errors)

    late_raw = doc.get("late_penalty") or {}
    late = LatePolicy(
        percent_per_day=float(late_raw.get("percent_per_day", 0)),
        max_percent=float(late_raw.get("max_percent", 100)),
        grace_minutes=int(late_raw.get("grace_minutes", 0)),
    )
    if late.percent_per_day and deadline is None:
        errors.append("late_penalty: requires a deadline")

    sb_raw = doc.get("sandbox") or {}
    sandbox = SandboxSpec(
        image=sb_raw.get("image"),
        timeout=int(sb_raw.get("timeout", 10)),
        memory=str(sb_raw.get("memory", "256m")),
        cpus=float(sb_raw.get("cpus", 1.0)),
        pids=int(sb_raw.get("pids", 128)),
        disk=str(sb_raw.get("disk", "64m")),
    )

    if sandbox.image is not None and not _IMAGE_RE.match(str(sandbox.image)):
        errors.append(f"sandbox.image: {sandbox.image!r} is not a valid image reference")

    prepare = None
    if doc.get("prepare") is not None:
        prepare = _parse_prepare(doc["prepare"], base, errors, confine)

    sim_raw = doc.get("similarity") or {}
    similarity = SimilaritySpec(
        enabled=bool(sim_raw.get("enabled", True)),
        threshold=float(sim_raw.get("threshold", 0.6)),
        min_fingerprints=int(sim_raw.get("min_fingerprints", 20)),
    )

    def _dir(key: str) -> Path | None:
        v = doc.get(key)
        if v is None:
            return None
        p = (base / str(v)).resolve()
        if confine and not p.is_relative_to(base.resolve()):
            errors.append(f"{key}: must stay inside the spec directory")
            return None
        if not p.is_dir():
            errors.append(f"{key}: directory {v!r} not found next to the spec")
            return None
        return p

    overlay, starter = _dir("overlay"), _dir("starter")

    criteria: list[Criterion] = []
    seen: set[str] = set()
    crit_raw = doc.get("criteria")
    if not isinstance(crit_raw, list) or not crit_raw:
        errors.append("criteria: at least one criterion is required")
        crit_raw = []

    for i, c in enumerate(crit_raw):
        where = f"criteria[{i}]"
        if not isinstance(c, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        cid = str(c.get("id") or "").strip()
        where = f"criteria[{cid or i}]"
        if not cid or not all(ch.isalnum() or ch in "_-" for ch in cid):
            errors.append(f"{where}: id is required and may only use letters, digits, '_' and '-'")
            continue
        if cid in seen:
            errors.append(f"{where}: duplicate id")
            continue
        points = c.get("points")
        if not isinstance(points, (int, float)) or isinstance(points, bool) or points <= 0:
            errors.append(f"{where}: points must be a positive number")
            continue
        check = c.get("check")
        if not isinstance(check, dict) or check.get("type") not in CHECK_TYPES:
            errors.append(f"{where}: check.type must be one of {sorted(CHECK_TYPES)}")
            continue
        check = dict(check)
        ctype = check["type"]
        for key in _REQUIRED[ctype]:
            if key not in check:
                errors.append(f"{where}: check.{key} is required for type '{ctype}'")
        extra = set(check) - _ALLOWED[ctype] - {"type"}
        if extra:
            errors.append(f"{where}: unknown check option(s) for '{ctype}': {sorted(extra)}")
        if ctype == "cases":
            cases = list(check.get("cases") or [])
            if check.get("cases_file"):
                cases += _load_cases_file(base, check["cases_file"], errors, where)
            if not cases:
                errors.append(f"{where}: 'cases' needs at least one case (inline or cases_file)")
            for j, case in enumerate(cases):
                if not isinstance(case, dict) or not ({"stdout", "stdout_regex", "exit"} & set(case)):
                    errors.append(f"{where}.cases[{j}]: needs stdout, stdout_regex or exit")
            check["cases"] = cases
            check.pop("cases_file", None)
            if check.get("compare", "trim") not in ("trim", "exact", "tokens"):
                errors.append(f"{where}: compare must be trim, exact or tokens")
        if ctype == "junit":
            from .sandbox import safe_relative
            if safe_relative(str(check.get("report", "report.xml"))) is None:
                errors.append(f"{where}: report must be a relative path inside the project (no '..')")
            mt = check.get("min_tests", 1)
            if not isinstance(mt, int) or isinstance(mt, bool) or mt < 0:
                errors.append(f"{where}: min_tests must be a non-negative integer")
        if ctype == "file_exists" and not isinstance(check.get("paths"), list):
            errors.append(f"{where}: paths must be a list of globs")
        requires = tuple(c.get("requires") or ())
        for r in requires:
            if r not in seen:
                errors.append(f"{where}: requires {r!r}, which must be defined earlier")
        seen.add(cid)
        criteria.append(Criterion(cid, str(c.get("title") or cid), float(points), check, requires))

    if any(c.type in CODE_CHECKS for c in criteria) and not sandbox.image:
        errors.append("sandbox.image: required when any criterion runs code (e.g. python:3.12-slim)")

    if prepare is not None and not any(c.type in CODE_CHECKS for c in criteria):
        errors.append("prepare: has no effect because no criterion runs code (command, cases or junit)")

    if errors:
        raise SpecError("\n".join(f"  - {e}" for e in errors))

    max_points = sum(c.points for c in criteria)
    return Spec(
        name=name,
        version=int(doc.get("version", 1)),
        scale=float(scale),
        pass_mark=float(pass_mark) if pass_mark is not None else None,
        deadline=deadline,
        late=late,
        sandbox=sandbox,
        similarity=similarity,
        criteria=tuple(criteria),
        overlay=overlay,
        starter=starter,
        source_dir=base.resolve(),
        sha256=hashlib.sha256(raw_text.encode()).hexdigest(),
        borderline_margin=round(float(doc.get("borderline_margin", 0.02 * scale)), 4)
        if pass_mark is not None else 0.0,
        prepare=prepare,
    )
