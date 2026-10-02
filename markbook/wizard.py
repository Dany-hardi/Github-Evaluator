"""Interactive terminal wizard: asks plain questions and writes spec.yaml (and a roster).

It is a thin front-end over `specbuilder`, the same model the web form uses, so a spec written here is
validated exactly like one built in the browser. Nothing is written until the final confirmation, and
Ctrl-C or end-of-input at any point cancels cleanly.

`ask` and `say` are injected so the whole conversation can be tested with scripted answers.
"""
from __future__ import annotations

import csv
import os
import re
import sys
from pathlib import Path
from typing import Callable

from . import specbuilder as sb
from .roster import safe_id
from .spec import SpecError, load_spec

_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _COLOR else s


bold = lambda s: _c("1", s)
dim = lambda s: _c("2", s)
green = lambda s: _c("32", s)
yellow = lambda s: _c("33", s)
red = lambda s: _c("31", s)


class WizardAbort(Exception):
    """The teacher cancelled (Ctrl-C) or the input ended; nothing has been written."""


class Wizard:
    def __init__(self, ask: Callable[[str], str] = input, say: Callable[[str], None] = print):
        self._ask, self.say = ask, say

    # ── prompts ───────────────────────────────────────────────────────────────

    def ask(self, question: str, default: str = "", hint: str = "") -> str:
        shown = f" [{default}]" if default else ""
        try:
            answer = self._ask(f"{bold(question)}{shown}{dim('  ' + hint) if hint else ''}\n  > ")
        except (EOFError, KeyboardInterrupt) as exc:
            raise WizardAbort from exc
        return answer.strip() or default

    def text(self, question: str, default: str = "", hint: str = "", required: bool = False) -> str:
        while True:
            value = self.ask(question, default, hint)
            if value or not required:
                return value
            self.say(red("  Please type something."))

    def number(self, question: str, default, *, minimum=None, maximum=None, integer=False, allow_blank=False):
        while True:
            raw = self.ask(question, "" if default is None else f"{default:g}" if isinstance(default, float) else str(default))
            if raw == "" and allow_blank:
                return None
            try:
                value = float(raw)
                if integer and value != int(value):
                    raise ValueError
            except ValueError:
                self.say(red("  Please type a number" + (" (a whole number)." if integer else ".")))
                continue
            if minimum is not None and value < minimum:
                self.say(red(f"  It must be at least {minimum:g}."))
                continue
            if maximum is not None and value > maximum:
                self.say(red(f"  It must be at most {maximum:g}."))
                continue
            return int(value) if integer or value == int(value) else value

    def yes(self, question: str, default: bool = False, hint: str = "") -> bool:
        suffix = "Y/n" if default else "y/N"
        while True:
            raw = self.ask(f"{question} ({suffix})", "", hint).lower()
            if raw == "":
                return default
            if raw in ("y", "yes", "o", "oui"):
                return True
            if raw in ("n", "no", "non"):
                return False
            self.say(red("  Please answer y or n."))

    def choose(self, question: str, options: list[tuple[str, str]], default: int = 1) -> str:
        self.say(bold(question))
        for i, (_, label) in enumerate(options, 1):
            self.say(f"  {i}) {label}")
        while True:
            raw = self.ask("Choose a number", str(default))
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1][0]
            by_key = [k for k, _ in options if k == raw.lower()]
            if by_key:
                return by_key[0]
            self.say(red(f"  Type a number from 1 to {len(options)}."))

    # ── the conversation ──────────────────────────────────────────────────────

    def interview(self) -> dict:
        say = self.say
        say(bold("\nMarkbook rubric wizard"))
        say(dim("Answer in plain words. Press Enter to accept the [default]. Ctrl-C cancels; nothing is written until the end.\n"))

        model: dict = {"criteria": []}
        model["name"] = self.text("What is the assignment called?", required=True)
        model["scale"] = self.number("Grade out of", 20, minimum=0.01)
        model["pass_mark"] = self.number("Pass mark", model["scale"] / 2, minimum=0, maximum=model["scale"], allow_blank=True)

        if self.yes("Is there a deadline?"):
            while True:
                model["deadline_local"] = self.text("Deadline", hint="like 2026-03-01 23:59", required=True)
                model["utc_offset"] = self.text("Time zone offset from UTC", "+00:00", hint="Cameroon is +01:00")
                probe = sb.build({"name": "x", "deadline_local": model["deadline_local"], "utc_offset": model["utc_offset"],
                                  "criteria": [{"kind": "manual"}]})
                bad = [p.message for p in probe.problems if p.field in ("deadline_local", "utc_offset")]
                if not bad:
                    break
                say(red("  " + "; ".join(bad)))
            per_day = self.number("Late penalty, percent per day (0 for none)", 0, minimum=0, maximum=100)
            if per_day:
                model["late_percent_per_day"] = per_day
                model["late_max_percent"] = self.number("Never take off more than (percent)", 50, minimum=0, maximum=100)

        say("")
        model["language"] = self.choose("What language is the student code in?",
                                        [(k, v["label"]) for k, v in sb.LANGUAGES.items()], default=1)
        lang = sb.LANGUAGES[model["language"]]
        if model["language"] == "custom":
            model["image"] = self.text("Docker image the code runs in", hint="like python:3.12-slim", required=True)

        say(bold("\nWhat should Markbook check?") + dim("  (answer y or n for each)\n"))
        for meta in sb.KINDS:
            added = 0
            while True:
                first = added == 0
                label = meta["label"] if first else "another " + meta["label"].split(" (")[0].lower()
                say(dim(f"  {meta['help']}"))
                if not self.yes(f"Check: {label}?", default=meta["kind"] in ("files", "readme", "manual") and first):
                    break
                model["criteria"].append(self._card(meta, lang, model["language"]))
                added += 1
                if not meta.get("multiple"):
                    break
            say("")

        if any(c["kind"] in ("build", "output", "tests") for c in model["criteria"]):
            model["timeout"] = self.number("Time limit for each command, in seconds", 15, minimum=1, maximum=600, integer=True)
            if model["language"] in ("python", "node"):
                if model["language"] == "python" and any(c["kind"] == "tests" for c in model["criteria"]):
                    say(dim("  Markbook will install pytest once, with network access, before grading."))
                extra = self.text("Extra packages the code needs, comma separated (optional)",
                                  hint="installed once with network access; students never get network")
                if extra:
                    model["extra_packages"] = extra
        return model

    def _card(self, meta: dict, lang: dict, language: str) -> dict:
        item: dict = {"kind": meta["kind"]}
        kind = meta["kind"]
        item["title"] = self.text("  Name on the report", meta["title"])
        if kind == "files":
            item["paths"] = [p.strip() for p in self.text("  Files or patterns, comma separated", "README.md", required=True).split(",")]
        elif kind == "readme":
            item["sections"] = self.text("  Required headings, comma separated", "installation, usage")
            item["min_words"] = self.number("  Minimum words", 80, minimum=0, integer=True)
        elif kind == "history":
            item["min_commits"] = self.number("  Minimum commits", 3, minimum=0, integer=True)
            item["min_active_days"] = self.number("  Minimum days with a commit", 2, minimum=0, integer=True)
        elif kind in ("build", "output", "tests"):
            hint_key = {"build": "build_hint", "output": "run_hint", "tests": "test_cmd"}[kind]
            default = lang[hint_key]
            question = {"build": "  Command that builds or starts it", "output": "  Command that runs the program",
                        "tests": "  Test command (it must write a JUnit XML file called report.xml)"}[kind]
            item["run"] = self.text(question, default, required=not default)
            if kind == "output":
                item["cases"] = self._cases()
            if kind == "tests":
                item["min_tests"] = self.number("  Minimum number of tests that must run", 1, minimum=0, integer=True)
                item["hidden"] = self.yes("  Hide test names in the feedback?", False)
        elif kind == "manual":
            item["guidance"] = self.text("  What should the reviewer look for?", "Naming, structure, error handling.")
        item["points"] = self.number("  Points", meta["points"], minimum=0.01)
        self.say("")
        return item

    def _cases(self) -> list[dict]:
        cases: list[dict] = []
        self.say(dim("  Test cases: what to feed the program, and what it must print. Type \\n for a new line."))
        while True:
            n = len(cases) + 1
            if cases and not self.yes("  Add another test case?", False):
                return cases
            case = {"name": self.text(f"  Case {n}: name", f"case {n}"),
                    "args": self.text("    Arguments after the command", hint="optional"),
                    "stdin": self.text("    Input typed into the program", hint="optional").replace("\\n", "\n"),
                    "expected": self.text("    Expected output", required=True).replace("\\n", "\n")}
            if self.yes("    Keep this case secret (hide expected output in feedback)?", False):
                case["hidden"] = True
            cases.append(case)


