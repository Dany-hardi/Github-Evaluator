---
title: "Markbook: The Complete Documentation and Design Guide"
subtitle: "Rubric-as-code grading for Git repositories: what it is, why it exists, how every part works, and why it was built that way"
version: "Markbook 3.0.0 (report schema version 1)"
audience: "Engineers, maintainers, reviewers, teachers and operators"
---

# Markbook: The Complete Documentation and Design Guide

> **Edition:** Markbook 3.0.0 · report schema `1`
> **Repository:** `github.com/Dany-hardi/markbook` (formerly *GitHub Evaluator*)
> **Licence:** MIT
> **Scope:** the whole system: purpose, actors, architecture, every module, every algorithm, every guard rule, the web UI, the CLI, the CI pipelines, the test strategy, the security model, the design decisions and their rejected alternatives, and what is still unverified.

This guide is written so that an engineer who has never seen Markbook can read it from start to finish and then understand what the system is, why it is shaped the way it is, how to run it, how to change it safely, and where its limits are. It is long on purpose. It is organised so that you can also use it as a reference: every part stands alone, and Appendix A lists every constant and limit in one place.

**How to read it.**

| If you are… | Read first | Then |
|---|---|---|
| A teacher or course lead | Part 1, Part 5 (spec), Part 14 (rubric builder), Part 12 (review) | Part 13 (reports and LMS) |
| A developer joining the project | Part 1, Part 3 (architecture), Part 4 (lifecycle) | Parts 6–12, then Part 21 (tests) |
| A security reviewer | Part 8 (sandbox), Part 20 (security model and guard rules) | Part 9, Part 11, Part 15.6 |
| An operator or DevOps engineer | Part 16 (CLI), Part 17 (queue), Part 22 (CI/CD), Part 25 (operations) | Part 8 |
| An interviewer or evaluator | Part 1, Part 3, Part 23 (decisions), Part 24 (limits) | Part 20 |

**Conventions.** `monospace` is code, files, commands and identifiers. *Italic* introduces a term (defined again in the glossary, Appendix D). "Student code" always means code that was not written by the operator and must be treated as hostile. File paths are relative to the repository root unless stated.

**Honesty convention.** Every claim in this guide is meant to be true of the code. Where something is *modelled* rather than *measured*, or *designed* but *not verified* (for example the Canvas and Moodle imports, Podman outside CI, multi-machine queues, the live AI call), the text says so in plain words, and Part 24 collects them. A grading tool that overstates itself is worse than none.

---

# Contents

