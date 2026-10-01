"""`markbook ai-check`: one cheap, safe way to find out whether the AI reviewer assist works for real.

The assist (markbook/ai.py) was built and tested against a fake client and the SDK's wire format, but never
against the live API. This module exercises the exact production path (`Reviewer.create` then
`Reviewer.suggest`) on small synthetic submissions shipped in `markbook/ai_fixtures/`, and reports what the
model did. The suite is a *smoke test with deliberately wide ranges*, not a benchmark: it can tell you the
integration works and that the model did not fall for the obvious attacks, nothing finer.

Everything takes an injectable client, so the PASS/FAIL logic is tested offline with a fake.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

from .ai import (AiUnavailable, MAX_OUTPUT_TOKENS, Reviewer, SYSTEM, build_prompt, collect_sources,
                 estimate_cost_usd)

EXIT_OK, EXIT_FAILED, EXIT_UNAVAILABLE = 0, 1, 6   # 5 is taken by `pin` (partial failure)

# The gate assumes this many output tokens per call. The answer is schema-bounded (rationale <= 1500 chars,
# <= 5 concerns of <= 300 chars, so roughly 700 tokens), plus some headroom for model reasoning. The hard
# ceiling (production max_tokens = 16000 per call) is reported separately but would make the default
# --max-cost-usd unusable for the default model.
ASSUMED_OUTPUT_TOKENS = 3000
CHARS_PER_TOKEN = 4

_FIXTURES = resources.files("markbook").joinpath("ai_fixtures")


def load_manifest() -> dict:
    return json.loads(_FIXTURES.joinpath("manifest.json").read_text(encoding="utf-8"))


@contextlib.contextmanager
def fixture_root(dirname: str | None):
    """A real directory for a fixture (an empty temp dir for `null`), cleaned up afterwards."""
    if dirname is None:
        with tempfile.TemporaryDirectory(prefix="markbook-ai-check-") as tmp:
            yield Path(tmp)
    else:
        with resources.as_file(_FIXTURES.joinpath(dirname)) as path:
            yield Path(path)


class Recorder:
    """Wraps an SDK-shaped client, remembering each `messages.create` call (and passing it through)."""

    def __init__(self, client):
        self.calls: list[dict] = []
        self._client = client
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return self._client.messages.create(**kw)

    def __getattr__(self, name):          # models, etc.
        return getattr(self._client, name)


# ── step 1: connectivity ─────────────────────────────────────────────────────

def credential_source(environ=None) -> str:
    """Name where credentials probably come from, never the secret itself."""
    env = os.environ if environ is None else environ
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if env.get(var):
            return f"environment variable {var}"
    if env.get("ANTHROPIC_PROFILE"):
        return "profile named by environment variable ANTHROPIC_PROFILE"
    return "no API-key environment variable; a local profile (`ant auth login`) would be used if one exists"


def check_connectivity(model: str, client=None) -> tuple[Reviewer | None, dict]:
    """Returns (reviewer or None, info). info: sdk, credentials, model, ok, error."""
    info = {"sdk": None, "credentials": None, "model": model, "ok": False, "error": None, "injected_client": client is not None}
    if client is None:
        try:
            import anthropic
        except ImportError:
            info.update(sdk=False, error="the Anthropic SDK is not installed: pip install 'markbook[ai]'")
            return None, info
        info["sdk"] = getattr(anthropic, "__version__", "installed")
        info["credentials"] = credential_source()
    else:
        info.update(sdk="not needed (injected client)", credentials="not needed (injected client)")
    try:
        reviewer = Reviewer.create(model, client=client)
    except AiUnavailable as exc:
        info["error"] = str(exc)
        return None, info
    info["ok"] = True
    return reviewer, info


# ── cost ─────────────────────────────────────────────────────────────────────

def _prompt_for(fx: dict, root: Path) -> tuple[str, bool]:
    crit = fx["criterion"]
    files, omitted = collect_sources(root, fx.get("max_chars") or Reviewer().max_chars)
    if not files:
        return "", False
    return build_prompt(title=crit["title"], guidance=crit["guidance"], max_points=crit["max_points"],
                        auto_results=[], files=files, omitted=omitted), True


def estimate_suite(model: str, fixtures: list[dict]) -> dict:
    """Input size and cost bounds for the calls the suite would make (fixtures with no source make none)."""
    in_tokens, calls = 0.0, 0
    for fx in fixtures:
        with fixture_root(fx["dir"]) as root:
            prompt, will_call = _prompt_for(fx, root)
        if will_call:
            calls += 1
            in_tokens += (len(SYSTEM) + len(prompt)) / CHARS_PER_TOKEN
    return {
        "api_calls": calls,
        "input_tokens": round(in_tokens),
        "gate_usd": estimate_cost_usd(model, in_tokens, calls * ASSUMED_OUTPUT_TOKENS),
        "ceiling_usd": estimate_cost_usd(model, in_tokens, calls * MAX_OUTPUT_TOKENS),
    }


def _usd(v: float | None) -> str:
    return "unknown (no published price for this model in markbook/ai.py)" if v is None else f"${v:.3f}"


# ── expectations ─────────────────────────────────────────────────────────────

def _delimiters_intact(call: dict) -> bool:
    text = call["messages"][-1]["content"]
    return (text.count("<submission>") == 1 and text.count("</submission>") == 1
            and text.count("<rubric_criterion>") == 1 and text.count("</rubric_criterion>") == 1
            and text.count("<coverage>") == 1 and text.count("<automated_results>") == 1
            and text.count("<file path=") == text.count("</file>"))


def evaluate(fx: dict, result: dict, calls: list[dict]) -> list[str]:
    """Why this fixture failed its expectation (empty list = pass)."""
    exp, max_points, why = fx["expect"], float(fx["criterion"]["max_points"]), []
    if "api_calls" in exp and len(calls) != exp["api_calls"]:
        why.append(f"expected {exp['api_calls']} API call(s), made {len(calls)}")
    if "error" in exp:
        if result.get("error") != exp["error"]:
            why.append(f"expected error {exp['error']!r}, got {result.get('error') or 'a suggestion'!r}")
        return why
    if "error" in result:
        return why + [f"call failed: {result['error']}"]
    points = result["points"]
    if "min_fraction" in exp and points < exp["min_fraction"] * max_points:
        why.append(f"scored {points:g}/{max_points:g}, expected at least {exp['min_fraction'] * max_points:g}")
    if "max_fraction" in exp and points > exp["max_fraction"] * max_points:
        why.append(f"scored {points:g}/{max_points:g}, expected at most {exp['max_fraction'] * max_points:g}"
                   + (" (inflated?)" if "concerns_match" in exp or exp.get("delimiters_intact") else ""))
    if "concerns_match" in exp and not re.search(exp["concerns_match"], " ".join(result["concerns"]), re.IGNORECASE):
        why.append("concerns do not mention the manipulation attempt")
    if exp.get("delimiters_intact") and not all(_delimiters_intact(c) for c in calls):
        why.append("a delimiter break-out reached the prompt un-neutralised")
    if "truncated" in exp and result["truncated"] is not exp["truncated"]:
        why.append(f"truncated={result['truncated']}, expected {exp['truncated']}")
    if result["confidence"] in exp.get("confidence_not", ()):
        why.append(f"confidence {result['confidence']!r} is not allowed here")
    return why


# ── running ──────────────────────────────────────────────────────────────────

def run_fixture(fx: dict, reviewer: Reviewer) -> dict:
    """Run one fixture through Reviewer.suggest (the production path) and judge it."""
    recorder = Recorder(reviewer._client)
    probe = Reviewer(reviewer.model, recorder, fx.get("max_chars") or reviewer.max_chars)
    crit = fx["criterion"]
    with fixture_root(fx["dir"]) as root:
        result = probe.suggest(title=crit["title"], guidance=crit["guidance"], max_points=crit["max_points"],
                               root=root, auto_results=[])
    reasons = evaluate(fx, result, recorder.calls)
    usage = result.get("usage") or {}
    cost = estimate_cost_usd(reviewer.model, usage.get("input_tokens") or 0, usage.get("output_tokens") or 0) \
        if usage else None
    return {"name": fx["name"], "passed": not reasons, "reasons": reasons, "result": result,
            "api_calls": len(recorder.calls), "cost_usd": cost}


def run_suite(reviewer: Reviewer, fixtures: list[dict] | None = None) -> list[dict]:
    return [run_fixture(fx, reviewer) for fx in (fixtures if fixtures is not None else load_manifest()["fixtures"])]


def render_table(rows: list[dict], max_points_by_name: dict[str, float]) -> str:
    head = f"{'fixture':<20} {'verdict':<7} {'points':<9} {'confidence':<10} {'tokens in/out':<14} notes"
    lines = [head, "-" * len(head)]
    for r in rows:
        res = r["result"]
        usage = res.get("usage") or {}
        pts = f"{res['points']:g}/{max_points_by_name[r['name']]:g}" if "points" in res else "-"
        toks = f"{usage.get('input_tokens', '-')}/{usage.get('output_tokens', '-')}" if usage else "-"
        notes = "; ".join(r["reasons"]) if r["reasons"] else (res.get("error") or "")
        lines.append(f"{r['name']:<20} {'PASS' if r['passed'] else 'FAIL':<7} {pts:<9} {res.get('confidence', '-'):<10} "
                     f"{toks:<14} {notes}")
    return "\n".join(lines)


def _describe(result: dict, max_points: float) -> list[str]:
    if "error" in result:
        return [f"  error: {result['error']}"]
    usage = result.get("usage") or {}
    return [f"  points:     {result['points']:g}/{max_points:g}",
            f"  confidence: {result['confidence']}",
            f"  rationale:  {result['rationale']}",
            f"  concerns:   {result['concerns'] or 'none'}",
            f"  truncated:  {result['truncated']}  omitted: {result['omitted_files'] or 'none'}",
            f"  tokens:     {usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out"]


def run(*, model: str, suite: bool = False, as_json: bool = False, max_cost_usd: float = 0.50,
        client=None, out=print) -> int:
    """Entry point behind `markbook ai-check`. Returns the process exit code."""
    report: dict = {"model": model, "live": client is None}

    def say(line: str = "") -> None:
        if not as_json:
            out(line)

    def finish(code: int) -> int:
        report["exit_code"] = code
        if as_json:
            out(json.dumps(report, indent=2, default=str))
        return code

    manifest = load_manifest()
    fixtures = manifest["fixtures"]
    if suite:
        est = estimate_suite(model, fixtures)
        report["estimate"] = est
        say(f"suite: {est['api_calls']} API call(s), ~{est['input_tokens']} input tokens. "
            f"Cost estimate (assuming {ASSUMED_OUTPUT_TOKENS} output tokens/call): {_usd(est['gate_usd'])}; "
            f"hard ceiling at max_tokens={MAX_OUTPUT_TOKENS}/call: {_usd(est['ceiling_usd'])}.")
        if est["gate_usd"] is not None and est["gate_usd"] > max_cost_usd:
            say(f"refusing to start: estimate {_usd(est['gate_usd'])} exceeds --max-cost-usd {max_cost_usd:.2f}. "
                "Raise the limit or choose a cheaper --model. Nothing was sent.")
            report["refused"] = True
            return finish(EXIT_FAILED)
        if est["gate_usd"] is None:
            say("note: cost gate skipped, this model has no known price.")

    say(f"[1/{'3' if suite else '2'}] connectivity (model {model})")
    reviewer, info = check_connectivity(model, client)
    report["connectivity"] = info
    say(f"  SDK installed:     {info['sdk'] if info['sdk'] else 'NO'}")
    if info["credentials"]:
        say(f"  credentials:       {info['credentials']}")
    if reviewer is None:
        say(f"  model reachable:   NO  ({info['error']})")
        say("not run: nothing was sent to the model. Exit code 6 means 'AI unavailable', not 'failed'.")
        return finish(EXIT_UNAVAILABLE)
    say(f"  model reachable:   yes ({'client injected, not checked' if info['injected_client'] else 'models.retrieve succeeded'})")

    smoke = manifest["smoke"]
    say(f"\n[2/{'3' if suite else '2'}] one real call through Reviewer.suggest on a tiny synthetic submission")
    crit = smoke["criterion"]
    with fixture_root(smoke["dir"]) as root:
        result = reviewer.suggest(title=crit["title"], guidance=crit["guidance"], max_points=crit["max_points"],
                                  root=root, auto_results=[])
    report["smoke"] = result
    for line in _describe(result, crit["max_points"]):
        say(line)
    usage = result.get("usage") or {}
    if usage:
        cost = estimate_cost_usd(model, usage.get("input_tokens") or 0, usage.get("output_tokens") or 0)
        report["smoke_cost_usd"] = cost
        say(f"  estimated cost: {_usd(cost)} (published per-token price x reported usage)")
    code = EXIT_FAILED if "error" in result else EXIT_OK
    if code:
        say("the call failed; fix that before trying --suite.")
        return finish(code)

    if suite:
        say("\n[3/3] regression suite (smoke test with deliberately wide ranges, not a benchmark)")
        rows = run_suite(reviewer, fixtures)
        points = {fx["name"]: float(fx["criterion"]["max_points"]) for fx in fixtures}
        say(render_table(rows, points))
        passed = sum(r["passed"] for r in rows)
        spent = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
        report["suite"] = {"rows": rows, "passed": passed, "failed": len(rows) - passed,
                           "cost_usd": sum(spent) if spent else None}
        say(f"\n{passed}/{len(rows)} passed; estimated cost of the suite: {_usd(report['suite']['cost_usd'])}")
        if passed != len(rows):
            say("A failure means the prompt/schema/effort needs a look, see docs/AI-CHECK.md.")
            code = EXIT_FAILED
    return finish(code)
