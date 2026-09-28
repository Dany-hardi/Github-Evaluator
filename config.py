"""
Application configuration and settings management.
Priority order for sensitive keys:
  1. .env file (GITHUB_TOKEN, GEMINI_API_KEY)
  2. data/settings.json  (non-sensitive preferences)
  3. Hard-coded defaults
"""
import json
import os
import logging
from pathlib import Path

# Load .env file on import — safe: silently ignored if file absent
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed; env vars still work from shell

log = logging.getLogger(__name__)

BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR / "data"
DB_PATH       = DATA_DIR / "evaluator.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
ENV_PATH      = BASE_DIR / ".env"

DATA_DIR.mkdir(exist_ok=True)

# Non-sensitive defaults stored in settings.json
DEFAULT_SETTINGS: dict = {
    "default_rubric_code":          40,
    "default_rubric_execution":     30,
    "default_rubric_documentation": 30,
    "docker_enabled":               True,
    "execution_timeout":            10,
    "max_file_size_kb":             500,
    "grade_scale":                  20,
    "passing_grade":                10,
}

# Sensitive keys — sourced from .env / environment only
_SENSITIVE_KEYS = ("github_token", "gemini_api_key")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _env_token(key: str) -> str:
    """Read a sensitive key from environment (populated by .env via dotenv)."""
    env_map = {
        "github_token":   "GITHUB_TOKEN",
        "gemini_api_key": "GEMINI_API_KEY",
    }
    return os.environ.get(env_map.get(key, ""), "") or ""


def load_settings() -> dict:
    """Return merged settings: env secrets + stored preferences + defaults."""
    stored: dict = {}
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except Exception:
            pass

    merged = {**DEFAULT_SETTINGS, **stored}

    # Always overlay sensitive keys from environment (never from JSON)
    merged["github_token"]   = _env_token("github_token")
    merged["gemini_api_key"] = _env_token("gemini_api_key")

    return merged


def save_settings(data: dict) -> None:
    """Persist non-sensitive preferences; route sensitive keys to .env."""
    current: dict = {}
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                current = json.load(f)
        except Exception:
            pass

    # Remove sensitive keys before writing to JSON
    safe_data = {k: v for k, v in data.items() if k not in _SENSITIVE_KEYS}
    current.update(safe_data)

    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)

    # Write sensitive keys to .env
    _write_env_keys({k: data[k] for k in _SENSITIVE_KEYS if k in data})


def save_env_keys(github_token: str = "", gemini_api_key: str = "") -> None:
    """Public helper — write API keys to .env file safely."""
    _write_env_keys({
        "github_token":   github_token,
        "gemini_api_key": gemini_api_key,
    })
    # Refresh the current process environment so the new keys take effect
    # without requiring a server restart
    if github_token:
        os.environ["GITHUB_TOKEN"]   = github_token
    if gemini_api_key:
        os.environ["GEMINI_API_KEY"] = gemini_api_key


def _write_env_keys(keys: dict) -> None:
    """Write / update key=value pairs in the .env file."""
    env_map = {
        "github_token":   "GITHUB_TOKEN",
        "gemini_api_key": "GEMINI_API_KEY",
    }

    # Read existing .env lines
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    updated = {env_map[k]: v for k, v in keys.items() if k in env_map and v}

    # Update in-place if key already exists, otherwise append
    written: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        matched  = False
        for env_key, value in updated.items():
            if stripped.startswith(f"{env_key}=") or stripped == env_key:
                new_lines.append(f"{env_key}={value}")
                written.add(env_key)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for env_key, value in updated.items():
        if env_key not in written:
            new_lines.append(f"{env_key}={value}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    log.info("Updated .env: %s", ", ".join(updated.keys()))


def get_setting(key: str):
    return load_settings().get(key)


def has_github_token() -> bool:
    return bool(_env_token("github_token"))


def has_gemini_key() -> bool:
    return bool(_env_token("gemini_api_key"))