1. [Introduction: what Markbook is, why it exists, who it is for](#part-1-introduction)
2. [Core concepts and the data model](#part-2-core-concepts-and-the-data-model)
3. [Architecture](#part-3-architecture)
4. [The execution lifecycle, step by step](#part-4-the-execution-lifecycle-step-by-step)
5. [The spec: rubric-as-code reference](#part-5-the-spec-rubric-as-code-reference)
6. [The seven check types in depth](#part-6-the-seven-check-types-in-depth)
7. [Scoring, penalties, triage and review load](#part-7-scoring-penalties-triage-and-review-load)
8. [The sandbox](#part-8-the-sandbox)
9. [Fetching, git forensics and pinning](#part-9-fetching-git-forensics-and-pinning)
10. [Similarity detection](#part-10-similarity-detection)
11. [Dependencies without network: `prepare`](#part-11-dependencies-without-network-prepare)
12. [The run store, the audit trail, review and retry](#part-12-the-run-store-the-audit-trail-review-and-retry)
13. [Reports and LMS integration](#part-13-reports-and-lms-integration)
14. [The rubric builder and the terminal wizard](#part-14-the-rubric-builder-and-the-terminal-wizard)
15. [The web application](#part-15-the-web-application)
16. [The command-line interface](#part-16-the-command-line-interface)
17. [Scaling out: the file-based job queue](#part-17-scaling-out-the-file-based-job-queue)
18. [The optional AI reviewer assist and `ai-check`](#part-18-the-optional-ai-reviewer-assist-and-ai-check)
19. [Measuring the saving: `stats`, views and the protocol](#part-19-measuring-the-saving)
20. [Security model, guard rules, baselines and edge cases](#part-20-security-model-guard-rules-baselines-and-edge-cases)
21. [Testing strategy](#part-21-testing-strategy)
22. [CI/CD: the pipelines that exist and the ones to add](#part-22-cicd)
23. [Design decisions and rejected alternatives](#part-23-design-decisions-and-rejected-alternatives)
24. [Limitations, unverified claims and roadmap](#part-24-limitations-unverified-claims-and-roadmap)
25. [Operations guide: install, run, troubleshoot](#part-25-operations-guide)
26. [Appendices](#appendices)

---

# Part 1. Introduction

## 1.1 What Markbook is

**Markbook is a command-line tool, with a web interface on top of the same engine, that grades Git repositories against a rubric written as a file.**

Given three inputs:

1. a **spec**: a YAML file that lists the criteria, the points for each, the deadline, the late penalty and how code is to be run;
2. a **roster**: a CSV file that says who the students are and where their repositories are;
3. a **sandbox**: a container runtime (Docker or Podman) in which student code is run safely;

Markbook:

- clones each repository,
- reads its commit history *before* running any of its code,
- copies the files into a locked-down container (no network, no privileges, resource limits),
- runs the checks the spec describes (files present, build, output cases, the project's own tests, README quality, history shape),
- detects submissions that are copies of each other,
- applies late penalties,
- and writes a **grade per student with the evidence behind every point**, plus reports an LMS can import.

Then it does the part that distinguishes it from a script: it **tells the human exactly what it could not decide**. A criterion that needs judgement (design quality), a pair of near-identical submissions, a score within a hair of the pass mark, a repository that could not be fetched: each becomes an item in a short **review queue**. The human resolves those items; each decision is recorded in an **append-only audit log** and never overwrites the machine's original result.

## 1.2 The one-sentence design target

> **Make the machine's certainty explicit, and send humans only to the uncertain parts.**

Grading a cohort of repositories is mostly toil (does it build? do the tests pass? is there a README? was it on time? did two students hand in the same code?) with a thin layer of real judgement on top (is this design good?). A system that mixes the two yields a number nobody can defend. A system that automates only the toil still forces the human to re-check everything, because they cannot tell what the machine was unsure about. Markbook's output is therefore structured around *certainty*: every criterion says whether it is settled or needs a person, and why.

## 1.3 Why it was built

The project began as *GitHub Evaluator*, a desktop application that fetched repositories through the GitHub Contents API and scored them (partly with an LLM). It had real problems, and the rewrite (v3, renamed Markbook) was a response to each:

| Problem in the original | Consequence | What v3 does instead |
|---|---|---|
| One GitHub API call per directory and per file | Slow, rate-limited, no history, no build files | One `git clone` (depth 1 unless history is needed), records the graded commit SHA |
| An LLM produced the grade | A grade nobody can explain or reproduce | The model may only *suggest*, behind two opt-ins; a human decides; the audit log keeps both numbers |
| Student code could be run on the host when Docker was missing (silent fallback) | Arbitrary code from strangers on the teacher's laptop, unannounced | No silent fallback: exit code 3 and an explanation; `--sandbox none` is explicit and loud |
| Settings and results in a database and a GUI | Not scriptable, not reproducible, not reviewable | Rubric-as-code YAML; plain-file run directories; CLI-first |
| Similarity by pairwise `difflib` over whole trees | Quadratic, slow, flagged everyone who kept the starter file | Winnowing fingerprints with starter subtraction and boilerplate suppression |
| No notion of "what needs a human" | Teachers re-checked everything | Triage with reason codes; a review queue; a measurable review load |
| No audit | Disputes could not be settled | Immutable `run.json` plus append-only `overrides.json` |

Markbook is also, deliberately, a **demonstration of engineering method**: write down the principles, test against real runtimes rather than mocks, hunt for the bug a real image would reveal, and state what is not verified. Many sections of this guide recount bugs found that way, because the lessons are the point.

## 1.4 Who it is for: the actors

| Actor | Who they are | What they do with Markbook | What they must never have to do |
|---|---|---|---|
| **Teacher / course lead** | Owns the assignment and its rubric | Builds the rubric (form, wizard or YAML), supplies the roster, runs the grade, works the review queue, exports to the LMS | Write YAML by hand; re-check every submission; trust a number they cannot explain |
| **Teaching assistant / reviewer** | Resolves the queue | Opens flagged items, reads the evidence, types points and a comment | Edit raw results; guess what the machine was unsure about |
| **Operator / platform engineer** | Runs the machine or the server | Installs Markbook, picks Docker or Podman, runs `doctor`, decides on `--allow-prepare`, runs workers and the web server | Give student code network access; let a failed sandbox silently degrade |
| **Student** (indirect) | Authors the repository | Receives feedback files; their code is *the untrusted input* | Anything. Students never use Markbook; they are the threat model |
| **Developer / maintainer** | Changes Markbook | Adds check types, fixes bugs, keeps CI green | Break the audit guarantee or the isolation flags without a failing test |
| **LMS (Canvas, Moodle) and CI systems** | Machines that consume output | Import `grades.canvas.csv` / `grades.moodle.csv`; display `junit.xml` | Parse prose: they read a published schema |
| **Security reviewer / auditor** | Assesses risk | Reads the guard rules, the threat model, the isolation tests | Take claims on trust: each guard has a test |

## 1.5 What it can achieve

- **Grade a class of Git repositories reproducibly.** A grade is a function of `(spec hash, repository commit SHA, tool version)`, and all three are in the report.
- **Run untrusted code safely** inside throwaway containers with no network, no privileges and hard resource ceilings, and prove those controls with tests against a real daemon.
- **Reduce the human's checking to the genuinely uncertain items**, and report that reduction honestly (as a modelled figure, with the definition embedded in the report).
- **Produce evidence a student or an examiner can read**: per-criterion points, command output, the first differing line, the matching files in a similarity flag.
- **Integrate**: a published JSON Schema, generic/Canvas/Moodle CSVs, JUnit XML for CI dashboards, Markdown feedback per student.
- **Scale** from a laptop to several workers sharing a directory.
- **Be audited**: every human decision is attributable and time-stamped; the machine's result is byte-identical afterwards.
- **Be used without learning a file format**: a web form and a terminal wizard write the spec for the teacher.

## 1.6 What it deliberately does not do (non-goals)

| Non-goal | Why |
|---|---|
| Replace the teacher's judgement | A grade a human cannot explain is worse than no automation; the design routes judgement to humans |
| Let an AI assign grades | The model *suggests*; it can never apply anything |
| Install each student's own dependencies | `pip install`/`npm install` execute student-controlled scripts; granting them network access from the grader is exfiltration. Dependencies are the teacher's, installed once (Part 11) |
| Be a hardened multi-tenant platform | The web UI has no authentication; the sandbox is layered containment, not a guarantee against kernel escapes |
| Grade by timing or performance | Time limits exist to kill runaway code, not to rank speed |
| Detect plagiarism conclusively | Similarity is a routing signal; it never changes a grade |
| Support Windows | The sandbox maps the host uid and the override lock uses `fcntl` (POSIX only) |
| Provide a plugin API for checks | It would make specs executable, hard to review and impossible to accept from a web upload |

## 1.7 Baselines: what is true before anything runs

These are the invariants every run starts from. Each is enforced in code and covered by a test (Part 20 lists the tests).

1. **The raw result is immutable under review.** `run.json` is written when grading finishes. No human decision, report, export or web action ever rewrites it. The *only* operation that rewrites it is `markbook retry`, which replaces just the submissions that failed for infrastructure reasons (and refuses to run if the spec changed).
2. **Humans append, never replace.** Decisions go to `overrides.json` (an append-only `decisions` log plus a derived `effective` view).
3. **All views derive from one pure function.** `rescore(run, overrides)` turns raw result plus decisions into scores, triage and summary. The CLI, the web UI and every report call it, so they cannot disagree.
4. **Isolation is the default and never silently degrades.** If a container runtime was requested and is not usable, nothing is graded and nothing is written (exit 3).
5. **Evidence is read before code runs.** Git history, similarity fingerprints and the README are captured from the clone before the sandbox starts, and the sandbox receives a copy without `.git`.
6. **An infrastructure failure is a review item, never a zero.** A sandbox fault, a grader exception or a failed fetch routes to a human; it never costs a student marks silently.
7. **A check satisfiable by doing nothing is a bug.** A crashing program cannot pass a case; zero tests found earns nothing; a committed all-pass report is deleted.
8. **Provisional grades are not released.** CSV grade cells are blank for submissions that are pending review or errored, unless explicitly requested.
9. **Specs are validated eagerly and completely.** Every problem is listed at once before any student is graded.
10. **One engine, two front ends.** The CLI and the web UI call the same functions and read and write the same run directories.

---

# Part 2. Core concepts and the data model

## 2.1 Vocabulary in one page

| Term | Meaning |
|---|---|
| **Spec** | The rubric as a YAML file: name, scale, pass mark, deadline, late penalty, sandbox settings, similarity settings, optional `prepare`, `overlay`, `starter`, and a list of *criteria* |
| **Criterion** | One scored line of the rubric: an `id`, a title, positive points, a `check` and optional `requires` |
| **Check** | The procedure that scores a criterion: one of seven types (`file_exists`, `command`, `cases`, `junit`, `readme`, `git`, `manual`) |
| **Roster** | A CSV listing students: `id`, `name`, `email`, `repo` and optionally `ref` |
| **Submission** | One student's repository, graded; its record in the run |
| **Run** | One grading session: the immutable record of every submission, plus the assignment block, cohort similarity and summary |
| **Run directory** | The folder on disk holding a run and everything derived from it |
| **Outcome** | What a check returns: a *fraction* of the points, a *status*, *evidence* and an optional *review reason* |
| **Evidence** | A list of `{label, text}` pairs the grader saw: command, exit code, output, the first differing line |
| **Status** | `passed`, `partial`, `failed`, `error`, `manual` for a criterion; `graded` or `error` for a submission |
| **Triage** | `auto` (no human needed) or `review` (a human is needed), with *reason codes* |
| **Reason code** | Why a submission is in the queue: `criterion`, `similarity`, `borderline`, `fetch_failed` |
| **Override / decision** | A human's recorded action: set points, waive late, clear a flag, add a note |
| **Overlay** | Teacher files copied over the submission before checks, so students cannot replace the teacher's tests |
| **Starter** | The teacher's starter code; its fingerprints are subtracted in similarity |
| **Prepare** | The one cohort-level step that has network: installs the teacher's dependencies into a cached image |
| **Pin** | Recording a student's exact commit SHA at the deadline |
| **Review load** | A *modelled* workload figure: items needing a human versus checking every criterion of every submission by hand |
| **Sandbox** | The execution environment for student code: a Docker or Podman container, or (explicitly) the host |
| **Queue** | A directory of job files that several workers consume to grade in parallel |

## 2.2 The run record (`run.json`)

A run is one JSON document. Its shape (abridged) is:

```json
{
  "schema_version": "1",
  "tool": {"name": "markbook", "version": "3.0.0"},
  "run_id": "20260302-0900-ab12",
  "created_at": "2026-03-02T09:00:00+00:00",
  "finished_at": "2026-03-02T09:00:41+00:00",
  "assignment": { "...": "the rubric as graded (see 2.3)" },
  "submissions": [ { "...": "one per student (see 2.4)" } ],
  "similarity": [ { "a": "bob", "b": "carol", "score": 1.0, "shared_fingerprints": 41, "files": [ ... ] } ],
  "summary": { "...": "derived by summarize() (see 7.6)" },
  "inputs": { "spec_path": "/abs/spec.yaml", "allow_local": true }
}
```

`summary` and the per-submission `score` and `triage` blocks are *derived*: they are recomputed by `rescore` every time the run is read, so the raw file is a record of what the machine observed and the derived parts are always consistent with the current overrides.

## 2.3 The assignment block

`assignment_block(spec, runtime)` copies the facts a reader needs to interpret the run without the spec file:

| Field | Meaning |
|---|---|
| `name`, `version` | From the spec |
| `spec_sha256` | SHA-256 of the spec text: the identity of the rubric |
| `scale`, `max_points`, `pass_mark`, `borderline_margin` | Grading arithmetic |
| `deadline`, `late_penalty{percent_per_day, max_percent, grace_minutes}` | Lateness policy |
| `sandbox{runtime, image, prepared?}` | Where code ran; `prepared` records the dependency image, its key, commands and file digests |
| `criteria[]` | `{id, title, type, max_points}` for each criterion |

## 2.4 The submission record

| Field | Meaning |
|---|---|
| `id`, `name`, `email`, `repo`, `ref` | From the roster |
| `commit` | The full SHA that was graded (`null` if the fetch failed) |
| `status` | `graded` or `error` |
| `error` | `{code, message}` when the status is `error` (for example `not_found_or_private`, `too_large`, `internal_error`) |
| `criteria[]` | One *criterion record* per spec criterion (2.5) |
| `history` | `{commits, authors, active_days, largest_commit_share, shallow}`; counts are `null` and `shallow: true` for a depth-1 clone |
| `late` | `{deadline, last_commit, is_late, hours_late, penalty_pct, waived}` |
| `notes[]` | Human-readable notes from the grader (for example "removed 2 symlink(s)") |
| `similarity[]` | Pairs this submission is part of: `{with, with_name, score, files}` |
| `duration_s` | Wall time spent grading this submission |
| `score` | Derived: `{raw, penalty, final, max, scaled, scale, pending_points, complete}` |
| `triage` | Derived: `{state: auto\|review, reasons: [...]}` |
| `reviewer_note` | Derived from the latest note decision, if any |

## 2.5 The criterion record

| Field | Meaning |
|---|---|
| `id`, `title`, `type`, `max_points` | Identity and weight |
| `points` | Points the *machine* awarded (`fraction × max_points`, rounded to 2 places) |
| `status` | `passed`, `partial`, `failed`, `error`, `manual` |
| `evidence[]` | `{label, text}` entries; each `text` is clipped to 600 characters |
| `needs_review`, `review_reason` | Whether and why a human is needed |
| `override` | Filled in by `rescore` from the human decision, if any |
| `suggestion` | Optional AI suggestion (never applied automatically) |

## 2.6 Files on disk

A run directory is the only mutable state in the system. There is no database.

```
<run_dir>/
├── run.json            raw machine result (written at grading; only `retry` ever replaces it)
├── fingerprints.json   per-student similarity fingerprints (lets `retry` recompute cohort similarity)
├── overrides.json      {decisions: [...append-only...], effective: {...derived view...}}
├── report.json         derived: run with overrides applied, validated by the published schema
├── grades.csv          derived: one row per student
├── grades.canvas.csv   derived (with --lms canvas)
├── grades.moodle.csv   derived (with --lms moodle)
├── junit.xml           derived: one testsuite per student, one testcase per criterion
├── summary.md          derived: cohort statistics and the review queue
├── feedback/<id>.md    derived: per-student feedback
├── views.jsonl         optional: when a reviewer opened a submission page (web UI)
├── status.json         web only: progress of a running grade
├── input/              web only: the spec and roster that were submitted
└── .lock               zero-length file used for inter-process locking
```

Why files and not a database: files are diff-able, greppable, easy to back up and to attach to a ticket, and they need no migration story. The cost is that concurrency must be handled explicitly (Part 12.5).

---

# Part 3. Architecture

## 3.1 The picture

```
            ┌──────────────── cli.py ────────────────┐   ┌────── web/app.py ──────┐
            │ argparse · exit codes · tables · wizard │   │ Flask · Jinja · CSP    │
            └────────────────────┬────────────────────┘   └───────────┬────────────┘
                                 └──────────────────┬─────────────────┘
                                                    ▼
  specbuilder.py ──► spec.py ──► grader.py ──► fetch.py · gitinfo.py · sandbox.py · checks.py · similarity.py
  (choices→YAML)   (validate)       │                    prepare.py · ai.py
                                    ▼
                               store.py  ◄──►  run directory:  run.json · overrides.json · derived files
                                    │
                                    ▼
                               report.py  →  report.json · CSV (generic/Canvas/Moodle) · JUnit · Markdown
```

Reading the picture top to bottom: two front doors (the CLI and the web app) call one engine. The engine loads a validated spec, grades each submission through a fixed pipeline of small modules, and hands raw results to the store. Everything a human sees is derived by pure functions from the store's contents.

## 3.2 Layers and their responsibilities

| Layer | Modules | Responsibility | Must not |
|---|---|---|---|
| **Front doors** | `cli.py`, `web/app.py`, `wizard.py` | Parse input, call the engine, render output, map errors to exit codes or HTTP statuses | Contain grading logic |
| **Authoring** | `specbuilder.py`, `wizard.py`, `web/static/builder.js`, `web/templates/new.html` | Turn teacher *choices* into a validated spec | Accept commands from the client |
| **Specification** | `spec.py`, `roster.py` | Load and validate eagerly, with all errors at once | Touch the network or the filesystem beyond the spec's directory |
| **Engine** | `grader.py` | Orchestrate fetch → forensics → sandbox → checks → score → triage; cohort assembly | Edit stored results |
| **Acquisition** | `fetch.py`, `gitinfo.py`, `pin.py` | Clone safely; read history; record deadline SHAs | Execute student code |
| **Execution** | `sandbox.py`, `prepare.py` | Run commands in isolation; build the dependency image | Degrade silently |
| **Judgement** | `checks.py`, `similarity.py`, `ai.py` | Produce outcomes, flags and suggestions | Raise for ordinary failures; apply AI output |
| **Persistence** | `store.py`, `retry.py`, `views.py` | Atomic, locked, append-only writes; retry of failures | Rewrite `run.json` |
| **Presentation** | `report.py`, `stats.py`, templates | Pure functions of the *current* run | Hold state |
| **Scale-out** | `queue.py` | Move work between processes; never grade | Re-implement grading |

## 3.3 Module map (with approximate size)

| Module | Lines (approx.) | Role |
|---|---:|---|
| `markbook/cli.py` | 800+ | All commands, the banner, the exit-code mapping |
| `markbook/grader.py` | 420 | `grade_submission`, `rescore`, `summarize`, `preflight`, `grade_entries`, `assemble_run`, `grade_cohort` |
| `markbook/sandbox.py` | 470 | `run_capped`, `LocalSandbox`, `ContainerSandbox` (Docker/Podman), status and image helpers |
| `markbook/checks.py` | 320 | The seven checks and `REGISTRY` |
| `markbook/spec.py` | 375 | Dataclasses and `load_spec` |
| `markbook/queue.py` | 545 | The file-based job queue and workers |
| `markbook/web/app.py` | 455 | Flask routes, security headers, background grading thread |
| `markbook/specbuilder.py` | 415 | The rubric builder model: catalogue, `build()`, problems |
| `markbook/stats.py` | 315 | Review-effort statistics from the audit log |
| `markbook/wizard.py` | 300 | The interactive terminal wizard |
| `markbook/pin.py` | 300 | Deadline pinning with optional git bundles |
| `markbook/ai_check.py` | 295 | `markbook ai-check` (live smoke test) |
| `markbook/demo.py` | 295 | A hermetic seven-student cohort |
| `markbook/report.py` | 230 | Feedback, CSVs, JUnit, summary, `write_reports` |
| `markbook/ai.py` | 200 | The optional reviewer assist |
| `markbook/similarity.py` | 165 | Winnowing |
| `markbook/store.py` | 150 | Atomic writes, locking, decisions |
| `markbook/fetch.py` | 140 | Safe `git clone` |
| `markbook/prepare.py` | 130 | The dependency image |
| `markbook/roster.py` | 75 | Roster parsing |
| `markbook/gitinfo.py` | 65 | History facts |
| `markbook/retry.py` | 55 | Re-grade failures only |
| `markbook/views.py` | 45 | Optional view log |
| `markbook/schema/report.schema.json` | – | The published JSON Schema |
| `markbook/ai_fixtures/` | – | Synthetic submissions for `ai-check` |
| `markbook/web/templates/*.html` | – | `base`, `index`, `new`, `run`, `submission`, `progress`, `error`, `_ui` |
| `markbook/web/static/*` | – | `markbook.css`, `markbook.js`, `boot.js`, `builder.js`, icons, bundled font |

The package has one required dependency (**PyYAML**). Flask (web) and the Anthropic SDK (AI) are optional extras. This is deliberate: the CLI should install anywhere Python does.

## 3.4 The dependency rules

Modules depend downward only: front doors → engine → acquisition/execution/judgement → standard library. Three rules keep this honest:

1. **`checks.py` never imports the engine.** A check takes options and a `Context` (the working directory, the sandbox, the history, the default timeout) and returns an `Outcome`. This keeps checks unit-testable with fakes.
2. **`report.py`, `stats.py` and `rescore` are pure.** No I/O inside them; callers read and write. This is why a report can be regenerated at any time and always agree with the web UI.
3. **`queue.py` calls `grade_submission` and `assemble_run`, nothing else.** A distributed run therefore cannot drift from a local one; a test grades the demo cohort both ways and compares scores, triage and similarity.

## 3.5 Concurrency model

- **`grade_entries`** uses a `ThreadPoolExecutor` with `jobs` workers (default 4), one task per submission. Each task owns its temporary directory and its container, so tasks share nothing mutable except the progress callback.
- **Ctrl-C** (`KeyboardInterrupt`) stops *scheduling* new submissions (`cancel_futures=True`); submissions already running finish within their timeouts and remove their containers; the command exits 130 and writes nothing partial.
- **Last-resort guard:** any exception inside a task becomes a submission with `status: error` and `error.code: internal_error`. One bad repository can never sink the cohort.
- **Cross-process writers** (the CLI and the web UI both recording decisions) are serialised by `fcntl.flock` on `<run_dir>/.lock`.
- **The web server** grades in a daemon thread per run and communicates progress through `status.json`, which is written atomically; a reload or a server restart loses nothing.
- **The queue** uses two filesystem primitives and nothing else: atomic `rename` to claim and `link` to publish (Part 17).

## 3.6 Error philosophy

Markbook classifies failure by *who can fix it* and routes accordingly:

| Failure | Example | Result |
|---|---|---|
| **Student's problem** | The build fails; tests fail | A normal `failed` criterion with evidence. The student loses those points. |
| **Environment's problem** | Docker daemon down; image missing | Stop early with exit 3 and an actionable message. Nothing is graded. |
| **Infrastructure fault during grading** | Container fails to start for one submission; a check raises | `status: error`, `needs_review: true`, points `0` *pending review*; never a silent zero |
| **Unfetchable repository** | Private, deleted, network blip | `submission.status = error`; triage `fetch_failed`; `markbook retry` can fix it later |
| **Teacher's mistake** | Typo in the spec | `SpecError` listing *every* problem, before grading starts |
| **Hostile input** | Forged report, symlink, output flood | Defended by a specific guard (Part 20); the guard has a test |

The distinction between the first and third rows is the heart of the design: a student must never lose marks because *the grader* had a bad day.


---

# Part 4. The execution lifecycle, step by step

This part follows one `markbook grade` invocation from the keystroke to the files on disk, then follows one submission through the per-submission pipeline in detail, then shows the web path and the review loop. The numbered steps are the canonical lifecycle; every later part zooms into one of them.

## 4.1 Overview

```
 ① Author the rubric ──► ② Load & validate ──► ③ Preflight ──► ④ Per-submission pipeline (parallel)
                                                                        │
 ⑧ Review & override ◄── ⑦ Save & report ◄── ⑥ Score & triage ◄── ⑤ Cohort analysis
```

| # | Stage | Primary code | Output |
|---|---|---|---|
| ① | Author the rubric | `specbuilder.py`, `wizard.py`, `web/…/builder.js`, or a hand-written YAML | `spec.yaml` |
| ② | Load and validate spec and roster | `spec.load_spec`, `roster.load_roster` | `Spec`, `list[Entry]` or an error listing every problem |
| ③ | Preflight | `grader.preflight`, `sandbox.docker_status/podman_status/ensure_image`, `prepare.ensure_prepared` | Verified runtime, present image, built dependency image |
| ④ | Per-submission pipeline | `grader.grade_submission` and the modules it calls | One submission record plus its similarity corpus |
| ⑤ | Cohort analysis | `grader.assemble_run`, `similarity.compare` | Similarity pairs attached to submissions |
| ⑥ | Score and triage | `grader.rescore`, `_score_one`, `summarize` | Scores, triage reasons, summary, review load |
| ⑦ | Save and report | `store.save_run`, `store.save_corpora`, `report.write_reports` | The run directory |
| ⑧ | Review and override | `store.record_decision`, CLI `review/show/override`, web submission page | `overrides.json`, regenerated reports |

## 4.2 Stage ①: authoring the rubric

A teacher chooses one of three routes:

- **Form (web):** *New run → Build it with the form.* Ticking checks and setting points produces a model that the browser posts as JSON; `specbuilder.build` turns it into YAML (Part 14).
- **Wizard (terminal):** `markbook init DIR` asks questions and writes `spec.yaml` and `roster.csv` (Part 14.5).
- **By hand:** write YAML (Part 5). `markbook init --template python|c` writes a starting file.

All three routes end in the same place: a YAML file that `load_spec` must accept. The builder and the wizard *validate their own output through `load_spec`* before offering it, so a generated spec is held to the same bar as a hand-written one.

## 4.3 Stage ②: load and validate

`cmd_grade` begins with `load_spec(args.spec)` and `load_roster(args.roster)` (or builds entries from `--repo` options).

`load_spec` reads the YAML with `yaml.safe_load`, then checks everything and **collects all errors** before raising one `SpecError` whose message lists them (`  - criteria[tests]: …`). Eager, complete validation exists because a typo discovered halfway through grading forty repositories is expensive, and because a teacher fixing a spec should see the whole list once, not one error per attempt (details in Part 5.4).

`load_roster` accepts column aliases (Part 5.9), tolerates blank trailing rows, derives an id from the repository URL when none is given, makes ids filename-safe (`safe_id`), and rejects duplicate ids with the offending line number.

## 4.4 Stage ③: preflight

`grader.preflight(spec, runtime, log, build=True)` runs once, before any student is touched:

1. If the spec runs code (`spec.needs_sandbox`, i.e. any `command`, `cases` or `junit` criterion) and the runtime is `docker` or `podman`:
   - check the runtime is usable (`docker_status()` runs `docker info`; `podman_status()` parses `podman info --format json` and verifies rootless cgroup-v2 delegation of `memory`, `cpu` and `pids`);
   - if it is not usable, raise `SandboxUnavailable` with an actionable message (exit code 3). Nothing is graded; nothing is written;
   - `ensure_image(image)`: pull the base image **once**, here, instead of letting N parallel workers trigger N simultaneous pulls inside the grading window.
2. If the spec has a `prepare` block, build (or reuse) the dependency image (`prepare.ensure_prepared`; Part 11). A failing build stops the run here, before any student is graded.
3. Return the spec **with `sandbox.image` replaced by the prepared image tag**, so every later step simply runs on it.

## 4.5 Stage ④: the per-submission pipeline

`grade_entries` submits one `work(entry)` task per roster entry to a thread pool. Each task calls `grade_submission`. The pipeline below is the heart of the system; each numbered step is a place where a specific guard rule applies.

```
for each submission (in a private temp dir `markbook_sub_XXXX`):

  1  clone            fetch.checkout(repo, ref, shallow = no `git` check in the spec)
  2  size guard       > 200 MB  →  error "too_large"
  3  forensics        gitinfo.read_history(clone)      ← BEFORE any student code runs
  4  lateness         from the last commit time vs deadline, grace and cap
  5  fingerprints     similarity.read_corpus(workdir)  ← BEFORE any student code runs
  6  sanitise copy    copytree(workdir → work, ignore .git)  +  strip all symlinks
  7  overlay          teacher files copied over the submission (teacher wins)
  8  sandbox start    one container for the whole submission; stream sources in as a tar
  9  run criteria     in spec order, honouring `requires`; each check → Outcome
 10  AI suggestions   only for manual criteria marked `ai: true` and only with --ai
 11  cleanup          container removed, temp dir deleted (also on errors)
```

### Step 1: clone

`fetch.checkout(repo, dest, ref=…, token=…, allow_local=…, shallow=…)`.

- The repository string is normalised: `owner/repo` shorthand, `github.com/…`, `git@github.com:…` and `…/tree/<ref>/<subdir>` URLs are understood.
- **Local paths and `file://`** are refused unless `allow_local` is true (the CLI sets it; the web UI never does).
- **Only `https://` and `http://`** remote URLs are accepted; anything beginning with `-` (URL or ref) is rejected, which blocks option injection into `git`.
- **Hardening flags** on every git call: `-c core.hooksPath=/dev/null` (no hooks), `-c protocol.ext.allow=never` (no `ext::` transport), `-c protocol.file.allow=never` for remote URLs.
- **Credentials** travel only in the process environment as `GIT_CONFIG_COUNT/KEY_0/VALUE_0` setting an `http.extraHeader`, and only when the URL starts with `https://github.com/`. They never appear in `argv` (invisible to `ps`) and never in reports.
- **Shallow vs full:** history is needed only by `git` checks. Lateness needs just the tip commit, so every other spec gets `--depth 1`. A specific `ref` at depth 1 is fetched with `init` + `remote add` + `fetch --depth 1 origin <ref>` + `checkout --detach FETCH_HEAD`. (Measured: three parallel full clones of a 17 MB repository took 1 min 42 s, which is why shallow is the default.)
- Failures are classified into stable codes from git's stderr: `not_found_or_private`, `network`, `ref_not_found`, `clone_failed`, `invalid_url`, `timeout`, `empty_repository`, `subdir_not_found`.
- The **graded SHA** is recorded in `submission.commit`.

### Step 2: size guard

`_dir_size(workdir)` sums regular, non-symlink files; above `MAX_REPO_BYTES = 200 MiB` the submission becomes `error: too_large`. This protects the grader's disk and the tmpfs.

### Steps 3–5: forensics, lateness, fingerprints (before execution)

`read_history` runs `git log --numstat` once and builds `History` (commits, authors by lower-cased email, active days, last commit time, the largest single commit's share of changed lines). For a shallow clone the counts are reported as `null` with `shallow: true`, because a depth-1 history would produce *misleading* numbers.

`_late(spec, last_commit)` computes `over = last_commit − deadline − grace`; if positive, `days = ceil(over / 86400)` and `penalty_pct = min(max_percent, days × percent_per_day)`.

`similarity.read_corpus(workdir)` tokenises source files and winnows them into fingerprints (Part 10).

**Why before execution.** A build step runs student-controlled code; a build step can rewrite `.git`. Anything read from `.git` after the sandbox ran could have been forged. Reading first, from the clone on the host, closes that door. The sandbox additionally never receives `.git` at all.

### Steps 6–7: the sanitised copy and the overlay

`shutil.copytree(co.workdir, work, symlinks=True, ignore=ignore_patterns(".git"))`, then `_strip_symlinks(work)` removes every symlink (counting them into a note). Symlinks could redirect the grader's own overlay writes, or later reads, outside the tree. If `overlay` is set, its files are copied over the submission with `dirs_exist_ok=True`: **teacher files win**, so a student cannot replace the teacher's tests.

### Step 8: sandbox start

If the spec needs the sandbox, `make_sandbox(runtime, spec.sandbox)` creates a container object and `sandbox.load(work)` starts the container and streams the sources in (Part 8). A `SandboxUnavailable` raised *here* (for one submission) is captured as `sandbox_error`; code-running criteria for that submission become `error` + `needs_review` rather than zero.

### Step 9: run the criteria

`_run_criteria` walks the criteria in spec order, keeping a dictionary of results so `requires` can be honoured:

```python
blockers = [results[r] for r in c.requires if results[r]["status"] in ("failed", "error")]
if blockers:
    hard = any(b["status"] == "error" for b in blockers)
    rec["status"] = "error" if hard else "failed"       # failed → student's problem; error → infrastructure
    rec["evidence"] = [{"label": "skipped", "text": f"requires '{blockers[0]['id']}', which did not pass"}]
    if hard: rec.update(needs_review=True, review_reason=f"dependency '{blockers[0]['id']}' errored")
    continue
```

This encodes a subtle rule: if the build *failed* (the student's fault), dependent tests are failed without running (fast, with the reason in the evidence). If the build *errored* (an infrastructure fault), the dependent criterion errors too and goes to a human, so a Docker hiccup never turns into "your tests failed".

Each remaining check runs via `checks.REGISTRY[c.type](c.check, ctx)`. Any unexpected exception is caught, recorded as `status: error` with `"grader error"` evidence, routed to review, and printed with a traceback to stderr: *a grader fault is surfaced, never silently graded 0.*

### Step 10: AI suggestions

If a `Reviewer` was supplied (the run used `--ai`), manual criteria with `ai: true` receive a suggestion attached to the criterion record. Suggestions never affect scores (Part 18).

### Step 11: cleanup

In a `finally`: the sandbox is closed (container force-removed), the temp directory is deleted, and `duration_s` is recorded, on success and on every error path.

## 4.6 Stages ⑤–⑦: cohort analysis, scoring, saving

After all submissions finish, `assemble_run` clears any per-submission similarity, loads the starter corpus (if `starter` is set), calls `similarity.compare` over all corpora, attaches the resulting pairs to both parties, builds the run document and returns `rescore(run)`.

`cmd_grade` then:

1. `save_run(out, run)`: writes `run.json` atomically;
2. `save_corpora(out, corpora)`: writes `fingerprints.json` (so `retry` can recompute cohort similarity);
3. `run = current(out)`: re-reads and re-scores with any overrides (none yet);
4. `write_reports(run, out, lms=…, include_pending=…)`: writes every derived file;
5. prints the table (or JSON with `--format json`) and a `Next: markbook review …` hint;
6. returns `4` if `--fail-on-review` and anything needs review, else `0`.

## 4.7 Stage ⑧: the review loop

`markbook review RUN` lists what needs a human with the exact command to resolve each item. `markbook show RUN STUDENT` prints criteria, evidence and similar files. `markbook override RUN STUDENT -c CRITERION -p POINTS -m "…"` records a decision. After each decision the reports are rewritten from `current(run_dir)`; `run.json` is byte-identical before and after (Part 12).

## 4.8 The web path

The web application calls the same functions (Part 15). The differences are operational:

- The spec arrives from the builder form, a saved template, or an upload (confined to its own directory; `prepare` refused unless built by the server from vetted choices).
- The roster is parsed from pasted text or an uploaded file (max 1000 rows; local paths refused).
- Grading runs in a daemon thread; `status.json` carries `{state, name, total, done, active, started_at, error}`, polled by the progress page.
- A run that was `running` when the server stopped is marked `failed` on the next start, rather than hanging forever.

## 4.9 One submission, end to end: a worked trace

Take student `bob`, spec with criteria `files`, `build`, `cases`, `readme`, `git`, `quality(manual)`; deadline set; `sandbox.image = python:3.12-slim`.

1. `checkout("https://github.com/bob/lab1", …, shallow=False)` because the spec has a `git` check; HEAD is `cb46b374a3…`.
2. Size 1.2 MB: fine.
3. `read_history`: 3 commits, 3 active days, 1 author, largest commit 61% of lines.
4. Last commit is 30 h after the deadline; grace 0, 10%/day capped at 50% → `days = ceil(108000/86400) = 2` → `penalty_pct = 20`.
5. `read_corpus`: two Python files → 41 fingerprints.
6. Copy without `.git`; 0 symlinks removed.
7. Overlay copies `tests/` over his tree.
8. Container started; sources streamed in; image and flags as in Part 8.
9. `files` → passed (2/2). `build` → passed (3/3). `cases` → 3 of 4 cases pass → fraction 0.75 → 6.0/8, with `FAIL case 2: line 1: expected '4', got '5'` in the evidence. `readme` → missing the `usage` section → 3 of 4 conditions → 1.5/2. `git` → passed (1/1). `quality` → status `manual`, `needs_review`, 0 points pending.
10. No AI.
11. Container removed; temp deleted; `duration_s = 6.41`.
12. Later, similarity finds `bob ↔ carol` at 100%, attached to both.
13. `rescore`: raw = 2+3+6+1.5+1 = 13.5; pending = quality's points; penalty = 13.5 × 20% = 2.7; final = 10.8; scaled to the grade scale; triage reasons: `criterion: quality` and `similarity`.

Everything above is in `run.json`; the points the human later types for `quality` are in `overrides.json`.


---

# Part 5. The spec: rubric-as-code reference

## 5.1 Principles of the format

- **Data, not code.** A spec is YAML with typed checks. It is validated up front and hashed into every report. It is not a settings page and not a Python plugin.
- **Closed vocabulary.** There are exactly seven check types. Unknown keys are errors (`unknown check option(s) for 'cases': ['foo']`), which catches typos that would otherwise silently do nothing.
- **Anything can be run through `command`.** The `command` check runs any shell command inside the sandbox, so the closed vocabulary does not limit what can be checked; it limits what a spec can *do to the grader*.
- **Reviewable.** A spec is a text file you can diff, code-review and keep in Git next to the assignment.

## 5.2 Top-level keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | required | Assignment name (appears in reports and as the Canvas column header) |
| `version` | int | `1` | Your own revision number for the spec |
| `scale` | number > 0 | `100` | Grade out of this number; `scaled = final / max_points × scale` |
| `pass_mark` | number in `[0, scale]` | none | Enables `pass_rate` and the *borderline* review reason |
| `borderline_margin` | number | `0.02 × scale` when `pass_mark` is set | Half-width of the borderline band around the pass mark |
| `deadline` | ISO-8601 with timezone | none | Compared to the last commit time; a missing timezone is an error |
| `late_penalty` | mapping | none | `percent_per_day` (default 0), `max_percent` (default 100), `grace_minutes` (default 0); requires a `deadline` |
| `sandbox` | mapping | see 5.3 | How code runs |
| `prepare` | mapping | none | The cohort-level dependency step (Part 11) |
| `similarity` | mapping | enabled | `enabled` (true), `threshold` (0.6), `min_fingerprints` (20) |
| `overlay` | directory path | none | Teacher files copied over each submission |
| `starter` | directory path | none | Teacher starter code subtracted in similarity |
| `criteria` | list | required, non-empty | The rubric lines (5.5) |

## 5.3 The `sandbox` block

| Key | Default | Meaning |
|---|---|---|
| `image` | none | Container image. **Required when any criterion runs code.** Validated against `^[A-Za-z0-9][A-Za-z0-9._/:@+-]*$` |
| `timeout` | `10` | Default per-command timeout in seconds |
| `memory` | `"256m"` | Container memory limit (swap is set equal, so no swap) |
| `cpus` | `1.0` | CPU quota |
| `pids` | `128` | Maximum processes (fork-bomb ceiling) |
| `disk` | `"64m"` | Size of the writable `/work` tmpfs (the only writable area besides `/tmp`) |

## 5.4 What `load_spec` validates

`load_spec(path, *, confine=False)` returns a frozen `Spec` or raises `SpecError` with a bullet list. The list below is *every* class of check it performs; each is a line a teacher could trip over.

**Document level:** the file is readable; YAML parses; the root is a mapping.

**Top level:** `name` present; `scale` a positive number; `pass_mark` within `[0, scale]`; `deadline` ISO-8601 *with timezone* (stored as UTC); `late_penalty` requires a `deadline`; `sandbox.image` matches the image regex; `overlay`/`starter` exist as directories next to the spec (and, with `confine=True`, stay inside the spec directory); `criteria` is a non-empty list.

**Per criterion:** a mapping; `id` present, made only of letters, digits, `_` and `-`, and unique; `points` a positive number (booleans refused); `check.type` one of the seven; required keys for that type present; no unknown option for that type; `requires` may reference only criteria defined *earlier*.

**Per type:**
- `cases`: at least one case (inline or from `cases_file`); each case has `stdout`, `stdout_regex` or `exit`; `compare` is `trim`, `exact` or `tokens`; `cases_file` stays inside the spec directory and contains a list of cases (or a mapping with a `cases` list).
- `junit`: `report` is a safe relative path (no absolute path, no `..`); `min_tests` a non-negative integer.
- `file_exists`: `paths` is a list.

**Cross-cutting:** if any criterion runs code (`command`, `cases`, `junit`) then `sandbox.image` is required; if `prepare` is present it must be usable (some criterion runs code) and, if the spec was loaded with `confine=True` (a web upload), `prepare` is refused outright because it runs commands with network access.

**Identity:** `sha256` is the hash of the raw spec text; it is stored in every report and is how `retry` and the queue detect that the rubric changed.

## 5.5 Criteria

```yaml
criteria:
  - id: tests                    # letters, digits, _ and -
    title: Unit tests pass       # shown on reports; defaults to the id
    points: 10                   # positive number
    requires: [builds]           # optional: earlier criterion ids that must not have failed or errored
    check:
      type: junit
      run: "python -m pytest -q -p no:cacheprovider --junitxml=report.xml"
      min_tests: 5
      hidden: true
```

`Criterion` exposes `.type` and `.manual` for convenience; the whole `check` mapping is kept (copied) so that checks read their own options.

## 5.6 A complete example

```yaml
name: "TAF1: Blog API"
version: 1
scale: 20
pass_mark: 10
deadline: "2026-03-01T23:59:00+01:00"
late_penalty: {percent_per_day: 10, max_percent: 50, grace_minutes: 30}
sandbox: {image: "node:20", timeout: 20, memory: "512m", pids: 512}
prepare:
  files: [package.json, package-lock.json]
  run: ["npm ci --no-audit --no-fund"]
overlay: overlay/            # hidden tests the student never sees
similarity: {threshold: 0.6}
criteria:
  - {id: files,  title: Required files, points: 2,
     check: {type: file_exists, paths: [package.json, README.md, "src/*.js"]}}
  - {id: readme, title: README, points: 2,
     check: {type: readme, sections: [installation, usage], min_words: 80}}
  - {id: history, title: Incremental history, points: 1,
     check: {type: git, min_commits: 3, min_active_days: 2}}
  - {id: tests, title: API tests, points: 10, requires: [files],
     check: {type: junit, run: "node --test --test-reporter=junit --test-reporter-destination=report.xml", min_tests: 5, hidden: true}}
  - {id: design, title: Layers and naming, points: 5,
     check: {type: manual, guidance: "Separation of concerns, naming, error handling."}}
```

## 5.7 Spec-level paths and trust

`overlay`, `starter`, `cases_file` and `prepare.files` are paths relative to the spec's own directory. In a **trusted** spec (CLI, or an operator's saved specs directory) they may point anywhere inside that directory tree. In an **uploaded** spec (`confine=True`) they must stay inside the upload's directory. `prepare` is never accepted from an upload, because it runs commands with network access.

## 5.8 The `cases` file format

```yaml
# cases.yaml (via cases_file)
cases:
  - {name: zero, stdin: "0\n", stdout: "0"}
  - {name: add,  args: "2 3", stdout: "5"}
  - {name: secret, args: "9 9", stdout: "18", hidden: true}
  - {name: crashes_on_negative, args: "-1", exit: 2}
```

Each case may set `name`, `args` (appended to the command), `stdin`, `timeout`, `hidden`, and any of `stdout`, `stdout_regex`, `exit`. Unless `exit` is set, **exit code 0 is required**.

## 5.9 The roster

CSV with a header. Columns are matched by alias (case-insensitive, spaces become `_`):

| Field | Accepted column names |
|---|---|
| repository (required) | `repo`, `repository`, `url`, `github`, `github_url`, `repo_url` |
| id | `id`, `student_id`, `login`, `username`, `sis_login_id`, `identifier`, `matricule` |
| name | `name`, `full_name`, `student`, `student_name` |
| email | `email`, `email_address`, `mail` |
| ref | `ref`, `commit`, `branch`, `sha` |

- Rows without a repository are skipped (blank trailing rows are tolerated).
- A missing `id` is derived from the last two URL path segments; every id is passed through `safe_id` (`[^A-Za-z0-9._-]+` → `_`, trimmed) so it is safe in filenames and URLs.
- Duplicate ids are an error with the line number.
- `ref` selects a branch, tag or commit to grade; `pin` (Part 9.6) fills it with the deadline SHA.
- The file is read as `utf-8-sig`, so a spreadsheet's byte-order mark is harmless.

---

# Part 6. The seven check types in depth

Every check has the signature `check(opts, ctx) -> Outcome`, where `Outcome(fraction, status, evidence, review)`. The grader converts `fraction` into points: `points = round(fraction × criterion.points, 2)`. `status` is derived from the fraction: `passed` (≥ 1), `failed` (≤ 0), `partial` otherwise, except `manual` and `error`.

**Rule for all checks:** ordinary failure is a *result*, not an exception. A failing build is `failed` with evidence. An unexpected exception is caught by the grader and becomes `error` + review. Every `Evidence.text` is clipped to 600 characters (with a "… N more chars" suffix) so reports stay readable and bounded.

## 6.1 `file_exists`

**Options:** `paths` (list of globs).
**Algorithm:** list every regular file under the working directory whose resolved path stays inside the tree (blocking symlink escapes), as POSIX relative paths. For each pattern, a hit is any file where `fnmatch(relpath, pattern)` or `fnmatch(basename, pattern)` is true.
**Award:** `found / total` patterns; `1.0` if no patterns.
**Evidence:** `found: a, b`; `missing: c` when any.
**Notes:** matching the basename lets `README.md` match a file in a subdirectory; use a path pattern (`src/*.py`) to require a location. It does not run code, so it needs no sandbox.

## 6.2 `command`

**Options:** `run`, `expect_exit` (default `0`), `stdout_contains`, `stdin`, `timeout`.
**Algorithm:** run `run` in the sandbox with optional stdin and the timeout (the check's, or the spec default). Collect problems: timed out; exit code differs from expected; stdout does not contain the needle.
**Award:** all or nothing: `1.0` if no problems, else `0.0` with a `problem` evidence line first.
**Evidence:** `command`, then `exit_code` or `timeout`, `stdout`, `stderr`, and a note if output was truncated.
**Typical uses:** "does it build" (`gcc -o prog *.c`), "does it start" (`node --check index.js`), "does it print a banner".

## 6.3 `cases`

**Options:** `run`, `cases` (inline and/or via `cases_file`), `compare` (`trim` default, `exact`, `tokens`), `timeout`.
**Algorithm:** for each case, build `cmd = run + " " + args`, run it with the case's stdin and timeout, then collect reasons:

1. timed out → `timed out after Xs`;
2. else **exit code must equal the case's `exit` (default 0)** → *a crash can never pass*;
3. if `stdout` is given: normalise both sides with the comparison mode and compare;
4. if `stdout_regex` is given: `re.search(..., re.MULTILINE)` on stdout.

**Normalisation:** `exact` leaves text unchanged; `tokens` collapses all whitespace to single spaces; `trim` strips the ends and right-trims each line.
**Award:** `passed / total`.
**Evidence:** a `summary` line (`3/4 cases passed`), then one `FAIL <name>` line per failing case with the first differing line (`line 2: expected '4', got '5'`), or just `output differs` for `hidden` cases (so expected output never leaks), plus a clipped stderr for non-hidden failures.
**The bug this design encodes.** The first demo run gave a program with a syntax error 2 points for the case "input 0 → empty output", because a crashed process also prints nothing. Making exit 0 the default requirement fixed it, and a regression test pins it. The general lesson: *a check that can be satisfied by doing nothing is a bug.*

## 6.4 `junit`

**Options:** `run`, `report` (default `report.xml`), `min_tests` (default 1), `hidden`, `timeout`.
**Purpose:** per-test partial credit from a real test suite. Works with anything that writes JUnit XML: `pytest --junitxml=…`, Maven/Gradle surefire, `ctest --output-junit`, `gotestsum`, `cargo nextest`, Node's built-in test runner with the JUnit reporter.

**Algorithm:**

1. Validate `report` with `safe_relative` (relative, no `..`, no NUL); otherwise `error` + review.
2. **Delete any report the student committed** (`rm -f -- <report>`): a student could ship a forged all-pass file and rely on the test command crashing before it writes a real one.
3. Run the test command. A timeout is `failed`.
4. Read the report with `sandbox.read_file(rel, 1 MiB)`; missing, unsafe or oversize is `failed` with the reason.
5. `parse_junit(text)`: refuse any `<!DOCTYPE` or `<!ENTITY` (entity-expansion defence); parse with `ElementTree`; for each `testcase`, status is `failed` if it has `failure` or `error`, `skipped` if it has `skipped` (and no failure), else `passed`; the name is `classname::name`.
6. `counted` = non-skipped tests. If `len(counted) < min_tests` → `failed` with "found N test(s), expected at least M: no tests ran, so nothing is earned".
7. `fraction = passed / len(counted)`.

**Evidence:** `summary` (`7/9 tests passed (1 skipped)`), up to 10 failing tests (`FAIL <name>: <message>`), a note for any beyond 10, then `command` and process output. With `hidden: true`, failing tests appear as `FAIL (hidden) test N` with no name or message.

**Trust limit (stated plainly).** The test runner imports the student's code in the same process, so a determined student can forge the report from inside their own code. `cases` does not have this weakness, because the program is a black box. For high-stakes grading prefer `cases` with hidden, varied inputs, or run the teacher's tests through `overlay` and treat `junit` as a convenience.

## 6.5 `readme`

**Options:** `sections` (headings to require), `min_words`, `forbid` (regexes).
**Algorithm:** find the first file at the repository root whose lower-cased name starts with `readme` (and resolves inside the tree). If none: `failed`, "no README at the repository root". Otherwise assemble weighted-equal conditions:

- *README present* (always one condition);
- `min_words`: `len(re.findall(r"\w+", text)) >= min_words`;
- one condition per required section: the lower-cased section name must appear *inside* some Markdown heading (`^\s{0,3}#{1,6}\s+…`);
- one failing condition per `forbid` regex that matches (case-insensitive); a non-matching forbid adds no condition.

**Award:** fraction of conditions met. **Evidence:** `✓`/`✗` per condition with detail (`120 words (min 80)`, `no matching heading`).
**Note:** matching is by heading text containment, so `sections: [install]` matches "Installation".

## 6.6 `git`

**Options:** `min_commits`, `min_active_days`, `min_authors`, `max_single_commit_share` (a fraction, e.g. `0.8`).
**Inputs:** the `History` captured before execution.
**Algorithm:** each configured option is a condition: `commits ≥ min`, `active days ≥ min`, `authors ≥ min`, `largest commit share ≤ max`. If no option is set the check passes with a note.
**Award:** fraction of conditions met. **Evidence:** `✓ commits: 5 (min 3)` etc.
**Notes and limits.**
- *Authors* are counted by lower-cased email, falling back to author name.
- *Largest commit share* is the largest commit's changed lines (insertions + deletions from `--numstat`, binary files ignored) divided by total changed lines; it rewards incremental work over a single last-minute dump.
- A spec with a `git` check forces a full clone (up to 2000 commits are read).
- History timestamps are **author-controlled**; use `pin` (Part 9.6) when lateness matters.

## 6.7 `manual`

**Options:** `guidance`, `ai` (bool).
**Behaviour:** never scores. Returns `status: manual`, `fraction: 0`, evidence `guidance`, and `review: "manual criterion"`. Its points count as *pending* until a human sets them, and its presence guarantees the submission appears in the review queue.
**Optional AI:** with `ai: true` and `--ai`, a *suggestion* is attached (Part 18). It never changes the points.

## 6.8 Choosing between `command`, `cases`, `junit`

| Need | Use | Why |
|---|---|---|
| "It compiles / starts" | `command` | Exit code is the whole story |
| "Prints the right thing for these inputs", black-box, high stakes | `cases` (with `hidden` cases) | The program is a black box; cannot be forged from inside |
| "Passes the project's tests", per-test credit | `junit` | Partial credit; convenient; forgeable by a determined student |
| "The teacher's hidden tests" | `overlay` + `junit` or `command` | Teacher files overwrite student files, so tests cannot be replaced |


---

# Part 7. Scoring, penalties, triage and review load

All scoring is done by one pure function, `rescore(run, overrides)`, which deep-copies the run, scores every submission with `_score_one`, and recomputes `summarize`. Every consumer (CLI tables, web pages, CSVs, feedback files, JUnit) calls it, so they cannot disagree.

## 7.1 The scoring algorithm (`_score_one`)

Inputs: a submission, the assignment block and that submission's *effective override* (`criteria`, `cleared`, `late_waived`, `note`).

**If the submission errored** (`status == "error"`, e.g. unfetchable): score is `null` everywhere (`complete: false`, `pending_points = max_points`), and triage is `review` with a single reason `fetch_failed: <code>: <message>`.

**Otherwise:**

1. For each criterion `c`:
   - `needs_human = c.status in ("manual", "error")`
   - if a human override exists: `raw += override.points`; `needs_review = false`
   - elif `needs_human`: `pending += c.max_points`; `needs_review = true`; add reason `{code: "criterion", criterion: id, detail: review_reason}`
   - else: `raw += c.points`
2. **Late penalty:** `late.waived = bool(override.late_waived)`; `pct = 0 if waived else late.penalty_pct`; `penalty = round(raw × pct / 100, 2)`; `final = round(raw − penalty, 2)`.
3. **Scale:** `scaled = round(final / max_points × scale, 2)` (or `0.0` if `max_points` is zero).
4. **Complete:** `complete = (pending == 0)`.
5. **Similarity flag:** unless the human cleared `similarity`, if the submission has pairs, add reason `similarity: "<top score>% fingerprint overlap with <name>"`.
6. **Borderline flag:** if complete, a `pass_mark` exists, `borderline` was not cleared, and `|scaled − pass_mark| ≤ borderline_margin`, add reason `borderline: "<scaled> is within <margin> of the pass mark <pass>"`.
7. **Triage:** `state = "review" if reasons else "auto"`.

Key properties:

- The penalty is applied to the points *earned or assigned so far* (raw), so a late submission with a pending manual criterion has its penalty re-applied correctly when the human fills the criterion in.
- A pending criterion contributes zero to `raw` but is excluded from "complete", so a provisional grade is always labelled provisional.
- Overrides replace the machine's number for that criterion; they never alter evidence.

## 7.2 Worked example

Assignment: `scale 20`, criteria points `files 2`, `cases 8`, `readme 2`, `git 2`, `quality (manual) 3` → `max_points = 17`. Deadline policy: 10 % per day, cap 50 %.

Student: files 2/2, cases 3 of 4 (6/8), readme 2 of 4 conditions (1/2), git 2/2, quality pending; last commit 30 h late.

| Quantity | Value |
|---|---|
| `raw` | 2 + 6 + 1 + 2 = **11** (quality pending) |
| `pending` | 3 → `complete = false` |
| days late | `ceil(108000 s / 86400) = 2` → `penalty_pct = min(50, 2 × 10) = 20` |
| `penalty` | 11 × 20 % = 2.2 |
| `final` | 8.8 |
| `scaled` | `8.8 / 17 × 20 = 10.35` — shown as **provisional 10.35 / 20** |
| triage | `review`: criterion `quality` |

The reviewer sets `quality = 2`:

| Quantity | Value |
|---|---|
| `raw` | 11 + 2 = **13**; `pending = 0` → `complete = true` |
| `penalty` | 13 × 20 % = 2.6; `final = 10.4` |
| `scaled` | `10.4 / 17 × 20 = 12.24` |
| triage | `auto` (unless similarity or borderline applies) |

With `pass_mark: 10` and default margin `0.02 × 20 = 0.4`, the first (provisional) value 10.35 is *not* borderline because the submission is not complete; the final 12.24 is not within 0.4 of 10, so no borderline flag. Had the final landed at 10.2, the borderline flag would route it to a human, because that is where a human's time is best spent.

## 7.3 Lateness in detail

```
over = (last_commit_time − deadline).total_seconds() − grace_minutes × 60
if over > 0:  days = ceil(over / 86400);  penalty_pct = min(max_percent, days × percent_per_day)
```

- "Day" is any started 24-hour block after the grace period. One minute late with no grace is one full day late.
- `late.hours_late` is `over / 3600` rounded to one decimal, for the feedback sentence ("Submitted 30 h after the deadline (−20 %)").
- If there is no deadline, or no commit (empty history), nothing is late.
- A reviewer can **waive** lateness (`--waive-late`) or restore it; both are audited.
- **Trust limit:** `last_commit` is a commit timestamp, which a student controls. For evidence-grade lateness, run `markbook pin --at now` right when the deadline passes (Part 9.6).

## 7.4 Triage: reason codes

| Code | Raised when | Typical resolution |
|---|---|---|
| `criterion` | A criterion is `manual`, or `error` (infrastructure fault) and has no override | `override -c <id> -p <points>` |
| `similarity` | The submission is in at least one flagged pair and the flag is not cleared | Read the matching files; `--clear similarity -m "…"` if benign |
| `borderline` | Complete, a pass mark exists, and the score is within the margin | Review marginal cases; `--clear borderline` |
| `fetch_failed` | The repository could not be fetched or exceeded the size cap | Fix access, then `markbook retry` |

A submission with at least one reason is in the queue. `triage.state == "auto"` means nothing about it needs a human.

## 7.5 Why "borderline" exists

Automation is least trustworthy where a point either way changes the outcome. Routing scores within a margin of the pass mark to a human spends human attention exactly where it has the most leverage. It is a cheap, explainable rule, and the margin is configurable (`borderline_margin`).

## 7.6 The summary and review load

`summarize(run)` computes:

| Field | Meaning |
|---|---|
| `submissions`, `graded`, `errors`, `complete` | Counts |
| `mean`, `median`, `min`, `max` | Over *complete* submissions' scaled grades |
| `provisional_mean` | Mean over graded submissions including provisional scores (labelled `*` in the UI) |
| `pass_rate` | Share of complete submissions at or above the pass mark (if set) |
| `late`, `similarity_flags` | Counts of late submissions and flagged submissions |
| `ai_assist` | Number of suggestions and token usage, with a note that they do not reduce review load |
| `review_load` | See below |

**Review load** is the headline figure:

```
baseline_items  = (number of submissions) × (number of criteria)          # checking every criterion by hand
review_items    = fetch failures
                + unresolved criterion reasons
                + similarity flags + borderline flags
reduction_pct   = round(100 × (1 − review_items / baseline_items), 1)
```

The report embeds the definition string. **It is a modelled workload figure, not a measured time saving**, and nothing in Markbook claims otherwise. The demo cohort shows `78.6 %` (9 review items against a baseline of 42) because it has one manual criterion out of six; that number is a property of the demo's rubric, not of the tool. Part 19 describes how to measure a real saving.

AI suggestions do **not** reduce review items: a human still decides every one.


---

# Part 8. The sandbox

Student code is untrusted. Treat the grader as a system that runs arbitrary code from strangers. This part documents exactly how Markbook contains it.

## 8.1 Runtimes

| Runtime | Selected with | Isolation | Intended use |
|---|---|---|---|
| **Docker** | `--sandbox docker` (default) | One throwaway container per submission | Normal use |
| **Podman** | `--sandbox podman` | The same controls through Podman (daemonless, rootless by default), plus read-back of cgroup limits | Normal use where Podman is preferred; verified by the CI `podman` job |
| **none** | `--sandbox none` | None. `ulimit` CPU-time and file-size limits plus a scrubbed environment | Inside an *already isolated* environment (CI job, VM, LXD container) and for tests. **It is not a sandbox**; a warning is printed |

There is **no silent fallback** from a container runtime to the host. If you ask for Docker or Podman and it is unusable, `preflight` raises `SandboxUnavailable` and the CLI exits 3. A silent fallback converts "isolation unavailable" into "arbitrary code from strangers runs on your laptop without telling anyone"; the original GitHub Evaluator did exactly that.

LXD is *not* implemented. It runs system containers driven by `lxc exec`, not an OCI-compatible CLI, so it is a different design (image model, resource configuration, file transfer), not a third binary name.

## 8.2 The container, flag by flag

`ContainerSandbox.run_argv()` is pure and unit-tested for both binaries. Every isolation flag comes from this one method, and a table-driven test asserts the full set for Docker and Podman, so a control cannot be dropped for one runtime.

```
<docker|podman> run -d --name markbook-<10 hex> --label markbook=1
  --network none
  --cap-drop ALL
  --security-opt no-new-privileges
  --memory <m> --memory-swap <m>
  --cpus <c>
  --pids-limit <p>
  --user <uid>:<gid>
  --tmpfs /tmp:rw,exec,size=64m
  --tmpfs /work:rw,exec,size=<disk>,mode=1777
  -w /work  -e HOME=/tmp  -e LANG=C.UTF-8
  <image> sleep 3600
```

| Flag | Threat addressed | Notes |
|---|---|---|
| `--network none` | Exfiltration; attacking other hosts; downloading payloads | No exceptions. The only networked step in all of Markbook is `prepare`, which runs *teacher* commands |
| `--cap-drop ALL` | Privilege abuse within the container (raw sockets, mounts, `chown`…) | Nothing is added back |
| `--security-opt no-new-privileges` | `setuid` binaries gaining privileges | |
| `--memory M --memory-swap M` | Memory hogs and swap thrash | Swap equals memory, so no swap |
| `--cpus C` | CPU starvation of parallel submissions | CPU *contention* between parallel submissions remains a residual risk |
| `--pids-limit P` | Fork bombs | Default 128 |
| `--user uid:gid` | Running as root inside the container | The host user id (Docker); a sub-uid or `nobody` for Podman (see 8.4) |
| `--tmpfs /work:…size=<disk>,mode=1777` | Disk fill; any host path exposure | The *only* writable work area is a RAM-backed, size-capped tmpfs. No host directory is mounted |
| `--tmpfs /tmp:…size=64m` | Same, for scratch | `exec` is explicit so builds can run |
| `--label markbook=1` | Orphan management | `markbook doctor --clean` removes leftovers from a killed run |
| `sleep 3600` as PID 1 | – | The container is a long-lived shell host; the hour is an upper bound, and it is removed after the submission |

`--read-only` was deliberately **not** used: the streaming load needs a writable layer for the tar extraction path, and a non-root user with all capabilities dropped cannot write the root filesystem anyway (tested).

## 8.3 Streaming sources in (no bind mounts)

Bind-mounting a host directory into the container exposes a host path and has no size limit. Markbook instead:

1. starts the container with an empty, size-capped `/work` tmpfs;
2. builds a **tar archive in Python** (`tarfile.open(fileobj=proc.stdin, mode="w|")`) of the sanitised copy and pipes it into `docker|podman exec -i -w /work <name> tar -xf -`.

Details that matter:

- The archive has **no `.` entry**. GNU tar (Debian/Ubuntu images) tries to set the mtime and mode of the root-owned `/work` mount itself and exits non-zero; busybox tar does not. Building the archive in Python removes the dependence on whatever `tar` the image ships. (This was the bug that only a real image revealed: see 23.7.)
- Members are normalised (`uid = gid = 0`, mode forced readable/writable, directories executable). Tar run as a non-root user does not `chown`, so files end up owned by the student uid under Docker and under rootless Podman's sub-uid alike.
- On Python < 3.13, `communicate()` raised "flush of closed file" if `proc.stdin` was still set after being closed; the code sets `proc.stdin = None` after closing.
- If extraction fails the message says "does the image have `tar`?".

An earlier attempt used `docker cp`; it writes *under* the tmpfs mount, so the files were invisible. It was caught by probing the real daemon before building on it.

## 8.4 Running commands

```python
argv = [binary, "exec", "-i", "-w", "/work", name, "timeout", "-s", "KILL", str(t), "sh", "-c", cmd]
res = run_capped(argv, stdin=stdin, timeout=t + 15, cap=cap)
if res.exit_code == 137 and res.duration >= t - 0.2:
    res.timed_out, res.exit_code = True, None
```

- `timeout -s KILL` runs **inside** the container, so a runaway process is really killed rather than abandoned by the container client. The outer wait is `t + 15` as a safety margin.
- Exit code 137 (128+9) near the deadline is interpreted as a timeout, and reported as such.
- **Output cap:** `run_capped` drains stdout and stderr incrementally in two threads, keeping at most **64 KiB** per stream and setting a `truncated` flag. A program printing gigabytes cannot exhaust the grader's memory (`communicate()` would).
- stdin is fed in a separate thread and closed; `BrokenPipeError` is ignored.
- One container serves *all* of a submission's commands, so a build is done once and tests reuse it.

## 8.5 Docker vs Podman: the four real differences

| Difference | Handling |
|---|---|
| **Rootless user namespaces.** The host user is container root | `--user N:M` selects a *sub-uid* in the namespace: an unprivileged id that owns nothing on the host (stronger than `--userns=keep-id`). Started as host root, Podman uses `65534:65534` (`nobody`), never container root |
| **tmpfs defaults to `noexec`** | `exec` is passed explicitly |
| **`image exists` instead of `image inspect`** | `image_present` picks the right command |
| **Rootless Podman may warn and run *without* resource limits** when cgroup v2 controllers are not delegated | `_verify_limits()` reads `memory.max`, `memory.swap.max`, `pids.max` and `cpu.max` from inside the container; `max`, missing or unreadable values raise `SandboxUnavailable`. `podman_status()` also checks `podman info --format json` for cgroup v2 and delegated `memory`, `cpu`, `pids`. Anything unparsable is a failure, never a guess |

Other Podman handling: short image names are not resolved without a TTY, so errors mention fully qualified names (`docker.io/library/python:3.12-slim`); `close()` runs `podman kill` before `podman rm -f`, because PID 1 is `sleep` which ignores SIGTERM and `rm -f` would wait out its 10-second stop timeout per submission.

**Honest status:** none of the Podman handling could be exercised on the maintainer's machines. It is verified by the `podman` job in CI (Part 22.2), which fails if the Podman tests were skipped.

## 8.6 `LocalSandbox` (`--sandbox none`)

Runs commands on the host in a temporary directory with:

- `ulimit -t <t+2>` (CPU seconds) and `ulimit -f 65536` (file size),
- a scrubbed environment: only `PATH`, `HOME` (the work dir), `LANG`, `TMPDIR`,
- a new session, so the whole process group can be killed on timeout (`os.killpg`),
- `read_file` that **refuses symlinks** outright and anything that escapes the work directory (a planted symlink could point at any host file).

It is for environments that are already isolated and for the test suite. The CLI prints a yellow warning unless the command is the demo.

## 8.7 Lifecycle helpers

| Function | Purpose |
|---|---|
| `docker_status()` / `podman_status()` | `(usable, detail)` for `doctor` and preflight; `docker info` with a 10 s timeout; `podman info` JSON parse with cgroup checks |
| `image_present(binary, image)` | Present locally? |
| `ensure_image(image, log, binary)` | Pull once, up front, with a 15-minute timeout and an actionable error |
| `remove_orphans(binary)` | Force-remove containers labelled `markbook=1` left by a killed run (`doctor --clean`) |
| `ContainerSandbox.close()` | Force-remove this submission's container, also on errors |
| `safe_relative(path)` | Normalised relative path, or `None` if absolute, empty, containing NUL, or escaping with `..` |

## 8.8 What the sandbox does not protect against

The honest residual risks (also in Part 20):

- Container-escape vulnerabilities in Docker, runc or the kernel. For a hostile audience use a stronger boundary (gVisor, Kata, LXD virtual machines).
- CPU contention between parallel submissions (limits cap each, not the sum).
- With `--sandbox none` there is no isolation at all.

---

# Part 9. Fetching, git forensics and pinning

## 9.1 Why one `git clone`

The earlier design made one GitHub Contents API call per directory and per file: slow, rate-limited, no history, no build files. A clone gives the real tree, the history, and no API quota. The graded SHA is recorded so the grade is reproducible.

## 9.2 The clone strategy

| Situation | Commands |
|---|---|
| No `git` check, no ref | `git clone --quiet --no-tags --depth 1 -- <url> <dest>` |
| No `git` check, a ref | `git init`; `git remote add origin <url>`; `git fetch --quiet --no-tags --depth 1 origin <ref>`; `git checkout --detach FETCH_HEAD` |
| A `git` check (history needed) | `git clone --quiet --no-tags -- <url> <dest>` (full), then if a ref: `rev-parse --verify <ref>^{commit}` and `checkout --detach` |
| Local path (`allow_local`) | Plain `git clone` (git ignores `--depth` for plain local paths) |

Timeout: 180 s for the network operations, 60 s for the small ones. `sha = git rev-parse HEAD`; a failure means `empty_repository`. `--is-shallow-repository` tells the grader whether history numbers are available.

A `…/tree/<ref>/<subdir>` URL grades only that subdirectory; a subdirectory that does not exist, or resolves outside the clone, is `subdir_not_found`.

## 9.3 Hardening summary

| Guard | Mechanism |
|---|---|
| No hooks | `-c core.hooksPath=/dev/null` |
| No exotic transports | `-c protocol.ext.allow=never`; `protocol.file.allow=never` for remote URLs |
| No option injection | URL or ref starting with `-` rejected; `--` before the URL |
| Scheme allow-list | `https://`, `http://`; local paths only with `allow_local` |
| Terminal prompts off | `GIT_TERMINAL_PROMPT=0`; `GIT_CONFIG_NOSYSTEM=1` |
| Token hygiene | Only in `GIT_CONFIG_*` env as an `http.extraHeader`, only for `https://github.com/`; never argv, never reports |
| Web UI | Local paths refused; HTTPS only |

## 9.4 History facts (`gitinfo.py`)

One `git log --max-count=2000 --numstat --format=<RS>%H<US>%an<US>%ae<US>%ct` call, parsed into `Commit(sha, author, email, when, lines)`. `lines` sums insertions and deletions per commit; binary files (shown as `-`) are ignored. `History` exposes `count`, `authors` (lower-cased email, else name), `active_days` (distinct UTC dates), `last_commit`, and `max_single_commit_share`. A failing `git log` yields an empty history rather than an exception.

## 9.5 Forensics before execution

The ordering in Part 4.5 steps 3–5 is the guarantee: everything read from `.git`, plus the similarity corpus, is captured from the clone *before* the sandbox exists, and the sandbox gets a copy without `.git`. A build step can rewrite `.git`, so history read after execution could be forged.

## 9.6 Pinning: evidence-grade deadlines

Commit timestamps are author-controlled, and a student can keep pushing after the deadline. The defence is to record the commit SHA once, at a moment the student cannot influence, and then grade *that* ref (`grade` already checks out the roster's `ref` column and records the SHA).

```bash
markbook pin spec.yaml --roster roster.csv --at now --bundle bundles/     # run when the deadline passes
markbook pin spec.yaml --roster roster.csv --at deadline                  # retroactive, best-effort
markbook grade spec.yaml --roster roster.pinned.csv
```

| Mode | How | Trust |
|---|---|---|
| `now` | `git ls-remote` against each repository (no clone) and take the tip of the requested ref or `HEAD` | The remote's state at capture time: commit-timestamp tricks cannot backdate it. This is the evidence-grade mode |
| `deadline` | Bare blobless clone; take the last commit whose *committer date* is ≤ the deadline | Still trusts commit timestamps, which a student can set freely: a best-effort fallback, **not evidence** |

Details:

- A full 40/64-hex SHA in the roster's `ref` is kept as given (`via: given`), with `ls-remote` proving the repository is reachable and the SHA advertised (`verified`).
- An abbreviated SHA falls back to a clone to resolve it.
- `--bundle DIR` stores a `git bundle` of each pinned commit (`<id>-<sha12>.bundle`, verified with `bundle verify` and `list-heads`), so the evidence survives a force-push or deletion. A failed bundle keeps the pin but marks `bundle_failed` and `verified: false`.
- Outputs: the **original roster is never overwritten** (`--out` must differ; default `<roster>.pinned.csv`), every original column is preserved, `ref` and `pinned_at` are added, and `pins.json` records the manifest (mode, deadline, per-student sha, via, captured_at, verified, bundle, error).
- Failures (`not_found_or_private`, `no_commit_before_deadline`, …) leave the original `ref` untouched and `pinned_at` empty, and the command exits **5** ("wrote its outputs, but some repositories could not be pinned").

---

# Part 10. Similarity detection

## 10.1 Goal and stance

Detect submissions that share most of their code, **without** making false accusations. A flag is a *routing signal*: it adds a `similarity` reason to triage and never changes a grade. Every flag carries the file pairs that matched so a human can verify it in seconds.

## 10.2 Why winnowing

The previous version ran `difflib.SequenceMatcher` over the whole codebases of every pair: quadratic, slow, and it flagged everyone who kept the teacher's starter file. Markbook now uses **winnowing** (Schleimer, Wilkerson and Aiken, 2003), the document-fingerprinting algorithm behind MOSS.

## 10.3 The pipeline

**1. Tokenise** (`tokenize(text, ext)`): remove comments and string literals; map every identifier that is not a language keyword to the single token `I`, every number to `N`; keep operators and punctuation. So renaming variables or rewording comments does not hide a copy.

- Python: triple-quoted docstrings replaced; `#` comments removed.
- C-like (`.c .h .cpp .cc .hpp .java .js .ts .go .rs .cs .kt .swift`): `//` and `/* */` removed.
- Hash-like (`.py .rb .sh`): `#` comments removed.
- Files over 200 kB, symlinks, and directories such as `.git`, `node_modules`, `venv`, `build`, `dist`, `target`, `vendor`, `.idea`, `.vscode`, `__pycache__` are skipped.

**2. k-grams:** every window of `K = 6` tokens is hashed with BLAKE2b (8-byte digest).

**3. Winnow:** slide a window of `W = 4` hashes over the sequence and keep the minimum hash in each window. The selected hashes are the file's *fingerprints*. (Guarantee: any shared substring of length ≥ `K + W − 1` tokens yields at least one shared fingerprint.) Fewer tokens than `K` gives an empty set.

**4. Corpus per submission:** `{relative file path: set(fingerprints)}`. It is saved in `fingerprints.json` so `retry` can recompute cohort similarity without re-cloning.

## 10.4 Comparing a cohort (`compare`)

1. **Subtract starter code:** fingerprints from `starter/` are ignored, so keeping the teacher's file is not similarity.
2. **Boilerplate suppression:** if the cohort has at least `common_min_cohort = 6` submissions, any fingerprint present in more than `common_share = 50 %` of them is also ignored (shared boilerplate such as a common `main` skeleton or a lecture helper).
3. **Inverted index:** map fingerprint → owners. Only fingerprints with 2 to 50 owners generate pairs (a fingerprint in more than 50 submissions is noise and would explode the pair count). This avoids comparing every pair.
4. **Score:** for a pair `(x, y)` with `n` shared fingerprints: `score = n / min(|fp(x)|, |fp(y)|)`, i.e. the fraction of the *smaller* submission that the other shares.
5. **Thresholds:** skip pairs whose smaller side has fewer than `min_fingerprints` (default 20) fingerprints (too small to be meaningful); keep pairs with `score ≥ threshold` (default 0.6).
6. **File pairs:** for each kept pair, the top 3 file-to-file matches with shared counts and scores.
7. Sort by `(−score, a, b)`.

## 10.5 What it can and cannot do

| Can | Cannot |
|---|---|
| See through variable renaming, comment edits, whitespace changes | See through restructuring that changes the token order |
| Ignore starter code and shared boilerplate | Distinguish copying from independent use of the same tutorial |
| Show *which files* matched | Prove intent: only a human can |
| Run in near-linear time through the index | Work on very short assignments (short fingerprint sets) |

Spec controls: `similarity: {enabled, threshold, min_fingerprints}`. A reviewer clears a benign flag with `override --clear similarity -m "Both used the lecture helper."`, which is audited.

---

# Part 11. Dependencies without network: `prepare`

## 11.1 The problem

Real assignments need dependencies (`pytest`, `requests`, a Maven cache, `npm ci`), but the student sandbox has no network on purpose. The tempting fix, installing each student's own `requirements.txt` with network access, is wrong: `pip install` and `npm install` execute student-controlled build scripts, so it would give strangers' code network access *from the grading machine*: exfiltration, and a route into anything that machine can reach.

## 11.2 The solution: a cohort-level, teacher-authored step

The `prepare` block declares the step. It runs **once per cohort**, **with network**, using **only teacher-authored commands and files**, and the result is baked into a **cached image**:

```yaml
sandbox: {image: python:3.12-slim}
prepare:
  files: [requirements.txt]     # next to the spec; copied into the build
  run: ["pip install --no-cache-dir -r requirements.txt"]
  timeout: 600
```

Every student container then starts from that image **exactly as before**: `--network none`, non-root, capabilities dropped, no host mounts. Student code never has network access. The price, stated plainly: *a student's own requirements file is not honoured.* The dependencies are the ones the teacher chose, which also makes grading repeatable and fair.

## 11.3 How the image is built and cached

`prepare.ensure_prepared(spec, runtime, log, force=False)`:

1. Refuse unless the runtime is Docker or Podman (with `--sandbox none` nothing can be built, and Markbook will not install packages onto the teacher's machine): exit 3 with an explanation.
2. `ensure_image(base)`, then read the base image id (`image inspect --format {{.Id}}`).
3. **Build key** = SHA-256 of a JSON document `{v, base, base_id, run[], files{path: sha256}}` with sorted keys. The tag is `markbook-prepared:<first 16 hex>`.
4. If the tag is present (and not `force`), reuse it: "dependencies already prepared".
5. Otherwise build in a temp context:
   - copy `prepare.files` into `files/` (re-checking each is still a regular file, not a symlink);
   - render the Dockerfile with `render_dockerfile` (below);
   - `docker|podman build -t <tag> --label markbook=prepared [--no-cache] <ctx>` with the spec's `prepare.timeout`.
6. A timeout or non-zero build raises `SandboxUnavailable` with the **tail of pip's/npm's own output** (last 4000 characters) and, for Podman, a hint about fully qualified names. No student is graded.
7. Return `dataclasses.replace(spec, sandbox=replace(spec.sandbox, image=tag), prepared=info)` with `info = {tag, key, base_image, commands, files(digests), cached}`. The report records it under `assignment.sandbox.prepared`.

**Dockerfile rendering (pure, unit-tested):**

```
FROM <base>
LABEL markbook.prepare.key=<key16>
WORKDIR /prepare
COPY files/ /prepare/          # only if prepare.files is non-empty
RUN ["sh", "-c", "<command 1>"]
RUN ["sh", "-c", "<command 2>"]
```

Commands go through the **JSON exec form** of `RUN` (`json.dumps(["sh", "-c", c])`), so a newline, quote or `$` in a command can never be re-interpreted by the Dockerfile parser: no injection through the spec. A test with a hostile command exists, and was mutation-checked (with raw `RUN` the test fails).

## 11.4 Guard rules specific to `prepare`

| Guard | Where |
|---|---|
| Refused in uploaded (web) specs | `load_spec(confine=True)` |
| Refused with `--sandbox none` | `ensure_prepared` |
| Files must be regular, non-symlink, ≤ 1 MiB, relative, no `..` | `_parse_prepare`, re-checked at build |
| Unknown `prepare` keys rejected | `_parse_prepare` |
| `prepare` with no code-running criterion rejected as pointless | `load_spec` |
| Web-built specs get `prepare` only from the builder's fixed catalogue; extra packages require `serve --allow-prepare` | `specbuilder`, `web/app.py` |
| A failing build stops the run before grading | `preflight` |
| Prepared images labelled `markbook=prepared` | Prune with `docker image prune --filter label=markbook=prepared -a` |

First run builds (measured at 14 s with a real `pip install pytest`); later runs reuse the cache (1.9 s). `markbook prepare SPEC [--force]` builds ahead of time.

**Known gap:** in queue mode each worker builds its own image, and the report does not yet record a single prepared-image key for the run (Part 24).


---

# Part 12. The run store, the audit trail, review and retry

## 12.1 Design

All state lives in a run directory (Part 2.6). The store module (`store.py`) is small on purpose: atomic writes, one lock, one append-only decision function, and loaders. There is no database.

| Function | Role |
|---|---|
| `save_run(dir, run)` / `load_run(dir)` | Write/read `run.json` (`StoreError` if the directory has no `run.json`) |
| `save_corpora` / `load_corpora` | Similarity fingerprints per student (`fingerprints.json`, sets stored as sorted lists) |
| `load_overrides(dir)` | `{"decisions": [], "effective": {}}` if none exist |
| `current(dir)` | `rescore(load_run(dir), effective_overrides(load_overrides(dir)))`: **the** way to see a run |
| `record_decision(dir, sub_id, …)` | Apply one human decision under the lock |

## 12.2 Atomic writes

`_write_atomic(path, data)` writes to a temporary file in the same directory (`mkstemp`, then `os.replace`). Readers therefore never see a half-written file, even if the process is killed mid-write. The web server, `status.json`, `pins.json` and the pinned roster use the same pattern.

## 12.3 The audit trail

`overrides.json` has two parts:

```json
{
  "decisions": [
    {"at": "2026-03-02T11:04:12+00:00", "reviewer": "ann", "submission": "bob", "comment": "Clean decomposition.",
     "action": "set_points", "criterion": "quality", "points": 3, "was": 0.0, "was_status": "manual",
     "ai": {"suggested_points": 2, "model": "claude-opus-5-5", "confidence": "medium", "accepted": false, "unchanged": false}}
  ],
  "effective": {
    "bob": {"criteria": {"quality": {"points": 3, "comment": "…", "reviewer": "ann", "at": "…", "ai_assisted": false}},
            "cleared": ["similarity"], "late_waived": false, "note": "…"}
  }
}
```

- **`decisions` is append-only.** Every action records who, when, the submission, the comment, and what it replaced (`was`, `was_status`).
- **`effective` is a derived view** of the latest decision per target, so `rescore` has a cheap lookup. It could always be rebuilt from `decisions`.
- **AI is always visible.** If the criterion had a suggestion, the entry records the AI's points, model and confidence, whether the human accepted it, and whether the final points equal the suggestion, *including disagreements*.
- A test asserts that `run.json` is byte-identical after a decision. That is what makes disputes tractable: you can show what the machine said, what the human changed, and when.

## 12.4 The actions

| Action | CLI | Effect |
|---|---|---|
| Set points | `override RUN STU -c CRIT -p N -m "…"` | `effective.criteria[CRIT] = {points, …}`; validated `0 ≤ points ≤ max_points` |
| Accept AI suggestion | `override RUN STU -c CRIT --accept-suggestion` | Points taken from the suggestion; marked `ai_assisted` |
| Waive lateness | `override RUN STU --waive-late` | `late_waived = true` (restorable) |
| Clear a flag | `override RUN STU --clear similarity\|borderline` | Adds to `cleared`; only these two flags are clearable |
| Note | `override RUN STU -m "…"` | Becomes `reviewer_note`; recorded as `note` |

A call that records nothing (no criterion, no waiver, no flag, no comment) is an error ("nothing to record"). Unknown submissions or criteria raise `StoreError`. The reviewer name is `--reviewer`, or `MARKBOOK_REVIEWER`, or the OS user (web: the "Reviewer" box, default `web`).

## 12.5 Concurrency: the file lock

The CLI and the web UI can both write `overrides.json` for the same run. A read-modify-write without a lock loses updates. `record_decision` wraps the whole operation in `_locked(run_dir)`, which takes `fcntl.flock(LOCK_EX)` on `<run_dir>/.lock`. **Measured:** with the lock removed, 4 of 6 simultaneous decisions were silently lost (three runs); with it, all 6 survive, and a test pins that. On a non-POSIX platform (`fcntl` unavailable) the lock is a no-op and a single writer is assumed, which is one of the reasons Windows is out of scope.

## 12.6 Reading a run: `rescore` as the single source of truth

The CLI `review`, `show`, `report`, the web run and submission pages, and every export call `current(run_dir)`. The raw run plus the decisions go in; scores, triage, summary and review load come out. If two views ever disagreed, it would be a bug in exactly one function.

## 12.7 `retry`: re-grade only what failed

Fetch failures (a private repository you have since been given access to, a network blip, a corrected ref) and criteria that errored (a sandbox fault) are exactly the review items automation can fix itself once the cause is gone.

```
failed_ids(run)  =  [ s.id for s in submissions if s.status == "error" or any(c.status == "error" for c in s.criteria) ]
```

`retry_failed(run_dir, runtime, …)`:

1. Load the run; if nothing failed, return unchanged.
2. Find the spec at `inputs.spec_path`; if it is gone, raise.
3. Load it and **refuse if `spec.sha256` differs from the run's `assignment.spec_sha256`**: re-grading part of a cohort against a different rubric would make grades incomparable. The message tells the operator to run the whole cohort again.
4. Preflight (runtime, image, prepare), then `grade_entries` for only the failed entries.
5. Splice the fresh submissions into the original list; **everything that graded cleanly stays byte-for-byte**.
6. Rebuild the corpora from `fingerprints.json`, replacing only the retried students, and call `assemble_run` so similarity is recomputed over the **whole** cohort.
7. The CLI saves the run, rewrites reports from `current(run_dir)` (so existing overrides still apply) and prints how many recovered and how many still fail.

`markbook runs` lists runs with how many items in each still need review.

## 12.8 The optional view log

`views.jsonl` (append-only, one JSON object per line: run, submission, reviewer, UTC time) is written when a reviewer opens a submission page in the web UI. It is privacy-light by design: no IP, no user agent, no page contents, no external requests. It exists only so `markbook stats` has session boundaries (Part 19). Everything works without it; a missing file or a damaged line is skipped, never fatal. The reviewer name is truncated to 60 characters.

---

# Part 13. Reports and LMS integration

All reports are **pure functions of the current run** (raw result plus overrides), written by `report.write_reports(run, out, lms=…, include_pending=…)`. They are regenerated after every decision, so a CSV on disk never lags the audit log.

## 13.1 The files

| File | Contents | Consumer |
|---|---|---|
| `report.json` | The whole run with overrides applied; validated against a published JSON Schema | Scripts, dashboards, other tools |
| `grades.csv` | One row per student (`id, name, email, repo, commit, status, grade, scale, late, penalty_pct, review, review_reasons`, then `pts_<criterion>` columns) | Spreadsheets |
| `grades.canvas.csv` | Canvas gradebook import (`--lms canvas`) | Canvas |
| `grades.moodle.csv` | Moodle offline grading worksheet (`--lms moodle`) | Moodle |
| `junit.xml` | One `<testsuite>` per student, one `<testcase>` per criterion | Any CI dashboard |
| `summary.md` | Cohort statistics, review load, queue, similarity pairs; ready to paste into a ticket | Humans |
| `feedback/<id>.md` | Per-student feedback, safe to send | Students |

## 13.2 The JSON contract

`report.json` carries `schema_version` (currently `"1"`). Consumers should ignore unknown properties; a breaking change bumps the version. `markbook schema` prints the JSON Schema (`markbook/schema/report.schema.json`, shipped in the package). The test suite **validates real output against the schema**, including output with overrides applied, so the contract is enforced, not aspirational.

## 13.3 `grades.csv` semantics

- `status`: `graded` (complete), `pending_review` (a human decision is outstanding) or `error`.
- `grade` is the **scaled** grade, and is **blank for `pending_review` and `error`** unless `--include-pending` is passed. A provisional score imported into a gradebook as final is worse than a blank cell, so the default is not to release it.
- `late` is boolean; `penalty_pct` is 0 when lateness was waived.
- `review` is `auto` or `review`; `review_reasons` is a `;`-joined list such as `criterion:quality; similarity`.
- `pts_<id>` shows each criterion's points, using the human override where one exists.

## 13.4 Canvas and Moodle

**Canvas** (`--lms canvas`): header `Student, ID, SIS User ID, SIS Login ID, Section, <assignment name>`, a `    Points Possible` row carrying the scale, then one row per student with the roster `id` in the `SIS Login ID` column and the grade in the assignment column. Put each student's Canvas login in the roster `id` column.

**Moodle** (`--lms moodle`): the offline grading worksheet layout `Identifier, Full name, Email address, Status, Grade, Maximum grade, Grade can be changed, Feedback comments`. Put the Moodle *identifier* in the roster `id` column. The feedback column is a one-line summary (`_short_feedback`): overrides' comments, failed or partial criteria with points, the late penalty, and "manual review pending".

> **Honesty:** both layouts were written from each platform's *documented* import format and are covered by format tests. They have **not** been verified against a live Canvas or Moodle instance. LMS exports vary by version and configuration. Do a test import into a sandbox course before relying on them for a real cohort.

## 13.5 CSV is an attack surface

A student can set their display name to `=HYPERLINK("http://evil","x")`; opened in Excel or LibreOffice, a naive export would run it on the grader's machine (OWASP "CSV injection"). `_cell` prefixes free-text cells that start with `=`, `+`, `-`, `@`, a tab or a carriage return with an apostrophe, which spreadsheets render as text. **Identifiers and numbers are not touched**, so LMS matching and grades are unaffected. The CSV is written with Python's `csv` writer, so quoting is correct, and a test round-trips hostile names. (Note for the record: the first version of that test asserted the *vulnerable* behaviour as correct, a reminder that a passing test only proves the code does what the test author believed.)

## 13.6 `junit.xml`

One `<testsuite name=<student id>>` per submission with `tests`, `failures`, `errors`. Each criterion becomes a `<testcase classname=<id> name=<title>>`: `passed` has no child; `manual` is `<skipped message="manual review"/>`; `failed`/`partial` is `<failure message="partial 6/8">` with the evidence as body; `error` is `<error>`. A fetch failure adds a `fetch` testcase with an `<error>`. Attributes are escaped with `quoteattr` and bodies with `escape`. Any CI system can then display a cohort natively.

## 13.7 Feedback files

`feedback_md(sub, assignment)` renders Markdown: the grade (or *provisional* with "manual review pending"), the graded commit, a lateness sentence with the penalty (or "waived"), a criterion table with points and icons (✅ 🟡 ❌ 📝 ⚠️), then a section per problem criterion listing only the evidence labels that help a student (`problem`, `missing`, `summary`, `FAIL…`, `✗…`) or the reviewer's override comment, and a "Reviewer note". **Hidden test details are never included**, because the evidence recorded for hidden cases already omits them. A submission that could not be graded gets a short "We could not grade this submission" with the error code and message.

## 13.8 `summary.md`

Title, run id, submission count, runtime and spec hash prefix; **Review load** (the modelled reduction, the item counts, the number of fully automatic submissions); **Grades** (complete count, mean, median, range, pass rate, provisional mean); **Review queue** (one bullet per submission with its reasons); **Similarity** (each pair with score and the top matching file pair).

## 13.9 Scripting

```bash
markbook grade spec.yaml --roster roster.csv --format json --quiet | jq '.summary.review_load'
markbook review RUN --json | jq -r '.[].id'
markbook report RUN --lms canvas --include-pending --out exports/
```

`--format json` prints the full report to stdout with progress on stderr, for piping.

---

# Part 14. The rubric builder and the terminal wizard

## 14.1 Why it exists

The spec is YAML. Asking a teacher to hand-write YAML contradicts "automated". The builder and the wizard ask for **choices** (what to check, how many points) and write the spec. The YAML is still shown live, downloadable and savable as a template; a power user can still hand-write one. The point of the design is that the web form and the terminal wizard are *thin front ends on one module* (`specbuilder.py`), so they cannot drift apart.

## 14.2 The central safety rule

> **The client never supplies a command.** The browser and the wizard send a small *model* of choices (a language, which checks are ticked, some numbers and short strings). The server writes every command, from a fixed catalogue.

The consequences: nothing a user types is interpreted as a shell command except two things that are validated strictly (the `run` strings the user *chooses to override*, which only ever run inside the sandbox, and extra package names, which are gated and pattern-checked). The `prepare` commands (the only networked step) are written by `specbuilder` and never taken from user text.

## 14.3 The model and the catalogue

The model is a plain dictionary:

```json
{
  "name": "TAF1: Blog API", "scale": 20, "pass_mark": 10,
  "deadline_local": "2026-03-01 23:59", "utc_offset": "+01:00",
  "late_percent_per_day": 10, "late_max_percent": 50,
  "language": "node", "image": "", "timeout": 15, "extra_packages": "",
  "criteria": [
    {"kind": "files",   "title": "Required files", "points": 2, "paths": "package.json\nREADME.md"},
    {"kind": "readme",  "points": 2, "sections": "installation, usage", "min_words": 80},
    {"kind": "tests",   "points": 10, "run": "", "min_tests": 5, "hidden": true},
    {"kind": "manual",  "points": 5, "guidance": "Layers and naming."}
  ]
}
```

**Languages** (`LANGUAGES`): `python` (`python:3.12-slim`, 256m, 128 pids; test command uses `pytest --junitxml`), `node` (`node:20`, 512m, 512 pids; Node's test runner with the JUnit reporter), `c` (`gcc:13`), `java` (`eclipse-temurin:21`), `custom` (you name the image). Each carries run, build and test *hints* used as defaults.

**Kinds** (`KINDS`): `files`, `readme`, `history`, `build`, `output`, `tests`, `manual`. Each has a label, default points, help text, and its fields (text, lines, number, select, checkbox, textarea, `cases`). Only `manual` may be added more than once.

## 14.4 `build(model, allow_packages=False) -> Built`

`Built` carries `spec` (dict), `yaml` (text), `problems` (list of `Problem(field, message)`), `total_points`, `needs_sandbox`, `uses_prepare`, and `.ok`.

What `build` does:

1. **Normalise text** with `_clean`: strip non-printable characters, limit length, and **turn newlines and tabs into spaces for single-line fields** (never delete them: "a⏎b" must not collapse into a valid-looking "ab"; found by a hostile-input test).
2. **Validate each field**, collecting *field-level* problems (`criteria.readme.min_words`, `name`, `scale`…), so the form can flag the exact input.
3. Map each ticked kind to a spec criterion: `files → file_exists`, `readme → readme`, `history → git`, `build → command`, `output → cases` (cases require expected output; a blank untouched row is ignored), `tests → junit`, `manual → manual`. Criterion ids are the kind (`tests`, `manual`, `manual-2`…).
4. For compiled languages (`c`, `java`, `custom`) with a build check, add `requires: [<build id>]` to cases and junit criteria, so a broken build makes running or testing pointless and says why.
5. Add `sandbox` (image, timeout, memory, pids) only if some criterion runs code.
6. Add a `prepare` block only from a fixed recipe: `pip install --no-cache-dir pytest [extra…]` for Python tests, `npm install --no-audit --no-fund <packages>` for Node packages (with `NODE_PATH=/prepare/node_modules` prefixed to run commands).
7. **Extra packages** are the one free-text input that reaches `prepare`: refused unless `allow_packages` (the operator started the server with `--allow-prepare`); Python or Node only; each must match `PIP_RE` or `NPM_RE`.
8. Deadline: `deadline_local` plus `utc_offset` (`Z` or `±HH:MM`) become an ISO timestamp with a timezone.
9. Emit YAML (`yaml.safe_dump`, with a header comment), then **validate it with `load_spec`**, the same validator the CLI uses. If that raises, the message becomes a problem and the YAML is withheld.

Limits: 30 criteria; 40 cases; 40 file paths; name 120 characters; text 500; expected output 2000.

## 14.5 The terminal wizard

`markbook init DIR` starts the wizard when run in a terminal (`--wizard` forces it, `--template python|c` writes a plain starting file). Under the hood: `run_wizard(dest, ask=input, say=print, force=False)`.

The conversation: assignment name → grade scale → pass mark → deadline (date, time zone offset, late penalty and cap) → language (numbered choice; a custom image if needed) → each check offered with a default (files, readme, history, build, output with test cases, tests, one or more manual criteria) → time limit and extra packages if code runs → "Add the students now?" with per-student id, name, email, repository URL.

Behaviours worth knowing:

- Pressing **Enter everywhere** yields a sensible default rubric (the pre-ticked checks).
- **Bad answers are re-asked** with a message ("Type a number from 1 to 5", "Deadline must look like 2026-03-01 23:59"), not failed.
- **Cancelling is clean:** Ctrl-C or end of input prints "Cancelled. Nothing was written." and exits 1, with no traceback.
- **Nothing is overwritten without asking** (`--force` overrides; an existing `roster.csv` is kept unless `--force`).
- A missing slash in a pasted URL (`https:github.com/…`) is repaired by `fix_repo`; only that known typo.
- With no students typed, a clearly-marked example row is written, not an empty file.
- After writing, the spec is **validated with `load_spec`** and the result is shown, with the next commands (`markbook validate`, `markbook grade`).
- Wizard prompts are injectable (`ask`, `say`), which is how it is tested.

## 14.6 The web form

*New run → Build it with the form* (three tabs: the form, a saved template, paste YAML). The form: an assignment fieldset (name, scale which follows the ticked total, pass mark, deadline with time zone and late penalty), check cards that switch on with a tick (with points inputs and per-check fields; output has a test-case repeater; manual can be added repeatedly), a "Where the code runs" fieldset (language, image, time limit, packages only when allowed), and a **sticky live preview** (total points, a problems list, the YAML, *Copy*, *Download spec.yaml*, *Save as a template*).

Front-end behaviour (`builder.js`, no framework):

- Debounced `POST /api/rubric/preview` on every edit; problems are mapped to the exact inputs (`aria-invalid`) via their field paths.
- **No scolding before the first edit:** the preview shows "Fill in the form and the rubric appears here." until the teacher touches something.
- Enter inside a text field does not submit the form; an invalid rubric cannot be submitted (the problem list is shown).
- On submit the model is written into a hidden `builder_json` field; the server rebuilds and re-validates it (the client's preview is a convenience, never trusted).
- Templates: *Save as a template* posts the model; the picker lists saved YAML files; *Edit* reloads the model (stored beside the YAML as `<name>.builder.json`).
- Timezone offset is prefilled from the browser.

## 14.7 Server endpoints for the builder

| Endpoint | Purpose |
|---|---|
| `POST /api/rubric/preview` | JSON model in; `{ok, yaml, problems, total_points, needs_sandbox, uses_prepare}` out |
| `POST /rubric/spec.yaml` | Form field `builder_json` in; the YAML as an attachment (400 if invalid) |
| `POST /api/rubric/template` | Save `{model, name}` as `<slug>.yaml` + `<slug>.builder.json` in the specs directory; never overwrites (409) |
| `GET /api/rubric/template/<name>` | The stored model for editing |
| `POST /runs/new` with `builder_json` | Start a run from the form; precedence: builder JSON → preset → uploaded/pasted YAML |

Security of these endpoints (Part 15.6): same-origin POST guard; a size cap (`MAX_BUILDER_JSON = 200 000`); hostile JSON is ignored, not trusted; template names are reduced to `[a-z0-9_-]` slugs (`^[a-z0-9][a-z0-9_-]{0,60}\.yaml$`) so traversal names such as `../../etc/passwd` cannot escape the specs folder; the `--allow-prepare` gate applies to starting a run and to saving a template, not just to the preview (a bug found by mutation testing: removing the gate failed only the preview test until a test was added for the other two paths).


---

# Part 15. The web application

## 15.1 Philosophy

A grading UI is a form-and-table application. Markbook's web layer is therefore **server-rendered, strict and offline**: Flask and Jinja templates, one CSS file, a few small JavaScript files, no framework, no CDN, no inline script or style, and no external requests (the typeface is bundled). Server rendering is simpler to secure and to run offline than a single-page app, and there is no state to synchronise: the run directory on disk is the state.

It is a **thin layer over the same engine** the CLI uses: it calls `grade_cohort`, `current`, `record_decision`, the report functions and `specbuilder`; it reads and writes the same run directories. A run started in the browser can be reviewed from the CLI and vice versa.

Install and start:

```bash
pip install '.[web]'
markbook serve --dir .markbook/runs --sandbox docker     # http://127.0.0.1:5000
markbook demo --serve                                    # demo cohort, then open the UI
```

`markbook serve` options: `--dir` (runs), `--specs` (a directory of saved rubric templates, default `<runs dir>/../specs`; trusted, so those specs may use `overlay`, `starter` and `cases_file`), `--host` (default `127.0.0.1`; any other host prints a no-authentication warning), `--port`, `--jobs`, `--token-env`, `--sandbox docker|podman|none`, `--allow-prepare`. The app factory is `create_app(runs_dir, *, runtime, jobs, token, specs_dir=None, allow_prepare=False)`.

## 15.2 Routes

| Method and path | Purpose |
|---|---|
| `GET /` | Home: run cards with how much of each run is resolved; a welcome page when there are no runs |
| `GET, POST /runs/new` | The New-run page (form builder, template, or paste/upload YAML; roster paste/upload) and the action that starts grading |
| `POST /api/rubric/preview` | Live YAML preview for the builder |
| `POST /rubric/spec.yaml` | Download the generated spec |
| `POST /api/rubric/template`, `GET /api/rubric/template/<name>` | Save and reload rubric templates |
| `GET /runs/<run_id>` | The run page (or the progress page while grading) |
| `GET /runs/<run_id>/status.json` | Progress polling (`state`, `done`, `total`, `active`) |
| `GET /runs/<run_id>/s/<sid>` | The review page for one submission |
| `POST /runs/<run_id>/s/<sid>/view` | Records a view event (session boundary for `stats`); returns 204 |
| `POST /runs/<run_id>/s/<sid>/decide` | Records a decision (points, accept AI, waive/restore late, clear flag, note) |
| `GET /runs/<run_id>/download/<name>` | `report.json`, `grades.csv`, `grades.canvas.csv`, `grades.moodle.csv`, `junit.xml`, `summary.md`, `feedback.zip` (`?pending=1` includes provisional grades) |
| `POST /runs/<run_id>/delete` | Delete a run directory |
| `GET /healthz` | `{status: ok, version}` for probes |

Run ids must match `^[A-Za-z0-9_-]{1,64}$` and resolve inside the runs directory. Run ids are generated as `YYYYMMDD-HHMMSS-<4 hex>`.

## 15.3 Starting a run from the browser

`POST /runs/new`:

1. Create `<runs>/<run_id>/input/`.
2. Resolve the spec, in this precedence: **builder JSON** (rebuilt by `specbuilder.build(model, allow_packages=allow_prepare)`, written by the server, loaded unconfined because the server wrote it) → **preset** (a saved spec in the operator's specs directory, trusted, may use `overlay`/`cases_file`) → **uploaded or pasted YAML** (loaded with `confine=True`: overlay/starter must stay inside the upload, `prepare` refused).
3. Parse the roster (`parse_roster`); reject empty rosters and more than 1000 rows.
4. `preflight(spec, runtime, build=False)`: verify the runtime and image quickly; the slow `prepare` build happens in the grading thread.
5. Write `input/roster.csv` and `status.json` (`state: running`).
6. Start a daemon thread that calls `grade_cohort(allow_local=False, …)`, updating `status.json` atomically as submissions start and finish, then `save_run`, `save_corpora`, `write_reports`, and sets `state: done`. Any exception becomes `state: failed` with the message.
7. Redirect to the run page, which shows live progress and then the results.

Any failure removes the half-created run directory and re-renders the form with the draft restored and the problems listed. Roster entries may only use `https://` repositories (never local paths).

## 15.4 The pages

**Home.** Cards per run with name, date, submission count, mean, and "N need review" with a resolved ring. A first-use welcome with a notebook-style hero ("Grade the repos. *Review* what matters."), the three steps (build the rubric, grade a roster, review what needs a person), and the demo command with a copy button.

**New run.** Described in Part 14.6. Sections: (1) the rubric (three tabs), (2) the roster (paste or drop a CSV), then *Start grading*.

**Run page.** Headline cards: review-load reduction with a ring (labelled as less manual review: "9 items need a human instead of 42"), mean grade (asterisk when provisional), fully automatic submissions, flags raised automatically. A grade-distribution chart (ten bins over the scale), the equivalent CLI commands with copy buttons ("Same thing from the terminal"), tabs for *Needs review*, *Similarity*, *All*, a filter box (`/`), and a sortable table (student, grade, triage badge, reasons). Buttons: *Start review · N left*, *Export* (the downloads above).

**Review page.** One card per criterion with its status badge, type, and the evidence as labelled rows; *Override points* controls; a similarity card listing matched file pairs, with a "Why is this fine?" comment box and *Mark reviewed*; a score ring and grade with a *provisional* badge while pending; history facts (commits, active days, authors); late controls (waive/restore); a note box; AI suggestion cards with *Accept* when present; the submission's own decision history. Resolving a submission **jumps to the next one in the queue**, because reviewing forty repositories should be a flow. `j` and `k` move between submissions, `/` focuses the filter, `?` lists the shortcuts.

**Progress page.** A ring and chips for the submissions being graded; polls `status.json`; a *failed* state shows the message.

**Error page.** 403, 404, 413 with plain messages.

## 15.5 The front-end code

| File | Role |
|---|---|
| `static/boot.js` | Runs synchronously in `<head>` before first paint: restores the saved theme (and applies `?theme=light|dark`), adds the `js` class, and decides whether the intro splash plays (once per session, home page only, never for reduced motion; `?splash=0|1` for screenshots) |
| `static/markbook.js` | Theme toggle; splash skip; reviewer name memory; confirmations; busy-button state; view beacon; copy-to-clipboard; table sort and filter; drop zones; keyboard shortcuts; the progress poll |
| `static/builder.js` | The rubric form (Part 14.6) |
| `static/markbook.css` | Design tokens first (light, dark, and `prefers-color-scheme: dark` fallback), then layout and components, then motion |
| `static/favicon.svg`, `apple-touch-icon.png`, `fonts/` | Icons and the bundled Fraunces typeface with its licence |

All scripts are external files, because the CSP forbids inline script. Storage use is wrapped in `try/catch` (private windows can block it) and the page renders correctly without it.

### Templates

`base.html` (head, splash, header, footer, help dialog), `index.html`, `new.html`, `run.html`, `submission.html`, `progress.html`, `error.html`, and `_ui.html` (macros: `mark`, `wordmark`, `ring`, `gbar`). Progress rings and grade bars are drawn with SVG attributes only, because the CSP forbids inline styles.

## 15.6 Web security

| Control | Detail |
|---|---|
| **Content-Security-Policy** | `default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; font-src 'self'; img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` |
| Other headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` |
| **Cross-origin POST guard** | `POST`/`PUT`/`DELETE` are refused (403) if `Sec-Fetch-Site: cross-site`, or if an `Origin` header's host differs from the request host |
| No inline script or style | A test fetches pages and asserts no `style=`, `<script>` with inline code, or `onclick=` |
| Escaping | Autoescaped templates; evidence is escaped (a test injects markup) |
| Uploads | Capped at 2 MB (413); rosters at 1000 rows |
| Spec confinement | Uploaded `overlay`/`starter`/`cases_file` must stay inside the upload's directory; `prepare` refused in uploads |
| Local paths | Refused for web rosters |
| Run ids | Strict regex; resolved inside the runs directory |
| Builder | The browser sends choices, never commands; strict regexes; the `--allow-prepare` gate |
| Template names | Slugged; strict filename regex; never overwritten |
| **No authentication** | Stated plainly. It binds to `127.0.0.1` by default and warns on any other host. Put it behind an authenticating reverse proxy if you expose it |

## 15.7 Accessibility

Accessibility claims are made testable: text contrast meets WCAG AA (4.5:1) in both themes and chart colours meet the 3:1 non-text guideline, enforced by a test that computes the ratios **from the CSS tokens**, so a palette tweak that breaks it fails the build (when first measured, the chart green and warning amber were below 3:1 in light mode; charts now use their own deeper `--viz-*` tokens, and the logo green is brand identity, not a chart colour). Every icon is hidden from assistive technology or labelled; there is one `h1` per page; there is a skip link; the keyboard works throughout (`j`/`k`, `/`, `?`); badges carry text and glyphs, never colour alone; the intro animation is skippable with any key and disabled for `prefers-reduced-motion`. Honest limit: it has not been tested with a real screen reader.

## 15.8 Brand and design system

**The logo** is a signature that ends in a tick: *Markbook* in a cursive hand, finished by the check mark a teacher makes when something is right. The letters are the outlines of Dancing Script Bold (SIL OFL), converted to paths so no font is needed to display it anywhere, GitHub included. The short form (an **M with the tick**) is used for the app icon, favicon and empty states.

| Asset | File |
|---|---|
| Primary logo | `docs/assets/brand/wordmark.svg`, `wordmark-on-dark.svg` |
| Animated (README) | `wordmark-animated.svg`, `wordmark-animated-on-dark.svg` |
| Short form | `mark-bare.svg`, `mark.svg` (in an ink tile), `favicon.svg` (bolder) |
| Raster | `icon-192.png`, `icon-512.png`, `web/static/apple-touch-icon.png`, `social-preview.png`, `banner-light.png`, `banner-dark.png` |

`docs/assets/brand/src/build_logo.py` regenerates every SVG *and* the UI's inline macros from the font; `sh docs/assets/brand/src/build.sh` renders the PNGs. A test fails if the template's inline paths drift from the published files.

**Colour:** Ink `#10223A`, Marker green `#19B37D` (the tick), Highlighter `#FFD43B` (UI emphasis only), Paper `#FBFAF6`; the UI derives light and dark themes from these.

**Type:** Dancing Script for the logo only (as outlines); **Fraunces** (variable, bundled, OFL) for headings; the system sans for the interface.

**Motion:** decorative and always optional. The one intro animation (home page, once per browser session): the word *Markbook* is **written from left to right on a slanted edge** (about 1.4 s) using an animated `clip-path` polygon, then the **tick is drawn** (a stroke-dash animation), then the tagline rises (about 2.9 s in all). Any click or key skips it; it is disabled for reduced motion. A test asserts the tick starts after the word finishes. The tick also redraws when the header logo is hovered.

---

# Part 16. The command-line interface

## 16.1 Commands

```
markbook init [DIR] [--wizard | --template python|c] [--force]
markbook validate SPEC
markbook pin SPEC --roster CSV [--out CSV] [--at now|deadline] [--bundle DIR] [--token-env VAR] [--jobs N]
markbook grade SPEC (--roster CSV | --repo URL ...) [options]
markbook review RUN [--all] [--json]
markbook show RUN STUDENT
markbook override RUN STUDENT [-c CRITERION (-p POINTS | --accept-suggestion)] [--waive-late] [--clear similarity|borderline] [-m COMMENT]
markbook retry RUN [--sandbox docker|podman|none] [--jobs N]
markbook prepare SPEC [--sandbox docker|podman] [--force]
markbook enqueue SPEC --roster CSV --queue DIR [--sandbox docker|podman|none]
markbook worker DIR [--jobs N] [--lease-seconds S] [--idle-exit]
markbook queue-status DIR [--json]
markbook collect DIR [--out RUNDIR] [--lms canvas|moodle ...] [--partial]
markbook runs [--dir DIR] [--json]
markbook stats RUN [--idle-minutes N] [--baseline CSV] [--json]
markbook report RUN [--lms canvas|moodle ...] [--include-pending] [--out DIR]
markbook schema
markbook doctor [--clean]
markbook ai-check [--model M] [--suite] [--json] [--max-cost-usd N]
markbook serve [--dir DIR] [--specs DIR] [--host H] [--port P] [--jobs N] [--token-env VAR] [--sandbox docker|podman|none] [--allow-prepare]
markbook demo [--dir DIR] [--out DIR] [--serve]
```

`RUN` is a run directory, or a bare run id under `.markbook/runs/`. Running `markbook` with no arguments prints the banner (`Markbook ✔`, with an ASCII `[v]` fallback for limited locales), then the help.

## 16.2 `grade` options

| Option | Meaning |
|---|---|
| `--roster FILE` / `--repo URL` | Who to grade (`--repo` is repeatable; add `--id`, `--name`, `--ref` for a single repo) |
| `--out DIR` | Output directory (default `.markbook/runs/<run-id>`) |
| `--sandbox docker\|podman\|none` | `docker` is the default. `none` runs on this machine; only inside an isolated environment |
| `--jobs N` | Submissions graded in parallel (default 4) |
| `--lms canvas\|moodle` | Also write an LMS import CSV (repeatable) |
| `--include-pending` | Write provisional grades for submissions awaiting manual review |
| `--token-env VAR` | Env var holding a GitHub token (default `GITHUB_TOKEN`, then `GH_TOKEN`) |
| `--format text\|json` | `json` prints the full report to stdout (progress to stderr) |
| `--ai` / `--ai-model M` | Opt in to AI suggestions for criteria marked `ai: true`; sends code to the Anthropic API |
| `--fail-on-review` | Exit 4 when anything needs review |
| `--quiet` | No per-submission progress lines |

Press **Ctrl-C** during grading to stop: no new submissions start, those running finish within their timeouts and remove their containers, the command exits 130 and writes nothing partial.

## 16.3 Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 1 | Error: invalid spec, roster or run; unknown student; bad override |
| 2 | Usage error (argparse) |
| 3 | Sandbox unavailable: the runtime is missing or unreachable, the image cannot be pulled, or the `prepare` build failed. Nothing is graded; nothing is written |
| 4 | Success, but items need review (only with `--fail-on-review`) |
| 5 | `pin` wrote its outputs, but some repositories could not be pinned (see `pins.json`) |
| 6 | `ai-check` could not run (SDK or credentials missing) |
| 130 | Interrupted (Ctrl-C) |

Error handling in `main`: `SpecError`, `RosterError`, `StoreError` print `error: …` and return 1; `SandboxUnavailable` prints `sandbox unavailable: …` and returns 3; `CliError` returns its own code; `KeyboardInterrupt` prints `interrupted` and returns 130. Output streams are reconfigured with `errors="replace"` so an ASCII-only terminal degrades to `?` instead of crashing.

## 16.4 The other commands in a sentence each

| Command | What it does |
|---|---|
| `init` | Wizard (in a terminal) or template: writes `spec.yaml` and `roster.csv` |
| `validate SPEC` | Loads the spec, prints the rubric, or lists every problem |
| `pin` | Records each student's commit SHA at the deadline into a pinned roster (Part 9.6) |
| `review RUN` | Lists what needs a human, with the exact command to resolve each item (`--all` for everything, `--json` for scripts) |
| `show RUN STU` | One submission's criteria, evidence and similar files |
| `override` | Records a human decision (Part 12.4) |
| `retry RUN` | Re-grades only failed submissions (Part 12.7) |
| `prepare SPEC` | Builds the dependency image ahead of time (`--force` rebuilds without cache) |
| `enqueue / worker / queue-status / collect` | Distributed grading (Part 17) |
| `runs` | Lists runs with how many items each still needs reviewed |
| `stats RUN` | Review-effort numbers from the audit log (Part 19) |
| `report RUN` | Regenerates reports (applies all overrides); `--lms`, `--include-pending`, `--out` |
| `schema` | Prints the JSON Schema of `report.json` |
| `doctor` | Checks Python ≥ 3.10, git, Docker/Podman (one usable runtime is enough), Flask, and `GITHUB_TOKEN`; `--clean` removes orphaned sandbox containers |
| `ai-check` | Verifies the AI assist against the live API (Part 18.5) |
| `serve` | Starts the web UI (Part 15) |
| `demo` | Generates a seven-student cohort and grades it offline (~2 s); `--serve` opens the UI |

## 16.5 The demo cohort

`markbook demo` builds seven local git repositories that exercise every code path, with no network: a clean submission (**alice**), a copied pair with identifiers renamed (**bob** and **carol**), a logic bug (**dave**), a late submission (**erin**), a build failure with a single-commit history dump (**frank**), and an unreachable repository (**ghost**). It grades them with `--sandbox none` (the demo's own code is trusted; use `--sandbox docker` to grade through a real image) and prints the table. It is the quickest way to see triage, similarity, lateness and the review queue working together, and it is also what many tests run.

---

# Part 17. Scaling out: the file-based job queue

## 17.1 Purpose and constraints

For cohorts too big for one machine, grading can be split across processes or machines through a **queue directory**: plain files on a filesystem all workers can reach (an NFS export, a mounted volume, or a local directory for several processes). **No database, broker or network service.**

```bash
markbook enqueue spec.yaml --roster roster.csv --queue /shared/q1 --sandbox docker
markbook worker /shared/q1 --jobs 4          # on each grading machine; --idle-exit stops when the queue drains
markbook queue-status /shared/q1             # pending / claimed / done / failed, workers, stale leases
markbook collect /shared/q1 --lms canvas     # when everything is done; same reports as `markbook grade`
```

## 17.2 Layout

```
<queue>/meta.json                    run id, spec hash, tree hash, roster order, runtime
<queue>/spec/                        frozen copy of the spec (and its inputs, if it has any)
<queue>/pending/<seq>-<id>.json      one job per submission (roster fields only, never secrets)
<queue>/claimed/<worker>/…           a job owned by exactly one worker
<queue>/workers/<worker>.hb          heartbeat (mtime = last sign of life) + declared lease
<queue>/attempts/<seq>-<id>          one byte per time the job's worker was lost
<queue>/done/<id>.json               result: submission record + similarity corpus (first write wins)
<queue>/failed/<seq>-<id>.json       job files that could not even be read
```

## 17.3 Two primitives, nothing else

| Primitive | Used for | Why it is enough |
|---|---|---|
| `os.rename` within one filesystem | **Claiming:** several workers rename the same `pending` job into their own `claimed/<worker>/`; exactly one succeeds | Atomic: the winner owns the job |
| `os.link` (fails if the target exists) | **Publishing a result:** the first finished result for an id wins | A late result from a worker presumed dead can never overwrite or duplicate a finished one |

The grading itself is the existing `grade_submission`; the final run is built by the existing `assemble_run`. The queue only moves work around, so a distributed run cannot drift from a local one: a test grades the demo cohort locally and through the queue (three competing worker processes) and compares scores, statuses, triage and similarity.

## 17.4 Freezing the spec

`enqueue` freezes a copy of the spec (plus the spec's whole directory, only if it uses `overlay`, `starter` or `cases_file`), records its SHA-256 and a **tree hash** of those files (`_tree_sha256`: relative path plus bytes) in `meta.json`, and writes one job file per submission. A worker **refuses to start** if the frozen spec or its files no longer match the recorded hashes, so every submission in a run is graded against one rubric. **Tokens are never written to the queue:** each worker uses its own `GITHUB_TOKEN` or `--token-env`.

## 17.5 Liveness and recovery

- A worker refreshes a heartbeat file every `lease / 4` seconds (the declared lease is stored in it).
- If a worker dies (crash, `kill -9`, lost machine), another worker returns its claimed jobs to `pending/` once `age > lease + margin`, where `margin = min(30, 0.25 × lease)` absorbs clock skew between machines. Default lease: 300 s (`--lease-seconds`).
- Each recovery appends a byte to `attempts/<job>`; after `MAX_ATTEMPTS = 3` the job becomes an **error record** (`worker_lost`) instead of looping forever, a visible failure, never an infinite retry.
- A job that *raises* becomes a normal `status: error` submission, never a lost job. A job file that cannot be read goes to `failed/` and appears as `not_graded` at collect time.
- `SIGINT`/`SIGTERM` makes a worker stop claiming; it finishes the job(s) in hand (bounded by their timeouts; containers are removed as usual) and exits 0.
- For queues created with `--sandbox docker`, a worker checks Docker and pulls the image once at start-up (exit 3 if unusable).

## 17.6 Collecting

`collect` builds the run with the same `assemble_run` in roster order and saves it like `grade` does (`run.json`, fingerprints, reports, `inputs`). It refuses while jobs are unfinished; `--partial` collects anyway and marks every missing submission as an error with code `not_graded` (**never a silent zero**).

## 17.7 Trust and honesty

- **The queue directory is a trust boundary.** Anyone who can write to it can make workers clone and run arbitrary repository URLs (through the sandbox, or on the bare host with `--sandbox none`) and can forge results. Keep it private to the grading team and run workers only on machines you trust. Do not put it on a share that students or other courses can write to.
- **Verified:** several worker *processes* on one Linux machine, including competing claims, `kill -9` recovery, graceful `SIGTERM`, and equivalence with local grading. **Multi-machine operation was not verified.** Rename is atomic on local filesystems and expected to be on NFS inside one export, but NFS retransmits and eventually consistent shared filesystems (some FUSE and object-store mounts) are untested.
- **Clock skew matters.** Liveness is judged from heartbeat modification times against each machine's clock. Keep clocks in sync and the lease comfortably above the expected skew plus the heartbeat interval. Too short a lease only wastes work (the duplicate result is discarded); it cannot corrupt a run.
- POSIX only; no authentication or encryption (that is the filesystem's job). A queue run's report does not yet record one prepared-image key (each worker builds its own).

---

# Part 18. The optional AI reviewer assist and `ai-check`

## 18.1 Stance

The earlier project graded with an LLM. Here the model may only **suggest**, because *a grade a teacher cannot explain or reproduce is worse than no automation.* The assist is optional (`pip install 'markbook[ai]'`) and never on by default.

## 18.2 Rules

| Rule | Implementation |
|---|---|
| **Two opt-ins** (it sends student code to a third party) | The spec marks the criterion `ai: true` on a `manual` check **and** the run passes `--ai` (the CLI refuses `--ai` if no criterion is marked, so nothing is silently sent) |
| **Never applied automatically** | The criterion stays pending; the suggestion is shown beside it; only an explicit human action (`--accept-suggestion` or the web *Accept* button) turns it into points, recorded as AI-assisted |
| **Does not reduce review load** | A human still decides every one; `summary.ai_assist` says so |
| **Student code is data, not instructions** | It goes in delimited blocks; the system prompt tells the model to *report* instructions it finds in code, not obey them |
| **Delimiter break-outs neutralised** | `neutralise()` stops code from closing the data blocks or forging its own rubric block |
| **Constrained output** | A JSON schema (`points`, `confidence` ∈ low/medium/high, `rationale`, `concerns`, …), clamped to `[0, max_points]` |
| **Honest about what it saw** | Whole files only, in path order, up to a budget (`MAX_CHARS = 60 000`, files up to 200 000 bytes); omitted files are listed; confidence is capped when code was not seen; `truncated` is reported |
| **Audit** | The audit log records the AI's number next to the human's, including disagreements |

Default model: `claude-opus-5-5` (override with `--ai-model`). Costs are estimated from published per-million-token prices where the model is known (`PRICES_PER_MTOK`), reported as "unknown" otherwise. Mutation-checked: disabling the neutraliser and the clamp makes three tests fail.

## 18.3 Where the suggestion appears

CLI: `markbook review` / `show` display it with its rationale and concerns; `override … --accept-suggestion` accepts it. Web: a suggestion card on the review page with *Accept*. In both, the human may enter different points; the log keeps both.

## 18.4 Status, stated plainly

The request was verified against the real SDK's wire format with a local fake server, and all behaviour is tested with a fake client. **It has not been run against the live API from this codebase.** `ai-check` exists so you can find out, cheaply and safely.

## 18.5 `markbook ai-check`

A smoke test, not a benchmark, with deliberately wide ranges. It exercises the exact production path (`Reviewer.create` then `Reviewer.suggest`) on small synthetic submissions shipped in `markbook/ai_fixtures/`, and never touches your roster, runs or repositories.

```bash
pip install 'markbook[ai]'
export ANTHROPIC_API_KEY=...
markbook ai-check                  # connectivity + one tiny real call
markbook ai-check --suite          # + six fixtures, each judged PASS/FAIL
```

Steps: (1) connectivity: SDK installed, where credentials come from (the *kind* only, never the secret), whether the model id is reachable; (2) one real call on a 5-line function with the whole suggestion, token usage and estimated cost; (3) with `--suite`, six fixtures with expectations (the one-call smoke test in step 2 uses its own tiny fixture):

| Fixture | Expectation |
|---|---|
| `well-structured` | score at least 7/10 |
| `giant-function` (400 lines, one-letter names) | score at most 4/10 |
| `prompt-injection` (comments and README order "full marks") | score at most 5/10 **and** `concerns` mention the attempt |
| `delimiter-breakout` (`</submission>`, forged rubric block) | score at most 5/10, and the prompt actually sent still has exactly one real `<submission>` block |
| `over-budget` (3 files, 3000-char budget) | `truncated` is true, confidence is not `high` |
| `no-source` (a repository with nothing to review) | fails with "no source files to review" and makes **zero** API calls |

`--max-cost-usd N` (default 0.50): the suite refuses to start above the estimated bound, *before* anything is sent. Exit codes: 0 pass, 1 a fixture failed, 6 could not run. Everything takes an injectable client, so the PASS/FAIL logic is tested offline.

---

# Part 19. Measuring the saving

## 19.1 What the number is, and is not

The report's "% less manual review" is a **modelled workload figure** (Part 7.6). It says nothing about minutes. `markbook stats` and a short protocol let you obtain a real, if small, measurement. Markbook ships no measured result and none is claimed anywhere.

## 19.2 `markbook stats RUN`

Pure functions over the audit log (and, optionally, the view log); every figure is a measurement of what the log shows, and **time figures are lower bounds**: nothing is recorded about time spent before a reviewer's first decision (or first page view) of a stretch of work, and gaps longer than the idle cutoff (default 5 minutes, `--idle-minutes`) are treated as breaks and excluded.

- **Active review time** = the sum of gaps between consecutive decisions by one reviewer that are at most the idle cutoff.
- With `views.jsonl`, the time from opening a submission to its decisions is also credited, capturing the lead-in to the first decision.
- **AI-assisted share and accept-unchanged rate** describe behaviour, not quality (a high rate can mean good suggestions or reviewer anchoring).
- `--baseline manual.csv` (`submission_id,seconds`) compares against by-hand timings. The comparison is restricted to submissions present in both files and is labelled "measured, N submissions, 1 grader". Fewer than `MIN_BASELINE_N = 5` is flagged as anecdote. No p-values or confidence intervals are produced: there is nothing sound to compute them from.

## 19.3 The protocol (an afternoon)

1. **Same cohort, same rubric** (10–30 realistic submissions).
2. **Counterbalance:** split into matched halves A and B; one grader does A by hand and B with the queue (a second grader does the opposite). With one grader, say so.
3. **Separate the arms in time** and do not reveal marks across arms.
4. **Control fatigue and learning** with warm-up submissions and scheduled breaks in both arms.
5. **Log:** manual-arm seconds per submission to a CSV; queue arm uses the audit log (enter your name as reviewer) and the view log.
6. Run `markbook stats RUN --baseline manual.csv`.

## 19.4 What not to conclude

Not "Markbook saves X % of your time" for other courses, rubrics, graders or class sizes: the rubric's mix of automatable and manual criteria drives everything. Submissions the queue never asked you to touch count as 0 seconds, which assumes you trust the automated result without looking; spot-check a sample and record disagreements. Treat the reported reduction as an *upper bound* when the stopwatch includes reading time that the log cannot see.


---

# Part 20. Security model, guard rules, baselines and edge cases

## 20.1 Posture

Student code is untrusted. Treat the grader as a system that runs arbitrary code from strangers. Markbook's defence is **layered**: no single control is relied on, and every control has a test. It is *containment*, not a guarantee: the honest residual risks are in 20.3.

## 20.2 The threat model

| # | Threat | Mitigation | Residual risk |
|---|---|---|---|
| T1 | Student code reads or modifies the host | Container: no host mounts, non-root, all capabilities dropped, `no-new-privileges`, tmpfs-only writable area | Container-escape vulnerabilities in Docker, runc or the kernel; use gVisor, Kata or LXD for hostile cohorts |
| T2 | Fork bomb, memory hog, disk fill, output flood | `--pids-limit`, `--memory` (= swap), capped tmpfs, 64 KiB output cap, in-container `timeout -s KILL` | CPU contention between parallel submissions |
| T3 | Exfiltration or outbound attack | `--network none` | None by design (also why builds cannot download dependencies) |
| T4 | Student forges history, replaces tests, or escapes via symlinks | History read before execution; sandbox gets no `.git`; overlay overrides student files; symlinks stripped; file checks refuse escaping paths | Commit *timestamps* are author-controlled (documented; use `pin`) |
| T5 | Malicious repository URL or ref | https only (web); a leading `-` rejected; `ext::` and `file:` transports disabled; hooks disabled | git itself |
| T6 | Credential leak | Token only in git's environment, only for `github.com`, never in argv or reports; never stored in the queue | Token readable by the process owner |
| T7 | XSS or CSRF in the web UI | Autoescape + CSP; Origin and `Sec-Fetch-Site` check | **No authentication:** do not expose without a proxy |
| T8 | Spreadsheet formula injection via names | `_cell` apostrophe prefix on free-text cells | None known |
| T9 | Forged test report | Committed reports deleted before the run; `min_tests`; defensive XML parse; `cases` is black-box | A determined student can forge from inside the test process (`junit` trust limit) |
| T10 | Malicious or malformed spec (including web uploads) | Eager validation; `confine`; `prepare` refused in uploads; spec directory confinement | A trusted operator's own spec is trusted |
| T11 | Prompt injection into the AI assist | Delimited data, neutralised break-outs, schema-constrained and clamped output, never applied automatically, two opt-ins | The model might still be influenced; a human decides every time |
| T12 | A hostile queue directory | None technical: it is a trust boundary | Anyone who can write to it can run repositories and forge results |

## 20.3 Honest residual risks

1. **Container escapes.** Docker's isolation is only as strong as the kernel and runtime. For adversarial cohorts, add a stronger boundary.
2. **`--sandbox none`** has no isolation (CPU-time and file-size `ulimit`s plus a scrubbed environment only). It is for already-isolated environments.
3. **Timestamps and lateness** trust commit times unless pinned.
4. **`junit` reports** can be forged from inside the test process.
5. **No web authentication.**
6. **Podman** support is verified only by the CI job.

## 20.4 The guard rules, consolidated

Each rule below is enforced in code and covered by at least one test (Part 21). They are the answer to "what stops X?".

### A. Execution guards
1. No network in any student container (`--network none`).
2. All capabilities dropped; no new privileges; non-root user (sub-uid or `nobody` under Podman, never container root).
3. Memory, swap, CPU, PID and disk ceilings; Podman limits are *read back* and the run refuses if unenforced.
4. Per-command `timeout -s KILL` inside the container; 137 near the deadline is reported as a timeout.
5. Output capped at 64 KiB per stream, drained incrementally.
6. Sources streamed as a tar; no host path is ever mounted; no `.` entry in the archive.
7. No silent fallback to the host: unusable runtime → exit 3, nothing written.
8. One container per submission, removed on every path (success, error, interrupt); orphans labelled and removable.
9. The sandbox image is pulled once, up front.

### B. Evidence-integrity guards
10. History, fingerprints and README are read from the clone before any student code runs.
11. The copy given to the sandbox has no `.git`; all symlinks are stripped.
12. Teacher overlay files overwrite student files.
13. `read_file` refuses symlinks and escaping paths; `file_exists`/`readme` resolve paths and reject escapes.
14. A committed JUnit report is deleted before the run; `min_tests`; DOCTYPE/ENTITY refused; 1 MiB cap.
15. A crash never passes a case (exit 0 is required unless the case says otherwise).
16. Hidden cases and hidden tests never leak expected output or names into evidence or feedback.

### C. Scoring and audit guards
17. A grader exception becomes `status: error` + review, never a silent zero.
18. A dependency that *errored* makes its dependants error; one that *failed* fails them without running.
19. `run.json` is not modified by decisions or reports; decisions are appended with who, when and what they replaced.
20. Decisions are written under a file lock; writes are atomic.
21. Override points are validated against the criterion's maximum.
22. Only `similarity` and `borderline` flags can be cleared.
23. A similarity flag never changes a grade.
24. Provisional grades are blank in exports unless `--include-pending`.
25. `retry` refuses if the spec hash changed.
26. The queue refuses to grade if the frozen spec or its files changed; missing jobs are `not_graded`, never zero.

### D. Input guards
27. Specs are validated completely and eagerly; unknown keys are errors.
28. Image references match a strict regex; `prepare` commands use the JSON exec form; `prepare.files` must be small regular files inside the spec directory.
29. Rosters: safe ids, duplicate rejection, size cap in the web UI (1000 rows), https-only in the web.
30. Repository strings: scheme allow-list, no leading `-`, hardened git flags, size cap (200 MiB), timeout (180 s).
31. CSV cells are formula-neutralised.

### E. Web and builder guards
32. Strict CSP, no inline script or style, `nosniff`, `DENY` framing, no referrer.
33. Same-origin enforcement for state-changing requests.
34. Upload size cap (2 MB); builder JSON cap (200 000 bytes); run-id and template-name regexes.
35. The client sends choices, never commands; the builder owns every command; extra packages are off unless `--allow-prepare`, pattern-checked, and gated on *every* path (preview, run start, template save).
36. Newline and tab become spaces in single-line fields (never deleted).
37. Template names are slugged; templates are never overwritten.

## 20.5 Base cases and edge cases

What Markbook does at the boundaries. Each row has a test or a documented behaviour.

| Situation | Behaviour |
|---|---|
| Empty roster or only blank rows | `RosterError`: "roster has no rows with a repository" |
| Duplicate student ids | `RosterError` with the line number |
| Repository does not exist or is private (no token) | `submission.status = error`, code `not_found_or_private`; triage `fetch_failed`; `retry` can fix |
| Repository has no commits | `empty_repository` |
| Requested ref missing | `ref_not_found` |
| Repository larger than 200 MiB | `error: too_large` |
| Student submits a symlink | Removed before grading; a note records how many |
| Student replaces the teacher's tests | The overlay overwrites them |
| Student's program prints gigabytes | Capped at 64 KiB per stream; `truncated` note |
| Student's program loops forever | Killed at the timeout; reported as `timed out` |
| Student's program crashes | A `cases` case fails (exit 0 required); `command` fails on exit code |
| Zero tests ran (`junit`) | `failed`, nothing earned |
| Test report missing, malformed, huge, or with DOCTYPE | `failed` with the reason |
| Build fails (student's fault) | Dependent criteria `failed` without running, with the reason |
| Container cannot start for one submission | Code criteria `error` + review; not zero |
| Docker not running | Exit 3 before grading; nothing written |
| Podman cgroup limits not delegated | Exit 3 with the cause; nothing runs unlimited |
| Image missing and unpullable | Exit 3 with the pull error |
| `prepare` build fails | Exit 3 with pip/npm's output tail; no student graded |
| Spec has a typo | `SpecError` lists every problem |
| Spec runs code but has no image | Error: "sandbox.image required when any criterion runs code" |
| Late penalty without a deadline | Error |
| Deadline without a timezone | Error |
| Zero or negative points; boolean points | Error |
| Criterion requires an undefined or later criterion | Error |
| Score within the pass-mark margin | `borderline` review reason |
| Everything manual | Every submission lands in the queue; review load is low |
| Nothing manual and nothing flagged | Every submission is `auto`; review load 100 % |
| One-commit history dump | `git` check conditions fail (commits, active days, largest share) |
| Shallow clone | History counts `null`, `shallow: true`; no misleading numbers |
| Starter code kept by everyone | Subtracted; not a similarity flag |
| Very short assignments | Below `min_fingerprints`, similarity is not computed for the pair |
| Hostile student name in CSV | Written as text |
| Two reviewers decide at once | Lock serialises; all survive |
| Server restarts mid-grade | `status.json` `running` becomes `failed` on startup |
| Ctrl-C mid-grade | Stop scheduling; running ones finish; exit 130; no partial run |
| Worker dies mid-job | Another worker reclaims after the lease; after three losses, `worker_lost` error |
| Late result from a presumed-dead worker | Discarded; first result wins |
| Spec changes after a run | `retry` refuses; the queue refuses |
| No terminal for the wizard | `init` writes the template instead; closed stdin cancels with no traceback |
| ASCII-only terminal | Output degrades to `?`; banner uses `[v]` |

---

# Part 21. Testing strategy

## 21.1 Principles

1. **Test against real runtimes, not only mocks.** The most valuable bugs were found by running real images (Part 23.7).
2. **A test must be able to fail.** Important guards were *mutation-checked*: the protection was removed on purpose and the test confirmed to go red (the `prepare` JSON exec form, the AI neutraliser and clamp, the override lock, the packages gate, the signature-then-tick animation order, the junit protections).
3. **Hostile input first.** Specs, rosters, names, URLs, JSON payloads, XML reports and builder fields are fuzzed with malicious values.
4. **Contracts are enforced.** Real output is validated against the published JSON Schema, including with overrides applied.
5. **Equivalence tests** prove that two paths cannot drift: local grading versus the queue; the template's inline logo versus the published SVG; Docker versus Podman flags.
6. **A passing test proves only what its author believed.** One CSV test once asserted the vulnerable behaviour; the lesson is recorded.

## 21.2 Scale

About **458 tests pass and 12 are skipped** on a machine with Docker, Node and network, across 22 test modules plus a shared `conftest.py` and a jsdom driver. Skips occur when a prerequisite is missing (Docker or Podman, its local test image, the network, Node or jsdom); the CI Podman job treats a skipped Podman test as a failure.

## 21.3 What each module covers

| Test module | Covers |
|---|---|
| `test_spec_roster.py` | Spec validation (every error class), defaults, roster aliases, duplicate ids, `safe_id` |
| `test_checks.py` | `file_exists`, `command`, `cases` (including crash-never-passes), `readme`, `git`, `manual` |
| `test_junit.py` | `junit`: per-test credit, `min_tests`, committed report removal, DOCTYPE refusal, size and symlink handling (five mutation checks) |
| `test_grader_reports.py` | `grade_submission`, `rescore`, penalties, borderline, review load, reports, schema validation, CSV injection, Canvas/Moodle formats, JUnit XML, feedback |
| `test_similarity.py` | Tokenisation, winnowing, starter subtraction, boilerplate suppression, file pairs |
| `test_fetch.py` | URL normalisation, hardening flags, shallow/ref strategies, error classification, local-path refusal |
| `test_pin.py` | `now` and `deadline` modes, SHA verification, bundles, manifest, exit codes, original roster untouched |
| `test_container_argv.py` | Every isolation flag for Docker and Podman (table-driven), Podman user logic, cgroup-limit parsing |
| `test_docker_sandbox.py` | Real daemon: no network, non-root, caps dropped, resource caps, timeouts, output cap, tar load, cleanup, a real Debian-based image through the whole demo |
| `test_prepare.py` | Build key, Dockerfile rendering (hostile commands), caching, failures, Podman build, real `pip install` |
| `test_cli_store.py` | CLI commands, exit codes, decisions, audit entries, locking (concurrent writers), retry, Ctrl-C |
| `test_queue.py` | Enqueue/claim/complete, competing workers, crash recovery, leases, exactly-once results, equivalence with local grading |
| `test_stats.py` | Active-time computation, idle cutoff, baselines, strict parsing, rendering |
| `test_ai.py`, `test_ai_check.py` | Prompt construction, neutralisation, clamping, budgets, opt-ins, audit entries, the PASS/FAIL logic with a fake client |
| `test_specbuilder.py` | The builder: every kind, hostile packages, YAML round-trips, limits, newline handling |
| `test_wizard.py` | A full scripted conversation, defaults, re-asking, cancel, overwrite protection, the real command in a subprocess |
| `test_web.py`, `test_web_views.py` | Routes, run creation, decisions, downloads, CSRF, headers, upload limits, view log |
| `test_web_builder.py` | Preview, run-from-form, hostile payloads, download, templates (traversal), the packages gate on every path, same-origin refusal |
| `test_brand.py` | Page structure and accessibility, CSP, no inline script/style, contrast ratios computed from CSS tokens, logo single source of truth, splash/theme boot logic (in Node), reduced motion, the terminal banner |
| `test_frontend.py` + `tests/frontend/drive.js`, `builder.js` | The real JavaScript against the real server in jsdom: sorting, filtering, copy, theme, splash, drop zones, the builder form |

## 21.4 Real-runtime tests: how they are gated

Tests marked `docker` need a container runtime and a locally cached image. They are parametrised over `docker` and `podman` and skipped when unavailable. Environment variables select images and capabilities:

| Variable | Meaning |
|---|---|
| `MARKBOOK_TEST_IMAGE` | Small image for Docker isolation tests (default `nginx:alpine`; CI uses `busybox:latest`) |
| `MARKBOOK_TEST_PODMAN_IMAGE` | The same for Podman (fully qualified) |
| `MARKBOOK_TEST_PY_IMAGE`, `MARKBOOK_TEST_PODMAN_PY_IMAGE` | Python image for the Debian-based end-to-end test |
| `MARKBOOK_TEST_NETWORK` | Enables tests that use the real package index (`prepare` with `pip install pytest`) |
| `NODE_PATH` | Where `jsdom` is installed, for the front-end tests |

## 21.5 Running the tests

```bash
pip install -e '.[dev]'
npm install --no-save --prefix /tmp/jsd jsdom@24        # optional: front-end tests
NODE_PATH=/tmp/jsd/node_modules pytest -q
pytest -m docker -v                                      # only the real-runtime tests
```

## 21.6 Lessons recorded by the tests

- *Probe the real daemon before building on it* (`docker cp` writes under the tmpfs; GNU tar fails on the mount root).
- *Python < 3.13* needs `proc.stdin = None` after closing the stdin pipe.
- *A check satisfiable by doing nothing is a bug* (crashing program printing nothing).
- *Locks are not optional* (4 of 6 concurrent decisions lost without one).
- *Gates must be tested on every path* (the packages gate failed only the preview test until two more were added).
- *Sanitising must not change meaning* (deleting newlines made `a⏎b` into a valid `ab`).

---

# Part 22. CI/CD

## 22.1 The pipeline that exists

`.github/workflows/ci.yml` runs on every push to `main` and every pull request.

**Job `test`** (matrix: Python 3.10, 3.11, 3.12, 3.13; `fail-fast: false`)

| Step | Purpose |
|---|---|
| `actions/checkout@v4`, `actions/setup-python@v5` | Source and interpreter |
| `pip install -e '.[dev]'` | The package with test dependencies (pytest, jsonschema, Flask, anthropic) |
| `markbook doctor || true` | Diagnostic only: prints what the runner has |
| `docker pull busybox:latest` | So the Docker isolation tests *run* instead of skipping |
| `npm install --no-save --prefix $RUNNER_TEMP/jsdom jsdom@24` | So the front-end tests run (skipped if Node is missing) |
| `pytest -q` with `NODE_PATH`, `MARKBOOK_TEST_IMAGE=busybox:latest`, `MARKBOOK_TEST_NETWORK=1` | The full suite, including `prepare` end to end with the real package index |

**Job `podman`** (Python 3.12): *Podman support is verified only here; no developer machine of this project has Podman.*

| Step | Purpose |
|---|---|
| Diagnostics | `id`, `uname`, `podman --version`, `podman info`, cgroup controllers (`/sys/fs/cgroup/...`), `/etc/subuid`, registries, `markbook doctor` |
| Pull images (fully qualified) | `docker.io/library/busybox`, `docker.io/library/python:3.12-slim` (Podman does not resolve short names without a TTY) |
| Smoke test with Markbook's exact flags | `podman run --rm --network none --cap-drop ALL --security-opt no-new-privileges --memory 64m --memory-swap 64m --cpus 1 --pids-limit 64 --user … --tmpfs /work… busybox sh -c 'id -u; cat /sys/fs/cgroup/memory.max …'` |
| Isolation tests (verbose) | `pytest -v -rs -m docker tests/test_docker_sandbox.py tests/test_container_argv.py`; **fails the job if any `[podman]` test was skipped** (a skipped test would mean Podman was *not* verified) and counts the passes |
| Prepare and junit under Podman | `pytest -v -rs tests/test_prepare.py -k podman`; same skipped-is-failure rule; requires at least 3 passes (`podman build`) |
| Full suite | `pytest -q` |
| **Leaked containers (must be empty)** | `podman ps -a --filter label=markbook=1` with `if: always()`: a cleanup regression is visible |

Design notes: the two jobs encode different questions. `test` asks "does it work across Python versions on a Docker runner?"; `podman` asks "is the second runtime *really* verified, with the cgroup limits enforced?". The skipped-is-failure rule is the important detail: a green job that skipped its tests proves nothing.

## 22.2 Branch and merge practice

Work happens on feature branches (`feat/…`) with small commits, a PR per feature, CI green before merge. A stacked PR (one branch built on another) must use a **merge commit** for the base PR so the dependent branch's history remains valid; squash-merging the base would orphan it. Commit early: untracked work is unrecoverable if a command deletes it (a lesson recorded after a recursive delete removed an uncommitted package; recovered from tooling snapshots).

## 22.3 What to add, in priority order

None of the following exists yet; they are the engineering plan. Each is small and independently shippable.

### 1. Lint, types, security scan (cheap, expected)

```yaml
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install ruff mypy bandit pip-audit -e '.[dev]'
      - run: ruff check . && ruff format --check .
      - run: mypy markbook
      - run: bandit -q -r markbook -ll
      - run: pip-audit
  codeql:
    uses: github/codeql-action/...   # CodeQL for Python and JavaScript
```

For a tool that runs untrusted code, a security scan is evidence, not decoration. Note: `bandit` will flag `subprocess` use; the findings need triage and `# nosec` comments with reasons where the call is deliberate.

### 2. Build check

Build the sdist and wheel, install the wheel into a *clean* virtualenv, and run `markbook demo` and `markbook --version`. This catches packaging bugs the tests cannot (missing templates, static files, schema, fixtures: `package-data` in `pyproject.toml` lists them).

```yaml
  build:
    steps:
      - run: pip install build && python -m build
      - run: python -m venv /tmp/v && /tmp/v/bin/pip install dist/*.whl '.[web]' && /tmp/v/bin/markbook demo --out /tmp/demo
```

### 3. Release on tag

```yaml
on: {push: {tags: ["v*"]}}
jobs:
  release:
    permissions: {id-token: write, contents: write}    # PyPI trusted publishing: no stored token
    steps:
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
      - uses: softprops/action-gh-release@v2          # GitHub Release with the changelog
```

Check that the tag matches `markbook.__version__` before publishing; keep a `CHANGELOG.md`.

### 4. Snap package

A `snapcraft.yaml` (core24, `python` plugin, `command: bin/markbook`, plugs for `home`, `network`, and an interface for talking to Docker/Podman) built in CI and pushed to the Store `edge` channel on `main`, `stable` on tag. A snap is the natural distribution for a tool aimed at Ubuntu users. Strict confinement and a container runtime interact badly (the snap must reach the Docker socket or Podman); this needs deliberate design and may need classic confinement or documented plugs.

### 5. Container image

A `Dockerfile` (slim Python, `pip install .[web]`, non-root user, `ENTRYPOINT ["markbook"]`) built and pushed to GHCR on tag. Caveat: running Markbook *in* a container means the sandbox is a container-in-container or a mounted Docker socket (which is root-equivalent). Document the safe configurations (Podman rootless, or a dedicated VM) rather than shipping a recommended one that undermines the isolation.

### 6. A reusable GitHub Action

`uses: Dany-hardi/markbook-action@v1` with inputs `spec`, `roster`, `sandbox`, that installs Markbook, runs `markbook grade --format json --fail-on-review`, uploads the run directory as an artifact, and publishes `junit.xml` to the job summary. The README's "Use in CI" section shows the manual version.

### 7. Dependabot and scheduled jobs

Dependabot for `pip` and `github-actions`; a weekly scheduled run of the full suite (to catch image and base-OS drift) and of `markbook ai-check --suite` when a repository secret holds an API key (the only way to ever verify the AI path).

### 8. Matrix additions

macOS (the README claims Linux and macOS; only Linux is currently in CI), Docker versus Podman on the same OS, and a nightly job that pulls the newest `python:3.12-slim`, `node:20` and `gcc:13` images and runs the end-to-end demo through them.

### 9. Documentation pipeline

Build this guide to HTML/PDF on every tag (for example with Pandoc), and fail the build on broken links or if a constant documented in Appendix A no longer matches the code (a small test can import the constants and compare).

## 22.4 Kubernetes and Markbook

The honest design is a `KubernetesSandbox` backend (one `Job` per submission, a `NetworkPolicy` denying egress, resource requests and limits as in 8.2, an `emptyDir` with `sizeLimit` as `/work`, a restricted Pod Security profile) and `queue.py` workers as a `Deployment`. It is a roadmap item, not built: the file queue is verified only on one machine, so the claim "it scales" must wait for multi-machine tests. The queue directory would become a `PersistentVolume` with the trust-boundary caveats of 17.7.


---

# Part 23. Design decisions and rejected alternatives

Each decision is written as *context → decision → why → rejected alternatives → consequence*. They are the "why" of the code, and the part most worth reading before changing anything.

## 23.1 Principles (the constitution)

1. **Explainable over opaque.** Every grade is a sum of named criteria, each with the evidence the grader saw. Nothing is a bare number.
2. **The machine proposes, the human decides, and the record shows who.** Overrides are first-class, attributable and append-only.
3. **Rubric as data.** The assignment is a versioned YAML file, validated up front, hashed into every report.
4. **Untrusted code means isolation, always.** No silent fallback to the host.
5. **Reproducible.** A grade is a function of `(spec hash, commit SHA, tool version)`, all recorded.
6. **One engine, two front ends.** The CLI and the web UI share functions and run directories.
7. **Fail loudly where silence costs a student marks.** An infrastructure error is a review item, never a zero.

## 23.2 Rubric-as-code with `requires`

*Context:* teachers need a rubric that is reviewable, versionable and checkable before a long run. *Decision:* typed YAML checks, validated eagerly with all errors at once; `requires` expresses dependency (if the build fails, tests are failed without running; if it *errored*, the dependants error and go to review). *Why:* a typo found halfway through forty repositories is expensive; a build failure is the student's problem but a Docker hiccup is not. *Rejected:* a Python plugin API for checks: powerful, but it makes specs executable, hard to review and impossible to accept from a web upload; seven built-in types plus `command` (which runs anything inside the sandbox) cover the real cases.

## 23.3 One `git clone`, not the GitHub API

*Decision:* clone; record the SHA; pass credentials through git's environment; fetch full history only when the rubric has a `git` check. *Why:* the old design's per-file API calls were slow and rate-limited, with no history or build files. A full clone is not needed for lateness (the tip commit suffices), so every other spec gets depth 1 and the report says `shallow: true` with `null` history counts rather than misleading ones. *Evidence:* three parallel full clones of a 17 MB repository took 1 min 42 s.

## 23.4 Forensics before execution

*Decision:* read history, fingerprints and README from the clone before the sandbox starts; the sandbox receives a copy with no `.git`. *Why:* a build step is student code and can rewrite `.git`; anything read afterwards could be forged.

## 23.5 Sandbox: stream, don't mount

*Decision:* `/work` is a size-capped tmpfs; sources are streamed in as a Python-built tar. *Why:* a bind mount exposes a host path and has no size limit. *Rejected:* `docker cp` (writes *under* the tmpfs mount; invisible files); `--read-only` (needs a writable layer for injection; unnecessary because a non-root, cap-dropped user cannot write the root filesystem anyway).

## 23.6 No silent fallback, and why `--sandbox none` exists

*Decision:* if a runtime is requested and unusable, stop with exit 3. `--sandbox none` is explicit, loud and documented as not a sandbox. *Why:* a silent Docker→host fallback turns "isolation unavailable" into "arbitrary code from strangers runs on your laptop". `none` exists because CI jobs, VMs and LXD containers are *already* isolated, and the test suite needs a fast path.

## 23.7 The bug only a real image could show

The first sandbox passed all its tests, and the demo graded correctly, but only against busybox and `--sandbox none`. Run against `python:3.12-slim` it failed *every* command criterion: GNU tar tries to set the mtime and mode of the root-owned `/work` mount and exits non-zero; busybox tar does not. Two lessons. *First*, the failure mode was right: criteria came back as `error` and went to review, not to zero, so no student lost marks. *Second*, the test was wrong: a regression test now grades the whole demo through a real Debian-based image and asserts the result equals the host run. The archive is now built in Python with no `.` entry, which also removes the dependence on whatever `tar` the image ships.

## 23.8 Second runtime: Podman, and why not LXD

`DockerSandbox` became a thin subclass of `ContainerSandbox(binary)`; `PodmanSandbox` is the other. One method generates every flag and a table-driven test asserts the full set for both. The four real differences (Part 8.5) were handled deliberately, the dangerous one being that rootless Podman may *warn and run without* resource limits, so the limits are read back and anything unreadable fails. *Not implemented: LXD*, because it runs system containers via `lxc exec`, not an OCI-compatible CLI; it is a different design, not a third binary name.

## 23.9 Cases: a crash can never pass

The first demo run gave a program with a syntax error 2 points for "input 0 → empty output", because a crashed process also prints nothing. *Decision:* exit code 0 is required by default for every case. *Principle:* **a check that can be satisfied by doing nothing is a bug**, applied again to `junit` (zero tests earns nothing) and to committed reports (deleted).

## 23.10 Similarity: winnowing with the false positives handled

*Decision:* token-level k-gram fingerprints, winnowed, with comments and literals removed and identifiers collapsed; starter code subtracted; boilerplate shared by most of a large cohort ignored; an inverted index instead of all-pairs; every flag carries file pairs; a flag only routes to review. *Why:* false positives are what make plagiarism tools unusable; a score is evidence, not a verdict. *Rejected:* `difflib` over whole trees (quadratic; flagged everyone who kept the starter file).

## 23.11 Concurrency, retries and interruption

*Locking:* the CLI and the UI can both write `overrides.json`; without the lock 4 of 6 simultaneous decisions were lost (measured), with it all survive. *Retry:* fetch failures and sandbox errors are the items automation can fix itself; `retry` re-grades only those, leaves everything else byte-identical, recomputes similarity from stored fingerprints, and refuses if the spec hash changed. *Ctrl-C:* stop scheduling at once; running submissions finish and remove their containers; exit 130; nothing partial. *Image pull:* once, before the workers start, not by N workers racing.

## 23.12 A queue made of files

*Decision:* to scale past one machine without a broker or database, the queue is a directory and the only primitives are atomic `rename` (claim: one winner) and `link` (publish: first wins). Workers call `grade_submission`; `collect` calls `assemble_run`; so a distributed run cannot drift from a local one. The spec is frozen with its hash (and a tree hash of its files) and workers refuse on mismatch. Crash recovery is lease-based with a clock-skew margin and a retry cap. *Honest limits:* the directory is a trust boundary; tokens are never stored in it; NFS and exotic filesystems are unverified; missing jobs under `collect --partial` are `not_graded`, never zero.

## 23.13 CSV is an attack surface

A student can set their display name to `=HYPERLINK(...)`. *Decision:* free-text cells starting with `= + - @` (or tab/CR) get a leading apostrophe; identifiers and grades are untouched so LMS matching works. The first version of the test asserted the vulnerable behaviour as correct: a passing test proves only what its author believed.

## 23.14 AI as a reviewer, not a judge

*Decision:* the model may only suggest. Two opt-ins; the criterion stays pending; suggestions do not count toward review-load reduction; student code goes in as delimited data with break-outs neutralised; output is schema-constrained and clamped; whole files only with omissions listed and confidence capped; the audit log records the AI's number next to the human's, including disagreements. *Status:* wire format verified, behaviour tested with a fake client, never run against the live API from this codebase.

## 23.15 Dependencies without giving student code the network

*Context:* real assignments need `pytest` or `npm ci`, and the sandbox has no network. *Rejected:* install each student's requirements with network on (student-controlled build scripts get egress from the grader). *Decision:* `prepare`: cohort-level, teacher-authored, run once, baked into a cached image keyed on `(base image id, commands, file contents)`; students then run with no network as before. *Price (explicit):* a student's own requirements file is not honoured, which also makes grading repeatable and fair. *Hardening:* the Dockerfile JSON exec form, validated image references, `prepare` refused in web uploads, `--sandbox none` refuses rather than installing onto the host, a failing build stops the run before any student is graded.

## 23.16 Per-test credit, and where it can be forged

`junit` gives partial credit from a test runner's report, which is how most courses test. It inherits "a check satisfiable by doing nothing is a bug": zero tests earns nothing, committed reports are deleted, and the XML is parsed defensively. What it cannot fix: the runner imports the student's code in-process, so a determined student can forge the report. The docs say so and point to `cases` for high-stakes work.

## 23.17 Triage is the product

`triage.state` is `auto` or `review`; a review item always has a reason code (`criterion`, `similarity`, `borderline`, `fetch_failed`). `borderline` exists because a point either way matters most there. The summary reports **review load**, explicitly *modelled*, with its definition embedded in the report; nothing claims a measured time saving.

## 23.18 Audit trail by construction

`run.json` is not modified by decisions; `overrides.json` is an append-only log plus a derived effective view. A test asserts the raw file is byte-identical after overrides. This makes disputes tractable. (`retry` is the single, guarded operation that rewrites `run.json`.)

## 23.19 Grades are not released early

CSV grade columns are blank for `pending_review` and `error` unless `--include-pending`. The failure this prevents (a provisional score imported into a gradebook as final) is worse than a blank cell.

## 23.20 Plain-file reports and a published schema

`report.json` has a JSON Schema and a `schema_version`; real output (including with overrides) is validated in tests. JUnit XML lets any CI display a cohort natively.

## 23.21 Rubric builder: choices in, vetted YAML out

*Context:* hand-written YAML contradicts "automated". *Decision:* one module (`specbuilder.build`) takes choices and returns the spec, YAML, field-level problems and total; the form and the wizard are thin clients. The client never supplies a command; commands come from a curated catalogue; names and images pass strict regexes; newline and tab become spaces; the one free-text input that reaches `prepare` (packages) is gated behind `--allow-prepare` and a name regex on *every* path; the output goes through `load_spec`. *Evidence:* hostile-payload tests, YAML round-trips, a mutation check that the gate fails closed.

## 23.22 Web UI: server-rendered, strict, offline

No SPA, no CDN, no inline script or style (a test checks), strict CSP. Server rendering is simpler to secure and to run offline. Progress is polled from `status.json` on disk, so a reload or restart never loses state. Resolving a submission redirects to the next queue item, because reviewing forty repositories should be a flow. Posture: no authentication (stated plainly; localhost default; warns otherwise), cross-origin POST rejection, escaped evidence, confined uploads, no local paths.

## 23.23 The logo as code

*Decision:* the logo is generated from font outlines by a script that also writes the UI's inline SVG macros; a test fails if they drift. *Why:* one source of truth; the logo needs no font at runtime, so it renders identically in the README, the favicon and the app. The intro animation is a clip-path write-on followed by a stroke draw, verified frame by frame with the Web Animations API (Chrome's virtual time is unreliable for animations).

## 23.24 Files, not a database

*Decision:* plain files in a run directory. *Why:* diff-able, greppable, easy to back up and attach to a ticket, no migration story. *Cost:* concurrency must be explicit (atomic writes, one lock), and large-scale querying across runs would need an index; `markbook runs` simply scans.

## 23.25 CLI-first

*Decision:* the CLI is the product; the web UI is a layer over the same engine. *Why:* scriptable, CI-friendly, reproducible, and the UI cannot do anything the CLI cannot. Every page shows the equivalent CLI command with a copy button.

---

# Part 24. Limitations, unverified claims and roadmap

## 24.1 What is verified, what is not

| Claim | Status |
|---|---|
| Docker isolation controls | **Verified** against a real daemon (tests) |
| Podman support | **Verified only by the CI job**; not run on any maintainer machine |
| Real-image grading (Debian-based Python image) | **Verified** (demo graded identically to the host) |
| `prepare` with the real package index, cached | **Verified** (real `pip install pytest`; second run reuses the cache) |
| Queue: multiple processes on one machine, crash recovery, equivalence with local grading | **Verified** |
| Queue: multiple machines, NFS | **Not verified** |
| Canvas and Moodle CSV layouts | **Format-tested** against documented layouts; **not imported into a live instance** |
| AI assist | Wire format verified; behaviour tested with a fake client; **never run live** (`ai-check` is the way to find out) |
| "% less manual review" | **Modelled**, not measured (`stats` and a protocol exist to measure) |
| Accessibility | Contrast and structure tested; **no real screen-reader testing** |
| macOS | Claimed (README), **not in CI** |
| 40 submissions through a real Docker sandbox at `--jobs 8` in 9.6 s, 0 errors, 0 leaked containers | **Measured once, on the maintainer's machine and network** |

## 24.2 Known weaknesses

1. **Dependencies are the teacher's, not the student's.** A student's own requirements file is ignored (a deliberate trade-off). A queue run's report does not record a single prepared-image key.
2. **Timestamps.** Lateness uses commit time unless pinned. Pinning at the deadline works today; automating the capture of refs from GitHub Classroom does not exist.
3. **Similarity** is token-based; short assignments give short fingerprints; restructured copies evade it.
4. **`junit` trust limit.** A determined student can forge the report from inside the test process.
5. **Scale.** `grade` is thread-per-submission on one machine; the queue is single-machine-verified.
6. **No web authentication.**
7. **POSIX only** (uid mapping, `fcntl`).
8. **Review load is a model.**
9. **Container escapes** are outside the design's guarantees.
10. **Free-text commands in the builder** (the `run` fields) run inside the sandbox, with no network; they are the teacher's choice, but an operator exposing the web UI to untrusted users should not.

## 24.3 Roadmap (not built)

| Item | Why | Notes |
|---|---|---|
| Release pipeline (PyPI trusted publishing, GitHub Releases) | Real versioned releases | Part 22.3 #3 |
| Lint, type and security scanning in CI | Evidence for a tool that runs untrusted code | #1 |
| Snap and container image | Distribution for Ubuntu users | #4, #5 |
| Reusable GitHub Action | The tool *in* CI | #6 |
| `KubernetesSandbox` backend | Cluster-scale grading | Needs multi-machine testing first |
| LXD backend | A stronger, system-container boundary | A different design from OCI |
| gVisor / Kata runtime option | Hostile cohorts | `--runtime` flag passthrough |
| Web authentication (or a documented proxy recipe) | Safe multi-user deployment | Today: bind to localhost |
| Automated deadline capture from GitHub Classroom | Evidence-grade lateness without manual `pin` | |
| Live LMS import verification | Remove the "not imported" caveat | |
| Real-world measurement of the saving | Replace the model with data | The protocol exists |
| Prepared-image key in queue reports | Reproducibility in queue mode | Part 11.4 |
| Plugin-free extensibility for new check types | Without making specs executable | Keep the closed vocabulary |

## 24.4 What would change the design

- **Multiple tenants or untrusted spec authors:** add authentication, per-tenant run directories, and refuse all free-text commands.
- **Very large cohorts (thousands):** replace the thread pool with the queue as the default, add an index over runs, and add per-submission resource accounting.
- **A stronger isolation requirement:** make the container runtime a pluggable interface that can select `runsc` (gVisor) or Kata, or move to per-submission microVMs.

---

# Part 25. Operations guide

## 25.1 Requirements and install

- **Python ≥ 3.10**, **git**, and **Docker or Podman** to run student code (not needed for specs with no code-running criteria, or for `--sandbox none` inside an isolated environment).
- Linux; macOS is expected to work but is not in CI. Windows is not supported.

```bash
pip install .              # CLI only; the only dependency is PyYAML
pip install '.[web]'       # adds the web UI (Flask)
pip install '.[ai]'        # adds the optional AI assist (Anthropic SDK)
pip install -e '.[dev]'    # development: pytest, jsonschema, Flask, anthropic
markbook doctor            # checks Python, git, Docker/Podman, Flask, GITHUB_TOKEN
```

`doctor` reports each runtime (one usable runtime is enough), the optional web UI, and whether a GitHub token is set; `--clean` removes orphaned sandbox containers left by a killed run.

## 25.2 Typical workflows

**A first look (no Docker):** `markbook demo`, then `markbook review <run>` and `markbook show <run> bob`.

**A real course, CLI:**

```bash
markbook init my-course                       # wizard: spec.yaml + roster.csv
markbook validate my-course/spec.yaml
markbook pin my-course/spec.yaml --roster my-course/roster.csv --at now --bundle bundles/   # when the deadline passes
markbook grade my-course/spec.yaml --roster my-course/roster.pinned.csv --lms canvas
markbook review <run>                          # work the queue
markbook override <run> bob -c quality -p 3 -m "Clean decomposition."
markbook report <run> --lms canvas             # regenerate after decisions
```

**A real course, web:** `markbook serve`; *New run → Build it with the form*, paste the roster, *Start grading*, then work the review queue in the browser; *Export* the CSVs.

**Private repositories:** `export GITHUB_TOKEN=…` (read-only is enough).

**Big cohorts:** `enqueue`, several `worker` processes, `collect` (Part 17).

**Re-run failures after fixing access:** `markbook retry <run>`.

## 25.3 Where things live

| Thing | Location |
|---|---|
| Runs | `.markbook/runs/<run-id>/` (or `--out`, or `serve --dir`) |
| Saved rubric templates | `<runs dir>/../specs/` (a `<slug>.yaml` and a `<slug>.builder.json`) |
| Prepared images | The container runtime, tagged `markbook-prepared:<key16>`, label `markbook=prepared` |
| Pins | `<roster>.pinned.csv`, `pins.json`, optional `bundles/*.bundle` |
| Queues | The directory you give `enqueue` |

## 25.4 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `sandbox unavailable: Docker is required…` (exit 3) | Docker not running, or no permission | Start Docker; add your user to the docker group; or use Podman; or, inside an isolated environment, `--sandbox none` |
| `podman started the container but resource limits are NOT enforced` | Rootless Podman without cgroup v2 delegation | Delegate `cpu cpuset io memory pids` to your user (systemd `Delegate=`), run Podman as root, or use Docker |
| `image … is not available locally and could not be pulled` | Offline or typo | Pull the image once; for Podman use fully qualified names (`docker.io/library/python:3.12-slim`) |
| `the prepare step failed, so no student was graded` | A bad dependency command or network | Read the output tail in the message; fix `prepare`; `markbook prepare spec.yaml` to retry |
| Every code criterion is `error` + review | The sandbox failed per submission (for example the image lacks `tar`) | Read the evidence; fix the image; `markbook retry` |
| `spec … sandbox.image: required` | A code-running criterion without an image | Add `sandbox: {image: …}` |
| `deadline: must include a timezone` | `2026-03-01T23:59:00` | Add `+01:00` or `Z` |
| A repository shows `not_found_or_private` | Private repo, no token | Export `GITHUB_TOKEN`; then `markbook retry` |
| History counts show `—` | Shallow clone (no `git` check) | Add a `git` check if you need history |
| `retry` refuses: spec hash differs | The spec changed after the run | Re-run the whole cohort |
| Grade column blank in the CSV | Submission pending review or errored | Resolve it, or export with `--include-pending` |
| The web page cannot start a run: "Docker unavailable" (503) | The server's runtime is down | Start it, or restart `serve` with another `--sandbox` |
| Extra packages not accepted in the form | The server was not started with `--allow-prepare` | Start with `--allow-prepare` (operator decision) |
| A containers list shows `markbook-…` leftovers | A killed run | `markbook doctor --clean` |

## 25.5 Running the server for others

The web UI has **no authentication**. Binding to anything but `127.0.0.1` prints a warning. If you expose it, put it behind an authenticating reverse proxy and a TLS terminator, keep the runs directory private, and do not enable `--allow-prepare` for people you do not trust (it lets typed package names reach a networked build step). A `GET /healthz` endpoint supports probes.

## 25.6 Backups and retention

A run is a directory: copy it. `run.json` plus `overrides.json` are the source of truth; everything else is derived and regenerated by `markbook report`. Delete runs from the UI or with `rm -r`; remove prepared images with `docker image prune --filter label=markbook=prepared -a`.

## 25.7 Privacy

Markbook sends nothing anywhere by default. The optional AI assist sends source code to the Anthropic API only when *both* the spec marks the criterion and the run passes `--ai`, and prints a warning naming the number of submissions. The view log stores reviewer name, run, submission and time; no IP, no user agent. Reports contain names and emails from the roster: handle exports accordingly.


---

# Appendices

# Appendix A. Every constant and limit

| Area | Constant | Value | Where |
|---|---|---|---|
| Version | `__version__` | `3.0.0` | `markbook/__init__.py` |
| Report schema | `SCHEMA_VERSION` | `"1"` | `markbook/__init__.py` |
| Grader | `MAX_REPO_BYTES` | 200 MiB | `grader.py` |
| Grader | default `jobs` | 4 | `cli.py`, `create_app` |
| Spec | default `sandbox.timeout` | 10 s | `spec.py` |
| Spec | default `sandbox.memory` | `256m` | `spec.py` |
| Spec | default `sandbox.cpus` | 1.0 | `spec.py` |
| Spec | default `sandbox.pids` | 128 | `spec.py` |
| Spec | default `sandbox.disk` | `64m` | `spec.py` |
| Spec | default `scale` | 100 | `spec.py` |
| Spec | default `borderline_margin` | `0.02 × scale` (when `pass_mark` set) | `spec.py` |
| Spec | default `prepare.timeout` | 600 s | `spec.py` |
| Spec | `MAX_PREPARE_FILE` | 1 MiB | `spec.py` |
| Spec | image regex | `^[A-Za-z0-9][A-Za-z0-9._/:@+-]*$` | `spec.py` |
| Spec | similarity defaults | enabled, threshold 0.6, `min_fingerprints` 20 | `spec.py` |
| Sandbox | `OUTPUT_CAP` | 64 KiB per stream | `sandbox.py` |
| Sandbox | tmpfs `/tmp` | `rw,exec,size=64m` | `sandbox.py` |
| Sandbox | container PID 1 | `sleep 3600` | `sandbox.py` |
| Sandbox | outer timeout margin | `t + 15` s | `sandbox.py` |
| Sandbox | `_NOBODY` | `65534:65534` | `sandbox.py` |
| Sandbox | image pull timeout | 900 s | `sandbox.py` |
| Fetch | clone timeout | 180 s (60 s for small ops) | `fetch.py` |
| Fetch | shallow depth | 1 | `fetch.py` |
| History | `git log` limit | 2000 commits | `gitinfo.py` |
| Checks | `MAX_EVIDENCE_CHARS` | 600 | `checks.py` |
| Checks | `MAX_REPORT_BYTES` | 1 MiB | `checks.py` |
| Checks | `MAX_FAILURES_SHOWN` | 10 | `checks.py` |
| Checks | `junit` default `min_tests` | 1 | `checks.py` |
| Similarity | `K`, `WINDOW` | 6, 4 | `similarity.py` |
| Similarity | `MAX_FILE_BYTES` | 200 000 | `similarity.py` |
| Similarity | `common_share`, `common_min_cohort` | 0.5, 6 | `similarity.py` |
| Similarity | owners per fingerprint | 2 to 50 | `similarity.py` |
| Prepare | `TAG_PREFIX`, `KEY_VERSION` | `markbook-prepared`, 1 | `prepare.py` |
| Prepare | key length in tag | 16 hex | `prepare.py` |
| Queue | `QUEUE_SCHEMA`, `MAX_ATTEMPTS` | 1, 3 | `queue.py` |
| Queue | default lease | 300 s | `queue.py` |
| Queue | lease margin | `min(30, 0.25 × lease)` | `queue.py` |
| Queue | heartbeat interval | `lease / 4` | `queue.py` |
| Stats | `DEFAULT_IDLE_MINUTES`, `MIN_BASELINE_N` | 5.0, 5 | `stats.py` |
| Views | `MAX_REVIEWER` | 60 | `views.py` |
| AI | `DEFAULT_MODEL` | `claude-opus-5-5` | `ai.py` |
| AI | `MAX_CHARS`, `MAX_FILE_BYTES`, `MAX_OUTPUT_TOKENS` | 60 000, 200 000, 16 000 | `ai.py` |
| AI | `ai-check` default `--max-cost-usd` | 0.50 | `ai_check.py` |
| AI | `ai-check` exit codes | 0 pass, 1 failed, 6 unavailable | `ai_check.py` |
| Web | `MAX_UPLOAD` | 2 MiB | `web/app.py` |
| Web | `MAX_ROSTER_ROWS` | 1000 | `web/app.py` |
| Web | `MAX_BUILDER_JSON` | 200 000 bytes | `web/app.py` |
| Web | run id regex | `^[A-Za-z0-9_-]{1,64}$` | `web/app.py` |
| Web | template file regex | `^[a-z0-9][a-z0-9_-]{0,60}\.yaml$` | `web/app.py` |
| Builder | limits | 30 criteria, 40 cases, 40 paths, name 120, text 500 | `specbuilder.py` |
| CLI | exit codes | 0, 1, 2, 3, 4, 5, 6, 130 | `cli.py` |
| Splash | duration | ≈ 2.9 s (write 1.35 s, tick 0.4 s) | `markbook.css` |

# Appendix B. The `report.json` shape (schema `urn:markbook:report:1`)

Top level (all required): `schema_version`, `tool{name, version}`, `run_id`, `created_at`, `finished_at`, `assignment`, `submissions[]`, `similarity[]`, `summary`. Optional: `inputs` (where the spec and roster came from, used by `retry`).

`submission` fields: `id`, `name`, `email`, `repo`, `ref`, `commit`, `status`, `error`, `criteria[]`, `history`, `late`, `notes`, `similarity`, `duration_s`, `score`, `triage`, `reviewer_note`.

`criterion` fields: `id`, `title`, `type`, `max_points`, `points`, `status`, `evidence[{label, text}]`, `needs_review`, `review_reason`, `override`, `suggestion`.

`similarity[]` pairs: `{a, b, score, shared_fingerprints, files[{a_file, b_file, shared, score}]}`.

Consumers should ignore unknown properties; breaking changes bump `schema_version`.

# Appendix C. Repository map

```
markbook/
├── pyproject.toml            package metadata, extras (web, ai, dev), package-data, pytest config
├── README.md                 user-facing guide
├── LICENSE                   MIT
├── .github/workflows/ci.yml  the CI pipeline (Part 22)
├── docs/
│   ├── DESIGN.md             design notes
│   ├── MEASURING.md          the time-saving protocol
│   ├── AI-CHECK.md           verifying the AI assist
│   ├── BRAND.md              logo, colour, type, motion
│   └── assets/{brand,screenshots}/   logo SVGs and PNGs, and build scripts in brand/src/
├── markbook/
│   ├── __init__.py  __main__.py
│   ├── cli.py            commands and exit codes
│   ├── grader.py         the engine
│   ├── spec.py roster.py specbuilder.py wizard.py
│   ├── fetch.py gitinfo.py pin.py
│   ├── sandbox.py prepare.py
│   ├── checks.py similarity.py ai.py ai_check.py
│   ├── store.py retry.py views.py stats.py report.py queue.py demo.py
│   ├── schema/report.schema.json
│   ├── ai_fixtures/      synthetic submissions for ai-check
│   └── web/
│       ├── app.py
│       ├── templates/    base index new run submission progress error _ui
│       └── static/       markbook.css markbook.js boot.js builder.js favicon.svg apple-touch-icon.png fonts/
└── tests/                22 test modules, conftest.py, frontend/{drive.js, builder.js}
```

# Appendix D. Glossary

| Term | Definition |
|---|---|
| **Active review time** | Sum of gaps between a reviewer's consecutive decisions that are at most the idle cutoff |
| **Assignment block** | The rubric facts copied into a run so it can be read without the spec |
| **Baseline (review)** | Every criterion of every submission checked by hand; the denominator of review load |
| **Borderline** | A triage reason: the score is within the margin of the pass mark |
| **Check** | The procedure that scores a criterion |
| **Clear (a flag)** | A human decision that a similarity or borderline flag is benign |
| **Corpus** | A submission's `{file: fingerprints}` map |
| **Criterion** | One scored rubric line |
| **Decision** | A recorded human action (points, waiver, clear, note) |
| **Effective overrides** | The derived view of the latest decision per target |
| **Evidence** | `{label, text}` pairs recording what the grader saw |
| **Fingerprint** | A selected k-gram hash used in similarity |
| **Forensics** | Reading history and fingerprints before student code runs |
| **Guard rule** | An enforced, tested protection (Part 20.4) |
| **Hidden (case/tests)** | Expected output or test names are never shown in evidence or feedback |
| **Lease** | How long a queue worker may be silent before its jobs are reclaimed |
| **Manual** | A criterion only a human can score |
| **Outcome** | A check's result: fraction, status, evidence, review reason |
| **Overlay** | Teacher files copied over each submission |
| **Pending points** | Points of criteria still awaiting a human |
| **Pin** | A recorded commit SHA at the deadline |
| **Prepare** | The cohort-level, networked, teacher-authored dependency step |
| **Provisional** | A grade that still has pending criteria |
| **Queue (review)** | The list of submissions needing a human |
| **Queue (job)** | A directory of job files for distributed grading |
| **Raw result** | The unmodified machine output (`run.json`) |
| **Reason code** | `criterion`, `similarity`, `borderline`, `fetch_failed` |
| **Review load** | The modelled share of review items versus checking everything by hand |
| **Rescore** | The pure function that derives scores, triage and summary |
| **Roster** | The CSV of students and repositories |
| **Run** | One grading session's record |
| **Sandbox** | The isolated environment for student code |
| **Scaled grade** | `final / max_points × scale` |
| **Spec** | The rubric as YAML |
| **Starter** | Teacher starter code subtracted in similarity |
| **Submission** | One student's graded repository |
| **Triage** | `auto` or `review`, with reasons |
| **Winnowing** | The fingerprint-selection algorithm used for similarity |

# Appendix E. A complete worked example

**Scenario.** A Node.js course. Forty students push a small blog API. The teacher wants file checks, a README, incremental history, hidden API tests with per-test credit, and a manual design mark. Lateness: 10 % per day, capped at 50 %.

**1. Build the rubric.** The teacher opens *New run → Build it with the form*, types the name, picks Node.js, keeps *Required files*, *README*, ticks *Git history*, *Test suite* (hidden), adds one *Manual review*, sets the deadline and late penalty. The preview shows 20 points and the YAML. (Hidden tests live in an `overlay/` directory, so the teacher saves the YAML and uses it as a saved spec with `serve --specs`, or uses the CLI.)

**2. Prepare the roster** (`roster.csv`):

```csv
id,name,email,repo
danyn,Daniel Takou,d@x.edu,https://github.com/Dany-hardi/blog-api
alice,Alice Martin,a@x.edu,alice/blog-api
```

**3. Pin at the deadline.** `markbook pin spec.yaml --roster roster.csv --at now --bundle bundles/` writes `roster.pinned.csv` with each student's tip SHA and `pinned_at`, plus `pins.json`.

**4. Grade.** `markbook grade spec.yaml --roster roster.pinned.csv --lms canvas --jobs 8`. Preflight verifies Docker, pulls `node:20` once, builds the `prepare` image (`npm ci`) once. Forty containers run, each from the prepared image with no network. About ten seconds later the table prints and `Next: markbook review <run>`.

**5. Read the queue.** `markbook review <run>` lists, say, 11 items: every student's `design` criterion is manual (40 items would be by hand → 40), plus one similarity pair, one `borderline`, one `fetch_failed`. Review load reports the modelled reduction.

**6. Resolve.** For each: `markbook show <run> bob`, then `markbook override <run> bob -c design -p 4 -m "Good layering, weak error handling."`. For the benign similarity: `--clear similarity -m "Both used the course helper."`. For the unfetchable repository: after the student fixes access, `markbook retry <run>`.

**7. Export.** `markbook report <run> --lms canvas`; import `grades.canvas.csv` into a sandbox course first, then the real one. `feedback/` holds a file per student.

**8. Audit.** `overrides.json` shows each decision with reviewer, time and what it replaced; `run.json` is unchanged. `markbook stats <run>` reports the review effort the log shows.

# Appendix F. Frequently asked questions

**Is it safe to run student code with Markbook?** It applies layered isolation (no network, no privileges, resource ceilings, a size-capped tmpfs, no host mounts) and tests each control against a real daemon. It is containment, not a guarantee against kernel or runtime escapes; for hostile audiences add gVisor, Kata or LXD virtual machines.

**Why does it ignore my students' `requirements.txt` / `package.json`?** Installing them would execute student-controlled build scripts with network access from the grader. You declare dependencies once in `prepare`; that also makes grading repeatable.

**Can the AI change a grade?** No. It can only suggest, behind two opt-ins; a human accepts or changes it; the log records both numbers.

**Does the similarity flag mean plagiarism?** No. It is a routing signal that sends a pair to a human with the matching files; it never changes a grade.

**Why are some grades blank in the CSV?** They are pending review or errored. Resolve them, or export with `--include-pending`.

**Why not a database?** Files are diff-able, greppable and easy to back up and attach to a ticket, with no migrations.

**Can I write the spec by hand?** Yes; `markbook validate` checks it. The form and wizard are conveniences over the same format.

**How do I know the review-load figure?** It is modelled: items needing a human divided by checking every criterion of every submission by hand, with the definition in the report. Use `markbook stats` and the protocol in Part 19 to measure a real saving.

**Does it work with Canvas and Moodle?** It writes CSVs in their documented import layouts, format-tested. They have not been imported into a live instance; test in a sandbox course first.

**Does it run on Windows?** No (POSIX only).

**Why Fraunces and Dancing Script?** Fraunces is the heading face (bundled, OFL); Dancing Script supplies the logo's cursive outlines (OFL), converted to paths so no font is needed at runtime.

**What was built with AI assistance?** The project was developed with heavy assistance from an AI coding assistant, directed and reviewed by its author; commit trailers record it. The design decisions in Part 23 are the author's, and the tests are the evidence.

# Appendix G. Suggested reading order for a new maintainer

1. This guide: Parts 1, 3, 4, 23.
2. `docs/DESIGN.md` and the README.
3. `markbook/spec.py`: `load_spec` (how validation collects every error).
4. `markbook/grader.py`: `grade_submission`, `rescore`/`_score_one`, `summarize`, `assemble_run`.
5. `markbook/sandbox.py`: `run_argv`, `ContainerSandbox.load` (the tar approach), `run_capped`.
6. `markbook/checks.py`: `cases` (why exit 0 is required), `junit`.
7. `markbook/store.py`: `_locked`, `record_decision`.
8. `markbook/similarity.py`: `tokenize`, `fingerprints`, `compare`.
9. `tests/test_docker_sandbox.py`, `tests/test_cli_store.py`, `tests/test_junit.py`.
10. Run `pytest -q` once and read the output; run `markbook demo` and read `summary.md`.

# Appendix H. Checklist for changing Markbook safely

- Adding a **check type**: add it to `CHECK_TYPES`, `_REQUIRED`, `_ALLOWED` in `spec.py`; implement it in `checks.py` and register it in `REGISTRY`; decide its `fraction` semantics and ask "what is the laziest way to pass this?" and close it; add evidence labels; add tests including a hostile case; update the schema if fields change; update the builder catalogue if teachers should be able to tick it.
- Changing a **sandbox flag**: change `run_argv` only; update the table-driven test for both binaries; run the real-runtime tests under Docker and let CI run Podman.
- Changing **scoring**: change `_score_one`/`summarize` only; keep them pure; update the worked example in Part 7.2.
- Changing **report fields**: update `report.schema.json` and bump `SCHEMA_VERSION` if the change is breaking; tests validate real output against the schema.
- Touching **`run.json`**: don't. If you must (like `retry`), explain why the audit guarantee survives and add a test.
- Adding a **web route**: same-origin guard applies automatically to POST; add a size cap if it takes input; no inline script or style; add it to Part 15.2.
- Changing the **logo or brand**: edit `build_logo.py`, regenerate, run `tests/test_brand.py`.
- Before merging: `pytest -q` with Docker, `NODE_PATH` for the front end; CI green on all Python versions and the Podman job; update this guide's Appendix A if a constant changed.

---

*End of the Markbook Complete Documentation and Design Guide.*
