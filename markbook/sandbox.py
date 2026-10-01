"""Execution sandboxes for untrusted student code.

Two runtimes, selected explicitly. There is deliberately no silent fallback
from Docker to the host: if you asked for isolation and it is unavailable,
grading stops with an actionable error.

  docker  one throwaway container per submission, shared by every command:
          no network, all capabilities dropped, non-root, PID/memory/CPU caps,
          and a size-capped tmpfs as the only writable area. No host path is
          mounted; sources are streamed in as a tar archive.
  none    runs on the host with time/file-size ulimits and a scrubbed
          environment. For use *inside* an already isolated environment
          (CI job, VM, LXD container) and for tests. It is NOT a sandbox.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .spec import SandboxSpec

OUTPUT_CAP = 64 * 1024  # bytes kept per stream


class SandboxUnavailable(RuntimeError):
    """The requested runtime cannot be used on this machine."""


@dataclass
class ExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    truncated: bool = False


def _decode(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


def run_capped(argv: list[str], *, stdin: str | None, timeout: float, cwd: str | None = None,
               env: dict[str, str] | None = None, new_session: bool = False,
               cap: int = OUTPUT_CAP) -> ExecResult:
    """Run a process, keeping at most `cap` bytes of each output stream.

    A student program that prints gigabytes must not exhaust our memory, so the
    pipes are drained incrementally rather than with communicate().
    """
    start = time.perf_counter()
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=cwd, env=env, start_new_session=new_session,
    )
    bufs: dict[str, bytearray] = {"out": bytearray(), "err": bytearray()}
    truncated = threading.Event()

    def drain(stream, key: str) -> None:
        buf = bufs[key]
        while chunk := stream.read(8192):
            room = cap - len(buf)
            if room > 0:
                buf.extend(chunk[:room])
            if len(chunk) > room:
                truncated.set()

    threads = [threading.Thread(target=drain, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, "err"), daemon=True)]
    for t in threads:
        t.start()

    def feed() -> None:
        try:
            if stdin:
                proc.stdin.write(stdin.encode())
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threading.Thread(target=feed, daemon=True).start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL) if new_session else proc.kill()
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
    for t in threads:
        t.join(timeout=2)
    return ExecResult(
        exit_code=None if timed_out else proc.returncode,
        stdout=_decode(bytes(bufs["out"])), stderr=_decode(bytes(bufs["err"])),
        duration=round(time.perf_counter() - start, 3),
        timed_out=timed_out, truncated=truncated.is_set(),
    )


def _normalise_member(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Drop host ownership; keep it readable/writable for the container user."""
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = (info.mode & 0o777) | 0o600 | (0o100 if info.isdir() else 0)
    return info


class Sandbox:
    """Interface: load a source tree, run commands, clean up."""

    runtime = "abstract"

    def load(self, src_dir: Path) -> None: raise NotImplementedError
    def exec(self, cmd: str, stdin: str | None = None, timeout: int | None = None) -> ExecResult:
        raise NotImplementedError
    def close(self) -> None: ...

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()


