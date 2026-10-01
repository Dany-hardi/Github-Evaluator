# Markbook

**Grade Git repositories against a rubric you write as code. Sandboxed, reproducible, and built so a human only looks at what a machine can't decide.**

`markbook` clones each student's repository, runs your tests inside a locked-down container, checks the README and commit history, flags copied code, applies late penalties, and produces reports your LMS can import. Everything the automation is unsure about lands in a short **review queue**; everything else is done.

It is a CLI first. The web UI is a thin layer over the same engine, for people who prefer clicking.

> Markbook was previously called *GitHub Evaluator*. v3 is a ground-up rewrite; the old tkinter and Flask apps are gone.

```console
$ markbook demo
FizzBuzz CLI (demo)  (grades out of 20; * = pending manual review)

  ID      GRADE  TRIAGE  FLAGS
  alice     17*  review  quality
  bob       17*  review  quality, similarity
  carol     17*  review  quality, similarity
  dave    8.08*  review  quality
  erin    13.6*  review  quality, late −20%
  frank      2*  review  quality
  ghost       —  review  invalid_url, fetch_failed

  Review load: 9 item(s) need a human vs 42 if checked by hand → 78.6% less
```

`markbook demo` builds a small cohort of local git repositories (a clean solution, a copied pair with renamed variables, an off-by-one bug, a late submission, a syntax error, an unreachable repo) and grades it offline in about two seconds.

---

## Contents

