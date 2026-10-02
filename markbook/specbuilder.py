"""Rubric builder: turn "what the teacher ticked" into a validated Markbook spec.

A teacher should not have to write YAML. This module is the one place that knows what each check needs
and how an answer becomes a spec; the web form and the terminal wizard are thin front-ends over it, so
they cannot drift apart, and every result still goes through `load_spec`, the same validator the CLI uses.

Safety rule: the front-ends send *choices* (a language, which checks are ticked, some numbers and
strings). The commands in the dependency step (`prepare`) are written HERE, from a fixed catalogue, never
taken from user text. Extra packages are the one free-text input that reaches `prepare`; they are matched
against a strict name pattern and refused unless the caller opts in (`allow_packages`).
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .spec import SpecError, _IMAGE_RE, load_spec

# ── catalogue ─────────────────────────────────────────────────────────────────

LANGUAGES: dict[str, dict] = {
    "python": {"label": "Python", "image": "python:3.12-slim", "memory": "256m", "pids": 128,
               "run_hint": "python main.py", "build_hint": "python -m py_compile main.py",
               "test_cmd": "python -m pytest -q -p no:cacheprovider --junitxml=report.xml", "needs": "pytest"},
    "node": {"label": "Node.js", "image": "node:20", "memory": "512m", "pids": 512,
             "run_hint": "node index.js", "build_hint": "node --check index.js",
             "test_cmd": "node --test --test-reporter=junit --test-reporter-destination=report.xml", "needs": None},
    "c": {"label": "C / C++", "image": "gcc:13", "memory": "256m", "pids": 128,
          "run_hint": "./prog", "build_hint": "gcc -Wall -Wextra -o prog *.c -lm", "test_cmd": "", "needs": None},
    "java": {"label": "Java", "image": "eclipse-temurin:21", "memory": "512m", "pids": 512,
             "run_hint": "java Main", "build_hint": "javac *.java", "test_cmd": "", "needs": None},
    "custom": {"label": "Other (I choose the Docker image)", "image": "", "memory": "256m", "pids": 128,
               "run_hint": "./run.sh", "build_hint": "make", "test_cmd": "", "needs": None},
}
COMPILED = {"c", "java", "custom"}      # a failed build makes running/testing pointless

KINDS: list[dict] = [
    {"kind": "files", "label": "Required files exist", "points": 2, "title": "Required files are present",
     "help": "Fail fast if the project is missing its basics. Patterns like src/*.py are allowed.",
     "fields": [{"name": "paths", "label": "Files or patterns, one per line", "type": "lines", "default": "README.md",
                 "placeholder": "README.md\nsrc/*.py"}]},
    {"kind": "readme", "label": "README quality", "points": 2, "title": "README explains the project",
     "help": "Checks that a README exists, has the headings you list, and is long enough.",
     "fields": [{"name": "sections", "label": "Required headings, comma separated", "type": "text",
                 "default": "installation, usage"},
                {"name": "min_words", "label": "Minimum words", "type": "number", "default": 80}]},
    {"kind": "history", "label": "Git history", "points": 1, "title": "Incremental development in Git",
     "help": "Rewards steady work over a single last-minute dump.",
     "fields": [{"name": "min_commits", "label": "Minimum commits", "type": "number", "default": 3},
                {"name": "min_active_days", "label": "Minimum days with a commit", "type": "number", "default": 2}]},
    {"kind": "build", "label": "It builds / starts", "points": 3, "title": "Project builds or starts",
     "help": "Runs one command in the sandbox and passes if it succeeds (exit code 0).",
     "fields": [{"name": "run", "label": "Command", "type": "text", "default": "", "hint_from": "build_hint"}]},
    {"kind": "output", "label": "Program output", "points": 8, "title": "Produces the correct output",
     "help": "Runs the program with inputs you give and compares what it prints. Partial credit per case.",
     "fields": [{"name": "run", "label": "Command that runs the program", "type": "text", "default": "",
                 "hint_from": "run_hint"},
                {"name": "compare", "label": "Comparison", "type": "select", "default": "trim",
                 "options": [["trim", "Ignore trailing spaces and blank lines"], ["tokens", "Ignore all whitespace"],
                             ["exact", "Exact match"]]},
                {"name": "cases", "label": "Test cases", "type": "cases"}]},
    {"kind": "tests", "label": "Test suite (per-test credit)", "points": 10, "title": "Automated tests pass",
     "help": "Runs the project's own tests and gives credit for each one that passes.",
     "fields": [{"name": "run", "label": "Test command (must write a JUnit XML report.xml)", "type": "text",
                 "default": "", "hint_from": "test_cmd"},
                {"name": "min_tests", "label": "Minimum number of tests that must run", "type": "number", "default": 1},
                {"name": "hidden", "label": "Hide test names in feedback", "type": "checkbox", "default": False}]},
    {"kind": "manual", "label": "Manual review (a person marks it)", "points": 5, "title": "Code design and clarity",
     "multiple": True,
     "help": "Never scored by the machine: it lands in your review queue.",
     "fields": [{"name": "guidance", "label": "What to look for", "type": "textarea",
                 "default": "Naming, structure, error handling."}]},
]
KIND_BY_NAME = {k["kind"]: k for k in KINDS}

PIP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}(==[A-Za-z0-9.*+!_-]{1,30})?$")
NPM_RE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]{0,60}(@[A-Za-z0-9.^~*+!_-]{1,30})?$")
OFFSET_RE = re.compile(r"^(Z|[+-]\d{2}:\d{2})$")
MAX_NAME, MAX_TEXT, MAX_CASES, MAX_PATHS = 120, 500, 40, 40


def catalog() -> dict:
    """Everything a front-end needs to render the form (JSON-serialisable)."""
    return {"languages": LANGUAGES, "kinds": KINDS}


# ── results ───────────────────────────────────────────────────────────────────

@dataclass
class Problem:
    field: str          # e.g. "criteria.readme.min_words", "" when it is about the whole spec
    message: str

    def to_dict(self) -> dict:
        return {"field": self.field, "message": self.message}


@dataclass
class Built:
    spec: dict = field(default_factory=dict)
    yaml: str = ""
    problems: list[Problem] = field(default_factory=list)
    total_points: float = 0.0
    needs_sandbox: bool = False
    uses_prepare: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems


# ── helpers ───────────────────────────────────────────────────────────────────

def slug(text: str, limit: int = 40, fallback: str = "check") -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:limit].strip("-") or fallback


def _clean(value, *, limit: int = MAX_TEXT, multiline: bool = False) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    keep = "\n\t" if multiline else "\t"
    text = "".join(ch for ch in text if ch.isprintable() or ch in keep)
    return (text if multiline else text.replace("\n", " ")).strip()[:limit]


def _number(value, label: str, problems: list[Problem], where: str, *, minimum=None, maximum=None,
            integer=False, default=None):
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        problems.append(Problem(where, f"{label} must be a number"))
        return default
    if integer:
        if num != int(num):
            problems.append(Problem(where, f"{label} must be a whole number"))
            return default
        num = int(num)
    if minimum is not None and num < minimum:
        problems.append(Problem(where, f"{label} must be at least {minimum:g}"))
    if maximum is not None and num > maximum:
        problems.append(Problem(where, f"{label} must be at most {maximum:g}"))
    return int(num) if integer else (int(num) if num == int(num) else num)


def _lines(value) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").splitlines()
    return [_clean(x, limit=200) for x in raw if _clean(x, limit=200)]


def _csv(value) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    return [_clean(x, limit=80) for x in raw if _clean(x, limit=80)]


def _deadline(model: dict, problems: list[Problem]) -> str | None:
    local = _clean(model.get("deadline_local"))
    if not local:
        return None
    offset = _clean(model.get("utc_offset")) or "+00:00"
    if not OFFSET_RE.match(offset):
        problems.append(Problem("utc_offset", "Time zone offset must look like +01:00 or Z"))
        return None
    stamp = local.replace(" ", "T")
    if len(stamp) == 16:
        stamp += ":00"
    try:
        datetime.fromisoformat(stamp)
    except ValueError:
        problems.append(Problem("deadline_local", "Deadline must look like 2026-03-01 23:59"))
        return None
    return f"{stamp}{offset}"


def _packages(model: dict, language: str, allow: bool, problems: list[Problem]) -> list[str]:
    pkgs = _csv(model.get("extra_packages"))
    if not pkgs:
        return []
    if not allow:
        problems.append(Problem("extra_packages", "Extra packages need the server to be started with --allow-prepare "
                                                  "(they are downloaded with network access)"))
        return []
    if language not in ("python", "node"):
        problems.append(Problem("extra_packages", "Extra packages are supported for Python and Node.js only"))
        return []
    pattern = PIP_RE if language == "python" else NPM_RE
    bad = [p for p in pkgs if not pattern.match(p)]
    if bad:
        problems.append(Problem("extra_packages", f"Not a valid package name: {', '.join(bad[:3])}"))
        return []
    return pkgs


# ── the build ─────────────────────────────────────────────────────────────────

def build(model: dict, *, allow_packages: bool = False) -> Built:
    """Model in, validated spec (dict + YAML text) out, or a list of field-level problems."""
    problems: list[Problem] = []
    if not isinstance(model, dict):
        return Built(problems=[Problem("", "The rubric must be an object")])

    name = _clean(model.get("name"), limit=MAX_NAME)
    if not name:
        problems.append(Problem("name", "Give the assignment a name"))
    scale = _number(model.get("scale"), "Grade out of", problems, "scale", minimum=0.01, default=20)
    pass_mark = _number(model.get("pass_mark"), "Pass mark", problems, "pass_mark", minimum=0, maximum=scale or 20)
    deadline = _deadline(model, problems)

    late = None
    per_day = _number(model.get("late_percent_per_day"), "Late penalty", problems, "late_percent_per_day", minimum=0)
    if per_day:
        if not deadline:
            problems.append(Problem("deadline_local", "A late penalty needs a deadline"))
        else:
            cap = _number(model.get("late_max_percent"), "Maximum late penalty", problems, "late_max_percent",
                          minimum=0, maximum=100, default=100)
            late = {"percent_per_day": per_day, "max_percent": cap}

    language = _clean(model.get("language")) or "python"
    if language not in LANGUAGES:
        problems.append(Problem("language", "Choose one of the listed languages"))
        language = "python"
    lang = LANGUAGES[language]
    image = lang["image"]
    if language == "custom":
        image = _clean(model.get("image"), limit=200)
        if image and not _IMAGE_RE.match(image):
            problems.append(Problem("image", "That does not look like a Docker image name (e.g. python:3.12-slim)"))
        elif not image and any(c.get("kind") in ("build", "output", "tests") for c in (model.get("criteria") or [])
                               if isinstance(c, dict)):
            problems.append(Problem("image", "Enter the Docker image your code runs in"))

    criteria, used, ids = [], {}, {}
    raw_criteria = model.get("criteria") or []
    if not isinstance(raw_criteria, list):
        raw_criteria = []
        problems.append(Problem("criteria", "criteria must be a list"))
    if len(raw_criteria) > 30:
        problems.append(Problem("criteria", "Too many checks (limit 30)"))
        raw_criteria = raw_criteria[:30]

    packages = _packages(model, language, allow_packages, problems)
    node_path = "NODE_PATH=/prepare/node_modules " if (language == "node" and packages) else ""
    needs_pytest = False
    build_id = None

    for item in raw_criteria:
        if not isinstance(item, dict) or item.get("kind") not in KIND_BY_NAME:
            problems.append(Problem("criteria", "Unknown check type"))
            continue
        kind = item["kind"]
        meta = KIND_BY_NAME[kind]
        used[kind] = used.get(kind, 0) + 1
        if used[kind] > 1 and not meta.get("multiple"):
            problems.append(Problem(f"criteria.{kind}", f"'{meta['label']}' can only be added once"))
            continue
        cid = kind if used[kind] == 1 else f"{kind}-{used[kind]}"
        where = f"criteria.{cid}"
        title = _clean(item.get("title"), limit=MAX_NAME) or meta["title"]
        points = _number(item.get("points"), "Points", problems, f"{where}.points", minimum=0.01,
                         default=meta["points"])
        check: dict = {}

        if kind == "files":
            paths = _lines(item.get("paths"))
            if not paths:
                problems.append(Problem(f"{where}.paths", "List at least one file"))
            if len(paths) > MAX_PATHS:
                problems.append(Problem(f"{where}.paths", f"At most {MAX_PATHS} entries"))
            if any(p.startswith("/") or ".." in Path(p).parts for p in paths):
                problems.append(Problem(f"{where}.paths", "Use paths inside the project (no leading / and no ..)"))
            check = {"type": "file_exists", "paths": paths[:MAX_PATHS]}
        elif kind == "readme":
            check = {"type": "readme"}
            sections = _csv(item.get("sections"))
            if sections:
                check["sections"] = sections
            words = _number(item.get("min_words"), "Minimum words", problems, f"{where}.min_words",
                            minimum=0, integer=True, default=80)
            if words:
                check["min_words"] = words
        elif kind == "history":
            check = {"type": "git"}
            for key, label, default in (("min_commits", "Minimum commits", 3), ("min_active_days", "Minimum days", 2)):
                val = _number(item.get(key), label, problems, f"{where}.{key}", minimum=0, integer=True, default=default)
                if val:
                    check[key] = val
            if len(check) == 1:
                check["min_commits"] = 1
        elif kind in ("build", "output", "tests"):
            run = _clean(item.get("run"), limit=MAX_TEXT) or _clean(lang.get({"build": "build_hint", "output": "run_hint",
                                                                         "tests": "test_cmd"}[kind]))
            if not run:
                problems.append(Problem(f"{where}.run", "Enter the command to run"))
            run = node_path + run if run else run
            if kind == "build":
                check = {"type": "command", "run": run, "expect_exit": 0}
            elif kind == "output":
                cases = _cases(item.get("cases"), where, problems)
                compare = _clean(item.get("compare")) or "trim"
                if compare not in ("trim", "tokens", "exact"):
                    problems.append(Problem(f"{where}.compare", "Choose how output is compared"))
                    compare = "trim"
                check = {"type": "cases", "run": run, "cases": cases}
                if compare != "trim":
                    check["compare"] = compare
            else:
                check = {"type": "junit", "run": run, "report": "report.xml", "timeout": 120}
                minimum = _number(item.get("min_tests"), "Minimum tests", problems, f"{where}.min_tests",
                                  minimum=0, integer=True, default=1)
                if minimum != 1:
                    check["min_tests"] = minimum
                if item.get("hidden"):
                    check["hidden"] = True
                needs_pytest = needs_pytest or (language == "python" and "pytest" in run)
        elif kind == "manual":
            check = {"type": "manual", "guidance": _clean(item.get("guidance"), limit=MAX_TEXT, multiline=True)
                     or "Mark this by hand."}

        entry = {"id": cid, "title": title, "points": points, "check": check}
        if kind == "build":
            build_id = cid
        ids[cid] = entry
        criteria.append(entry)

    if build_id and language in COMPILED:   # a broken build makes running or testing pointless
        for entry in criteria:
            if entry["check"]["type"] in ("cases", "junit"):
                entry["requires"] = [build_id]

    if not criteria and not any(p.field == "criteria" for p in problems):
        problems.append(Problem("criteria", "Tick at least one thing to check"))

    needs_sandbox = any(c["check"]["type"] in ("command", "cases", "junit") for c in criteria)
    spec: dict = {"name": name, "version": 1, "scale": scale}
    if pass_mark is not None:
        spec["pass_mark"] = pass_mark
    if deadline:
        spec["deadline"] = deadline
    if late:
        spec["late_penalty"] = late
    if needs_sandbox:
        timeout = _number(model.get("timeout"), "Time limit", problems, "timeout", minimum=1, maximum=600,
                          integer=True, default=15)
        spec["sandbox"] = {"image": image, "timeout": timeout, "memory": lang["memory"], "pids": lang["pids"]}

    uses_prepare = False
    if needs_sandbox:
        run_cmds = []
        if language == "python" and (needs_pytest or packages):
            run_cmds.append("pip install --no-cache-dir " + " ".join((["pytest"] if needs_pytest else []) + packages))
        elif language == "node" and packages:
            run_cmds.append("npm install --no-audit --no-fund " + " ".join(packages))
        if run_cmds:
            spec["prepare"] = {"run": run_cmds, "timeout": 900}
            uses_prepare = True
    spec["criteria"] = criteria

    built = Built(spec=spec, problems=problems, total_points=round(sum(c["points"] for c in criteria), 4),
                  needs_sandbox=needs_sandbox, uses_prepare=uses_prepare)
    if problems:
        return built
    built.yaml = _to_yaml(spec)
    try:   # the same validator the CLI uses: if it passes here it will load everywhere
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "spec.yaml"
            path.write_text(built.yaml, encoding="utf-8")
            load_spec(path)
    except SpecError as exc:
        built.problems.append(Problem("", str(exc).strip()))
        built.yaml = ""
    return built


def _cases(raw, where: str, problems: list[Problem]) -> list[dict]:
    items = raw if isinstance(raw, list) else []
    out = []
    for i, c in enumerate(items[:MAX_CASES], 1):
        if not isinstance(c, dict):
            continue
        expected = _clean(c.get("expected", c.get("stdout")), limit=2000, multiline=True)
        stdin = _clean(c.get("stdin"), limit=2000, multiline=True)
        args = _clean(c.get("args"), limit=200)
        if not (expected or stdin or args):
            continue                      # an untouched blank row
        if not expected:
            problems.append(Problem(f"{where}.cases", f"Test case {i} needs the expected output"))
            continue
        case: dict = {"name": _clean(c.get("name"), limit=80) or f"case {i}"}
        if args:
            case["args"] = args
        if stdin:
            case["stdin"] = stdin if stdin.endswith("\n") else stdin + "\n"
        case["stdout"] = expected
        if c.get("hidden"):
            case["hidden"] = True
        out.append(case)
    if not out and not any(p.field == f"{where}.cases" for p in problems):
        problems.append(Problem(f"{where}.cases", "Add at least one test case with its expected output"))
    return out


def _to_yaml(spec: dict) -> str:
    header = ("# Generated by Markbook's rubric builder. You can edit it by hand;\n"
              "# `markbook validate spec.yaml` checks it. Points are shown per criterion.\n")
    return header + yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)
