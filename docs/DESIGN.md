# Design notes

Why `markbook` is built the way it is: the problem, the principles, the decisions that mattered, what was rejected, and what is still weak.

## The problem

Grading a cohort of Git repositories is mostly toil with a thin layer of judgement. The toil (does it build, does it pass the tests, is there a README, was it on time, did two students hand in the same code) is mechanical. The judgement (is this design good?) is not. A grader that mixes the two produces a number nobody can defend; a grader that automates only the toil still leaves the human to re-check everything because they can't tell what the machine was unsure about.

So the design target is not "automatic grading". It is: **make the machine's certainty explicit, and send humans only to the uncertain parts.**

## Principles

1. **Explainable over opaque.** Every grade is a sum of named criteria, and each criterion carries the evidence the grader saw (command, exit code, the first differing line). Nothing is a bare number.
2. **The machine proposes, the human decides, and the record shows who.** Overrides are first-class, attributable and append-only. They never rewrite the machine's result.
3. **Rubric as data.** The assignment is a versioned YAML file, validated up front, hashed into every report. Not a settings page; not code.
4. **Untrusted code means isolation, always.** There is no silent fallback to running on the host.
5. **Reproducible.** A grade is a function of (spec hash, repository commit SHA, tool version). All three are in the report.
6. **One engine, two front-ends.** The CLI and the web UI call the same functions and read and write the same run directories.
7. **Fail loudly where silence costs a student marks.** An infrastructure error is a *review item*, never a zero.

## Architecture

```
            ┌──────────── cli.py ────────────┐   ┌──── web/app.py ────┐
            │ argparse, exit codes, tables   │   │ Flask, Jinja, CSP  │
            └───────────────┬────────────────┘   └─────────┬──────────┘
                            └───────────────┬──────────────┘
                                            ▼
   spec.py ─► grader.py ─► fetch.py / gitinfo.py / sandbox.py / checks.py / similarity.py
                 │
                 ▼
            store.py  ◄──►  run directory:  run.json (raw) · overrides.json (audit) · derived reports
                 │
                 ▼
            report.py  →  report.json · CSV (generic/Canvas/Moodle) · JUnit · Markdown
```

The only mutable state is the run directory. There is no database: files are diff-able, greppable, easy to back up and attach to a ticket, and need no migration story.

The key function is `rescore(run, overrides)`. It is pure: raw result + human decisions in, scores + triage + summary out. Reports, the web UI and the CLI all derive their view this way, so they cannot disagree.

## Decisions

### Rubric-as-code, with `requires`
A spec is YAML with typed checks. It is loaded and validated eagerly, and **all** errors are reported at once, because a typo found halfway through grading forty repositories is expensive.

`requires: [builds]` expresses dependency: if the build fails, the tests are *failed without running* (fast, and the evidence says why). If the dependency *errored* (an infrastructure fault) the dependent errors too and goes to review, so a Docker hiccup never turns into "your tests failed".

*Rejected:* a Python plugin API for checks. Powerful, but it makes specs executable, hard to review, and impossible to accept from a web upload. Six built-in check types plus `command` (which can run anything inside the sandbox) cover the real cases.

### One `git clone`, not the GitHub API
The previous version made one Contents API call per directory and per file: slow, rate-limited, no history, no build files. A clone gives the real tree, the history, and no API quota. The graded SHA is recorded. Credentials go through `GIT_CONFIG_*` environment variables, not argv (invisible to `ps`), and only for `github.com`.

Full history is only fetched when the rubric has a `git` check. Lateness needs just the tip commit, so every other spec gets a depth-1 fetch, and the report says `shallow: true` with `null` history counts rather than reporting misleading ones. (Found by timing a real 17 MB repository: three parallel full clones took 1m42s.)

### Forensics before execution
Student code runs a build step; a build step can rewrite `.git`. So history, similarity fingerprints and the README are read from the clone *before* the sandbox starts, and the sandbox receives a copy that has no `.git` at all.

### Sandbox: stream, don't mount
Bind-mounting a host directory into the container exposes a host path and has no size limit. Instead the container's `/work` is a size-capped tmpfs and the sources are streamed in with `tar | docker exec`. First attempt used `docker cp`; it writes *under* the tmpfs mount, so files were invisible. Caught by probing the real daemon before building on it. One container per submission is shared by all its commands, so a build is done once and tests reuse it.

`--read-only` was dropped because `docker cp`-style injection needs a writable layer. A non-root user with all capabilities dropped can't write the root filesystem anyway (tested).