- [Install](#install) · [Quick start](#quick-start) · [How grading works](#how-grading-works)
- [Writing a spec](#writing-a-spec) · [Roster](#roster)
- [CLI reference](#cli-reference) · [Exit codes](#exit-codes)
- [Reports & LMS integration](#reports--lms-integration) · [Use in CI](#use-in-ci)
- [Review workflow](#review-workflow) · [Retrying failures](#retrying-failures) · [AI reviewer assist](#ai-reviewer-assist-optional) · [Web UI](#web-ui)
- [Security model](#security-model) · [Limitations](#limitations)
- [Development](#development) · [More docs](#more-docs)

---

## Install

Requires **Python ≥ 3.10** and **git**. **Docker** is required to run student code (see [Security model](#security-model)).

```bash
pip install .            # CLI only; the only dependency is PyYAML
pip install '.[web]'     # adds the web UI (Flask)
markbook doctor               # checks git, Docker, optional parts
```

From a clone: `python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'`.

## Quick start

```bash
markbook demo                                  # see the whole thing work, offline, no Docker needed
markbook init my-course --template python      # writes spec.yaml + roster.csv to edit
markbook validate my-course/spec.yaml          # catches every spec mistake up front
markbook grade my-course/spec.yaml --roster my-course/roster.csv --lms canvas
markbook review .markbook/runs/<run-id>             # what still needs a human, with the command to resolve it
```

A single repository works too:

```bash
markbook grade spec.yaml --repo https://github.com/alice/lab1 --id alice
```

Private repositories: export `GITHUB_TOKEN` (read-only is enough). The token reaches `git` through its environment, never the command line, and is only sent to `github.com`.

## How grading works

```
roster ──► git clone ──► history & fingerprints ──► sandbox: build/tests ──► score ──► triage ──► reports
            (pinned SHA)   (read BEFORE student        (one container per      (points,   (auto /     (JSON, CSV,
                            code can run)               submission)             late)      review)     JUnit, MD)
```

1. **Fetch.** One `git clone` per repository (optionally pinned to a `ref`). The graded commit SHA is recorded, so a grade can be reproduced and disputed.
2. **Forensics, before anything runs.** Commit count, active days, authors, size of the largest commit, last-commit time (for lateness). Read before student code executes, because a build step could otherwise rewrite `.git`.
3. **Checks** run in the order of your rubric. Six check types: `file_exists`, `command`, `cases`, `readme`, `git`, `manual`. Criteria can `require` earlier ones: if the build fails, the tests are marked failed without being run.
4. **Scoring.** Each criterion earns a fraction of its points. A late penalty is applied from the last commit time. Grades are scaled to your `scale`.
5. **Triage.** A submission is `auto` (nothing for a human to do) or `review`, with explicit reasons: a `manual` criterion is pending, a grader/sandbox error occurred, the repository could not be fetched, it looks copied, or the score is within a margin of the pass mark.
6. **Reports.** Derived from the raw result plus any human decisions.

The raw result (`run.json`) is never edited. Human decisions go to a separate, append-only `overrides.json`; reports are recomputed from both. Every grade is explainable (each criterion carries its evidence) and every change is attributable.

## Writing a spec

A spec is YAML. `markbook init` writes a starting point; `markbook validate` checks it and lists every problem at once.

```yaml
name: "Lab 3: FizzBuzz"
version: 1
scale: 20                      # grades are reported out of 20
pass_mark: 10                  # enables the "borderline" review flag
deadline: 2026-03-01T23:59:00Z # timezone is mandatory
late_penalty: {percent_per_day: 10, max_percent: 50, grace_minutes: 15}

sandbox:
  image: python:3.12-slim      # student code runs only inside this image
  timeout: 10                  # seconds per command
  memory: 256m
  cpus: 1
  pids: 128
  disk: 64m                    # size of the writable /work area

overlay: tests/                # teacher files copied over the submission (students can't tamper with them)
starter: starter/              # code handed to students; excluded from similarity
similarity: {threshold: 0.6}

criteria:
  - id: builds
    title: Compiles
    points: 4
    check: {type: command, run: "make", expect_exit: 0}

  - id: behaviour
    title: Correct output
    points: 10
    requires: [builds]
    check:
      type: cases
      run: ./fizzbuzz
      cases_file: cases.yaml   # or inline `cases:`
      compare: trim            # trim (default) | tokens | exact

  - id: readme
    title: README
    points: 2
    check: {type: readme, sections: [install, usage], min_words: 40, forbid: ["lorem ipsum"]}

  - id: history
    title: Incremental work
    points: 2
    check: {type: git, min_commits: 3, min_active_days: 2, max_single_commit_share: 0.8}

  - id: design
    title: Code design
    points: 2
    check: {type: manual, guidance: "Naming, decomposition. 0-2."}
```

### Check types

| `type` | Options | Awards |
|---|---|---|
| `file_exists` | `paths`: list of globs (matched against the full path and the file name) | fraction of globs matched |
| `command` | `run`, `expect_exit` (default 0), `stdout_contains`, `stdin`, `timeout` | all or nothing |
| `cases` | `run`, `cases` / `cases_file`, `compare`, `timeout` | fraction of cases passed |
| `readme` | `sections` (matched against headings), `min_words`, `forbid` (regexes) | fraction of conditions met |
| `git` | `min_commits`, `min_active_days`, `min_authors`, `max_single_commit_share` | fraction of conditions met |
| `manual` | `guidance` | nothing until a human grades it; always routed to review |

A **case** has `name`, `args`, `stdin`, and any of `stdout`, `stdout_regex`, `exit`. Unless a case sets `exit`, **exit code 0 is required**, so a crashing program can't earn a case by printing nothing. A case with `hidden: true` never reveals its expected output in feedback.

## Roster

A CSV with a header row. Only a repository column is required; column names are matched case-insensitively with common aliases (`repo`/`url`/`github`, `id`/`login`/`username`, `name`/`student`, `email`, `ref`/`commit`/`branch`).

```csv
id,name,email,repo,ref
alice,Alice Martin,alice@uni.edu,https://github.com/alice/lab3,
bob,Bob Okafor,bob@uni.edu,owner/bob-lab3,a1b2c3d
```

Accepted repo forms: `https://github.com/o/r`, `github.com/o/r`, `o/r`, `git@github.com:o/r.git`, and `.../tree/<branch>/<subdir>` (grades only that subdirectory). The CLI also accepts local paths; the web UI does not. Put the identifier your LMS expects in `id` (see below).

## Pinning submissions at the deadline

Lateness read from commit timestamps is only as honest as the student's clock, and a student can keep pushing after the deadline. `markbook pin` records, per student, the exact commit that existed at the deadline; `grade` then grades *that* commit.

```bash
markbook pin spec.yaml --roster roster.csv --bundle evidence/   # run when the deadline passes
markbook grade spec.yaml --roster roster.pinned.csv
```

- **`--at now`** (default): resolves each repository's current tip with `git ls-remote` (no clone). It records the remote's state at capture time, so commit-timestamp tricks cannot backdate it. A roster `ref` (branch or tag) is resolved the same way; a full SHA is kept as is.
- **`--at deadline`**: retroactive. Clones (blobless) and takes the last commit dated on or before the spec's `deadline`. This **still trusts commit timestamps**, which a student can backdate, so it is a best-effort fallback, not evidence. Needs a `deadline` in the spec.
- **Output:** `roster.pinned.csv` (all original columns, `ref` filled, plus `pinned_at`, UTC) and `pins.json` next to it (per student: repo, sha, mode, captured_at, `verified`, bundle, `error`). `verified` means the SHA was seen as an advertised tip or found in a clone; a given SHA that is not a current tip is not claimed as verified.
- **Failures** (private, missing or empty repo, no commit before the deadline) keep an empty `pinned_at` and an `error` code in `pins.json`; any original `ref` is left untouched, so check `pinned_at`. Everything else is still written and the command exits **5**.
- **`--bundle DIR`** also stores `<id>-<sha12>.bundle` per student (a full clone, so it can be large) and checks it contains the SHA. Restore with `git init r && git -C r fetch ../evidence/alice-<sha12>.bundle refs/markbook/pin`.

`pin` only helps if it runs promptly: with `--at now` the capture time is when you run it, not the deadline. Tokens work as in `grade` (`--token-env`, only sent to `github.com`).

## CLI reference

```
markbook init [DIR] [--template python|c] [--force]
markbook validate SPEC
markbook pin SPEC --roster CSV [--out CSV] [--at now|deadline] [--bundle DIR] [--token-env VAR] [--jobs N]
markbook grade SPEC (--roster CSV | --repo URL ...) [options]
markbook review RUN [--all] [--json]
markbook show RUN STUDENT
markbook override RUN STUDENT [-c CRITERION (-p POINTS | --accept-suggestion)] [--waive-late] [--clear similarity|borderline] [-m COMMENT]
markbook retry RUN [--sandbox docker|none] [--jobs N]
markbook runs [--dir DIR] [--json]
markbook stats RUN [--idle-minutes N] [--baseline CSV] [--json]
markbook report RUN [--lms canvas|moodle ...] [--include-pending] [--out DIR]
markbook schema
markbook doctor [--clean]
markbook ai-check [--model M] [--suite] [--json] [--max-cost-usd N]
markbook serve [--dir DIR] [--host H] [--port P] [--sandbox docker|none]
markbook demo [--dir DIR] [--out DIR] [--serve]
```

`RUN` is a run directory, or a bare run id under `.markbook/runs/`.

`markbook grade` options:

| Option | Meaning |
|---|---|
| `--roster FILE` / `--repo URL` | Who to grade (`--repo` is repeatable; add `--id`, `--name`, `--ref` for a single repo). |
| `--out DIR` | Output directory (default `.markbook/runs/<run-id>`). |
| `--sandbox docker\|none` | `docker` (default) isolates student code. `none` runs it on this machine; use only inside an environment that is already isolated (CI job, VM, LXD container). |
| `--jobs N` | Submissions graded in parallel (default 4). |
| `--lms canvas\|moodle` | Also write an LMS import CSV (repeatable). |
| `--include-pending` | Write provisional grades for submissions still awaiting manual review. |
| `--token-env VAR` | Env var holding a GitHub token (default `GITHUB_TOKEN`, then `GH_TOKEN`). |
| `--format text\|json` | `json` prints the full report to stdout (progress goes to stderr), for piping into `jq` or another tool. |
| `--ai` / `--ai-model M` | Opt in to [AI suggestions](#ai-reviewer-assist-optional) for criteria marked `ai: true`. Sends code to the Anthropic API. |
| `--fail-on-review` | Exit 4 when anything needs review. |

Press **Ctrl-C** during grading to stop: no new submissions are started, the ones already running finish (bounded by their timeouts) and remove their containers, and the command exits 130 without writing a partial run.

Scripting examples:

```bash
markbook grade spec.yaml --roster roster.csv --format json --quiet | jq '.summary.review_load'
markbook review .markbook/runs/20260302-0900-ab12 --json | jq -r '.[].id'
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error: invalid spec/roster/run, unknown student, bad override |
| 2 | Usage error |
| 3 | Sandbox unavailable (Docker missing or unreachable and `--sandbox docker` requested). Nothing is graded and nothing is written. |
| 4 | Success, but items need review (only with `--fail-on-review`) |
| 5 | `pin` wrote its outputs, but some repositories could not be pinned (see `pins.json`) |

## Reports & LMS integration

`markbook grade` writes to the run directory:

| File | Contents |
|---|---|
| `report.json` | Everything, validated against a published JSON Schema (`markbook schema`). |
| `grades.csv` | One row per student: `id,name,email,repo,commit,status,grade,scale,late,penalty_pct,review,review_reasons` plus `pts_<criterion>` columns. |
| `grades.canvas.csv` / `grades.moodle.csv` | LMS import files (`--lms`). |
| `junit.xml` | One `<testsuite>` per student, one `<testcase>` per criterion, so any CI can display the cohort. |
| `summary.md` | Cohort statistics and the review queue, ready to paste into a ticket. |
| `feedback/<id>.md` | Per-student feedback, safe to send (hidden test details are never included). |
| `run.json`, `overrides.json` | Raw machine result and the audit trail of human decisions. |

**Grades are never released early.** The `grade` column in the CSVs is blank while a submission is `pending_review` or `error`. Pass `--include-pending` to get provisional numbers.

**JSON contract.** `report.json` carries `schema_version`. Consumers should ignore unknown properties; breaking changes bump the version. `markbook schema` prints the schema; the test suite validates real output against it, including output with overrides applied.

**Canvas.** `grades.canvas.csv` follows Canvas's gradebook import layout: `Student, ID, SIS User ID, SIS Login ID, Section, <assignment>` and a `Points Possible` row, matching students on `SIS Login ID`. Put each student's Canvas login in the roster `id` column.

**Moodle.** `grades.moodle.csv` follows the offline grading worksheet layout (`Identifier`, `Full name`, `Email address`, `Grade`, `Maximum grade`, `Feedback comments`…). Put the Moodle *identifier* in the roster `id` column.

> The LMS layouts were written from each platform's documented import format and are covered by format tests, but they have **not** been verified against a live Canvas or Moodle instance. Do a test import into a sandbox course before relying on them for a real cohort. LMS exports vary by version and configuration.

## Use in CI

Grade on every push of a submissions repository, publish the cohort as test results, and fail the job if anything needs a human:

```yaml
# .github/workflows/grade.yml
on: workflow_dispatch
jobs:
  grade:
    runs-on: ubuntu-latest        # already an isolated, throwaway VM
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install .
      - run: markbook grade spec.yaml --roster roster.csv --lms canvas --out out --fail-on-review
        env: {GITHUB_TOKEN: "${{ secrets.READ_TOKEN }}"}
      - uses: actions/upload-artifact@v4
        if: always()
        with: {name: grades, path: out/}
```

The GitHub-hosted runner is a disposable VM with Docker, so the default `--sandbox docker` should work as is (this workflow is provided as a starting point and has not been run on GitHub Actions).

## Review workflow

The point of the tool is a short queue.

```console
$ markbook review .markbook/runs/demo1
bob  Bob Okafor  17*
    • criterion quality (3 pts): manual criterion
        markbook override demo1 bob -c quality -p <0-3> -m "…"
    • similarity: 100% fingerprint overlap with Carol Nguyen

$ markbook show demo1 bob                      # criteria, evidence, similar files
$ markbook override demo1 bob -c quality -p 3 -m "Clean decomposition."
$ markbook override demo1 bob --clear similarity -m "Both used the lecture helper."
$ markbook override demo1 erin --waive-late -m "Approved extension."
```

- A decision never edits the raw result; it is appended to `overrides.json` with reviewer, time, and what it replaced.
- `--reviewer NAME` (or `MARKBOOK_REVIEWER`) sets who is recorded; default is the OS user.
- Similarity is a **routing signal, not a verdict**. It never changes a grade, and each flag shows the matching files so a human can verify it.
- "Review load" in the summary is a *modelled* figure: baseline is every criterion of every submission checked by hand; review items are unresolved manual/error criteria plus similarity, borderline and fetch-failure flags. It is a workload estimate, not a measured time saving; see [Measuring the saving](#measuring-the-saving) for how to measure one.

## Measuring the saving

`markbook stats RUN` derives review-effort numbers from the audit log (and, in the web UI, a small view log): decisions per reviewer, active review time (a lower bound; gaps over `--idle-minutes` are breaks), median seconds per decision, items still queued, and the AI-assisted share and accept-unchanged rate. With `--baseline manual.csv` (`submission_id,seconds`, the same submissions graded entirely by hand) it prints the measured comparison next to the modelled review-load figure, labelled with its sample size and with cautions when the sample is small or mismatched. It never extrapolates. Markbook makes no measured time-saving claim; [docs/MEASURING.md](docs/MEASURING.md) is a short protocol for getting a fair one.

## Retrying failures

Fetch failures (a private repo you've since been given access to, a network blip, a corrected ref) and criteria that errored in the sandbox are exactly the review items automation can resolve itself once the cause is fixed:

```console
$ markbook retry .markbook/runs/20260302-0900-ab12
retrying 2 submission(s): ghost, frank
✓ re-graded 2; 1 recovered, 1 still failing
```

Only the failed submissions are re-graded; everything else stays byte-for-byte as it was, and similarity is recomputed across the whole cohort from fingerprints stored with the run. `retry` refuses if the spec's hash has changed since the run (re-grading part of a cohort against a different rubric would make grades incomparable). `markbook runs` lists runs with how many items in each still need review.

## AI reviewer assist (optional)

For `manual` criteria you can ask Claude for an *advisory* score and rationale, to speed up the human, never to replace them.

```yaml
- id: design
  points: 5
  check: {type: manual, guidance: "Decomposition, naming, error handling. 0-5.", ai: true}
```

```bash
pip install 'markbook[ai]'         # and set ANTHROPIC_API_KEY (or run `ant auth login`)
markbook grade spec.yaml --roster roster.csv --ai
markbook review RUN                # shows "AI suggests 4/5 (medium confidence; advisory)"
markbook show RUN alice            # the rationale, concerns, and which files it did not see
markbook override RUN alice -c design --accept-suggestion -m "Agree."
```

How it is constrained:

- **Two opt-ins.** The spec must mark the criterion `ai: true` *and* you must pass `--ai`. Without both, nothing is sent. `--ai` prints which criteria and how many submissions' source code will go to the Anthropic API; check that against your institution's data policy first.
- **Suggestion only.** The criterion stays pending, the score is unchanged, the submission stays in the review queue, and the suggestion does **not** count toward the "less manual review" figure. A human accepts, edits or ignores it; the audit log records the AI's suggested points next to the human's decision (including when they disagree).
- **Untrusted input.** Student code goes to the model as delimited *data*; delimiter break-outs are neutralised; the system prompt tells the model to ignore instructions inside the submission and to report attempts under `concerns`; output is constrained to a JSON schema and clamped to `[0, max]`.
- **Honest about coverage.** Whole files only, up to a budget (60k characters). Anything left out is listed, and confidence is capped at `medium` when code was not seen.
- **Fails safe.** No SDK, no credentials, a refusal, an API error: the criterion simply has no suggestion. Credentials are checked before any repository is cloned.
- Default model `claude-opus-5-5` (`--ai-model` to change).

> **Verification status.** The request format was checked against the real Anthropic SDK (1.11) by capturing the exact JSON it puts on the wire, and all behaviour is tested with a fake client. It has **not** been run against the live API: there was no API key in the development environment. To check it with your own key, run `markbook ai-check` (one tiny call) and `markbook ai-check --suite` (a few cents of synthetic fixtures, including prompt-injection and delimiter break-out cases); see [docs/AI-CHECK.md](docs/AI-CHECK.md). As of this commit neither has been run live.

## Web UI

```bash
pip install '.[web]'
markbook serve                 # http://127.0.0.1:5000, runs stored in .markbook/runs
markbook demo --serve          # demo cohort, then open the UI
```

Same engine, same run directories: a run started in the browser can be reviewed from the CLI and vice versa.

- **Run page:** headline numbers, grade distribution, review queue first, exports.
- **Review page:** one card per criterion with the evidence; type points and a comment. Resolving a submission jumps to the next one in the queue. `j`/`k` move between submissions.
- Server-rendered, no CDN or external requests, strict Content-Security-Policy, keyboard accessible, light and dark themes.
- The UI has **no authentication**. It binds to `127.0.0.1` by default; `markbook serve --host 0.0.0.0` prints a warning. Put it behind an authenticating reverse proxy if you expose it.
- Uploaded specs must be self-contained: `overlay`/`starter` paths outside the upload are refused. For specs that use `overlay`, `starter` or `cases_file`, put them in a directory and start the UI with `markbook serve --specs DIR`; they then appear in a dropdown on the *New run* page (operator-provided, so trusted).
- Web rosters accept `https://` repositories only, never local paths.

## Security model

Student code is untrusted. Treat the grader as a system that runs arbitrary code from strangers.

**Docker sandbox (default).** One throwaway container per submission, shared by all of its commands:

| Control | Setting |
|---|---|
| Network | `--network none` |
| Privileges | non-root (your uid), `--cap-drop ALL`, `no-new-privileges` |
| Resources | `--memory` (swap = memory), `--cpus`, `--pids-limit`, per-command `timeout -s KILL` inside the container |
| Filesystem | no host paths mounted; sources streamed in as a tar into a **size-capped tmpfs** (`disk`), root FS not writable by the user |
| Output | at most 64 KiB kept per stream |
| Cleanup | container removed after the submission, including on errors |

**No silent fallback.** If you ask for Docker and it isn't usable, grading stops with exit code 3 and an explanation. `--sandbox none` exists for environments that are *already* isolated, prints a warning, and only applies `ulimit` CPU-time and file-size limits plus a scrubbed environment. It is **not** a sandbox.

**Other protections:**
- Git history, similarity fingerprints and the README are read from the clone *before* any student code runs.
- The sandbox image is pulled once, up front, not by N parallel workers mid-grading. `markbook doctor --clean` removes containers orphaned by a killed run.
- CSV exports neutralise spreadsheet formula injection: a student named `=HYPERLINK(...)` is written as text, so opening the export in Excel cannot run it. Identifiers and grades are untouched.
- `overrides.json` is written under a file lock, so the CLI and the web UI can record decisions at the same time without losing any.
- The container receives a copy without `.git`; symlinks are removed from the copy; README/file checks refuse paths that resolve outside the submission.
- Teacher files (`overlay`) are copied over the submission, so students cannot replace your tests.
- Credentials travel in git's environment, only to `github.com`; clone uses `core.hooksPath=/dev/null`, refuses `ext::`/`file:` transports for remote URLs, and rejects URLs or refs starting with `-`.
- The web UI sends a strict CSP, `X-Frame-Options: DENY` and `nosniff`, rejects cross-origin POSTs, escapes all evidence, validates run ids, and caps uploads at 2 MB.

## Limitations

Known and deliberate; please don't discover them in production:

- **No network inside the sandbox**, so builds that download dependencies (`pip install`, `npm install`, Maven) fail. Vendor dependencies, or use an image that has them pre-installed.
- **Commit timestamps are author-controlled.** Lateness is measured from the last commit's time, which a student can set arbitrarily. For high-stakes deadlines, run [`markbook pin`](#pinning-submissions-at-the-deadline) when the deadline passes and grade the pinned roster (`pin --at deadline` is only a best-effort fallback, because it also trusts timestamps).
- **Similarity is token-based.** It catches renamed variables and reworded comments. It does not catch semantically rewritten code, and short assignments produce short fingerprints (below `min_fingerprints` nothing is flagged). It understands C-family languages (C, C++, Java, JavaScript/TypeScript, Go, Rust, C#, Kotlin, Swift), Python, Ruby and shell.
- **Rubric checks are mechanical.** README and history checks measure structure, not quality. That is what `manual` criteria are for.
- **AI is advisory only** and has not been run against the live API (see above). No grade ever depends on a model: there is no automatic AI scoring.
- **POSIX only.** Linux and macOS (and WSL on Windows). Native Windows is not supported: the sandbox maps your uid into the container and the overrides lock uses `fcntl`.
- **Clones are shallow unless the rubric has a `git` check**, so history numbers (`commits`, `active_days`…) are `null` in the report for such runs. Lateness only needs the tip commit.
- **GitHub only for token support**; other hosts work for public https URLs without authentication.
- **No multi-user auth or database** in the web UI; runs are plain files in a directory.

## Development

```bash
pip install -e '.[dev]'
pytest                      # 138 tests, ~40 s
MARKBOOK_TEST_IMAGE=busybox:latest pytest -m docker   # isolation tests need Docker + a local image
markbook demo --dir /tmp/demo    # regenerate the fixture cohort
```

CI runs the full suite, including the real-Docker isolation tests, on Python 3.10, 3.11, 3.12 and 3.13; local development is on 3.14. (CI caught a bug that local runs on 3.13/3.14 could not: `communicate()` on a closed pipe fails on Python < 3.13.)

The suite includes real-Docker isolation tests (non-root, no network, disk cap, runaway-process kill, no leaked containers) which skip automatically when Docker or the image is unavailable, and validates all report output against the JSON Schema.

```
markbook/
  spec.py        rubric-as-code: loading and validation
  fetch.py       git clone, URL normalisation, credentials
  gitinfo.py     commit-history facts
  sandbox.py     DockerSandbox / LocalSandbox
  checks.py      the six check types
  similarity.py  winnowing-based similarity
  grader.py      pipeline, scoring, triage, cohort runner
  store.py       run directory + audited overrides
  report.py      JSON / CSV / JUnit / Markdown
  cli.py         the `markbook` command
  web/           Flask UI over the same core
  schema/        report.schema.json
```

## More docs

- [`docs/DESIGN.md`](docs/DESIGN.md): the design decisions, trade-offs, what was rejected and why.

## License

MIT. See [LICENSE](LICENSE).
