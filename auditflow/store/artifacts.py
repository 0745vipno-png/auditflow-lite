# auditflow/store/artifacts.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


class ArtifactStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactRow:
    artifact_id: str
    run_id: str
    type: str
    path: str
    sha256: str
    created_at: str


def insert_artifact(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    run_id: str,
    artifact_type: str,
    path: str,
    sha256: str,
    created_at: str,
    commit: bool = True,
) -> None:
    """
    Append one artifact row.

    Default commit=True because artifacts are typically inserted one-by-one.
    Caller may set commit=False if wrapping in a larger transaction.
    """
    try:
        conn.execute(
            """
            INSERT INTO artifacts (
              artifact_id, run_id, type, path, sha256, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                run_id,
                artifact_type,
                path,
                sha256,
                created_at,
            ),
        )
        if commit:
            conn.commit()
    except sqlite3.IntegrityError as e:
        if commit:
            conn.rollback()
        raise ArtifactStoreError(f"insert_artifact integrity error: {e}") from e
    except sqlite3.Error as e:
        if commit:
            conn.rollback()
        raise ArtifactStoreError(f"insert_artifact sqlite error: {e}") from e


def list_artifacts_for_run(conn: sqlite3.Connection, run_id: str) -> list[ArtifactRow]:
    """
    Return artifacts for a run ordered by created_at, then artifact_id.
    """
    try:
        rows = conn.execute(
            """
            SELECT artifact_id, run_id, type, path, sha256, created_at
              FROM artifacts
             WHERE run_id = ?
             ORDER BY created_at ASC, artifact_id ASC
            """,
            (run_id,),
        ).fetchall()

        return [
            ArtifactRow(
                artifact_id=r["artifact_id"],
                run_id=r["run_id"],
                type=r["type"],
                path=r["path"],
                sha256=r["sha256"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
    except sqlite3.Error as e:
        raise ArtifactStoreError(f"Failed to list artifacts: {e}") from e


def get_artifact(conn: sqlite3.Connection, artifact_id: str) -> Optional[ArtifactRow]:
    try:
        r = conn.execute(
            """
            SELECT artifact_id, run_id, type, path, sha256, created_at
              FROM artifacts
             WHERE artifact_id = ?
            """,
            (artifact_id,),
        ).fetchone()

        if not r:
            return None

        return ArtifactRow(
            artifact_id=r["artifact_id"],
            run_id=r["run_id"],
            type=r["type"],
            path=r["path"],
            sha256=r["sha256"],
            created_at=r["created_at"],
        )
    except sqlite3.Error as e:
        raise ArtifactStoreError(f"Failed to get artifact: {e}") from e