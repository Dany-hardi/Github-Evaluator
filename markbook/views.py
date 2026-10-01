"""Optional view log: when a reviewer opens a submission page in the web UI.

    <run_dir>/views.jsonl   one JSON object per line, append-only:
                            {"run": ..., "submission": ..., "reviewer": ..., "at": <UTC ISO>}

Privacy-light by design: no IP, no user agent, no page contents, no external requests.
It exists only so `markbook stats` has session boundaries. The file is optional;
everything works without it.
"""
from __future__ import annotations

import json
from pathlib import Path

from .grader import now_iso
from .store import _locked

VIEWS_FILE = "views.jsonl"
MAX_REVIEWER = 60


def record_view(run_dir: Path, run_id: str, sub_id: str, reviewer: str, at: str | None = None) -> dict:
    entry = {"run": run_id, "submission": sub_id, "reviewer": reviewer, "at": at or now_iso()}
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _locked(Path(run_dir)):
        with open(Path(run_dir) / VIEWS_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    return entry


def load_views(run_dir: Path) -> list[dict]:
    """All well-formed view events; a missing file or a damaged line is skipped, never fatal."""
    p = Path(run_dir) / VIEWS_FILE
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            v = json.loads(line)
        except ValueError:
            continue
        if isinstance(v, dict) and all(isinstance(v.get(k), str) for k in ("submission", "reviewer", "at")):
            out.append(v)
    return out
