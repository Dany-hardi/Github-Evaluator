"""Execution sandboxes for untrusted student code.

Container runtimes (docker, podman) plus an explicit host mode. There is
deliberately no silent fallback from a container runtime to the host: if you
asked for isolation and it is unavailable, grading stops with an actionable error.

  docker  one throwaway container per submission, shared by every command:
          no network, all capabilities dropped, non-root, PID/memory/CPU caps,
          and a size-capped tmpfs as the only writable area. No host path is
          mounted; sources are streamed in as a tar archive.
  podman  the same controls through Podman (daemonless, rootless by default);
          additionally verifies that the resource limits are really enforced.
  none    runs on the host with time/file-size ulimits and a scrubbed
          environment. For use *inside* an already isolated environment
          (CI job, VM, LXD container) and for tests. It is NOT a sandbox.
"""
from __future__ import annotations

import json
import os
import shlex
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


def safe_relative(path: str) -> str | None:
    """Return a normalised relative path, or None if it is absolute, empty or escapes via `..`."""
    p = Path(path)
    if not path.strip() or p.is_absolute() or ".." in p.parts or "\x00" in path:
        return None
    return p.as_posix()


class Sandbox:
    """Interface: load a source tree, run commands, clean up."""

    runtime = "abstract"

    def load(self, src_dir: Path) -> None: raise NotImplementedError
    def exec(self, cmd: str, stdin: str | None = None, timeout: int | None = None,
             cap: int = OUTPUT_CAP) -> ExecResult:
        raise NotImplementedError

    def read_file(self, path: str, max_bytes: int = 1 << 20) -> tuple[str | None, str | None]:
        """Read one file the commands produced, from the working tree. Returns (text, error).

        The path must be relative and stay inside the tree, and at most `max_bytes` are read:
        the file's contents are influenced by student code, so they are treated as hostile input.
        """
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

    def exec(self, cmd: str, stdin: str | None = None, timeout: int | None = None,
             cap: int = OUTPUT_CAP) -> ExecResult:
        t = timeout or self.cfg.timeout
        wrapper = 'ulimit -t "$1"; ulimit -f 65536; exec sh -c "$2"'
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.work),
               "LANG": "C.UTF-8", "TMPDIR": str(self.work)}
        return run_capped(["sh", "-c", wrapper, "markbook", str(t + 2), cmd], stdin=stdin,
                          timeout=t, cwd=str(self.work), env=env, new_session=True, cap=cap)

    def read_file(self, path: str, max_bytes: int = 1 << 20) -> tuple[str | None, str | None]:
        rel = safe_relative(path)
        if rel is None:
            return None, f"unsafe report path {path!r}: it must be relative and stay inside the project"
        target = self.work / rel
        # A symlink the student planted could point at any host file; refuse links outright.
        if target.is_symlink() or not target.resolve().is_relative_to(self.work.resolve()):
            return None, f"{rel} is a symlink or escapes the project; refusing to read it"
        if not target.is_file():
            return None, f"{rel} was not produced"
        data = target.read_bytes()[: max_bytes + 1]
        if len(data) > max_bytes:
            return None, f"{rel} is larger than {max_bytes // 1024} KiB"
        return data.decode("utf-8", errors="replace"), None

    def close(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


CONTAINER_RUNTIMES = ("docker", "podman")
RUNTIMES = (*CONTAINER_RUNTIMES, "none")

# Rootless Podman has no real "root" to run the student as, see ContainerSandbox.user.
_NOBODY = "65534:65534"


class ContainerSandbox(Sandbox):
    """One throwaway container per submission, driven through an OCI-style CLI.

    `binary` is "docker" or "podman". Every isolation flag is identical for both
    (see `run_argv`); the places where Podman genuinely differs are marked with
    a comment saying why, and the ones we cannot verify without a daemon fail
    loudly instead of degrading silently.
    """

    runtime = "abstract"
    binary = ""

    def __init__(self, cfg: SandboxSpec, binary: str | None = None):
        if binary:
            self.binary = binary
            self.runtime = binary
        if not cfg.image:
            raise SandboxUnavailable("sandbox.image is not set in the spec")
        if not shutil.which(self.binary):
            raise SandboxUnavailable(f"{self.binary} not found on PATH. Install {self.binary.capitalize()}, or pass "
                                     "--sandbox none when already running inside an isolated environment.")
        self.cfg = cfg
        self.name = f"markbook-{uuid.uuid4().hex[:10]}"
        self._started = False

    def _cli(self, *args: str, stdin: str | None = None, timeout: float = 120) -> ExecResult:
        return run_capped([self.binary, *args], stdin=stdin, timeout=timeout)

    @property
    def user(self) -> str:
        """uid:gid the student code runs as inside the container."""
        if self.binary == "podman" and os.getuid() == 0:
            # Docker keeps the host uid (existing behaviour). For Podman started as host root that would be
            # container root, the one thing we must never run student code as; use `nobody` instead.
            return _NOBODY
        # Rootless Podman: the host user is mapped to container root, so `--user N:M` selects a *sub-uid* of
        # the user namespace, an unprivileged uid that owns nothing on the host (stronger than
        # `--userns=keep-id`, which would map the student onto the real host uid). The price: N must lie
        # inside the /etc/subuid range, otherwise `run` fails and we report Podman's own message.
        return f"{os.getuid()}:{os.getgid()}"

    def run_argv(self) -> list[str]:
        """Full argv of the `run` that creates the sandbox container (pure; unit-tested for both runtimes)."""
        c = self.cfg
        return [
            self.binary, "run", "-d", "--name", self.name, "--label", "markbook=1",
            "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", c.memory, "--memory-swap", c.memory, "--cpus", str(c.cpus),
            "--pids-limit", str(c.pids), "--user", self.user,
            # Podman's tmpfs default is noexec,nosuid,nodev; "exec" is explicit here so builds can run, and the
            # /work mode=1777 lets the (sub-uid) student write into a mount that is root-owned in the userns.
            "--tmpfs", "/tmp:rw,exec,size=64m", "--tmpfs", f"/work:rw,exec,size={c.disk},mode=1777",
            "-w", "/work", "-e", "HOME=/tmp", "-e", "LANG=C.UTF-8",
            c.image, "sleep", "3600",
        ]

    def _start(self) -> None:
        r = run_capped(self.run_argv(), stdin=None, timeout=600)  # first run may pull the image
        if r.exit_code != 0:
            hint = ""
            if self.binary == "podman":
                hint = (" (rootless Podman: check /etc/subuid, and use a fully qualified image name such as "
                        "docker.io/library/python:3.12-slim)")
            raise SandboxUnavailable(f"could not start container from {self.cfg.image!r}: "
                                     f"{r.stderr.strip()[:400]}{hint}")
        self._started = True
        self._verify_limits()

    def _verify_limits(self) -> None:
        """Docker applies limits or errors. Rootless Podman can warn and run WITHOUT them (no cgroup v2
        delegation), which would silently void the design's resource caps, so read what the kernel enforces."""
        if self.binary != "podman":
            return
        probe = ('for f in memory.max memory.swap.max pids.max cpu.max; do '
                 'printf "%s=" "$f"; cat /sys/fs/cgroup/$f 2>/dev/null || echo MISSING; done')
        r = self._cli("exec", self.name, "sh", "-c", probe, timeout=60)
        got = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
        problems = []
        for key in ("memory.max", "pids.max"):
            if got.get(key, "MISSING") in ("max", "MISSING"):
                problems.append(f"{key}={got.get(key, 'unreadable')}")
        if got.get("cpu.max", "MISSING").split()[:1] in (["max"], ["MISSING"], []):
            problems.append(f"cpu.max={got.get('cpu.max', 'unreadable')}")
        if got.get("memory.swap.max", "0") not in ("0", "MISSING"):
            problems.append(f"memory.swap.max={got['memory.swap.max']}")
        if r.exit_code != 0 or problems:
            self.close()
            raise SandboxUnavailable(
                "podman started the container but resource limits are NOT enforced inside it ("
                + (", ".join(problems) or f"probe failed: {r.stderr.strip()[:200]}") + "). Refusing to run student "
                "code without memory/CPU/PID caps. Rootless Podman needs cgroup v2 with the memory, cpu and pids "
                "controllers delegated to your user (systemd: Delegate=cpu cpuset io memory pids in "
                "user@.service), or run Podman as root. Use --sandbox docker, or --sandbox none inside an "
                "isolated environment.")

    def load(self, src_dir: Path) -> None:
        self._start()
        # Stream a tar into the container: no bind mount, so student code can
        # never see or touch a host path, and the tmpfs size cap applies.
        # The archive is built in Python and deliberately has no "." entry:
        # GNU tar (Debian/Ubuntu images) tries to set the mtime and mode of the
        # root-owned /work mount itself and exits non-zero, busybox does not.
        # (Ownership: members are written as uid/gid 0 and tar run as a non-root
        # user does not chown, so files end up owned by the student uid under
        # Docker and under rootless Podman's sub-uid alike.)
        proc = subprocess.Popen([self.binary, "exec", "-i", "-w", "/work", self.name, "tar", "-xf", "-"],
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

    def exec(self, cmd: str, stdin: str | None = None, timeout: int | None = None,
             cap: int = OUTPUT_CAP) -> ExecResult:
        t = timeout or self.cfg.timeout
        # `timeout -s KILL` runs inside the container so a runaway process is
        # really killed, not just abandoned by the container client.
        argv = [self.binary, "exec", "-i", "-w", "/work", self.name,
                "timeout", "-s", "KILL", str(t), "sh", "-c", cmd]
        res = run_capped(argv, stdin=stdin, timeout=t + 15, cap=cap)
        if res.exit_code == 137 and res.duration >= t - 0.2:
            res.timed_out, res.exit_code = True, None
        return res

    def read_file(self, path: str, max_bytes: int = 1 << 20) -> tuple[str | None, str | None]:
        rel = safe_relative(path)
        if rel is None:
            return None, f"unsafe report path {path!r}: it must be relative and stay inside the project"
        # `head -c` bounds what leaves the container; a symlink inside the container can only reach
        # the container's own filesystem, never a host path.
        r = self.exec(f"head -c {max_bytes + 1} -- {shlex.quote(rel)}", timeout=30, cap=max_bytes + 2)
        if r.timed_out or r.exit_code != 0:
            return None, f"{rel} was not produced" + (f" ({r.stderr.strip()[:120]})" if r.stderr.strip() else "")
        if len(r.stdout.encode("utf-8", errors="replace")) > max_bytes:
            return None, f"{rel} is larger than {max_bytes // 1024} KiB"
        return r.stdout, None

    def close(self) -> None:
        if self._started:
            if self.binary == "podman":
                # PID 1 is `sleep`, which ignores SIGTERM, so `podman rm -f` would wait out its stop timeout
                # (10 s) per submission. Kill first (best effort; `rm -t` is not in every Podman version).
                subprocess.run([self.binary, "kill", self.name], capture_output=True, timeout=60)
            subprocess.run([self.binary, "rm", "-f", self.name], capture_output=True, timeout=60)
            self._started = False


class DockerSandbox(ContainerSandbox):
    runtime = "docker"
    binary = "docker"


class PodmanSandbox(ContainerSandbox):
    runtime = "podman"
    binary = "podman"


def make_sandbox(runtime: str, cfg: SandboxSpec) -> Sandbox:
    if runtime == "docker":
        return DockerSandbox(cfg)
    if runtime == "podman":
        return PodmanSandbox(cfg)
    if runtime == "none":
        return LocalSandbox(cfg)
    raise SandboxUnavailable(f"unknown sandbox runtime {runtime!r} (use 'docker', 'podman' or 'none')")


def container_status(binary: str) -> tuple[bool, str]:
    """Return (usable, detail) for `markbook doctor` and preflight."""
    if not shutil.which(binary):
        return False, f"{binary} not found on PATH"
    if binary == "podman":
        return _podman_status()
    try:
        r = subprocess.run([binary, "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return False, f"{binary} daemon did not answer within 10s"
    if r.returncode != 0:
        return False, (r.stderr.strip().splitlines() or [f"{binary} daemon unreachable"])[-1]
    return True, f"{binary} {r.stdout.strip()}"


def docker_status() -> tuple[bool, str]:
    return container_status("docker")


def podman_status() -> tuple[bool, str]:
    return container_status("podman")


def _podman_status() -> tuple[bool, str]:
    # JSON, not a Go template: the JSON key names (host.cgroupVersion, host.cgroupControllers,
    # host.security.rootless, version.Version) are the ones I know; anything missing or
    # unparsable is reported as a failure rather than guessed at.
    try:
        r = subprocess.run(["podman", "info", "--format", "json"], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "podman did not answer `podman info` within 30s"
    if r.returncode != 0:
        return False, (r.stderr.strip().splitlines() or ["podman info failed"])[-1]
    try:
        info = json.loads(r.stdout)
        version = info["version"]["Version"]
        host = info["host"]
        rootless = bool(host["security"]["rootless"])
        cgver = str(host["cgroupVersion"])
        controllers = set(host["cgroupControllers"] or [])
    except (ValueError, KeyError, TypeError) as exc:
        return False, (f"could not read cgroup/rootless fields from `podman info --format json` ({exc!r}); "
                       "refusing to guess")
    detail = f"podman {version}" + (" rootless" if rootless else " rootful")
    if rootless:
        missing = {"memory", "cpu", "pids"} - controllers
        if cgver != "v2" or missing:
            return False, (f"{detail}: resource limits cannot be enforced (cgroup {cgver}, "
                           f"missing controllers for your user: {sorted(missing) or 'none'}). Rootless Podman needs "
                           "cgroup v2 with memory, cpu and pids delegated (systemd Delegate=cpu cpuset io memory pids)")
    return True, detail


def image_present(binary: str, image: str) -> bool:
    # Podman's `image exists` exits 0/1 by design (and 125 on error); Docker has no such command.
    have = [binary, "image", "exists", image] if binary == "podman" else [binary, "image", "inspect", image]
    return subprocess.run(have, capture_output=True).returncode == 0


def ensure_image(image: str, log=None, binary: str = "docker") -> None:
    """Make sure `image` is present locally, pulling it once, before any submission starts.

    Without this, N parallel workers would each start a container and trigger N
    simultaneous pulls inside the grading window (and their timeouts).
    """
    if image_present(binary, image):
        return
    if log:
        log(f"pulling {image} (first use)…")
    try:
        r = subprocess.run([binary, "pull", "--quiet", image], capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired as exc:
        raise SandboxUnavailable(f"pulling {image!r} timed out after 15 minutes") from exc
    if r.returncode != 0:
        hint = (" (Podman needs fully qualified names, e.g. docker.io/library/python:3.12-slim)"
                if binary == "podman" else "")
        raise SandboxUnavailable(f"image {image!r} is not available locally and could not be pulled: "
                                 + (r.stderr.strip().splitlines() or ["unknown error"])[-1] + hint)


def remove_orphans(binary: str = "docker") -> int:
    """Remove containers left behind by a killed run (labelled markbook=1). Returns how many."""
    r = subprocess.run([binary, "ps", "-aq", "--filter", "label=markbook=1"],
                       capture_output=True, text=True, timeout=30)
    ids = r.stdout.split()
    if ids:
        subprocess.run([binary, "rm", "-f", *ids], capture_output=True, timeout=120)
    return len(ids)