class LocalSandbox(Sandbox):
    runtime = "none"

    def __init__(self, cfg: SandboxSpec):
        self.cfg = cfg
        self._tmp = tempfile.mkdtemp(prefix="markbook_local_")
        self.work = Path(self._tmp) / "work"

    def load(self, src_dir: Path) -> None:
        shutil.copytree(src_dir, self.work, symlinks=True)

    def exec(self, cmd: str, stdin: str | None = None, timeout: int | None = None) -> ExecResult:
        t = timeout or self.cfg.timeout
        wrapper = 'ulimit -t "$1"; ulimit -f 65536; exec sh -c "$2"'
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.work),
               "LANG": "C.UTF-8", "TMPDIR": str(self.work)}
        return run_capped(["sh", "-c", wrapper, "markbook", str(t + 2), cmd], stdin=stdin,
                          timeout=t, cwd=str(self.work), env=env, new_session=True)

    def close(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


class DockerSandbox(Sandbox):
    runtime = "docker"

    def __init__(self, cfg: SandboxSpec):
        if not cfg.image:
            raise SandboxUnavailable("sandbox.image is not set in the spec")
        if not shutil.which("docker"):
            raise SandboxUnavailable("docker not found on PATH. Install Docker, or pass --sandbox none "
                                     "when already running inside an isolated environment.")
        self.cfg = cfg
        self.name = f"markbook-{uuid.uuid4().hex[:10]}"
        self._started = False

    def _docker(self, *args: str, stdin: str | None = None, timeout: float = 120) -> ExecResult:
        return run_capped(["docker", *args], stdin=stdin, timeout=timeout)

    def _start(self) -> None:
        c = self.cfg
        r = self._docker(
            "run", "-d", "--name", self.name, "--label", "markbook=1",
            "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", c.memory, "--memory-swap", c.memory, "--cpus", str(c.cpus),
            "--pids-limit", str(c.pids), "--user", f"{os.getuid()}:{os.getgid()}",
            "--tmpfs", "/tmp:rw,exec,size=64m", "--tmpfs", f"/work:rw,exec,size={c.disk},mode=1777",
            "-w", "/work", "-e", "HOME=/tmp", "-e", "LANG=C.UTF-8",
            c.image, "sleep", "3600", timeout=600,  # first run may pull the image
        )
        if r.exit_code != 0:
            raise SandboxUnavailable(f"could not start container from {c.image!r}: {r.stderr.strip()[:400]}")
        self._started = True

    def load(self, src_dir: Path) -> None:
        self._start()
        # Stream a tar into the container: no bind mount, so student code can
        # never see or touch a host path, and the tmpfs size cap applies.
        # The archive is built in Python and deliberately has no "." entry:
        # GNU tar (Debian/Ubuntu images) tries to set the mtime and mode of the
        # root-owned /work mount itself and exits non-zero, busybox does not.
        proc = subprocess.Popen(["docker", "exec", "-i", "-w", "/work", self.name, "tar", "-xf", "-"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|") as tar:
                for child in sorted(Path(src_dir).iterdir()):
                    tar.add(child, arcname=child.name, filter=_normalise_member)
        except (BrokenPipeError, OSError):
            pass  # the container-side error below explains it
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            # Detach the closed pipe: communicate() flushes stdin if it is still set, and
            # Python < 3.13 raises "ValueError: flush of closed file" (3.13 tolerates it).
            proc.stdin = None
        _, err = proc.communicate(timeout=120)
        if proc.returncode != 0:
            raise SandboxUnavailable("failed to copy sources into the container (does the image have `tar`?): "
                                     + _decode(err)[:300])

    def exec(self, cmd: str, stdin: str | None = None, timeout: int | None = None) -> ExecResult:
        t = timeout or self.cfg.timeout
        # `timeout -s KILL` runs inside the container so a runaway process is
        # really killed, not just abandoned by the docker client.
        argv = ["docker", "exec", "-i", "-w", "/work", self.name,
                "timeout", "-s", "KILL", str(t), "sh", "-c", cmd]
        res = run_capped(argv, stdin=stdin, timeout=t + 15)
        if res.exit_code == 137 and res.duration >= t - 0.2:
            res.timed_out, res.exit_code = True, None
        return res

    def close(self) -> None:
        if self._started:
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, timeout=60)
            self._started = False


def make_sandbox(runtime: str, cfg: SandboxSpec) -> Sandbox:
    if runtime == "docker":
        return DockerSandbox(cfg)
    if runtime == "none":
        return LocalSandbox(cfg)
    raise SandboxUnavailable(f"unknown sandbox runtime {runtime!r} (use 'docker' or 'none')")


def docker_status() -> tuple[bool, str]:
    """Return (usable, detail) for `markbook doctor`."""
    if not shutil.which("docker"):
        return False, "docker not found on PATH"
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return False, "docker daemon did not answer within 10s"
    if r.returncode != 0:
        return False, (r.stderr.strip().splitlines() or ["docker daemon unreachable"])[-1]
    return True, f"docker {r.stdout.strip()}"


def ensure_image(image: str, log=None) -> None:
    """Make sure `image` is present locally, pulling it once, before any submission starts.

    Without this, N parallel workers would each start a container and trigger N
    simultaneous pulls inside the grading window (and their timeouts).
    """
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0:
        return
    if log:
        log(f"pulling {image} (first use)…")
    try:
        r = subprocess.run(["docker", "pull", "--quiet", image], capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired as exc:
        raise SandboxUnavailable(f"pulling {image!r} timed out after 15 minutes") from exc
    if r.returncode != 0:
        raise SandboxUnavailable(f"image {image!r} is not available locally and could not be pulled: "
                                 + (r.stderr.strip().splitlines() or ["unknown error"])[-1])


def remove_orphans() -> int:
    """Remove containers left behind by a killed run (labelled markbook=1). Returns how many."""
    r = subprocess.run(["docker", "ps", "-aq", "--filter", "label=markbook=1"], capture_output=True, text=True, timeout=30)
    ids = r.stdout.split()
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, timeout=120)
    return len(ids)
