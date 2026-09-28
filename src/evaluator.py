"""
Core evaluation orchestrator.
Runs in a background thread; pushes structured progress events into a queue.
"""
import os
import time
import logging
from typing import Callable

from src.github_api import GitHubClient
from src.analyzer import CodeAnalyzer, DocAnalyzer, CODE_EXTENSIONS, DOC_EXTENSIONS
from src.executor import execute_code, execution_grade
from src.ai_feedback import get_ai_feedback
from src.plagiarism import detect_similarities

log = logging.getLogger(__name__)


def _emit(q, event_type: str, **payload):
    """Put a typed event into the SSE queue."""
    q.put({"type": event_type, **payload})


class Evaluator:
    def __init__(
        self,
        session_id: str,
        github_token: str,
        rubric: dict,            # {code, execution, documentation} — weights summing to 100
        ai_enabled: bool,
        ai_api_key: str,
        plagiarism_enabled: bool,
        docker_enabled: bool,
        execution_timeout: int,
        max_file_kb: int,
    ):
        self.session_id       = session_id
        self.rubric           = rubric
        self.ai_enabled       = ai_enabled
        self.ai_api_key       = ai_api_key
        self.plagiarism_enabled = plagiarism_enabled
        self.docker_enabled   = docker_enabled
        self.execution_timeout = execution_timeout
        self.max_file_bytes   = max_file_kb * 1024

        self.github = GitHubClient(token=github_token)
        self.code_analyzer = CodeAnalyzer()
        self.doc_analyzer  = DocAnalyzer()

    # ── Public entry point ───────────────────────────────────────────────────

    def run(self, students: list[dict], queue) -> list[dict]:
        """
        Evaluate all students. Returns updated student dicts.
        Emits progress events into `queue`.
        """
        _emit(queue, "session_start", total=len(students))

        results: list[dict] = []
        plagiarism_corpora: list[dict] = []

        for idx, student in enumerate(students):
            _emit(queue, "student_start",
                  student_id=student["id"],
                  name=student["name"],
                  index=idx,
                  total=len(students))
            try:
                result = self._evaluate_student(student, queue)
            except Exception as exc:
                log.exception("Error evaluating %s", student["name"])
                result = {**student, "status": "error", "error_message": str(exc),
                          "grade_final": 0.0}
                _emit(queue, "student_error",
                      student_id=student["id"], error=str(exc))

            results.append(result)
            plagiarism_corpora.append({
                "id":         result["id"],
                "name":       result["name"],
                "code_files": result.get("_code_corpus", []),
            })

            _emit(queue, "student_done",
                  student_id=result["id"],
                  grade_final=result.get("grade_final", 0),
                  status=result.get("status", "complete"))

        # ── Plagiarism detection ─────────────────────────────────────────────
        plagiarism_alerts = []
        if self.plagiarism_enabled and len(results) > 1:
            _emit(queue, "plagiarism_start")
            try:
                plagiarism_alerts = detect_similarities(plagiarism_corpora)
                _emit(queue, "plagiarism_done", alerts=len(plagiarism_alerts))
            except Exception as exc:
                log.warning("Plagiarism detection failed: %s", exc)
                _emit(queue, "plagiarism_error", error=str(exc))

        # Clean internal corpus field before returning
        for r in results:
            r.pop("_code_corpus", None)

        _emit(queue, "session_done",
              total=len(results),
              alerts=plagiarism_alerts)

        return results, plagiarism_alerts

    # ── Per-student evaluation ───────────────────────────────────────────────

    def _evaluate_student(self, student: dict, queue) -> dict:
        sid    = student["id"]
        name   = student["name"]
        c_url  = student.get("code_url", "")
        d_url  = student.get("doc_url", "")

        start = time.perf_counter()

        # Set up GitHub client log forwarding
        self.github._log = lambda msg, **_: _emit(queue, "log", student_id=sid, msg=msg)

        # ── Code repository ──────────────────────────────────────────────────
        code_results = []
        code_corpus  = []
        exec_grades  = []

        if c_url:
            parsed = self.github.parse_url(c_url)
            if parsed:
                owner, repo, path = parsed
                _emit(queue, "log", student_id=sid,
                      msg=f"Fetching code repo: {owner}/{repo}")
                all_files = self.github.list_files_recursive(owner, repo, path)
                code_files = [
                    f for f in all_files
                    if os.path.splitext(f["name"])[1].lower() in CODE_EXTENSIONS
                    and f.get("size", 0) <= self.max_file_bytes
                ]
                _emit(queue, "log", student_id=sid,
                      msg=f"Found {len(code_files)} code file(s)")

                for fi in code_files:
                    content = self.github.get_file_content(owner, repo, fi["path"])
                    ext     = os.path.splitext(fi["name"])[1].lower()
                    lang    = CODE_EXTENSIONS.get(ext, "Unknown")

                    static = self.code_analyzer.analyze(content, lang)

                    exec_result = None
                    if lang in ("C", "C++", "Python", "Java", "JavaScript"):
                        _emit(queue, "log", student_id=sid,
                              msg=f"Executing {fi['name']} ({lang})")
                        exec_result = execute_code(
                            content, lang, fi["name"],
                            timeout=self.execution_timeout,
                            use_docker=self.docker_enabled,
                        )
                        eg = execution_grade(exec_result)
                        exec_grades.append(eg)

                    file_rec = {
                        "filename":    fi["name"],
                        "language":    lang,
                        "grade":       static["grade"],
                        **{k: v for k, v in static.items() if k != "grade"},
                        "execution_grade":       execution_grade(exec_result) if exec_result else None,
                        "execution_success":     exec_result.success if exec_result else False,
                        "compilation_success":   (exec_result.success or (exec_result and not exec_result.skipped)) if exec_result else False,
                        "execution_time":        exec_result.execution_time if exec_result else 0,
                        "execution_error":       exec_result.error if exec_result else "",
                        "execution_skipped":     exec_result.skipped if exec_result else False,
                        "skip_reason":           exec_result.skip_reason if exec_result else "",
                    }
                    code_results.append(file_rec)
                    # Store content mapped by filename for AI re-use (no re-fetch)
                    code_corpus.append({"language": lang, "content": content, "filename": fi["name"]})
            else:
                _emit(queue, "log", student_id=sid,
                      msg=f"Invalid code URL: {c_url}")

        # ── Documentation repository ─────────────────────────────────────────
        doc_results = []
        if d_url:
            parsed = self.github.parse_url(d_url)
            if parsed:
                owner, repo, path = parsed
                _emit(queue, "log", student_id=sid,
                      msg=f"Fetching doc repo: {owner}/{repo}")
                all_files = self.github.list_files_recursive(owner, repo, path)
                doc_files = [
                    f for f in all_files
                    if os.path.splitext(f["name"])[1].lower() in DOC_EXTENSIONS
                    and f.get("size", 0) <= self.max_file_bytes
                ]
                _emit(queue, "log", student_id=sid,
                      msg=f"Found {len(doc_files)} documentation file(s)")

                for fi in doc_files:
                    content = self.github.get_file_content(owner, repo, fi["path"])
                    result  = self.doc_analyzer.analyze(content)
                    doc_results.append({
                        "filename": fi["name"],
                        "_raw_content": content,   # cached for AI, stripped before DB
                        **result,
                    })

        # ── Grade aggregation ────────────────────────────────────────────────
        grade_code = (
            sum(f["grade"] for f in code_results) / len(code_results)
            if code_results else 0.0
        )
        grade_exec = (
            sum(exec_grades) / len(exec_grades)
            if exec_grades else 10.0   # neutral if no executable files
        )
        grade_doc = (
            sum(f["grade"] for f in doc_results) / len(doc_results)
            if doc_results else 0.0
        )

        w = self.rubric
        total = w["code"] + w["execution"] + w["documentation"]
        grade_final = (
            (grade_code  * w["code"]          / total) +
            (grade_exec  * w["execution"]     / total) +
            (grade_doc   * w["documentation"] / total)
        )

        # ── AI Feedback ───────────────────────────────────────────────────────
        ai_fb = ""
        if self.ai_enabled:
            _emit(queue, "log", student_id=sid, msg="Generating AI feedback")
            try:
                # Use already-fetched content — zero extra GitHub API calls
                code_snippets = [
                    {"filename": c["filename"], "language": c["language"],
                     "content": c["content"]}
                    for c in code_corpus[:3]
                ]
                doc_snippets = [
                    {"filename": d["filename"],
                     "content": d.get("_raw_content", "")}
                    for d in doc_results[:2]
                ]
                ai_fb = get_ai_feedback(
                    name, code_snippets, doc_snippets,
                    grade_code, grade_doc, grade_exec, grade_final,
                    api_key=self.ai_api_key,
                )
            except Exception as exc:
                log.warning("AI feedback error: %s", exc)

        elapsed = round(time.perf_counter() - start, 2)

        # Strip internal-only fields before returning
        for d in doc_results:
            d.pop("_raw_content", None)

        return {
            **student,
            "status":               "complete",
            "grade_code":           round(grade_code, 2),
            "grade_execution":      round(grade_exec, 2),
            "grade_documentation":  round(grade_doc, 2),
            "grade_final":          round(grade_final, 2),
            "code_files_count":     len(code_results),
            "doc_files_count":      len(doc_results),
            "compilation_success":  any(f.get("compilation_success") for f in code_results),
            "execution_success":    any(f.get("execution_success") for f in code_results),
            "ai_feedback":          ai_fb,
            "error_message":        "",
            "analysis_time":        elapsed,
            "details": {
                "code_files":  code_results,
                "doc_files":   doc_results,
            },
            "_code_corpus": code_corpus,  # stripped before DB write
        }