# ── helpers for the roster ────────────────────────────────────────────────────

def fix_repo(url: str) -> str:
    """Repair the most common typo: `https:github.com/x` (missing //)."""
    url = url.strip()
    return re.sub(r"^(https?):(?!//)", r"\1://", url)


def _roster(w: Wizard, dest: Path, force: bool) -> Path | None:
    path = dest / "roster.csv"
    if path.exists() and not force:
        w.say(dim(f"  {path} already exists; leaving it alone."))
        return path
    rows: list[list[str]] = []
    if w.yes("Add the students now?", True, hint="one line per student; you can edit roster.csv later"):
        w.say(dim("  Press Enter on an empty id to finish."))
        while True:
            sid = w.text(f"  Student {len(rows) + 1}: id", hint="a short login, like danyn")
            if not sid:
                break
            name = w.text("    Full name", sid)
            email = w.text("    Email", hint="optional")
            while True:
                repo = fix_repo(w.text("    Repository URL", required=True, hint="https://github.com/owner/repo"))
                if re.match(r"^(https?://\S+|[\w.-]+/[\w.-]+|\S*/\S+)$", repo):
                    break
                w.say(red("  That does not look like a repository address."))
            rows.append([safe_id(sid), name, email, repo])
    if not rows:
        rows = [["example", "Jane Doe", "jane@example.edu", "https://github.com/jane/assignment-1"]]
        w.say(dim("  Wrote one example row; replace it with your students."))
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["id", "name", "email", "repo"])
        writer.writerows(rows)
    return path


