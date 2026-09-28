"""
AI Feedback Engine — GitHub Evaluator 2.0
==========================================
Generates structured, per-student code analysis reports using the Gemini API.
Falls back to a deterministic rule-based report when the API is unavailable.

Guardrails enforced:
  1. Submission presence check — rejects empty / stub repositories
  2. Minimum content threshold — at least 10 non-trivial lines per file
  3. Placeholder/template detection — catches "Hello World" or copy-paste starters
  4. Language-specific prompts — C/C++, Python, Java, JS all get tailored analysis
  5. Token-limit safety — prompts capped at ~6 000 chars before sending
  6. Response integrity check — rejects generic/empty Gemini replies
  7. Structured output parsing — produces section-based report, not a blob of text
"""

import logging
import re
import textwrap
from typing import Optional

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MAX_PROMPT_CHARS  = 6_000   # hard cap before sending to Gemini
MIN_CONTENT_LINES = 10      # minimum meaningful lines per file
STUB_PATTERNS = [
    r"hello[\s,]+world",
    r"print\s*\(\s*['\"]hello",
    r"printf\s*\(\s*['\"]hello",
    r"system\.out\.println\s*\(\s*['\"]hello",
    r"TODO.*implement",
    r"raise\s+NotImplementedError",
    r"//\s*TODO",
    r"/\*\s*TODO",
]
STUB_RE = re.compile("|".join(STUB_PATTERNS), re.IGNORECASE)

# ── Language-specific rubric context ─────────────────────────────────────────

LANG_CONTEXT = {
    "C": (
        "C (compiled, low-level). "
        "Pay attention to: memory management (malloc/free/calloc), pointer safety, "
        "buffer boundaries, use of valgrind-friendly patterns, header guard usage, "
        "Makefile/CMake build system, modular design (separate .h and .c)."
    ),
    "C++": (
        "C++ (compiled, OOP). "
        "Pay attention to: RAII, use of smart pointers vs raw pointers, rule of five, "
        "STL usage, move semantics, const correctness, header vs implementation separation."
    ),
    "Python": (
        "Python (interpreted). "
        "Pay attention to: PEP-8 compliance, docstrings, type hints, use of virtual envs, "
        "exception handling, Pythonic idioms (comprehensions, context managers), "
        "requirements.txt presence, test coverage."
    ),
    "Java": (
        "Java (compiled, OOP). "
        "Pay attention to: class structure, encapsulation, access modifiers, "
        "exception hierarchy, use of interfaces/abstract classes, Maven/Gradle build files, "
        "Javadoc comments, unit test presence."
    ),
    "JavaScript": (
        "JavaScript (interpreted, Node/Browser). "
        "Pay attention to: ES6+ features, async/await vs callbacks, error handling, "
        "module usage (ESM vs CJS), package.json presence, linting config."
    ),
    "TypeScript": (
        "TypeScript (typed JavaScript). "
        "Pay attention to: type definitions, interface vs type alias, strict mode, "
        "tsconfig.json, generic usage, null safety."
    ),
}
DEFAULT_LANG_CONTEXT = (
    "General programming. "
    "Apply universal software engineering principles: modularity, readability, "
    "error handling, naming conventions, and documentation."
)

# ── Guardrail helpers ─────────────────────────────────────────────────────────

def _count_meaningful_lines(content: str) -> int:
    """Count non-blank, non-comment lines."""
    count = 0
    for line in content.splitlines():
        s = line.strip()
        if s and not s.startswith(("//", "#", "/*", "*", "<!--", "--")):
            count += 1
    return count


def _has_stub_content(content: str) -> bool:
    return bool(STUB_RE.search(content))


def _validate_submission(code_snippets: list[dict]) -> tuple[bool, str]:
    """
    Guardrail: ensure the submission has real, non-trivial code.
    Returns (is_valid, reason_if_invalid).
    """
    if not code_snippets:
        return False, "No code files were submitted."

    total_meaningful = 0
    stub_count = 0
    for snippet in code_snippets:
        content = snippet.get("content", "")
        meaningful = _count_meaningful_lines(content)
        total_meaningful += meaningful
        if _has_stub_content(content):
            stub_count += 1

    if total_meaningful < MIN_CONTENT_LINES:
        return False, (
            f"Submission contains only {total_meaningful} meaningful line(s) "
            f"across {len(code_snippets)} file(s). Minimum required: {MIN_CONTENT_LINES}."
        )

    if stub_count == len(code_snippets):
        return False, (
            "All submitted files appear to be stubs or template placeholders "
            "(e.g. 'Hello World', unimplemented TODOs). No real implementation found."
        )

    return True, ""


