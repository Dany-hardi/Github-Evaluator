"""
Static code and documentation analysis.
Produces deterministic, rubric-driven scores that cannot be trivially gamed.
"""
import re
import os
from dataclasses import dataclass, field
from typing import Optional

# ── Language registry ────────────────────────────────────────────────────────

CODE_EXTENSIONS: dict[str, str] = {
    ".c": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".h": "C Header", ".hpp": "C++ Header",
    ".py": "Python",
    ".java": "Java",
    ".js": "JavaScript", ".ts": "TypeScript",
    ".go": "Go", ".rs": "Rust",
}

DOC_EXTENSIONS: set[str] = {".md", ".txt", ".rst", ".adoc"}

INTERACTIVE_PATTERNS: dict[str, list[str]] = {
    "Python":     [r"\binput\s*\(", r"\braw_input\s*\("],
    "C":          [r"\bscanf\s*\(", r"\bfgets\s*\(", r"\bgets\s*\(", r"\bgetchar\s*\("],
    "C++":        [r"\bcin\s*>>", r"\bgetline\s*\("],
    "Java":       [r"\bScanner\b", r"\bBufferedReader\b"],
    "JavaScript": [r"\breadline\b", r"\bprocess\.stdin\b"],
}

TECHNICAL_KEYWORDS = {
    "algorithm", "algorithme", "complexity", "complexite",
    "function", "fonction", "variable", "pointer", "pointeur",
    "array", "tableau", "loop", "boucle", "struct", "class",
    "recursion", "recursivite", "stack", "heap", "linked list",
    "binary", "sort", "search", "tree", "graph",
}


# ── Result dataclasses ───────────────────────────────────────────────────────

@dataclass
class CodeFileResult:
    filename: str
    language: str
    lines_of_code: int = 0
    comment_ratio: float = 0.0
    function_count: int = 0
    is_interactive: bool = False
    grade: float = 0.0
    execution_grade: float = 0.0
    compilation_success: bool = False
    execution_success: bool = False
    execution_time: float = 0.0
    execution_error: str = ""
    raw_output: str = ""


@dataclass
class DocFileResult:
    filename: str
    word_count: int = 0
    line_count: int = 0
    has_structure: bool = False
    has_code_blocks: bool = False
    technical_score: float = 0.0
    grade: float = 0.0


# ── Code Analyzer ────────────────────────────────────────────────────────────