# ── entry point ───────────────────────────────────────────────────────────────

def run_wizard(dest: Path, *, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
               force: bool = False) -> int:
    """Returns 0 on success and 1 if cancelled or the answers could not make a valid spec."""
    w = Wizard(ask, say)
    spec_path = dest / "spec.yaml"
    try:
        while True:
            model = w.interview()
            built = sb.build(model, allow_packages=True)      # on the CLI the teacher is the operator: extras allowed
            if built.ok:
                break
            say(red("\nThat rubric has problems:"))
            for p in built.problems:
                say(red(f"  - {p.message}") + dim(f"  ({p.field})" if p.field else ""))
            if not w.yes("Start again?", True):
                raise WizardAbort

        say(bold("\nSummary"))
        for c in built.spec["criteria"]:
            say(f"  {c['points']:>5g}  {c['title']}  {dim(c['check']['type'])}")
        say(f"  {built.total_points:>5g}  total, reported out of {built.spec['scale']:g}")
        if built.uses_prepare:
            say(dim("        (a dependency step runs once with network access: " + built.spec['prepare']['run'][0] + ")"))
        if w.yes("\nShow the YAML?", False):
            say(built.yaml)

        if spec_path.exists() and not force and not w.yes(f"{spec_path} already exists. Overwrite it?", False):
            raise WizardAbort
        if not w.yes(f"Write {spec_path}?", True):
            raise WizardAbort
    except WizardAbort:
        say(yellow("\nCancelled. Nothing was written."))
        return 1

    dest.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(built.yaml, encoding="utf-8")
    try:
        load_spec(spec_path)
    except SpecError as exc:      # the builder validated it already; this is a belt-and-braces check of the file itself
        say(red(f"The written spec did not validate: {exc}"))
        return 1
    say(green(f"\n✓ Wrote {spec_path} ({built.total_points:g} points)"))
    try:
        roster = _roster(w, dest, force)
    except WizardAbort:
        say(yellow("Roster skipped. You can write roster.csv yourself later."))
        roster = None
    say(bold("\nNext"))
    say(f"  markbook validate {spec_path}")
    say(f"  markbook grade {spec_path} --roster {roster or dest / 'roster.csv'} --out out")
    say("  markbook serve        # or do it in the browser")
    return 0
