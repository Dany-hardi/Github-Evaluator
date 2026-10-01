"""Cross-submission similarity via winnowing (Schleimer, Wilkerson & Aiken, 2003).

Source is tokenised with comments and literals removed and identifiers
collapsed, so renaming variables or rewording comments does not hide a copy.
k-gram hashes are winnowed to a small fingerprint set; two submissions are
compared by the fraction of the smaller one's fingerprints they share.

False positives are what make plagiarism tools unusable in practice, so:
  * fingerprints found in the teacher's `starter/` code are subtracted;
  * fingerprints present in a large share of the cohort (boilerplate) are
    ignored when the cohort is big enough to tell;
  * every flag carries the file pairs that matched, so a human can verify it.
A flag only routes a submission to review. It never changes a grade.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

K, WINDOW = 6, 4
MAX_FILE_BYTES = 200_000
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", "target",
             "vendor", ".idea", ".vscode"}

C_LIKE = {".c", ".h", ".cpp", ".cc", ".hpp", ".java", ".js", ".ts", ".go", ".rs", ".cs", ".kt", ".swift"}
HASH_LIKE = {".py", ".rb", ".sh"}
SOURCE_EXT = C_LIKE | HASH_LIKE

KEYWORDS = set("""
if else elif for while do switch case break continue return def class struct enum union import from
include using namespace public private protected static const final void int long short char float double
bool boolean string let var function new delete try catch except finally throw throws raise with as
lambda yield async await in is not and or true false null none self this super package interface
extends implements typedef sizeof fn mut impl trait match pub use mod go func defer chan select
""".split())

_TOKEN = re.compile(r"[A-Za-z_]\w*|\d+(?:\.\d+)?|==|!=|<=|>=|&&|\|\||->|\+\+|--|\S")
_C_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_HASH_COMMENT = re.compile(r"#[^\n]*")
_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
_PY_DOC = re.compile(r'("""|\'\'\').*?\1', re.DOTALL)


def tokenize(text: str, ext: str) -> list[str]:
    if ext == ".py":
        text = _PY_DOC.sub(' "S" ', text)
    text = _STRING.sub(' "S" ', text)
    text = (_C_COMMENT if ext in C_LIKE else _HASH_COMMENT).sub(" ", text)
    out = []
    for t in _TOKEN.findall(text):
        if t[0].isdigit():
            out.append("N")
        elif t[0].isalpha() or t[0] == "_":
            out.append(t if t in KEYWORDS else "I")
        else:
            out.append(t)
    return out


def _h(gram: tuple[str, ...]) -> int:
    return int.from_bytes(hashlib.blake2b(" ".join(gram).encode(), digest_size=8).digest(), "big")


def fingerprints(tokens: list[str], k: int = K, w: int = WINDOW) -> set[int]:
    if len(tokens) < k:
        return set()
    hashes = [_h(tuple(tokens[i:i + k])) for i in range(len(tokens) - k + 1)]
    if len(hashes) <= w:
        return {min(hashes)}
    picked = set()
    for i in range(len(hashes) - w + 1):
        picked.add(min(hashes[i:i + w]))
    return picked


def read_corpus(root: Path) -> dict[str, set[int]]:
    """Map relative file path → fingerprints, for source files under root."""
    corpus: dict[str, set[int]] = {}
    root = root.resolve()
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in SOURCE_EXT or not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root)
        if SKIP_DIRS & set(rel.parts) or p.stat().st_size > MAX_FILE_BYTES:
            continue
        fp = fingerprints(tokenize(p.read_text(encoding="utf-8", errors="replace"), p.suffix.lower()))
        if fp:
            corpus[rel.as_posix()] = fp
    return corpus


@dataclass
class Pair:
    a: str
    b: str
    score: float
    shared: int
    files: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "score": round(self.score, 3),
                "shared_fingerprints": self.shared, "files": self.files}


def compare(corpora: dict[str, dict[str, set[int]]], *, threshold: float = 0.6, min_fingerprints: int = 20,
            starter: dict[str, set[int]] | None = None, common_share: float = 0.5,
            common_min_cohort: int = 6) -> list[Pair]:
    """Return suspicious pairs, highest score first."""
    ignore: set[int] = set()
    for fps in (starter or {}).values():
        ignore |= fps

    per_sub: dict[str, set[int]] = {}
    for sid, files in corpora.items():
        s: set[int] = set()
        for fps in files.values():
            s |= fps
        per_sub[sid] = s - ignore

    if len(per_sub) >= common_min_cohort:
        freq: dict[int, int] = defaultdict(int)
        for s in per_sub.values():
            for f in s:
                freq[f] += 1
        limit = common_share * len(per_sub)
        ignore |= {f for f, n in freq.items() if n > limit}
        per_sub = {sid: s - ignore for sid, s in per_sub.items()}

    index: dict[int, list[str]] = defaultdict(list)
    for sid, s in per_sub.items():
        for f in s:
            index[f].append(sid)
    shared: dict[tuple[str, str], int] = defaultdict(int)
    for owners in index.values():
        if 1 < len(owners) <= 50:
            for x, y in combinations(sorted(owners), 2):
                shared[(x, y)] += 1

    pairs = []
    for (x, y), n in shared.items():
        smaller = min(len(per_sub[x]), len(per_sub[y]))
        if smaller < min_fingerprints:
            continue
        score = n / smaller
        if score >= threshold:
            pairs.append(Pair(x, y, score, n, _file_matches(corpora[x], corpora[y], ignore)))
    pairs.sort(key=lambda p: (-p.score, p.a, p.b))
    return pairs


def _file_matches(fa: dict[str, set[int]], fb: dict[str, set[int]], ignore: set[int], top: int = 3) -> list[dict]:
    rows = []
    for na, sa in fa.items():
        sa = sa - ignore
        for nb, sb in fb.items():
            sb = sb - ignore
            n = len(sa & sb)
            if n:
                rows.append({"a_file": na, "b_file": nb, "shared": n,
                             "score": round(n / min(len(sa), len(sb)), 3)})
    rows.sort(key=lambda r: -r["shared"])
    return rows[:top]