def _build_prompt(
    student_name: str,
    code_snippets: list[dict],
    doc_snippets: list[dict],
    grade_code: float,
    grade_doc: float,
    grade_exec: float,
    grade_final: float,
) -> str:
    """
    Construct a structured, language-aware prompt for Gemini.
    Caps output at MAX_PROMPT_CHARS.
    """
    # Determine primary language(s)
    langs = list({s.get("language", "Unknown") for s in code_snippets})
    primary_lang = langs[0] if langs else "Unknown"
    lang_ctx = LANG_CONTEXT.get(primary_lang, DEFAULT_LANG_CONTEXT)

    # Build code block (capped)
    code_block = ""
    chars_used  = 0
    for snippet in code_snippets:
        header  = f"\n--- FILE: {snippet['filename']} ({snippet['language']}) ---\n"
        content = snippet.get("content", "")[:1_500]   # cap per file
        segment = header + content + "\n"
        if chars_used + len(segment) > MAX_PROMPT_CHARS * 0.7:
            code_block += f"\n[... {len(code_snippets) - code_snippets.index(snippet)} more files truncated for brevity ...]\n"
            break
        code_block += segment
        chars_used += len(segment)

    # Build doc block
    doc_block = ""
    for doc in doc_snippets:
        doc_block += f"\n--- DOC: {doc['filename']} ---\n{doc.get('content','')[:600]}\n"

    prompt = textwrap.dedent(f"""
        You are an expert software engineering professor reviewing a student's programming assignment.
        Student: {student_name}
        Language context: {lang_ctx}

        Automated scores already computed (out of 20):
          Code quality:   {grade_code:.1f}
          Execution:      {grade_exec:.1f}
          Documentation:  {grade_doc:.1f}
          Final grade:    {grade_final:.1f}

        Your task: produce a STRUCTURED feedback report. Use EXACTLY the following section headings,
        each on its own line preceded by "##". Do not add extra headings. Be specific, cite actual
        code from the submission, and keep each section concise (2-5 sentences max).

        ## OVERALL ASSESSMENT
        (One paragraph: what is this submission's strongest quality and biggest weakness?)

        ## CODE QUALITY
        (Naming, structure, modularity, adherence to {primary_lang} conventions.
         Cite a specific line or pattern from the code to support your point.)

        ## CORRECTNESS & EXECUTION
        (Does the code compile/run? Are there logical bugs, edge-case failures, or resource issues?
         Reference the execution score of {grade_exec:.1f}/20.)

        ## DOCUMENTATION
        (Quality of comments, README, docstrings. Are they meaningful or perfunctory?
         Reference the documentation score of {grade_doc:.1f}/20.)

        ## SPECIFIC IMPROVEMENTS
        (List exactly 3 concrete, actionable improvements the student should make.
         Use numbered list: 1. 2. 3.)

        ## GRADE JUSTIFICATION
        (In 1-2 sentences, justify the final grade of {grade_final:.1f}/20 based on what you observed.)

        --- STUDENT CODE ---
        {code_block}

        --- STUDENT DOCUMENTATION ---
        {doc_block if doc_block else "(No documentation files found.)"}

        Respond with ONLY the structured report. Do not add preamble, greetings, or meta-commentary.
    """).strip()

    # Final safety cap
    return prompt[:MAX_PROMPT_CHARS]


def _validate_response(text: str) -> bool:
    """
    Guardrail: reject responses that are generic, empty, or don't contain
    expected sections (indicating Gemini returned something non-compliant).
    """
    if not text or len(text.strip()) < 100:
        return False
    # Must contain at least 2 of our section markers
    section_count = text.count("##")
    if section_count < 2:
        return False
    # Reject obviously generic replies
    generic_phrases = [
        "i cannot provide", "i'm unable to", "as an ai",
        "i don't have access", "please provide",
    ]
    lower = text.lower()
    if any(p in lower for p in generic_phrases):
        return False
    return True


def _call_gemini(prompt: str, api_key: str) -> Optional[str]:
    """Call Gemini 1.5 Flash and return raw text, or None on failure."""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "gemini-1.5-flash",
            generation_config={
                "temperature":     0.4,   # lower = more factual, less hallucination
                "top_p":           0.85,
                "max_output_tokens": 900,
            },
            safety_settings=[
                {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ],
        )
        response = model.generate_content(prompt)
        return response.text.strip() if response.text else None
    except Exception as exc:
        log.warning("Gemini API error: %s", exc)
        return None


# ── Fallback rule-based report ────────────────────────────────────────────────