class CodeAnalyzer:
    """Analyzes code content and returns a static quality grade /20."""

    def analyze(self, content: str, language: str) -> dict:
        lines = content.splitlines()

        # ── Line counts ──────────────────────────────────────────────────────
        blank = sum(1 for l in lines if not l.strip())
        comment_lines = self._count_comment_lines(lines, language)
        code_lines = len(lines) - blank - comment_lines
        code_lines = max(code_lines, 0)

        comment_ratio = comment_lines / len(lines) if lines else 0.0

        # ── Functions / methods ──────────────────────────────────────────────
        func_count = self._count_functions(content, language)

        # ── Indentation consistency ──────────────────────────────────────────
        indent_ok = self._check_indentation(lines)

        # ── Interactive flag ─────────────────────────────────────────────────
        is_interactive = self._is_interactive(content, language)

        # ── Grade calculation (out of 20) ────────────────────────────────────
        grade = 0.0

        # Size component (4 pts) — penalise trivially small or bloated files
        if code_lines >= 10:
            grade += 2.0
        if code_lines >= 30:
            grade += 1.0
        if 10 <= code_lines <= 800:
            grade += 1.0

        # Comments component (4 pts)
        if comment_ratio >= 0.05:
            grade += 2.0
        if comment_ratio >= 0.15:
            grade += 2.0

        # Functions component (4 pts)
        if func_count >= 1:
            grade += 2.0
        if func_count >= 3:
            grade += 2.0

        # Indentation (2 pts)
        if indent_ok:
            grade += 2.0

        # Completeness — has entry point (2 pts)
        if self._has_entry_point(content, language):
            grade += 2.0

        # Complexity indicators (2 pts) — loops, conditionals
        complexity = len(re.findall(
            r'\b(for|while|if|switch|case|foreach|do)\b', content))
        if complexity >= 3:
            grade += 1.0
        if complexity >= 8:
            grade += 1.0

        return {
            "lines_of_code": code_lines,
            "comment_ratio": round(comment_ratio, 3),
            "function_count": func_count,
            "is_interactive": is_interactive,
            "grade": round(min(grade, 20.0), 2),
        }

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _count_comment_lines(lines: list[str], lang: str) -> int:
        count = 0
        in_block = False
        for line in lines:
            stripped = line.strip()
            if lang in ("C", "C++", "Java", "JavaScript", "TypeScript", "Go"):
                if "/*" in stripped:
                    in_block = True
                if in_block:
                    count += 1
                    if "*/" in stripped:
                        in_block = False
                    continue
                if stripped.startswith("//"):
                    count += 1
            elif lang == "Python":
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    count += 1
        return count

    @staticmethod
    def _count_functions(content: str, lang: str) -> int:
        patterns = {
            "Python":     r'^\s*def\s+\w+\s*\(',
            "Java":       r'(public|private|protected|static|void|int|String|boolean|double|float)\s+\w+\s*\([^)]*\)\s*\{',
            "JavaScript": r'(function\s+\w+\s*\(|const\s+\w+\s*=\s*(async\s*)?\([^)]*\)\s*=>|\w+\s*:\s*function)',
            "TypeScript": r'(function\s+\w+\s*\(|const\s+\w+\s*=\s*(async\s*)?\([^)]*\)\s*=>)',
            "Go":         r'^\s*func\s+\w+',
            "Rust":       r'^\s*fn\s+\w+',
        }
        # C and C++ share the same pattern
        c_pattern = r'^\s*\w[\w\s\*]+\s+\w+\s*\([^)]*\)\s*\{'
        p = patterns.get(lang) or c_pattern
        return len(re.findall(p, content, re.MULTILINE))

    @staticmethod
    def _check_indentation(lines: list[str]) -> bool:
        code_lines = [l for l in lines if l.strip()]
        if not code_lines:
            return True
        indented = sum(1 for l in code_lines if l.startswith("    ") or l.startswith("\t"))
        return indented / len(code_lines) > 0.15

    @staticmethod
    def _is_interactive(content: str, lang: str) -> bool:
        patterns = INTERACTIVE_PATTERNS.get(lang, [])
        for p in patterns:
            if re.search(p, content):
                return True
        return False

    @staticmethod
    def _has_entry_point(content: str, lang: str) -> bool:
        entries = {
            "C":    r'\bint\s+main\s*\(',
            "C++":  r'\bint\s+main\s*\(',
            "Java": r'\bpublic\s+static\s+void\s+main\b',
            "Python": r'if\s+__name__\s*==\s*["\']__main__["\']',
            "JavaScript": r'(module\.exports|export\s+default|\.listen\s*\()',
        }
        p = entries.get(lang, "")
        return bool(re.search(p, content)) if p else True


# ── Documentation Analyzer ───────────────────────────────────────────────────

class DocAnalyzer:
    """Scores a documentation file out of 20."""

    def analyze(self, content: str) -> dict:
        words = content.split()
        word_count = len(words)
        line_count = len(content.splitlines())

        has_structure = bool(re.search(r"^#{1,6}\s|^={3,}|^-{3,}", content, re.MULTILINE))
        has_code_blocks = bool(re.search(r"```|~~~|`[^`]+`", content))

        content_lower = content.lower()
        tech_hits = sum(1 for kw in TECHNICAL_KEYWORDS if kw in content_lower)
        technical_score = min(tech_hits / 8.0, 1.0)

        grade = 0.0

        # Length (6 pts — non-linear to prevent word padding)
        if word_count >= 50:
            grade += 2.0
        if word_count >= 150:
            grade += 2.0
        if word_count >= 400:
            grade += 2.0

        # Structure (5 pts)
        if has_structure:
            heading_count = len(re.findall(r"^#{1,6}\s", content, re.MULTILINE))
            grade += min(heading_count * 1.5, 5.0)

        # Code examples (3 pts)
        if has_code_blocks:
            block_count = len(re.findall(r"```", content)) // 2
            grade += min(block_count * 1.5, 3.0)

        # Technical depth (6 pts)
        grade += technical_score * 6.0

        return {
            "word_count": word_count,
            "line_count": line_count,
            "has_structure": has_structure,
            "has_code_blocks": has_code_blocks,
            "technical_score": round(technical_score, 3),
            "grade": round(min(grade, 20.0), 2),
        }
