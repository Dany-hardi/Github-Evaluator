"""Check implementations.

Each check takes its options and a Context and returns an Outcome:
  fraction  0.0-1.0 of the criterion's points earned
  status    passed | partial | failed | error | manual
  evidence  what the grader saw (shown to the student and the reviewer)
  review    a reason a human should look, or None

Checks never raise for ordinary failures (a failing build is a result, not an
exception). An unexpected exception becomes status "error" in the grader and
is always routed to review: infrastructure faults must never silently cost a
student marks.
"""
from __future__ import annotations

import fnmatch
import re
import shlex
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .gitinfo import History
from .sandbox import ExecResult, Sandbox, safe_relative

MAX_EVIDENCE_CHARS = 600


@dataclass
class Evidence:
    label: str
    text: str = ""

    def to_dict(self) -> dict:
        return {"label": self.label, "text": clip(self.text)}


@dataclass
class Outcome:
    fraction: float
    status: str
    evidence: list[Evidence] = field(default_factory=list)
    review: str | None = None


@dataclass
class Context:
    workdir: Path               # pristine copy of the student's tree (no .git)
    sandbox: Sandbox | None
    history: History
    default_timeout: int = 10


def clip(text: str, n: int = MAX_EVIDENCE_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n] + f"… [{len(text) - n} more chars]"


def _status(fraction: float) -> str:
    return "passed" if fraction >= 1 else "failed" if fraction <= 0 else "partial"


def _inside(root: Path, p: Path) -> bool:
    """True if `p` resolves to somewhere inside `root` (blocks symlink escapes)."""
    try:
        return p.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _describe(r: ExecResult) -> list[Evidence]:
    ev = []
    if r.timed_out:
        ev.append(Evidence("timeout", f"killed after {r.duration:.1f}s"))
    elif r.exit_code is not None:
        ev.append(Evidence("exit_code", str(r.exit_code)))
    if r.stdout.strip():
        ev.append(Evidence("stdout", r.stdout))
    if r.stderr.strip():
        ev.append(Evidence("stderr", r.stderr))
    if r.truncated:
        ev.append(Evidence("note", "output truncated"))
    return ev


# ── file_exists ───────────────────────────────────────────────────────────────

def file_exists(opts: dict, ctx: Context) -> Outcome:
    files = [p for p in ctx.workdir.rglob("*") if p.is_file() and _inside(ctx.workdir, p)]
    rels = [p.relative_to(ctx.workdir).as_posix() for p in files]
    found, missing = [], []
    for pattern in opts["paths"]:
        pat = str(pattern)
        hit = [r for r in rels if fnmatch.fnmatch(r, pat) or fnmatch.fnmatch(Path(r).name, pat)]
        (found if hit else missing).append(pat)
    total = len(opts["paths"])
    frac = len(found) / total if total else 1.0
    ev = [Evidence("found", ", ".join(found) or "none")]
    if missing:
        ev.append(Evidence("missing", ", ".join(missing)))
    return Outcome(frac, _status(frac), ev)


# ── command ───────────────────────────────────────────────────────────────────

def command(opts: dict, ctx: Context) -> Outcome:
    assert ctx.sandbox is not None
    r = ctx.sandbox.exec(opts["run"], stdin=opts.get("stdin"), timeout=opts.get("timeout") or ctx.default_timeout)
    want = int(opts.get("expect_exit", 0))
    problems = []
    if r.timed_out:
        problems.append("timed out")
    elif r.exit_code != want:
        problems.append(f"exit code {r.exit_code}, expected {want}")
    needle = opts.get("stdout_contains")
    if needle and needle not in r.stdout:
        problems.append(f"stdout does not contain {needle!r}")
    ev = [Evidence("command", opts["run"])] + _describe(r)
    if problems:
        return Outcome(0.0, "failed", [Evidence("problem", "; ".join(problems))] + ev)
    return Outcome(1.0, "passed", ev)


# ── cases ─────────────────────────────────────────────────────────────────────

def _norm(text: str, mode: str) -> str:
    if mode == "exact":
        return text
    if mode == "tokens":
        return " ".join(text.split())
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _first_diff(want: str, got: str) -> str:
    wl, gl = want.splitlines() or [""], got.splitlines() or [""]
    for i in range(max(len(wl), len(gl))):
        w = wl[i] if i < len(wl) else "<missing>"
        g = gl[i] if i < len(gl) else "<missing>"
        if w != g:
            return f"line {i + 1}: expected {w!r}, got {g!r}"
    return "outputs differ in whitespace"


