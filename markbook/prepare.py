"""Cohort-level dependency preparation.

Real assignments need dependencies (`pip install pytest requests`, a Maven cache, `npm ci`), but the
student sandbox has no network on purpose. The tempting fix, installing each student's own
requirements with network access, is the wrong one: `pip install` / `npm install` execute
student-controlled build scripts, so it would hand strangers' code network access from the grader
(data exfiltration, and a route into whatever the grader machine can reach).

Instead the teacher declares the dependency step in the spec. It runs ONCE per cohort, with network,
using only teacher-authored commands and files, and the result is baked into a cached image:

    spec:  prepare: {files: [requirements.txt], run: [pip install --no-cache-dir -r requirements.txt]}
    here:  docker build  ->  markbook-prepared:<key>   (cached; rebuilt only when an input changes)

Every student container is then started from that image exactly as before: `--network none`,
non-root, capabilities dropped, no host mounts. Student code never has network access.
Consequence, stated plainly in the docs: a student's own requirements file is NOT installed;
the dependencies are the ones the teacher chose, which is also what makes grading fair and repeatable.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .sandbox import CONTAINER_RUNTIMES, SandboxUnavailable, ensure_image, image_present
from .spec import Spec

TAG_PREFIX = "markbook-prepared"
KEY_VERSION = 1


def _base_image_id(binary: str, image: str) -> str:
    r = subprocess.run([binary, "image", "inspect", "--format", "{{.Id}}", image],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else ""


def file_digests(spec: Spec) -> dict[str, str]:
    out = {}
    for rel in spec.prepare.files:
        p = spec.source_dir / rel
        if p.is_symlink() or not p.is_file():       # re-checked at build time: the spec dir may have changed
            raise SandboxUnavailable(f"prepare.files: {rel!r} is no longer a regular file next to the spec")
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def build_key(spec: Spec, base_id: str) -> str:
    """Identity of the prepared image: base image id + commands + the content of every input file."""
    payload = {"v": KEY_VERSION, "base": spec.sandbox.image, "base_id": base_id,
               "run": list(spec.prepare.run), "files": file_digests(spec)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def tag_for(key: str) -> str:
    return f"{TAG_PREFIX}:{key[:16]}"


def render_dockerfile(base: str, commands: tuple[str, ...], has_files: bool, key: str) -> str:
    """Pure and unit-tested. Commands go through the JSON exec form, so a newline, quote or `$` in a
    command can never be re-interpreted by the Dockerfile parser (no injection through the spec)."""
    lines = [f"FROM {base}", f"LABEL markbook.prepare.key={key[:16]}", "WORKDIR /prepare"]
    if has_files:
        lines.append("COPY files/ /prepare/")
    lines += [f"RUN {json.dumps(['sh', '-c', c])}" for c in commands]
    return "\n".join(lines) + "\n"


def _tail(text: str, n: int = 4000) -> str:
    text = text.strip()
    return text if len(text) <= n else "…" + text[-n:]


def ensure_prepared(spec: Spec, runtime: str, log: Callable[[str], None] | None = None, *,
                    force: bool = False) -> Spec:
    """Return `spec` with `sandbox.image` replaced by the prepared image, building it if needed."""
    if spec.prepare is None:
        return spec
    if runtime not in CONTAINER_RUNTIMES:
        raise SandboxUnavailable(
            "this spec has a `prepare` step, which builds a dependency image and so needs a container "
            "runtime (--sandbox docker or podman). With --sandbox none nothing is installed for you: "
            "install the dependencies on this machine yourself and remove `prepare` from the spec.")
    say = log or (lambda m: None)
    base = spec.sandbox.image
    ensure_image(base, log, runtime)
    key = build_key(spec, _base_image_id(runtime, base))
    tag = tag_for(key)
    info = {"tag": tag, "key": key[:16], "base_image": base, "commands": list(spec.prepare.run),
            "files": file_digests(spec), "cached": True}

    if force or not image_present(runtime, tag):
        info["cached"] = False
        say(f"preparing dependencies ({len(spec.prepare.run)} command(s), with network) → {tag} …")
        ctx = Path(tempfile.mkdtemp(prefix="markbook_prepare_"))
        try:
            if spec.prepare.files:
                (ctx / "files").mkdir()
                for rel in spec.prepare.files:
                    dest = ctx / "files" / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(spec.source_dir / rel, dest)
            (ctx / "Dockerfile").write_text(
                render_dockerfile(base, spec.prepare.run, bool(spec.prepare.files), key), encoding="utf-8")
            argv = [runtime, "build", "-t", tag, "--label", "markbook=prepared"]
            if force:
                argv.append("--no-cache")
            argv.append(str(ctx))
            try:
                r = subprocess.run(argv, capture_output=True, text=True, timeout=spec.prepare.timeout)
            except subprocess.TimeoutExpired as exc:
                raise SandboxUnavailable(
                    f"the `prepare` step did not finish within {spec.prepare.timeout}s "
                    "(raise prepare.timeout in the spec if it is legitimately slow)") from exc
            if r.returncode != 0:
                hint = (" (Podman needs fully qualified base image names, e.g. docker.io/library/python:3.12-slim)"
                        if runtime == "podman" else "")
                raise SandboxUnavailable("the `prepare` step failed, so no student was graded. Output (tail):\n"
                                         + _tail(r.stdout + "\n" + r.stderr) + hint)
        finally:
            shutil.rmtree(ctx, ignore_errors=True)
    else:
        say(f"dependencies already prepared ({tag}, cached)")

    return dataclasses.replace(spec, sandbox=dataclasses.replace(spec.sandbox, image=tag), prepared=info)
