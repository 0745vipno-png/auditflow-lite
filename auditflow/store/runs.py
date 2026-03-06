# auditflow/store/runs.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Mapping


class RunsStoreError(RuntimeError):
    pass


# User-facing status (DB: runs.status)
STATUS_RUNNING = "RUNNING"
STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"
STATUS_ABORTED = "ABORTED"

TERMINAL_STATUSES = {STATUS_OK, STATUS_WARN, STATUS_FAIL, STATUS_ABORTED}


def _canonical_json(obj: Any) -> str:
    """
    Canonical JSON for DB storage:
      - sort_keys=True
      - compact separators
      - ensure_ascii=False
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class RunRow:
    run_id: str
    started_at: str
    finished_at: Optional[str]
    status: str
    profile: str
    targets_json: str
    tags_json: str
    notes: Optional[str]
    env_fingerprint: str
    run_seed_hash: str
    warnings_count: int
    errors_count: int
    evidence_count: Optional[int]
    final_chain_hash: Optional[str]


def create_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    started_at: str,
    profile: str,
    targets: Sequence[str],
    tags: Mapping[str, Any] | Sequence[Any] | None,
    notes: Optional[str],
    env_fingerprint: str,
    run_seed_hash: str,
) -> None:
    """
    Insert a RUNNING run row.

    NOTE: runs table enforces immutability via triggers.
    """
    try:
        targets_json = _canonical_json(list(targets))
        tags_json = _canonical_json(tags if tags is not None else {})

        conn.execute(
            """
            INSERT INTO runs (
              run_id, started_at, finished_at, status,
              profile, targets_json, tags_json, notes,
              env_fingerprint, run_seed_hash,
              warnings_count, errors_count, evidence_count, final_chain_hash
            )
            VALUES (?, ?, NULL, ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    0, 0, NULL, NULL)
            """,
            (
                run_id,
                started_at,
                STATUS_RUNNING,
                profile,
                targets_json,
                tags_json,
                notes,
                env_fingerprint,
                run_seed_hash,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise RunsStoreError(f"Failed to create run (integrity): {e}") from e
    except sqlite3.Error as e:
        conn.rollback()
        raise RunsStoreError(f"Failed to create run: {e}") from e


def seal_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    finished_at: str,
    status: str,
    warnings_count: int,
    errors_count: int,
    evidence_count: int,
    final_chain_hash: str,
) -> None:
    """
    Seal the run by updating restricted fields only.

    Allowed updates (enforced by triggers):
      finished_at, status, warnings_count, errors_count, evidence_count, final_chain_hash
    """
    if status not in (STATUS_OK, STATUS_WARN, STATUS_FAIL, STATUS_ABORTED):
        raise RunsStoreError(f"Invalid terminal status: {status}")

    try:
        cur = conn.execute(
            """
            UPDATE runs
               SET finished_at = ?,
                   status = ?,
                   warnings_count = ?,
                   errors_count = ?,
                   evidence_count = ?,
                   final_chain_hash = ?
             WHERE run_id = ?
            """,
            (
                finished_at,
                status,
                int(warnings_count),
                int(errors_count),
                int(evidence_count),
                final_chain_hash,
                run_id,
            ),
        )
        if cur.rowcount != 1:
            conn.rollback()
            raise RunsStoreError(f"seal_run: run_id not found: {run_id}")
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        # triggers may raise ABORT -> sqlite3.Error
        raise RunsStoreError(f"Failed to seal run: {e}") from e


def mark_aborted_if_running(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    finished_at: str,
    warnings_count: int,
    errors_count: int,
    evidence_count: Optional[int] = None,
    final_chain_hash: Optional[str] = None,
) -> None:
    """
    Best-effort helper: if run is still RUNNING (e.g., crash), mark as ABORTED.

    This does NOT attempt to "fix" chain; it just closes the run for visibility.
    Provide evidence_count/final_chain_hash if you can compute them; otherwise leave NULL.
    """
    try:
        row = get_run(conn, run_id)
        if row is None:
            raise RunsStoreError(f"mark_aborted: run_id not found: {run_id}")
        if row.status != STATUS_RUNNING:
            return

        conn.execute(
            """
            UPDATE runs
               SET finished_at = ?,
                   status = ?,
                   warnings_count = ?,
                   errors_count = ?,
                   evidence_count = ?,
                   final_chain_hash = ?
             WHERE run_id = ?
            """,
            (
                finished_at,
                STATUS_ABORTED,
                int(warnings_count),
                int(errors_count),
                evidence_count,
                final_chain_hash,
                run_id,
            ),
        )
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        raise RunsStoreError(f"Failed to mark aborted: {e}") from e


def get_run(conn: sqlite3.Connection, run_id: str) -> Optional[RunRow]:
    try:
        r = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not r:
            return None
        return RunRow(
            run_id=r["run_id"],
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            status=r["status"],
            profile=r["profile"],
            targets_json=r["targets_json"],
            tags_json=r["tags_json"],
            notes=r["notes"],
            env_fingerprint=r["env_fingerprint"],
            run_seed_hash=r["run_seed_hash"],
            warnings_count=int(r["warnings_count"]),
            errors_count=int(r["errors_count"]),
            evidence_count=r["evidence_count"],
            final_chain_hash=r["final_chain_hash"],
        )
    except sqlite3.Error as e:
        raise RunsStoreError(f"Failed to get run: {e}") from e


def list_runs(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    status: Optional[str] = None,
    profile: Optional[str] = None,
    since_iso: Optional[str] = None,
) -> list[RunRow]:
    """
    List runs ordered by started_at desc.

    Filters are optional:
      - status: RUNNING/OK/WARN/FAIL/ABORTED
      - profile: exact match
      - since_iso: started_at >= since_iso (ISO8601 string)
    """
    where = []
    params: list[Any] = []

    if status:
        where.append("status = ?")
        params.append(status)
    if profile:
        where.append("profile = ?")
        params.append(profile)
    if since_iso:
        where.append("started_at >= ?")
        params.append(since_iso)

    sql = "SELECT * FROM runs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(int(limit))

    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
        out: list[RunRow] = []
        for r in rows:
            out.append(
                RunRow(
                    run_id=r["run_id"],
                    started_at=r["started_at"],
                    finished_at=r["finished_at"],
                    status=r["status"],
                    profile=r["profile"],
                    targets_json=r["targets_json"],
                    tags_json=r["tags_json"],
                    notes=r["notes"],
                    env_fingerprint=r["env_fingerprint"],
                    run_seed_hash=r["run_seed_hash"],
                    warnings_count=int(r["warnings_count"]),
                    errors_count=int(r["errors_count"]),
                    evidence_count=r["evidence_count"],
                    final_chain_hash=r["final_chain_hash"],
                )
            )
        return out
    except sqlite3.Error as e:
        raise RunsStoreError(f"Failed to list runs: {e}") from e


def count_evidence(conn: sqlite3.Connection, run_id: str) -> int:
    try:
        r = conn.execute(
            "SELECT COUNT(1) AS c FROM evidence WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(r["c"]) if r else 0
    except sqlite3.Error as e:
        raise RunsStoreError(f"Failed to count evidence: {e}") from e


def get_last_chain_hash(conn: sqlite3.Connection, run_id: str) -> Optional[str]:
    """
    Return the last chain.this_hash for the run, or None if no chain rows exist.
    """
    try:
        r = conn.execute(
            """
            SELECT this_hash
              FROM chain
             WHERE run_id = ?
             ORDER BY seq DESC
             LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        return str(r["this_hash"]) if r else None
    except sqlite3.Error as e:
        raise RunsStoreError(f"Failed to get last chain hash: {e}") from e