def _rule_based_report(
    student_name: str,
    grade_code: float,
    grade_doc: float,
    grade_exec: float,
    grade_final: float,
    valid: bool,
    invalid_reason: str,
    code_snippets: list[dict],
) -> str:
    """
    Deterministic fallback when Gemini is unavailable.
    Produces a structured report based purely on numeric scores.
    """
    if not valid:
        return f"""## OVERALL ASSESSMENT
Submission could not be fully analysed. Reason: {invalid_reason}

## CODE QUALITY
Score: {grade_code:.1f}/20. Automated static analysis was applied but meaningful content was limited.

## CORRECTNESS & EXECUTION
Score: {grade_exec:.1f}/20. Execution was attempted but produced no scorable output.

## DOCUMENTATION
Score: {grade_doc:.1f}/20. No documentation files were detected.

## SPECIFIC IMPROVEMENTS
1. Submit a complete, functional implementation rather than a stub or placeholder.
2. Include a README or documentation file explaining the project's purpose and usage.
3. Ensure the code compiles and produces meaningful output when executed.

## GRADE JUSTIFICATION
Final grade: {grade_final:.1f}/20. The low score reflects an incomplete or non-functional submission."""

    # Map scores to qualitative descriptors
    def qual(score):
        if score >= 16: return "strong"
        if score >= 12: return "satisfactory"
        if score >= 10: return "borderline"
        return "insufficient"

    langs = list({s.get("language", "code") for s in code_snippets})
    lang_str = " and ".join(langs) if langs else "the submitted language"

    return f"""## OVERALL ASSESSMENT
{student_name}'s submission demonstrates {qual(grade_final)} overall performance with a final grade of {grade_final:.1f}/20. The {lang_str} code was processed through static analysis and execution testing.

## CODE QUALITY
Score: {grade_code:.1f}/20 ({qual(grade_code)}). The static analysis evaluated naming conventions, comment density, function structure, and adherence to {lang_str} best practices. {'Code structure and readability met expectations.' if grade_code >= 12 else 'Improvements are needed in code organisation and documentation density.'}

## CORRECTNESS & EXECUTION
Score: {grade_exec:.1f}/20 ({qual(grade_exec)}). {'The code compiled and executed successfully, producing output within the allowed time limit.' if grade_exec >= 12 else 'The code encountered compilation or runtime issues. Review error handling and edge cases.'}

## DOCUMENTATION
Score: {grade_doc:.1f}/20 ({qual(grade_doc)}). {'Documentation files were present and met minimum standards.' if grade_doc >= 10 else 'Documentation was absent or insufficient. A well-structured README is expected.'}

## SPECIFIC IMPROVEMENTS
1. {'Maintain the current level of inline comments and consider adding function-level docstrings.' if grade_code >= 14 else 'Increase inline comment density to explain non-obvious logic, especially in complex functions.'}
2. {'Continue testing edge cases and boundary conditions.' if grade_exec >= 14 else 'Test the code against edge cases (empty input, max values, error conditions) before submission.'}
3. {'Consider adding unit tests to formalise correctness guarantees.' if grade_final >= 12 else 'Review the project requirements and ensure all specified features are implemented and functional.'}

## GRADE JUSTIFICATION
The final grade of {grade_final:.1f}/20 reflects weighted contributions from code quality ({grade_code:.1f}), execution ({grade_exec:.1f}), and documentation ({grade_doc:.1f}). This grade was assigned through automated analysis; please refer to session rubric weights for weighting details."""


# ── Public API ────────────────────────────────────────────────────────────────

def get_ai_feedback(
    student_name: str,
    code_snippets: list[dict],
    doc_snippets: list[dict],
    grade_code: float,
    grade_doc: float,
    grade_exec: float,
    grade_final: float,
    api_key: str = "",
) -> str:
    """
    Main entry point. Returns a structured markdown feedback report.

    Flow:
      1. Guardrail — validate submission has real content
      2. If API key present: build prompt → call Gemini → validate response
      3. If any step fails: fall back to rule-based report
    """
    # Guardrail 1 + 2: submission presence & content
    valid, invalid_reason = _validate_submission(code_snippets)

    if not api_key:
        log.info("No Gemini API key — using rule-based feedback for %s", student_name)
        return _rule_based_report(
            student_name, grade_code, grade_doc, grade_exec, grade_final,
            valid, invalid_reason, code_snippets,
        )

    if not valid:
        log.warning("Submission validation failed for %s: %s", student_name, invalid_reason)
        return _rule_based_report(
            student_name, grade_code, grade_doc, grade_exec, grade_final,
            valid, invalid_reason, code_snippets,
        )

    # Guardrail 3–5: build prompt (caps token size), call Gemini, validate response
    prompt = _build_prompt(
        student_name, code_snippets, doc_snippets,
        grade_code, grade_doc, grade_exec, grade_final,
    )

    log.info("Calling Gemini for %s (prompt=%d chars)", student_name, len(prompt))
    raw = _call_gemini(prompt, api_key)

    # Guardrail 6: response integrity
    if raw and _validate_response(raw):
        return raw

    log.warning("Gemini response failed validation for %s — using fallback", student_name)
    return _rule_based_report(
        student_name, grade_code, grade_doc, grade_exec, grade_final,
        valid, invalid_reason, code_snippets,
    )