*Rejected:* a silent Docker→host fallback (the old design). It converts "isolation unavailable" into "arbitrary code from strangers runs on your laptop" without telling anyone. Now it's exit code 3 and an explanation; `--sandbox none` exists but is explicit, loud, and documented as not a sandbox.

### Second runtime: Podman, and why not LXD
`DockerSandbox` became a thin subclass of `ContainerSandbox(binary)`; `PodmanSandbox` is the other. Every isolation flag is generated by one method and a table-driven test asserts the full set for both binaries, so a control cannot be dropped for one runtime. Podman differs in four ways that were handled deliberately: rootless user namespaces (`--user N:M` picks an unprivileged sub-uid, never the host uid; host-root Podman runs as `nobody`), tmpfs defaults to `noexec` (we already pass `exec`), `image exists` replaces `image inspect`, and, the important one, rootless Podman may *warn and run without* `--memory/--cpus/--pids-limit` when cgroup v2 controllers are not delegated. Silently unlimited execution violates the design, so `doctor`/preflight read `podman info` and each container's `memory.max`/`pids.max`/`cpu.max` are checked from inside; anything unreadable fails. **Honest status:** none of this could be run where it was written; Podman is verified only by the `podman` job in CI. *Not implemented:* LXD, because it runs system containers driven by `lxc exec`, not an OCI-compatible CLI, so it is a different design (image model, resource config, file transfer), not a third binary name.

### The bug only a real image could show
The first sandbox passed all its tests, and the demo graded correctly, but only against busybox and `--sandbox none`. Run against `python:3.12-slim` it failed *every* command criterion: GNU tar tries to set the mtime and mode of the root-owned `/work` mount and exits non-zero; busybox tar doesn't. Two lessons. First, the failure mode was right: the criteria came back as `error` and went to review, not to zero, so no student lost marks. Second, the test was wrong: a regression test now grades the whole demo through a real Debian-based image and asserts the result is identical to the host run. The archive is now built in Python (no `.` entry), which also removes the dependence on whatever `tar` the image ships.

### Cases: a crash can never pass
The first demo run gave a program with a syntax error 2 points for the case "input 0 → empty output", because a crashed process also prints nothing. Fixed by making exit code 0 the default requirement of every case, with a regression test. Worth remembering as a class of bug: **a check that can be satisfied by doing nothing is a bug**.

### Similarity: winnowing, with the false positives handled
Token-level k-gram fingerprints, winnowed (Schleimer et al.), with comments and literals removed and identifiers collapsed, so renamed variables don't hide a copy. The previous version ran `difflib.SequenceMatcher` over the whole codebases of every pair: quadratic, slow, and it flagged everyone who kept the teacher's starter file.

Now: starter code is subtracted; fingerprints shared by most of a large cohort are treated as boilerplate; an inverted index avoids comparing every pair; every flag carries the matching file pairs. A flag **only routes to review**. It never changes a grade, because a similarity score is evidence, not a verdict.

### Concurrency, retries and interruption
Three things that only matter once it is used for real. **Locking:** the CLI and the web UI can both write `overrides.json`; with the file lock removed, 4 of 6 simultaneous decisions were silently lost (measured, 3 runs), with it all 6 survive, and a test pins that. **Retry:** fetch failures and sandbox errors are the review items automation can fix itself once the cause is gone, so `markbook retry` re-grades only those, leaves everything else byte-identical, recomputes similarity from stored fingerprints, and refuses if the spec hash changed (partial re-grades against a different rubric would make grades incomparable). **Ctrl-C:** stops scheduling new submissions immediately, lets running ones finish and remove their containers, exits 130, writes nothing partial. The image is pulled once before the workers start, not by N workers racing.

### A queue made of files
To scale past one machine without adding a broker or database, the queue is a directory and the only primitives used are atomic `rename` (claim: one winner) and `link` (publish result: first wins). Workers call the existing `grade_submission`, and `collect` calls the existing `assemble_run`, so a distributed run cannot drift from a local one; a test grades the demo cohort both ways and compares scores, triage and similarity. The spec is frozen into the queue with its hash (and a tree hash of overlay/starter/cases files) and workers refuse to grade on mismatch, so every submission in a run is graded against one rubric. Crash recovery is lease-based: a heartbeat file per worker, expiry plus a margin for clock skew, and a retry cap so a repository that kills its worker becomes a visible `worker_lost` error instead of a loop. A late result from a worker wrongly presumed dead is discarded, not merged. Honest limits: the directory is a trust boundary (write access means arbitrary repos run on workers and results can be forged), tokens are never stored in it, and NFS/exotic filesystems are unverified. Missing jobs under `collect --partial` are `not_graded` errors, never zeros.

