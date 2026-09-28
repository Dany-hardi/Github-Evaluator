"""
Plagiarism detection using normalised token similarity.
Compares all pairs of student code bases and flags those above the threshold.
"""
import re
from difflib import SequenceMatcher
from itertools import combinations


_WHITESPACE_RE = re.compile(r'\s+')
_COMMENT_C_RE  = re.compile(r'//.*?$|/\*.*?\*/', re.MULTILINE | re.DOTALL)
_COMMENT_PY_RE = re.compile(r'#.*?$', re.MULTILINE)
_STRING_RE     = re.compile(r'"[^"]*"|\'[^\']*\'')
_NUMBER_RE     = re.compile(r'\b\d+\b')


def _normalise(code: str, language: str) -> str:
    """Strip comments, strings, numbers, and collapse whitespace."""
    if language in ("C", "C++", "Java", "JavaScript", "TypeScript", "Go"):
        code = _COMMENT_C_RE.sub(" ", code)
    elif language == "Python":
        code = _COMMENT_PY_RE.sub(" ", code)

    code = _STRING_RE.sub('"S"', code)
    code = _NUMBER_RE.sub("N", code)
    code = _WHITESPACE_RE.sub(" ", code)
    return code.strip().lower()


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=True).ratio()


def detect_similarities(
    students: list[dict],   # [{"id": str, "name": str, "code_files": [...]}]
    threshold: float = 0.72,
) -> list[dict]:
    """
    Compare all student pairs.
    Each code_files entry: {"language": str, "content": str}
    Returns list of: {student1_id, student1_name, student2_id, student2_name, score}
    """
    # Build normalised corpus per student
    corpora: dict[str, str] = {}
    for student in students:
        combined = []
        for f in student.get("code_files", []):
            norm = _normalise(f.get("content", ""), f.get("language", ""))
            combined.append(norm)
        corpora[student["id"]] = " ".join(combined)

    alerts = []
    for s1, s2 in combinations(students, 2):
        c1 = corpora.get(s1["id"], "")
        c2 = corpora.get(s2["id"], "")
        if not c1 or not c2:
            continue
        score = _similarity(c1, c2)
        if score >= threshold:
            alerts.append({
                "student1_id":   s1["id"],
                "student1_name": s1["name"],
                "student2_id":   s2["id"],
                "student2_name": s2["name"],
                "score":         round(score, 3),
            })

    # Sort by descending similarity
    alerts.sort(key=lambda x: x["score"], reverse=True)
    return alerts
