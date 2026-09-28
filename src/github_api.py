"""
GitHub API client with token support, exponential back-off on rate limits,
and recursive file listing.
"""
import re
import time
import base64
import logging
from typing import Optional
import requests

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str = "", log_cb=None):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHub-Evaluator/2.0",
        })
        if token:
            self.session.headers["Authorization"] = f"token {token}"
        self._log = log_cb or (lambda msg, level="info": None)

    # ── URL parsing ──────────────────────────────────────────────────────────

    @staticmethod
    def parse_url(url: str) -> Optional[tuple[str, str, str]]:
        """Return (owner, repo, sub_path) or None."""
        url = url.strip().rstrip("/")
        patterns = [
            r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/tree/[^/]+/(.*))?$",
            r"github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/tree/[^/]+/(.*))?$",
            r"^([^/\s]+)/([^/\s]+?)(?:\.git)?$",
        ]
        for p in patterns:
            m = re.match(p, url)
            if m:
                owner, repo, path = m.group(1), m.group(2), (m.group(3) if len(m.groups()) > 2 else "")
                return owner, repo, path or ""
        return None

    # ── API requests with back-off ───────────────────────────────────────────

    def _get(self, url: str, retries: int = 3) -> Optional[dict | list]:
        for attempt in range(retries):
            try:
                r = self.session.get(url, timeout=20)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 403:
                    reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
                    wait = min(reset - int(time.time()) + 2, 120)
                    self._log(f"Rate limit hit — waiting {wait}s")
                    time.sleep(wait)
                    continue
                if r.status_code == 404:
                    return None
                log.warning("GitHub API %s → %d", url, r.status_code)
                return None
            except requests.RequestException as exc:
                wait = 2 ** attempt
                self._log(f"Request error ({exc}) — retry in {wait}s")
                time.sleep(wait)
        return None

    # ── Repository traversal ─────────────────────────────────────────────────

    def get_contents(self, owner: str, repo: str, path: str = "") -> list[dict]:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
        data = self._get(url)
        if isinstance(data, list):
            return data
        return []

    def get_file_content(self, owner: str, repo: str, path: str) -> str:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
        data = self._get(url)
        if not isinstance(data, dict):
            return ""
        if data.get("encoding") == "base64":
            try:
                return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            except Exception:
                return ""
        return ""

    def list_files_recursive(self, owner: str, repo: str,
                              path: str = "", _depth: int = 0) -> list[dict]:
        """Recursively list all files. Capped at depth=8 to avoid infinite loops."""
        if _depth > 8:
            return []
        items = self.get_contents(owner, repo, path)
        files: list[dict] = []
        for item in items:
            if item["type"] == "file":
                files.append(item)
            elif item["type"] == "dir":
                files.extend(
                    self.list_files_recursive(owner, repo, item["path"], _depth + 1)
                )
        return files

    def get_repo_metadata(self, owner: str, repo: str) -> dict:
        url = f"{GITHUB_API}/repos/{owner}/{repo}"
        data = self._get(url)
        return data if isinstance(data, dict) else {}