### CSV is an attack surface
A student can set their display name to `=HYPERLINK(...)`; opened in Excel, the export would run it on the grader's machine. Free-text cells starting with `= + - @` are written with a leading apostrophe; identifiers and grades are untouched so LMS matching still works. The first version of this test asserted the *vulnerable* behaviour ("round-trips unchanged") as correct, which is a good reminder that a passing test only proves the code does what the test author believed.

### AI as a reviewer, not a judge
The earlier project graded with an LLM. Here the model may only *suggest*, because a grade a teacher cannot explain or reproduce is worse than no automation. Design: two opt-ins (the spec marks the criterion `ai: true`, and the run passes `--ai`) because it sends student code to a third party; the criterion stays pending and the suggestion is shown beside it, so it **does not count toward the review-load reduction** (a human still decides); student code goes in as delimited data with break-outs neutralised and the model told to report, not obey, instructions inside it; output is schema-constrained and clamped; whole files only, with the omitted files listed and confidence capped when code wasn't seen; the audit log records the AI's number next to the human's, including disagreements. Mutation-checked: disabling the neutraliser and the clamp makes three tests fail. The request was verified against the real SDK's wire format with a local fake server; it has **not** been run against the live API.

### Dependencies without giving student code the network
Real assignments need `pytest` or a Maven cache, and the sandbox has no network. The obvious fix (install each student's requirements with network on) is wrong: `pip install` and `npm install` run student-controlled build scripts, so it would give strangers' code egress from the grading machine, including a route to anything that machine can reach. So the dependency step is **cohort-level and teacher-authored**: `prepare` runs once, with network, using only the spec's commands and files, and bakes the result into an image keyed on (base image id, commands, file contents). Students then run on that image with `--network none` as before. The price is explicit: a student's own requirements file is not honoured. Hardening that came with it: commands go through the Dockerfile JSON exec form (a newline or quote in a command cannot add an instruction; tested, and mutation-checked), image references are validated, `prepare` is refused in web uploads, `--sandbox none` refuses rather than installing onto the host, and a failing build stops the run before any student is graded.

### Per-test credit, and where it can be forged
`junit` gives partial credit from a test runner's JUnit XML, which is how most courses actually test. It inherits the rule "a check satisfiable by doing nothing is a bug": zero tests found earns nothing (`min_tests`, default 1), a committed report is deleted before the run, and the report is parsed defensively (size limit, no DOCTYPE/ENTITY, no symlink reads). What it cannot fix is that the runner imports the student's code in-process, so a determined student can forge the report from inside; the docs say so and point to `cases` (black-box) for high-stakes work.

### Triage is the product
`triage.state` is `auto` or `review`, and a review item always has a *reason code*: `criterion` (manual or errored), `similarity`, `borderline`, `fetch_failed`. `borderline` (score within a margin of the pass mark) exists because that is exactly where a human's time is best spent.

The summary reports **review load**: items that need a human versus the baseline of checking every criterion of every submission by hand. It is explicitly a *modelled* workload figure, with its definition embedded in the report. It is not a measured time saving, and nothing here claims it is. (The demo shows 78.6%; that number is a consequence of the demo having one manual criterion out of six, not a property of the tool.)

### Audit trail by construction
`run.json` is written once. `overrides.json` has two parts: an append-only `decisions` log (who, when, what was replaced) and an `effective` view derived from it. A test asserts the raw file is byte-identical after overrides. This makes disputes tractable: you can show what the machine said, what the human changed, and when.

### Grades are not released early
CSV grade columns are blank for `pending_review` and `error` submissions unless `--include-pending` is passed. The failure this prevents (a provisional score imported into a gradebook as final) is worse than a blank cell.

### Plain-file reports and a published schema
`report.json` has a JSON Schema (`markbook schema`) and a `schema_version`. The tests validate real output against it, including output with overrides applied, so the contract is enforced rather than aspirational. Also: JUnit XML so any CI can show a cohort natively, and CSV cells are written with Python's `csv` writer (a test round-trips hostile names).

### Rubric builder: choices in, vetted YAML out
Asking teachers to hand-write YAML contradicts "automated". One module (`specbuilder.build`) takes a plain model of choices and returns the spec, the YAML text, field-level problems and the total points; the web form and the terminal wizard are both thin front ends on it. The key property: the client never supplies a command. Commands are chosen server-side from a curated catalogue, names and images pass strict regexes, newline/tab become spaces (never deleted, so `"a\nb"` cannot collapse into a valid-looking `"ab"`), and the only free text that reaches a shell, extra packages, is gated behind `--allow-prepare` and a package-name regex. The output is validated by the ordinary `load_spec`, so a generated spec is held to the same bar as a hand-written one. Tests include hostile payloads, YAML round-trips, a mutation check that the packages gate really fails closed, and a jsdom test of the page.

### Web UI: server-rendered, strict, offline
No SPA, no CDN, no inline script or style (a test checks), strict CSP. A grading UI is a form-and-table application; server rendering is simpler to secure and to run offline. Progress is polled from `status.json` on disk, so a reload or a server restart never loses state (an interrupted run is marked failed on startup rather than hanging forever). Resolving a submission redirects to the next item in the review queue, because reviewing forty repositories should be a flow.

Security posture: no authentication (stated plainly in the README; binds to localhost, warns otherwise), cross-origin POST rejection, escaped evidence (test), uploaded specs confined to their own directory (test), local-path repositories refused in the web (test).

## Threat model, briefly

| Threat | Mitigation | Residual risk |
|---|---|---|
| Student code reads/modifies the host | Docker/Podman: no host mounts, non-root, caps dropped | Container-escape vulnerabilities in Docker/kernel; use gVisor/Kata/LXD for hostile cohorts |
| Fork bomb / memory hog / disk fill / output flood | `--pids-limit`, `--memory`, capped tmpfs, capped output, in-container `timeout -s KILL` | CPU contention between parallel submissions |
| Exfiltration | `--network none` | None by design (also why builds can't download dependencies) |
| Student forges history / replaces tests / escapes via symlink | History read before execution; sandbox gets no `.git`; overlay overrides student files; symlinks stripped; file checks refuse escaping paths | Commit *timestamps* are still author-controlled (documented) |
| Malicious repo URL / ref | https only (web), `-`-prefix rejected, `ext::`/`file:` transports disabled, hooks disabled | git itself |
| Credential leak | Token only in git's environment, only for github.com, never in argv or reports | Token readable by the process owner |
| XSS / CSRF in the web UI | Autoescaping + CSP; Origin / Sec-Fetch-Site check | No authentication: do not expose without a proxy |

## What is still weak

- **Dependencies are the teacher's, not the student's.** `prepare` installs teacher-chosen dependencies once, so a student's own requirements file is ignored, and a queue run's report does not yet record the prepared-image key (each worker builds its own image).
- **Timestamps**: lateness uses commit time. Pinning to a deadline-time `ref` from the roster works today; automating the capture of those refs from GitHub Classroom does not exist yet.
- **Similarity** is token-based and only as good as the token stream; short assignments give short fingerprints.
- **LMS formats** follow the documented layouts and are format-tested, but were not imported into a live Canvas or Moodle.
- **Scale**: `grade` is thread-per-submission on one machine. `enqueue`/`worker`/`collect` add a file-queue mode (see "A queue made of files"); it is verified for multiple processes on one machine only, not for multiple machines or NFS, and has no authentication.
- **The AI assist is untested live.** Wire format verified, behaviour tested with a fake client, but no real API call has been made from this codebase.
- **POSIX only.** The sandbox maps the host uid; the override lock uses `fcntl`.
- **Review load** is a model, not a measurement. The honest way to claim a time saving is to time a real grader with and without the queue.

## Five-minute walkthrough

1. `markbook doctor`, then `markbook demo`: the table, the 78.6% line, the copied pair, the late penalty, the unreachable repo that went to review instead of getting a zero.
2. `markbook review <run>` and `markbook show <run> bob`: the evidence behind a flag, and the exact command to resolve it.
3. `markbook override … -c quality -p 3 -m …`, then `markbook review` again: the queue shrinks; `run.json` is unchanged; `overrides.json` has the audit entry.
4. `markbook schema` and `grades.canvas.csv`: the integration surface.
5. `markbook demo --serve`: the same run in the browser, resolving items with the queue flow.
6. Open `sandbox.py` and `tests/test_docker_sandbox.py`: the isolation controls and the tests that prove them against a real daemon.
7. Say what's weak (above) before being asked.
