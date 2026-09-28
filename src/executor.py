"""
Code execution engine.
Tries Docker first (sandboxed); falls back to subprocess if Docker is unavailable.
Interactive programs are detected and skipped rather than hung.
"""
import os
import re
import time
import shutil
import tempfile
import subprocess
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

DOCKER_IMAGES: dict[str, str] = {
    "Python":     "python:3.11-slim",
    "C":          "gcc:12",
    "C++":        "gcc:12",
    "Java":       "openjdk:17-slim",
    "JavaScript": "node:18-slim",
    "TypeScript": "node:18-slim",
}

COMPILE_CMDS: dict[str, str] = {
    "C":    "gcc {src} -o {exe} -lm",
    "C++":  "g++ {src} -o {exe} -lm",
    "Java": "javac {src}",
}

RUN_CMDS: dict[str, str] = {
    "C":          "{exe}",
    "C++":        "{exe}",
    "Python":     "python3 {src}",
    "Java":       "java -cp {dir} {cls}",
    "JavaScript": "node {src}",
    "TypeScript": "npx ts-node {src}",
}

INTERACTIVE_RE: dict[str, list[str]] = {
    "Python":     [r"\binput\s*\(", r"\braw_input\s*\("],
    "C":          [r"\bscanf\s*\(", r"\bfgets\s*\(", r"\bgets\s*\("],
    "C++":        [r"\bcin\s*>>", r"\bgetline\s*\(stdin"],
    "Java":       [r"\bScanner\b", r"\bBufferedReader\b"],
    "JavaScript": [r"\breadlineSync\b", r"\bprocess\.stdin\b"],
}


@dataclass
class ExecResult:
    success: bool
    output: str
    error: str
    execution_time: float
    exit_code: int
    skipped: bool = False
    skip_reason: str = ""


def _is_interactive(content: str, language: str) -> bool:
    for pattern in INTERACTIVE_RE.get(language, []):
        if re.search(pattern, content):
            return True
    return False


def _docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


_DOCKER_OK: bool | None = None  # cached once per process


def _check_docker() -> bool:
    global _DOCKER_OK
    if _DOCKER_OK is None:
        _DOCKER_OK = _docker_available()
    return _DOCKER_OK


# ── Docker executor ──────────────────────────────────────────────────────────

def _run_docker(code: str, language: str, filename: str,
                timeout: int = 10) -> ExecResult:
    image = DOCKER_IMAGES.get(language)
    if not image:
        return ExecResult(False, "", f"No Docker image for {language}", 0, -1)

    tmpdir = tempfile.mkdtemp(prefix="ge_docker_")
    try:
        src = os.path.join(tmpdir, filename)
        with open(src, "w", encoding="utf-8") as f:
            f.write(code)

        # Build the in-container command
        if language in ("C", "C++"):
            exe_name = "program"
            compile_cmd = COMPILE_CMDS[language].format(
                src=f"/work/{filename}", exe=f"/work/{exe_name}")
            run_cmd = f"/work/{exe_name}"
            cmd = f"sh -c '{compile_cmd} && {run_cmd}'"
        elif language == "Java":
            cls = os.path.splitext(filename)[0]
            cmd = f"sh -c 'javac /work/{filename} && java -cp /work {cls}'"
        else:
            run_tmpl = RUN_CMDS[language]
            in_container = run_tmpl.format(
                src=f"/work/{filename}",
                dir="/work",
                cls=os.path.splitext(filename)[0],
                exe=f"/work/program",
            )
            cmd = in_container

        docker_cmd = [
            "docker", "run", "--rm",
            "--memory=128m", "--cpus=0.5",
            "--network=none",
            "--security-opt=no-new-privileges",
            "-v", f"{tmpdir}:/work",
            image,
            "sh", "-c", cmd,
        ]

        start = time.perf_counter()
        proc = subprocess.run(
            docker_cmd, capture_output=True, text=True, timeout=timeout + 15
        )
        elapsed = time.perf_counter() - start

        return ExecResult(
            success=proc.returncode == 0,
            output=proc.stdout[:4000],
            error=proc.stderr[:2000],
            execution_time=round(elapsed, 3),
            exit_code=proc.returncode,
        )

    except subprocess.TimeoutExpired:
        return ExecResult(False, "", "Execution timeout", timeout, -1)
    except Exception as exc:
        return ExecResult(False, "", str(exc), 0, -1)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Subprocess executor (fallback) ───────────────────────────────────────────

def _run_subprocess(code: str, language: str, filename: str,
                    timeout: int = 10) -> ExecResult:
    tmpdir = tempfile.mkdtemp(prefix="ge_sub_")
    try:
        src = os.path.join(tmpdir, filename)
        with open(src, "w", encoding="utf-8") as f:
            f.write(code)

        # Compile step
        if language in ("C", "C++"):
            exe = os.path.join(tmpdir, "program")
            comp = COMPILE_CMDS[language].format(src=src, exe=exe)
            cr = subprocess.run(comp, shell=True, capture_output=True,
                                text=True, timeout=30)
            if cr.returncode != 0:
                return ExecResult(False, "", cr.stderr[:2000], 0, -1)
            run_cmd = exe

        elif language == "Java":
            cr = subprocess.run(f'javac "{src}"', shell=True,
                                capture_output=True, text=True, timeout=30)
            if cr.returncode != 0:
                return ExecResult(False, "", cr.stderr[:2000], 0, -1)
            cls = os.path.splitext(filename)[0]
            run_cmd = f'java -cp "{tmpdir}" {cls}'

        elif language == "Python":
            run_cmd = f'python3 "{src}"'

        elif language == "JavaScript":
            run_cmd = f'node "{src}"'

        else:
            return ExecResult(False, "", f"Unsupported language: {language}", 0, -1)

        start = time.perf_counter()
        proc = subprocess.run(
            run_cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, input=""
        )
        elapsed = time.perf_counter() - start

        return ExecResult(
            success=proc.returncode == 0,
            output=proc.stdout[:4000],
            error=proc.stderr[:2000],
            execution_time=round(elapsed, 3),
            exit_code=proc.returncode,
        )

    except subprocess.TimeoutExpired:
        return ExecResult(False, "", "Execution timeout", float(timeout), -1)
    except Exception as exc:
        return ExecResult(False, "", str(exc), 0, -1)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Public interface ─────────────────────────────────────────────────────────

def execute_code(code: str, language: str, filename: str,
                 timeout: int = 10, use_docker: bool = True) -> ExecResult:
    """
    Execute the given source code. Returns an ExecResult.
    Skips interactive programs gracefully instead of hanging.
    """
    if not code.strip():
        return ExecResult(False, "", "Empty file", 0, -1)

    if _is_interactive(code, language):
        return ExecResult(
            success=False, output="", error="",
            execution_time=0, exit_code=0,
            skipped=True,
            skip_reason="Program requires interactive input — execution skipped",
        )

    if language not in DOCKER_IMAGES and language not in RUN_CMDS:
        return ExecResult(False, "", f"Language '{language}' not supported for execution",
                          0, -1)

    if use_docker and _check_docker():
        return _run_docker(code, language, filename, timeout)
    else:
        return _run_subprocess(code, language, filename, timeout)


def execution_grade(result: ExecResult) -> float:
    """Convert an ExecResult to a grade component out of 20."""
    if result.skipped:
        return 10.0   # Neutral: can't penalise interactive programs
    if result.success:
        return 20.0
    if result.exit_code == -1 and not result.error:
        return 2.0    # Timeout or unknown error
    if result.error:
        return 4.0    # Attempted but failed — partial credit
    return 0.0
