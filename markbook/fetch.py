"""Fetching student repositories.

One `git clone` replaces dozens of Contents-API calls: it is faster, is not
subject to API rate limits, includes history (needed for forensics) and the
real build files. Credentials are passed through the environment, never argv,
so they do not show up in `ps`.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FetchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Checkout:
    path: Path        # root of the clone
    workdir: Path     # clone root, or the requested sub-directory
    commit: str       # full SHA that was graded
    url: str
    shallow: bool = False   # True: only the tip commit is available


_SHORTHAND = re.compile(r"^[\w.-]+/[\w.-]+$")
_TREE = re.compile(r"^(https://github\.com/[^/]+/[^/]+?)(?:\.git)?/tree/([^/]+)(?:/(.*))?$")
_SCP_GITHUB = re.compile(r"^git@github\.com:([\w.-]+/[\w.-]+?)(?:\.git)?$")


def normalize_repo(repo: str) -> tuple[str, str | None, str]:
    """Return (clone_url_or_path, ref_from_url, subdir_from_url)."""
    repo = repo.strip()
    if m := _TREE.match(repo.rstrip("/")):
        return m.group(1) + ".git", m.group(2), (m.group(3) or "").strip("/")
    if m := _SCP_GITHUB.match(repo):
        return f"https://github.com/{m.group(1)}.git", None, ""
    if repo.startswith("github.com/"):
        repo = "https://" + repo
    if _SHORTHAND.match(repo) and not os.path.exists(repo):
        return f"https://github.com/{repo}.git", None, ""
    return repo, None, ""


def _git_env(token: str | None, url: str) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "LANG": "C",
    }
    if token and url.startswith("https://github.com/"):
        cred = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
                   GIT_CONFIG_VALUE_0=f"Authorization: Basic {cred}")
    return env


def _classify(stderr: str) -> tuple[str, str]:
    s = stderr.lower()
    if "repository not found" in s or "could not read username" in s or "authentication failed" in s:
        return "not_found_or_private", ("Repository not found or private. Check the URL, "
                                        "or provide a token with read access (GITHUB_TOKEN).")
    if "could not resolve host" in s or "unable to access" in s or "timed out" in s:
        return "network", "Network error while cloning: " + stderr.strip().splitlines()[-1][:200]
    if any(k in s for k in ("couldn't find remote ref", "remote branch", "pathspec", "unknown revision",
                            "not our ref", "invalid reference", "bad revision", "needed a single revision")):
        return "ref_not_found", "The requested branch, tag or commit does not exist in the repository."
    return "clone_failed", (stderr.strip().splitlines() or ["git clone failed"])[-1][:300]


def checkout(repo: str, dest: Path, *, ref: str | None = None, token: str | None = None,
             allow_local: bool = False, shallow: bool = False, timeout: int = 180) -> Checkout:
    """Clone `repo` into `dest`. With shallow=True only the tip commit is fetched
    (much faster; enough for lateness, not for history checks)."""
    url, url_ref, subdir = normalize_repo(repo)
    ref = ref or url_ref

    is_local = os.path.exists(url) or (allow_local and url.startswith("file://"))
    if is_local and not allow_local:
        raise FetchError("invalid_url", "Local paths are not accepted here; use an https:// repository URL.")
    if not is_local and not (url.startswith("https://") or url.startswith("http://")):
        raise FetchError("invalid_url", f"Unsupported repository location: {repo!r}")
    if url.startswith("-") or (ref and ref.startswith("-")):
        raise FetchError("invalid_url", "Repository and ref must not start with '-'.")

    env = _git_env(token, url)
    base = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never",
            "-c", "protocol.file.allow=" + ("always" if is_local else "never")]
    # Plain local paths ignore --depth; only remote URLs (and file://) benefit.
    depth = ["--depth", "1"] if shallow and not (is_local and not url.startswith("file://")) else []

    def git(*args: str, t: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run([*base, *args], capture_output=True, text=True, timeout=t, env=env)

    try:
        if ref and depth:
            # Fetch exactly the requested ref (branch, tag or SHA) at depth 1.
            for step in (["init", "--quiet", str(dest)],
                         ["-C", str(dest), "remote", "add", "origin", url]):
                r = git(*step)
                if r.returncode != 0:
                    raise FetchError(*_classify(r.stderr))
            r = git("-C", str(dest), "fetch", "--quiet", "--no-tags", "--depth", "1", "origin", ref, t=timeout)
            if r.returncode != 0:
                raise FetchError(*_classify(r.stderr))
            r = git("-C", str(dest), "checkout", "--quiet", "--detach", "FETCH_HEAD")
            if r.returncode != 0:
                raise FetchError("ref_not_found", f"Cannot check out {ref!r}.")
        else:
            r = git("clone", "--quiet", "--no-tags", *depth, "--", url, str(dest), t=timeout)
            if r.returncode != 0:
                raise FetchError(*_classify(r.stderr))
            if ref:
                v = git("-C", str(dest), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
                if v.returncode != 0:
                    raise FetchError("ref_not_found",
                                     f"The branch, tag or commit {ref!r} does not exist in the repository.")
                r = git("-C", str(dest), "checkout", "--quiet", "--detach", v.stdout.strip())
                if r.returncode != 0:
                    raise FetchError("ref_not_found", f"Cannot check out {ref!r}.")
        sha = git("-C", str(dest), "rev-parse", "HEAD")
        if sha.returncode != 0:
            raise FetchError("empty_repository", "The repository has no commits.")
        is_shallow = git("-C", str(dest), "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    except subprocess.TimeoutExpired as exc:
        raise FetchError("timeout", f"git timed out after {exc.timeout}s") from exc

    workdir = (dest / subdir).resolve() if subdir else dest
    if not workdir.is_relative_to(dest.resolve()) or not workdir.is_dir():
        raise FetchError("subdir_not_found", f"Directory {subdir!r} does not exist in the repository.")
    return Checkout(path=dest, workdir=workdir, commit=sha.stdout.strip(), url=url, shallow=is_shallow)
