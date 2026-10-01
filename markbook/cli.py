"""`markbook` command line interface.

Exit codes (stable, for scripts and CI):
  0  success
  1  runtime error (bad input file, unknown run, …)
  2  usage error (argparse)
  3  sandbox unavailable (Docker missing/unreachable and --sandbox docker requested)
  4  success, but some submissions need human review (only with --fail-on-review)
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
from datetime import datetime
from importlib import resources
from pathlib import Path

from . import SCHEMA_VERSION, __version__
from .ai import AiUnavailable, DEFAULT_MODEL, Reviewer
from .grader import grade_cohort
from .roster import Entry, RosterError, load_roster, safe_id
from .report import write_reports
from .retry import failed_ids, retry_failed
from .sandbox import SandboxUnavailable, docker_status, remove_orphans
from .spec import SpecError, load_spec
from .store import StoreError, current, load_run, record_decision, save_corpora, save_run

DEFAULT_RUNS = Path(".markbook/runs")

EXIT_ERROR, EXIT_SANDBOX, EXIT_REVIEW = 1, 3, 4

_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _COLOR else s
dim, bold, red, green, yellow = (lambda s: _c("2", s), lambda s: _c("1", s), lambda s: _c("31", s),
                                 lambda s: _c("32", s), lambda s: _c("33", s))


class CliError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR):
        super().__init__(message)
        self.code = code


# ── helpers ───────────────────────────────────────────────────────────────────

def _token(env_name: str | None) -> str | None:
    names = [env_name] if env_name else ["GITHUB_TOKEN", "GH_TOKEN"]
    return next((os.environ[n] for n in names if os.environ.get(n)), None)


def _new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def _reviewer(args) -> str:
    return getattr(args, "reviewer", None) or os.environ.get("MARKBOOK_REVIEWER") or getpass.getuser()


def _run_dir(arg: str) -> Path:
    """Accept a run directory, or a bare run id inside .markbook/runs."""
    p = Path(arg)
    if (p / "run.json").is_file():
        return p
    if (DEFAULT_RUNS / arg / "run.json").is_file():
        return DEFAULT_RUNS / arg
    raise CliError(f"no run found at {arg!r} (looked for {p}/run.json and {DEFAULT_RUNS / arg}/run.json)")


def _grade_str(sub: dict) -> str:
    if sub["status"] == "error":
        return "—"
    sc = sub["score"]
    return f"{sc['scaled']:g}" + ("" if sc["complete"] else "*")


def render_table(run: dict) -> str:
    a, sm = run["assignment"], run["summary"]
    rows = []
    for s in run["submissions"]:
        flags = []
        if s["status"] == "error":
            flags.append(s["error"]["code"])
        for r in s["triage"]["reasons"]:
            flags.append(r["criterion"] if r["code"] == "criterion" else r["code"])
        if s.get("late") and s["late"]["is_late"] and not s["late"].get("waived"):
            flags.append(f"late −{s['late']['penalty_pct']:g}%")
        rows.append((s["id"], _grade_str(s), "review" if s["triage"]["state"] == "review" else "auto",
                     ", ".join(dict.fromkeys(flags))))
    w0 = max([len(r[0]) for r in rows] + [2])
    out = [bold(f"{a['name']}") + dim(f"  (grades out of {a['scale']:g}; * = pending manual review)"), ""]
    out.append(f"  {'ID':<{w0}}  {'GRADE':>6}  {'TRIAGE':<6}  FLAGS")
    for sid, g, t, f in rows:
        t_col = green(f"{t:<6}") if t == "auto" else yellow(f"{t:<6}")
        out.append(f"  {sid:<{w0}}  {g:>6}  {t_col}  {f}")
    rl = sm["review_load"]
    out += ["", f"  {bold('Review load:')} {rl['review_items']} item(s) need a human vs {rl['baseline_items']} "
                f"if checked by hand → {green(str(rl['reduction_pct']) + '% less')}",
            f"  {bold('Cohort:')} {sm['graded']}/{sm['submissions']} graded, {sm['errors']} error(s), "
            f"{sm['late']} late, {sm['similarity_flags']} similarity flag(s)"]
    return "\n".join(out)


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_validate(args) -> int:
    spec = load_spec(args.spec)
    print(f"{green('✓')} {spec.name} v{spec.version}: {len(spec.criteria)} criteria, "
          f"{spec.max_points:g} points → scale {spec.scale:g}")
    for c in spec.criteria:
        extra = " (manual)" if c.manual else ""
        print(f"    {c.id:<14} {c.points:>5g}  {c.type}{extra}  {dim(c.title)}")
    if spec.needs_sandbox:
        print(dim(f"  sandbox image: {spec.sandbox.image}"))
    return 0


def cmd_grade(args) -> int:
    spec = load_spec(args.spec)
    if bool(args.roster) == bool(args.repo):
        raise CliError("give exactly one of --roster FILE or --repo URL (repeatable)")
    if args.roster:
        entries = load_roster(args.roster)
    else:
        entries = []
        for i, r in enumerate(args.repo):
            ident = safe_id(args.id) if args.id and len(args.repo) == 1 else \
                safe_id("_".join(r.rstrip("/").removesuffix(".git").split("/")[-2:]))
            entries.append(Entry(ident, args.name or ident if len(args.repo) == 1 else ident, "", r, args.ref))

    if args.sandbox == "none" and not getattr(args, "_demo", False):
        print(yellow("⚠ --sandbox none: student code runs directly on this machine. "
                     "Use only inside an isolated environment (CI job, VM, container)."), file=sys.stderr)

    reviewer = None
    if args.ai:
        wants = [c.id for c in spec.criteria if c.type == "manual" and c.check.get("ai")]
        if not wants:
            raise CliError("--ai given, but no manual criterion in the spec has `ai: true`, so nothing would be sent")
        try:
            reviewer = Reviewer.create(args.ai_model)
        except AiUnavailable as exc:
            raise CliError(str(exc)) from exc
        print(yellow(f"⚠ --ai: source code of up to {len(entries)} submission(s) will be sent to the Anthropic API "
                     f"({args.ai_model}) for criteria: {', '.join(wants)}. Suggestions are advisory only."),
              file=sys.stderr)

    run_id = _new_run_id()
    out = Path(args.out) if args.out else DEFAULT_RUNS / run_id
    done, lock = [0], threading.Lock()

    def progress(ev: dict) -> None:
        if ev["event"] == "done" and not args.quiet:
            with lock:
                done[0] += 1
                print(dim(f"[{done[0]}/{len(entries)}] {ev['id']}: {ev['status']}"), file=sys.stderr)

    corpora: dict = {}
    run = grade_cohort(spec, entries, args.sandbox, jobs=args.jobs, token=_token(args.token_env),
                       allow_local=True, progress=progress, run_id=run_id, corpora_out=corpora, reviewer=reviewer,
                       inputs={"spec_path": str(Path(args.spec).resolve()), "allow_local": True},
                       log=lambda m: print(dim(m), file=sys.stderr))
    save_run(out, run)
    save_corpora(out, corpora)
    run = current(out)
    write_reports(run, out, lms=args.lms, include_pending=args.include_pending)

    if args.format == "json":
        print(json.dumps(run, indent=2, ensure_ascii=False))
    else:
        print(render_table(run))
        print(f"\n  {bold('Reports:')} {out}/  {dim('(report.json, grades.csv, junit.xml, summary.md, feedback/)')}")
        queue = [s for s in run["submissions"] if s["triage"]["state"] == "review"]
        if queue:
            print(f"  {bold('Next:')} markbook review {out}")
    needs = any(s["triage"]["state"] == "review" for s in run["submissions"])
    return EXIT_REVIEW if (args.fail_on_review and needs) else 0


def cmd_runs(args) -> int:
    root = Path(args.dir)
    rows = []
    for d in sorted((x for x in root.iterdir() if (x / "run.json").is_file()), reverse=True) if root.is_dir() else []:
        run = current(d)
        sm = run["summary"]
        queue = sum(1 for s in run["submissions"] if s["triage"]["state"] == "review")
        rows.append({"id": d.name, "assignment": run["assignment"]["name"], "created": run["created_at"],
                     "submissions": sm["submissions"], "needs_review": queue, "errors": sm["errors"],
                     "path": str(d)})
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print(dim(f"no runs in {root}"))
        return 0
    w = max(len(r["id"]) for r in rows)
    print(f"{'RUN':<{w}}  {'SUBS':>4}  {'REVIEW':>6}  ASSIGNMENT")
    for r in rows:
        flag = yellow(f"{r['needs_review']:>6}") if r["needs_review"] else green(f"{0:>6}")
        print(f"{r['id']:<{w}}  {r['submissions']:>4}  {flag}  {r['assignment']}")
    return 0


def cmd_retry(args) -> int:
    run_dir = _run_dir(args.run)
    before = load_run(run_dir)
    ids = failed_ids(before)
    if not ids:
        print(green("✓ Nothing to retry: no fetch failures or errored criteria."))
        return 0
    print(f"retrying {len(ids)} submission(s): {', '.join(ids)}", file=sys.stderr)
    run, _ = retry_failed(run_dir, args.sandbox, jobs=args.jobs, token=_token(args.token_env),
                          log=lambda m: print(dim(m), file=sys.stderr))
    save_run(run_dir, run)
    cur = current(run_dir)
    write_reports(cur, run_dir)
    still = failed_ids(cur)
    print(f"{green('✓')} re-graded {len(ids)}; {len(ids) - len(still)} recovered, {len(still)} still failing")
    print(render_table(cur))
    return 0


def cmd_review(args) -> int:
    run = current(_run_dir(args.run))
    queue = [s for s in run["submissions"] if args.all or s["triage"]["state"] == "review"]
    if args.json:
        print(json.dumps([{"id": s["id"], "name": s["name"], "state": s["triage"]["state"],
                           "reasons": s["triage"]["reasons"]} for s in queue], indent=2))
        return 0
    if not queue:
        print(green("✓ Nothing needs review."))
        return 0
    for s in queue:
        print(f"{bold(s['id'])}  {s['name']}  {dim(_grade_str(s))}")
        if s["status"] == "error":
            print(f"    {red('✗')} {s['error']['code']}: {s['error']['message']}")
        for r in s["triage"]["reasons"]:
            if r["code"] == "criterion":
                crit = next(c for c in s["criteria"] if c["id"] == r["criterion"])
                print(f"    {yellow('•')} criterion {bold(r['criterion'])} ({crit['max_points']:g} pts): {r['detail']}")
                sug = crit.get("suggestion")
                if sug:
                    print(f"        {dim('AI suggests')} {sug['points']:g}/{crit['max_points']:g} "
                          f"{dim('(' + sug['confidence'] + ' confidence; advisory)')}  "
                          + dim(f"markbook override {args.run} {s['id']} -c {r['criterion']} --accept-suggestion"))
                print(dim(f"        markbook override {args.run} {s['id']} -c {r['criterion']} -p <0-{crit['max_points']:g}> -m \"…\""))
            else:
                print(f"    {yellow('•')} {r['code']}: {r['detail']}")
    return 0


def cmd_show(args) -> int:
    run = current(_run_dir(args.run))
    sub = next((s for s in run["submissions"] if s["id"] == args.submission), None)
    if not sub:
        raise CliError(f"no submission {args.submission!r}; ids: {', '.join(s['id'] for s in run['submissions'])}")
    print(bold(f"{sub['name']} ({sub['id']})"), dim(f"commit {(sub['commit'] or '—')[:10]}  {sub['repo']}"))
    if sub["status"] == "error":
        print(red(f"  ✗ {sub['error']['code']}: {sub['error']['message']}"))
        return 0
    print(f"  grade {_grade_str(sub)}/{sub['score']['scale']:g}  penalty {sub['score']['penalty']:g}  "
          f"triage {sub['triage']['state']}")
    for c in sub["criteria"]:
        o = c.get("override")
        pts = f"{o['points']:g}*" if o else f"{c['points']:g}"
        print(f"\n  {bold(c['title'])}  [{c['status']}]  {pts}/{c['max_points']:g}")
        for e in c["evidence"]:
            text = e["text"].replace("\n", "\n        ")
            print(f"      {dim(e['label'] + ':')} {text}")
        sug = c.get("suggestion")
        if sug:
            print(f"      {yellow('AI suggestion (advisory, not applied)')}: {sug['points']:g}/{c['max_points']:g}, "
                  f"{sug['confidence']} confidence, {sug['model']}")
            print(f"        {sug['rationale']}")
            for concern in sug["concerns"]:
                print(f"        {yellow('!')} {concern}")
            if sug["truncated"]:
                print(f"        {dim('did not see: ' + ', '.join(sug['omitted_files']))}")
        if o:
            print(f"      {green('override')} by {o['reviewer']}: {o.get('comment', '')}"
                  + (" (AI-assisted)" if o.get("ai_assisted") else ""))
    return 0


def cmd_override(args) -> int:
    run_dir = _run_dir(args.run)
    if args.criterion and args.points is None and not args.accept_suggestion:
        raise CliError("--criterion needs --points (or --accept-suggestion)")
    if args.accept_suggestion and not args.criterion:
        raise CliError("--accept-suggestion needs --criterion")
    entry = record_decision(run_dir, args.submission, reviewer=_reviewer(args), criterion=args.criterion,
                            points=args.points, comment=args.message or "",
                            late_waived=True if args.waive_late else None, clear=args.clear,
                            from_suggestion=args.accept_suggestion)
    run = current(run_dir)
    write_reports(run, run_dir)
    sub = next(s for s in run["submissions"] if s["id"] == args.submission)
    print(f"{green('✓')} recorded {entry['action']} for {args.submission}; "
          f"grade now {_grade_str(sub)}/{run['assignment']['scale']:g}  triage {sub['triage']['state']}")
    return 0


def cmd_report(args) -> int:
    run_dir = _run_dir(args.run)
    run = current(run_dir)
    files = write_reports(run, Path(args.out) if args.out else run_dir, lms=args.lms,
                          include_pending=args.include_pending)
    print(f"{green('✓')} wrote {len(files)} files to {args.out or run_dir}")
    return 0


def cmd_init(args) -> int:
    dest = Path(args.dir)
    dest.mkdir(parents=True, exist_ok=True)
    spec_path = dest / "spec.yaml"
    if spec_path.exists() and not args.force:
        raise CliError(f"{spec_path} already exists (use --force to overwrite)")
    templates = {"python": _PY_TEMPLATE, "c": _C_TEMPLATE}
    spec_path.write_text(templates[args.template], encoding="utf-8")
    roster = dest / "roster.csv"
    if not roster.exists():
        roster.write_text("id,name,email,repo\n"
                          "jdoe,Jane Doe,jdoe@example.edu,https://github.com/jdoe/assignment-1\n", encoding="utf-8")
    print(f"{green('✓')} created {spec_path} and {roster}\n  next: markbook validate {spec_path}")
    return 0


def cmd_schema(args) -> int:
    print(resources.files("markbook").joinpath("schema/report.schema.json").read_text(encoding="utf-8"), end="")
    return 0


def cmd_doctor(args) -> int:
    ok_all = True
    def line(ok, msg, detail=""):
        nonlocal ok_all
        ok_all &= ok
        print(f"  {green('✓') if ok else red('✗')} {msg}" + (dim(f"  {detail}") if detail else ""))
    print(bold(f"markbook {__version__} (report schema v{SCHEMA_VERSION})"))
    line(sys.version_info >= (3, 10), f"python {sys.version.split()[0]}", "needs ≥ 3.10")
    line(bool(shutil.which("git")), "git", shutil.which("git") or "not found: required to fetch repositories")
    ok, detail = docker_status()
    line(ok, "docker (sandbox)", detail if ok else detail + " → use --sandbox none only in an isolated environment")
    try:
        import flask  # noqa: F401
        line(True, "flask (web UI)", flask.__version__ if hasattr(flask, "__version__") else "")
    except ImportError:
        line(True, "web UI not installed", "optional: pip install 'markbook[web]'")
    if args.clean:
        n = remove_orphans() if ok else 0
        print(f"  {green('✓') if ok else red('✗')} removed {n} orphaned sandbox container(s)")
    tok = _token(None)
    print(f"  {dim('•')} GITHUB_TOKEN: {'set' if tok else 'not set (only needed for private repositories)'}")
    return 0 if ok_all else 1


def cmd_serve(args) -> int:
    try:
        from .web.app import create_app
    except ImportError as exc:
        raise CliError("the web UI needs Flask: pip install 'markbook[web]'") from exc
    runs = Path(args.dir)
    runs.mkdir(parents=True, exist_ok=True)
    specs = Path(args.specs) if args.specs else None
    if specs and not specs.is_dir():
        raise CliError(f"--specs {specs} is not a directory")
    app = create_app(runs, runtime=args.sandbox, jobs=args.jobs, token=_token(args.token_env), specs_dir=specs)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(yellow("⚠ Binding to a non-local address: the web UI has no authentication. "
                     "Put it behind a reverse proxy with auth."), file=sys.stderr)
    print(f"markbook web UI → http://{args.host}:{args.port}  (runs in {runs.resolve()}, sandbox: {args.sandbox})")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


def cmd_demo(args) -> int:
    from .demo import build_demo
    dest = Path(args.dir) if args.dir else Path(tempfile.mkdtemp(prefix="markbook_demo_"))
    spec_path, roster_path = build_demo(dest)
    print(dim(f"demo cohort written to {dest}"))
    ns = argparse.Namespace(spec=str(spec_path), roster=str(roster_path), repo=None, id=None, name=None,
                            ref=None, out=str(args.out) if args.out else None, sandbox=args.sandbox, jobs=4,
                            lms=["canvas", "moodle"], include_pending=False, token_env=None,
                            fail_on_review=False, format="text", quiet=False, _demo=True,
                            ai=False, ai_model=DEFAULT_MODEL)
    rc = cmd_grade(ns)
    if args.serve:
        out = Path(ns.out) if ns.out else None
        runs = out.parent if out else DEFAULT_RUNS
        return cmd_serve(argparse.Namespace(dir=str(runs), host="127.0.0.1", port=5000, sandbox=args.sandbox,
                                            jobs=4, token_env=None, specs=None))
    return rc


_PY_TEMPLATE = """\
name: "My assignment"
version: 1
scale: 100
pass_mark: 50
# deadline: 2026-03-01T23:59:00Z        # timezone is required
# late_penalty: {percent_per_day: 10, max_percent: 50}

