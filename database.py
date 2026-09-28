"""
SQLite database layer. Thread-safe using per-thread connections.
"""
import sqlite3
import json
import uuid
import threading
from datetime import datetime
from config import DB_PATH

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            github_token TEXT DEFAULT '',
            rubric_code        INTEGER NOT NULL DEFAULT 40,
            rubric_execution   INTEGER NOT NULL DEFAULT 30,
            rubric_documentation INTEGER NOT NULL DEFAULT 30,
            ai_feedback_enabled  INTEGER NOT NULL DEFAULT 0,
            plagiarism_enabled   INTEGER NOT NULL DEFAULT 1,
            total_students       INTEGER NOT NULL DEFAULT 0,
            completed_students   INTEGER NOT NULL DEFAULT 0,
            failed_students      INTEGER NOT NULL DEFAULT 0,
            average_grade        REAL
        );

        CREATE TABLE IF NOT EXISTS students (
            id           TEXT PRIMARY KEY,
            session_id   TEXT NOT NULL,
            name         TEXT NOT NULL,
            matricule    TEXT DEFAULT '',
            code_url     TEXT NOT NULL,
            doc_url      TEXT DEFAULT '',
            position     INTEGER NOT NULL DEFAULT 0,
            status       TEXT NOT NULL DEFAULT 'pending',
            grade_code          REAL DEFAULT 0,
            grade_execution     REAL DEFAULT 0,
            grade_documentation REAL DEFAULT 0,
            grade_final         REAL DEFAULT 0,
            code_files_count    INTEGER DEFAULT 0,
            doc_files_count     INTEGER DEFAULT 0,
            compilation_success INTEGER DEFAULT 0,
            execution_success   INTEGER DEFAULT 0,
            ai_feedback         TEXT DEFAULT '',
            error_message       TEXT DEFAULT '',
            analysis_time       REAL DEFAULT 0,
            details             TEXT DEFAULT '{}',
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS plagiarism_alerts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id       TEXT NOT NULL,
            student1_id      TEXT NOT NULL,
            student2_id      TEXT NOT NULL,
            student1_name    TEXT NOT NULL,
            student2_name    TEXT NOT NULL,
            similarity_score REAL NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_students_session  ON students(session_id);
        CREATE INDEX IF NOT EXISTS idx_plague_session    ON plagiarism_alerts(session_id);
    """)
    conn.commit()


# ── Session helpers ──────────────────────────────────────────────────────────

def create_session(name: str, github_token: str, rubric: dict,
                   ai_feedback: bool, plagiarism: bool) -> str:
    sid = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    get_conn().execute(
        """INSERT INTO sessions
           (id, name, created_at, updated_at, github_token,
            rubric_code, rubric_execution, rubric_documentation,
            ai_feedback_enabled, plagiarism_enabled)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (sid, name, now, now, github_token,
         rubric.get("code", 40), rubric.get("execution", 30),
         rubric.get("documentation", 30),
         int(ai_feedback), int(plagiarism))
    )
    get_conn().commit()
    return sid


def get_session(sid: str) -> dict | None:
    row = get_conn().execute(
        "SELECT * FROM sessions WHERE id=?", (sid,)
    ).fetchone()
    return dict(row) if row else None


def list_sessions() -> list[dict]:
    rows = get_conn().execute(
        "SELECT * FROM sessions ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def update_session(sid: str, **kwargs) -> None:
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [sid]
    get_conn().execute(f"UPDATE sessions SET {cols} WHERE id=?", vals)
    get_conn().commit()


def delete_session(sid: str) -> None:
    get_conn().execute("DELETE FROM sessions WHERE id=?", (sid,))
    get_conn().commit()


# ── Student helpers ──────────────────────────────────────────────────────────

def add_student(session_id: str, name: str, matricule: str,
                code_url: str, doc_url: str, position: int) -> str:
    stid = str(uuid.uuid4())
    get_conn().execute(
        """INSERT INTO students
           (id, session_id, name, matricule, code_url, doc_url, position)
           VALUES (?,?,?,?,?,?,?)""",
        (stid, session_id, name, matricule, code_url, doc_url, position)
    )
    get_conn().commit()
    return stid


def get_student(stid: str) -> dict | None:
    row = get_conn().execute(
        "SELECT * FROM students WHERE id=?", (stid,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["details"] = json.loads(d.get("details") or "{}")
    return d


def list_students(session_id: str) -> list[dict]:
    rows = get_conn().execute(
        "SELECT * FROM students WHERE session_id=? ORDER BY position",
        (session_id,)
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["details"] = json.loads(d.get("details") or "{}")
        result.append(d)
    return result


def update_student(stid: str, **kwargs) -> None:
    if "details" in kwargs and isinstance(kwargs["details"], dict):
        kwargs["details"] = json.dumps(kwargs["details"])
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [stid]
    get_conn().execute(f"UPDATE students SET {cols} WHERE id=?", vals)
    get_conn().commit()


# ── Plagiarism helpers ───────────────────────────────────────────────────────

def add_plagiarism_alert(session_id: str, s1_id: str, s1_name: str,
                          s2_id: str, s2_name: str, score: float) -> None:
    get_conn().execute(
        """INSERT INTO plagiarism_alerts
           (session_id, student1_id, student2_id, student1_name, student2_name, similarity_score)
           VALUES (?,?,?,?,?,?)""",
        (session_id, s1_id, s2_id, s1_name, s2_name, score)
    )
    get_conn().commit()


def list_plagiarism_alerts(session_id: str) -> list[dict]:
    rows = get_conn().execute(
        """SELECT * FROM plagiarism_alerts WHERE session_id=?
           ORDER BY similarity_score DESC""",
        (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]
