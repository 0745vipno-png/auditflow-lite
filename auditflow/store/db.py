# auditflow/store/db.py
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DBPaths:
    home: Path
    db_path: Path
    artifacts_dir: Path
    logs_dir: Path
    profiles_dir: Path


class AuditflowDBError(RuntimeError):
    pass


def resolve_home(auditflow_home: Optional[str] = None) -> DBPaths:
    """
    Resolve AUDITFLOW_HOME (repo-independent runtime directory).
    Default:
      - Windows: %USERPROFILE%\\.auditflow
      - Others:  ~/.auditflow
    """
    if auditflow_home:
        home = Path(auditflow_home).expanduser().resolve()
    else:
        env = os.environ.get("AUDITFLOW_HOME")
        if env:
            home = Path(env).expanduser().resolve()
        else:
            home = (Path.home() / ".auditflow").resolve()

    return DBPaths(
        home=home,
        db_path=home / "auditflow.db",
        artifacts_dir=home / "artifacts",
        logs_dir=home / "logs",
        profiles_dir=home / "profiles",
    )


def ensure_runtime_dirs(paths: DBPaths) -> None:
    """
    Create runtime directories (idempotent).
    """
    paths.home.mkdir(parents=True, exist_ok=True)
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.profiles_dir.mkdir(parents=True, exist_ok=True)


def connect(db_path: Path) -> sqlite3.Connection:
    """
    Open a SQLite connection with safe defaults.
    Note: foreign_keys must be enabled per-connection.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Safety / correctness pragmas
        conn.execute("PRAGMA foreign_keys = ON;")
        # WAL improves reliability for one-shot writes + read-only web later
        conn.execute("PRAGMA journal_mode = WAL;")
        # Normal sync is a decent tradeoff; can be FULL if you want maximum durability
        conn.execute("PRAGMA synchronous = NORMAL;")

        return conn
    except sqlite3.Error as e:
        raise AuditflowDBError(f"Failed to connect SQLite DB: {db_path} ({e})") from e


def _schema_sql_path() -> Path:
    """
    Locate schema.sql relative to this file.
    """
    return (Path(__file__).parent / "schema.sql").resolve()


def apply_schema(conn: sqlite3.Connection) -> None:
    """
    Apply schema.sql (idempotent). Uses executescript to run multiple statements.
    """
    schema_path = _schema_sql_path()
    if not schema_path.exists():
        raise AuditflowDBError(f"schema.sql not found at: {schema_path}")

    try:
        sql = schema_path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        raise AuditflowDBError(f"Failed to apply schema: {e}") from e


def is_initialized(conn: sqlite3.Connection) -> bool:
    """
    Check whether core tables exist.
    """
    q = """
    SELECT name FROM sqlite_master
    WHERE type='table' AND name IN ('runs','evidence','chain','artifacts')
    """
    rows = conn.execute(q).fetchall()
    names = {r["name"] for r in rows}
    return names == {"runs", "evidence", "chain", "artifacts"}


def init_db(auditflow_home: Optional[str] = None) -> DBPaths:
    """
    Initialize runtime directories and DB schema if needed.
    Safe to call repeatedly.

    Returns:
      DBPaths (resolved locations)
    """
    paths = resolve_home(auditflow_home)
    ensure_runtime_dirs(paths)

    conn = connect(paths.db_path)
    try:
        if not is_initialized(conn):
            apply_schema(conn)
        else:
            # still re-apply schema idempotently (safe) to ensure triggers exist
            apply_schema(conn)
    finally:
        conn.close()

    return paths


def open_db(auditflow_home: Optional[str] = None) -> tuple[DBPaths, sqlite3.Connection]:
    """
    Convenience: ensure initialized and return an open connection.
    Caller owns connection lifecycle (close it).
    """
    paths = init_db(auditflow_home)
    conn = connect(paths.db_path)
    return paths, conn