def cases(opts: dict, ctx: Context) -> Outcome:
    assert ctx.sandbox is not None
    mode = opts.get("compare", "trim")
    timeout = opts.get("timeout") or ctx.default_timeout
    passed, ev = 0, []
    all_cases = opts["cases"]
    for i, case in enumerate(all_cases, 1):
        name = str(case.get("name") or f"case {i}")
        hidden = bool(case.get("hidden"))
        cmd = opts["run"] + (" " + case["args"] if case.get("args") else "")
        r = ctx.sandbox.exec(cmd, stdin=case.get("stdin"), timeout=case.get("timeout") or timeout)
        reasons = []
        if r.timed_out:
            reasons.append(f"timed out after {r.duration:.1f}s")
        else:
            # A crash must never pass: unless the case says otherwise, exit 0 is required.
            want_exit = int(case.get("exit", 0))
            if r.exit_code != want_exit:
                reasons.append(f"exit code {r.exit_code}, expected {want_exit}")
            if "stdout" in case and _norm(r.stdout, mode) != _norm(str(case["stdout"]), mode):
                reasons.append("output differs" if hidden else _first_diff(
                    _norm(str(case["stdout"]), mode), _norm(r.stdout, mode)))
            if "stdout_regex" in case and not re.search(str(case["stdout_regex"]), r.stdout, re.MULTILINE):
                reasons.append("output does not match the expected pattern")
        if reasons:
            detail = "; ".join(reasons)
            if r.stderr.strip() and not hidden:
                detail += f" | stderr: {clip(r.stderr, 200)}"
            ev.append(Evidence(f"FAIL {name}" + (" (hidden)" if hidden else ""), detail))
        else:
            passed += 1
    frac = passed / len(all_cases)
    ev.insert(0, Evidence("summary", f"{passed}/{len(all_cases)} cases passed"))
    return Outcome(frac, _status(frac), ev)


# ── junit ─────────────────────────────────────────────────────────────────────

MAX_REPORT_BYTES = 1 << 20
MAX_FAILURES_SHOWN = 10


def parse_junit(text: str) -> tuple[list[dict], str | None]:
    """Parse JUnit XML into [{name, status: passed|failed|skipped, message}], or ([], error).

    The report is influenced by student code, so it is parsed defensively: no DOCTYPE or entity
    declarations (entity-expansion bombs) and a hard size limit applied before we get here.
    """
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.IGNORECASE):
        return [], "the report declares a DOCTYPE/ENTITY, which is not valid JUnit output; refusing to parse it"
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [], f"the report is not valid XML ({exc})"
    cases = []
    for tc in root.iter("testcase"):
        cls, name = tc.get("classname") or "", tc.get("name") or "?"
        status, message = "passed", ""
        for child in tc:
            if child.tag in ("failure", "error"):
                status, message = "failed", (child.get("message") or child.text or "").strip()
            elif child.tag == "skipped" and status == "passed":
                status = "skipped"
        cases.append({"name": f"{cls}::{name}" if cls else name, "status": status, "message": message})
    return cases, None


def junit(opts: dict, ctx: Context) -> Outcome:
    """Run a test command that writes a JUnit XML report and award points per passing test.

    Works with pytest (`--junitxml`), Maven/Gradle surefire, ctest, Go (`gotestsum`), cargo-nextest...
    Partial credit is passed / (passed + failed); skipped tests are reported but not counted.
    A run that finds fewer than `min_tests` tests (default 1) FAILS: "no tests ran" must never
    look like a clean pass, the same rule as "a crash can never pass".
    """
    assert ctx.sandbox is not None
    report = str(opts.get("report", "report.xml"))
    rel = safe_relative(report)
    if rel is None:
        return Outcome(0.0, "error", [Evidence("problem", f"unsafe report path {report!r}")],
                       review="invalid report path in the spec")
    # Remove any report that shipped in the repository: a student could commit a forged all-pass file
    # and rely on the test command crashing before it writes a real one.
    ctx.sandbox.exec(f"rm -f -- {shlex.quote(rel)}", timeout=15)
    r = ctx.sandbox.exec(opts["run"], timeout=opts.get("timeout") or ctx.default_timeout)
    ev = [Evidence("command", opts["run"])]
    if r.timed_out:
        return Outcome(0.0, "failed", [Evidence("problem", "timed out")] + ev + _describe(r))
    text, err = ctx.sandbox.read_file(rel, MAX_REPORT_BYTES)
    if err:
        return Outcome(0.0, "failed", [Evidence("problem", f"no usable test report: {err}")] + ev + _describe(r))
    cases, err = parse_junit(text)
    if err:
        return Outcome(0.0, "failed", [Evidence("problem", err)] + ev)

    counted = [c for c in cases if c["status"] != "skipped"]
    skipped = len(cases) - len(counted)
    passed = sum(1 for c in counted if c["status"] == "passed")
    need = int(opts.get("min_tests", 1))
    if len(counted) < need:
        return Outcome(0.0, "failed", [Evidence(
            "problem", f"found {len(counted)} test(s), expected at least {need}: no tests ran, so nothing is earned")]
            + ev + _describe(r))
    frac = passed / len(counted) if counted else 1.0
    out = [Evidence("summary", f"{passed}/{len(counted)} tests passed" + (f" ({skipped} skipped)" if skipped else ""))]
    hidden = bool(opts.get("hidden"))
    failed = [c for c in counted if c["status"] == "failed"]
    for i, c in enumerate(failed[:MAX_FAILURES_SHOWN], 1):
        out.append(Evidence(f"FAIL (hidden) test {i}", "failed") if hidden
                   else Evidence(f"FAIL {c['name']}", clip(c["message"], 200)))
    if len(failed) > MAX_FAILURES_SHOWN:
        out.append(Evidence("note", f"…and {len(failed) - MAX_FAILURES_SHOWN} more failing test(s)"))
    return Outcome(frac, _status(frac), out + ev)