sandbox:
  image: python:3.12-slim               # student code runs only inside this image
  timeout: 10

criteria:
  - id: structure
    title: Required files exist
    points: 10
    check: {type: file_exists, paths: [main.py, README.md]}

  - id: behaviour
    title: Program behaves correctly
    points: 50
    requires: [structure]
    check:
      type: cases
      run: python3 main.py
      cases:
        - {name: example, stdin: "2 3\\n", stdout: "5"}

  - id: readme
    title: README is useful
    points: 15
    check: {type: readme, sections: [usage], min_words: 40}

  - id: history
    title: Work done incrementally
    points: 10
    check: {type: git, min_commits: 3, max_single_commit_share: 0.8}

  - id: quality
    title: Code quality
    points: 15
    check: {type: manual, guidance: "Readability, structure, error handling."}
"""

_C_TEMPLATE = """\
name: "My C assignment"
version: 1
scale: 100
pass_mark: 50

sandbox:
  image: gcc:13
  timeout: 10
  memory: 256m

criteria:
  - id: builds
    title: Project compiles without errors
    points: 20
    check: {type: command, run: "gcc -Wall -Wextra -o prog *.c -lm", expect_exit: 0}

  - id: behaviour
    title: Program behaves correctly
    points: 50
    requires: [builds]
    check:
      type: cases
      run: ./prog
      cases:
        - {name: example, stdin: "2 3\\n", stdout: "5"}

  - id: readme
    title: README is useful
    points: 10
    check: {type: readme, sections: [build, usage], min_words: 40}

  - id: quality
    title: Memory safety and style
    points: 20
    check: {type: manual, guidance: "Leaks, bounds, naming, structure."}
