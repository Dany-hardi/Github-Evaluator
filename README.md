# GitHub Evaluator 2.0

**Automated Student Code Review & Evaluation Platform for Educators, TAs, and Developers.**

GitHub Evaluator is a high-performance web platform designed to automate the grading, static analysis, sandboxed execution, plagiarism detection, and AI feedback generation for student code repositories at scale.

---

## Key Features

- **Microsoft-Inspired Clean UI**: Functional, high-density interface built with a clean White & Green palette, subtle micro-animations, and the Inter font family.
- **Automated Repository Fetching**: Ingests batch GitHub repositories or ZIP archives effortlessly using authenticated GitHub API calls.
- **Multi-Metric Evaluation Engine**:
  - **Static Analysis**: Code complexity, comment density, function structure, and PEP-8/language convention checks.
  - **Sandboxed Execution**: Isolated execution testing with configurable timeouts and resource constraints.
  - **Documentation Scoring**: Automatic verification of README presence, structure, completeness, and docstrings.
  - **AST Plagiarism Detection**: Token-level structural fingerprinting to identify cross-student code similarity.
- **Gemini 1.5 Flash AI Feedback Engine**:
  - **6 Enforced Guardrails**: Submission validation, minimum code line threshold, stub/placeholder regex detection, language-tailored prompts, token safety caps, and strict structural response verification.
  - **Deterministic Rule-Based Fallback**: Ensures complete evaluation reports even when offline or without an API key.
- **Security & Secret Protection**:
  - Sensitive keys (`GITHUB_TOKEN`, `GEMINI_API_KEY`) stored strictly in `.env`.
  - Settings UI protected with masked inputs and live connection diagnostics.

---

## System Architecture

```
GitHub Evaluator
├── app.py                # Flask Server & API Routes
├── config.py             # Secure Environment & Settings Manager
├── database.py           # SQLite Database Manager & Session Store
├── src/
│   ├── evaluator.py      # Core Evaluation Orchestrator & Locking
│   ├── github_api.py     # GitHub API Client & Caching
│   ├── static_analysis.py# Language Parsers & Metrics
│   ├── execution.py      # Sandboxed Execution Runner
│   ├── plagiarism.py     # AST/Token Similarity Detector
│   └── ai_feedback.py    # Gemini 1.5 AI Engine & Guardrails
├── templates/            # Jinja2 Templates (Microsoft Aesthetic)
└── static/               # CSS Design System & Client JS
```

---

## Quick Start

### 1. Prerequisites
- Python 3.10+
- Virtual environment tool (`venv` or `uv`)

### 2. Installation
```bash
# Clone repository
git clone https://github.com/Dany-hardi/Github-Evaluator.git
cd Github-Evaluator

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file in the root directory:
```env
GITHUB_TOKEN=ghp_your_github_personal_access_token
GEMINI_API_KEY=AIza_your_gemini_api_key
```

### 4. Run Locally
```bash
python app.py
```
Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

---

## API Key Setup Guide

### GitHub Personal Access Token
1. Go to **GitHub Settings** → **Developer Settings** → **Personal Access Tokens** → **Tokens (classic)**.
2. Click **Generate new token (classic)**.
3. Select `repo` scope (read-only access is sufficient).
4. Copy the token into `.env` or paste it directly in the app's **Settings** tab.

### Gemini API Key (Free)
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Click **Create API Key**.
3. Copy the key into `.env` or paste it in the **Settings** page.

---

## License
Built for educational institutions, educators, and software engineers.