# ── readme ────────────────────────────────────────────────────────────────────

def readme(opts: dict, ctx: Context) -> Outcome:
    cands = [p for p in ctx.workdir.iterdir() if p.is_file() and p.name.lower().startswith("readme")
             and _inside(ctx.workdir, p)]
    if not cands:
        return Outcome(0.0, "failed", [Evidence("problem", "no README at the repository root")])
    path = sorted(cands)[0]
    text = path.read_text(encoding="utf-8", errors="replace")
    parts: list[tuple[str, bool, str]] = [("README present", True, path.name)]

    words = len(re.findall(r"\w+", text))
    if "min_words" in opts:
        ok = words >= int(opts["min_words"])
        parts.append(("length", ok, f"{words} words (min {opts['min_words']})"))

    headings = [h.lower() for h in re.findall(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE)]
    for section in opts.get("sections", []):
        ok = any(str(section).lower() in h for h in headings)
        parts.append((f"section '{section}'", ok, "found" if ok else "no matching heading"))

    for pattern in opts.get("forbid", []):
        bad = re.search(str(pattern), text, re.IGNORECASE)
        if bad:
            parts.append((f"forbidden text /{pattern}/", False, f"found {bad.group(0)!r}"))

    frac = sum(ok for _, ok, _ in parts) / len(parts)
    ev = [Evidence(("✓ " if ok else "✗ ") + label, detail) for label, ok, detail in parts]
    return Outcome(frac, _status(frac), ev)


# ── git ───────────────────────────────────────────────────────────────────────

def git(opts: dict, ctx: Context) -> Outcome:
    h = ctx.history
    tests: list[tuple[str, bool, str]] = []
    if "min_commits" in opts:
        tests.append(("commits", h.count >= opts["min_commits"], f"{h.count} (min {opts['min_commits']})"))
    if "min_active_days" in opts:
        tests.append(("active days", h.active_days >= opts["min_active_days"],
                      f"{h.active_days} (min {opts['min_active_days']})"))
    if "min_authors" in opts:
        tests.append(("authors", len(h.authors) >= opts["min_authors"],
                      f"{len(h.authors)} (min {opts['min_authors']})"))
    if "max_single_commit_share" in opts:
        share = h.max_single_commit_share
        tests.append(("largest commit share", share <= opts["max_single_commit_share"],
                      f"{share:.0%} of changed lines (max {opts['max_single_commit_share']:.0%})"))
    if not tests:
        return Outcome(1.0, "passed", [Evidence("note", "no history conditions configured")])
    frac = sum(ok for _, ok, _ in tests) / len(tests)
    return Outcome(frac, _status(frac), [Evidence(("✓ " if ok else "✗ ") + k, v) for k, ok, v in tests])


# ── manual ────────────────────────────────────────────────────────────────────

def manual(opts: dict, ctx: Context) -> Outcome:
    return Outcome(0.0, "manual", [Evidence("guidance", opts.get("guidance", "Requires human judgement."))],
                   review="manual criterion")


REGISTRY = {"file_exists": file_exists, "command": command, "cases": cases, "junit": junit,
            "readme": readme, "git": git, "manual": manual}