"""


# ── parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="markbook", description="Grade Git repositories against a rubric-as-code spec.",
                                epilog="Docs: README.md · Exit codes: 0 ok, 1 error, 3 sandbox unavailable, "
                                       "4 needs review (--fail-on-review)")
    p.add_argument("--version", action="version", version=f"markbook {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(name, fn, help):
        sp = sub.add_parser(name, help=help, description=help)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, "create a starter spec.yaml and roster.csv")
    sp.add_argument("dir", nargs="?", default=".")
    sp.add_argument("--template", choices=["python", "c"], default="python")
    sp.add_argument("--force", action="store_true")

    sp = add("validate", cmd_validate, "check a spec file and print its rubric")
    sp.add_argument("spec")

    sp = add("grade", cmd_grade, "grade a roster (or single repository) against a spec")
    sp.add_argument("spec")
    sp.add_argument("--roster", help="CSV with columns id,name,email,repo[,ref]")
    sp.add_argument("--repo", action="append", help="repository URL or path (repeatable); alternative to --roster")
    sp.add_argument("--id"); sp.add_argument("--name"); sp.add_argument("--ref", help="branch/tag/commit for --repo")
    sp.add_argument("--out", help=f"output directory (default {DEFAULT_RUNS}/<run-id>)")
    sp.add_argument("--sandbox", choices=["docker", "none"], default="docker",
                    help="docker (default, isolated) or none (host; only inside an already-isolated environment)")
    sp.add_argument("--jobs", type=int, default=4, help="submissions graded in parallel (default 4)")
    sp.add_argument("--lms", action="append", choices=["canvas", "moodle"], default=[],
                    help="also write an LMS import CSV (repeatable)")
    sp.add_argument("--include-pending", action="store_true",
                    help="write provisional grades for submissions still awaiting manual review")
    sp.add_argument("--token-env", help="env var holding a GitHub token (default GITHUB_TOKEN, then GH_TOKEN)")
    sp.add_argument("--format", choices=["text", "json"], default="text", help="stdout format")
    sp.add_argument("--ai", action="store_true",
                    help="send code for manual criteria marked `ai: true` to the Anthropic API for an advisory score "
                         "suggestion (needs the `ai` extra and credentials)")
    sp.add_argument("--ai-model", default=DEFAULT_MODEL, help=f"model for --ai (default {DEFAULT_MODEL})")
    sp.add_argument("--fail-on-review", action="store_true", help="exit 4 if anything needs review")
    sp.add_argument("--quiet", action="store_true")

    sp = add("review", cmd_review, "list what needs a human, with the exact command to resolve each item")
    sp.add_argument("run"); sp.add_argument("--all", action="store_true"); sp.add_argument("--json", action="store_true")

    sp = add("show", cmd_show, "show one submission's criteria and evidence")
    sp.add_argument("run"); sp.add_argument("submission")

    sp = add("override", cmd_override, "record a human decision (audited, never edits the raw result)")
    sp.add_argument("run"); sp.add_argument("submission")
    sp.add_argument("-c", "--criterion"); sp.add_argument("-p", "--points", type=float)
    sp.add_argument("-m", "--message", help="comment shown to the student")
    sp.add_argument("--waive-late", action="store_true")
    sp.add_argument("--clear", choices=["similarity", "borderline"], help="mark a flag as reviewed")
    sp.add_argument("--accept-suggestion", action="store_true",
                    help="use the AI suggestion's points for --criterion (recorded as AI-assisted in the audit log)")
    sp.add_argument("--reviewer")

    sp = add("report", cmd_report, "regenerate reports (applies all overrides)")
    sp.add_argument("run"); sp.add_argument("--out")
    sp.add_argument("--lms", action="append", choices=["canvas", "moodle"], default=[])
    sp.add_argument("--include-pending", action="store_true")

    add("schema", cmd_schema, "print the JSON Schema of report.json")
    sp = add("doctor", cmd_doctor, "check that git, Docker and optional parts are available")
    sp.add_argument("--clean", action="store_true", help="also remove sandbox containers left behind by a killed run")

    sp = add("runs", cmd_runs, "list runs and how many items in each still need review")
    sp.add_argument("--dir", default=str(DEFAULT_RUNS)); sp.add_argument("--json", action="store_true")

    sp = add("retry", cmd_retry, "re-grade only submissions that failed to fetch or errored (same spec)")
    sp.add_argument("run")
    sp.add_argument("--sandbox", choices=["docker", "none"], default="docker")
    sp.add_argument("--jobs", type=int, default=4); sp.add_argument("--token-env")

    sp = add("serve", cmd_serve, "start the web UI")
    sp.add_argument("--dir", default=str(DEFAULT_RUNS), help="runs directory (default .markbook/runs)")
    sp.add_argument("--host", default="127.0.0.1"); sp.add_argument("--port", type=int, default=5000)
    sp.add_argument("--sandbox", choices=["docker", "none"], default="docker")
    sp.add_argument("--jobs", type=int, default=4); sp.add_argument("--token-env")
    sp.add_argument("--specs", help="directory of saved *.yaml specs offered in the UI (may use overlay/starter)")

    sp = add("demo", cmd_demo, "generate a sample cohort and grade it (offline, ~2 s)")
    sp.add_argument("--dir"); sp.add_argument("--out")
    sp.add_argument("--sandbox", choices=["docker", "none"], default="none",
                    help="default none: the demo's own code is trusted; docker needs python:3.12-slim")
    sp.add_argument("--serve", action="store_true", help="open the web UI on the result afterwards")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (SpecError, RosterError, StoreError) as exc:
        print(red("error: ") + str(exc), file=sys.stderr)
        return EXIT_ERROR
    except SandboxUnavailable as exc:
        print(red("sandbox unavailable: ") + str(exc), file=sys.stderr)
        return EXIT_SANDBOX
    except CliError as exc:
        print(red("error: ") + str(exc), file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
