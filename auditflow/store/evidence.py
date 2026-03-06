# auditflow/store/evidence.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence, Tuple


class EvidenceStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceInsert:
    """
    A fully-prepared evidence row ready for insertion.

    Requirements:
    - payload_json is canonical JSON (already serialized string)
    - payload_hash already computed as:
        sha256(payload_json || attachment_sha256 || attachment_size)
    - ts is ISO8601 string
    - attachment_path should be stable; prefer relative under artifacts/<run_id>/
    """
    evidence_id: str
    run_id: str
    collector: str
    kind: str
    ts: str

    payload_json: str
    payload_hash: str

    attachment_path: Optional[str] = None
    attachment_sha256: Optional[str] = None
    attachment_size: Optional[int] = None


@dataclass(frozen=True)
class EvidenceRow:
    evidence_id: str
    run_id: str
    collector: str
    kind: str
    ts: str
    payload_json: str
    attachment_path: Optional[str]
    attachment_sha256: Optional[str]
    attachment_size: Optional[int]
    payload_hash: str


def insert_evidence_batch(
    conn: sqlite3.Connection,
    rows: Sequence[EvidenceInsert],
    *,
    commit: bool = False,
) -> None:
    """
    Append-only batch insert into evidence table.

    - Does NOT write chain rows (chain.py will do that).
    - Prefer to call within a transaction controlled by the caller:
        conn.execute("BEGIN") ... insert_evidence_batch(..., commit=False) ... COMMIT
      If commit=True, this function commits on success.

    Raises EvidenceStoreError on failure.
    """
    if not rows:
        return

    sql = """
    INSERT INTO evidence (
      evidence_id, run_id, collector, kind, ts,
      payload_json, attachment_path, attachment_sha256, attachment_size,
      payload_hash
    )
    VALUES (?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?)
    """

    params: list[Tuple[Any, ...]] = []
    for r in rows:
        params.append(
            (
                r.evidence_id,
                r.run_id,
                r.collector,
                r.kind,
                r.ts,
                r.payload_json,
                r.attachment_path,
                r.attachment_sha256,
                r.attachment_size,
                r.payload_hash,
            )
        )

    try:
        conn.executemany(sql, params)
        if commit:
            conn.commit()
    except sqlite3.IntegrityError as e:
        if commit:
            conn.rollback()
        raise EvidenceStoreError(f"insert_evidence_batch integrity error: {e}") from e
    except sqlite3.Error as e:
        if commit:
            conn.rollback()
        raise EvidenceStoreError(f"insert_evidence_batch sqlite error: {e}") from e


def get_evidence_payload_hash(
    conn: sqlite3.Connection,
    evidence_id: str,
) -> Optional[str]:
    """
    Load payload_hash for a given evidence_id.
    Useful for chain replay/verify and chain append logic.
    """
    try:
        r = conn.execute(
            "SELECT payload_hash FROM evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        return str(r["payload_hash"]) if r else None
    except sqlite3.Error as e:
        raise EvidenceStoreError(f"Failed to get payload_hash: {e}") from e


def get_evidence_for_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    limit: int = 200,
    offset: int = 0,
    collector: Optional[str] = None,
    kind: Optional[str] = None,
) -> list[EvidenceRow]:
    """
    Read evidence rows for display/report building (read-only query).
    """
    where = ["run_id = ?"]
    params: list[Any] = [run_id]

    if collector:
        where.append("collector = ?")
        params.append(collector)
    if kind:
        where.append("kind = ?")
        params.append(kind)

    sql = f"""
    SELECT evidence_id, run_id, collector, kind, ts,
           payload_json, attachment_path, attachment_sha256, attachment_size,
           payload_hash
      FROM evidence
     WHERE {' AND '.join(where)}
     ORDER BY ts ASC, evidence_id ASC
     LIMIT ? OFFSET ?
    """
    params.extend([int(limit), int(offset)])

    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
        out: list[EvidenceRow] = []
        for r in rows:
            out.append(
                EvidenceRow(
                    evidence_id=r["evidence_id"],
                    run_id=r["run_id"],
                    collector=r["collector"],
                    kind=r["kind"],
                    ts=r["ts"],
                    payload_json=r["payload_json"],
                    attachment_path=r["attachment_path"],
                    attachment_sha256=r["attachment_sha256"],
                    attachment_size=r["attachment_size"],
                    payload_hash=r["payload_hash"],
                )
            )
        return out
    except sqlite3.Error as e:
        raise EvidenceStoreError(f"Failed to query evidence: {e}") from e


def count_evidence_by_collector_and_kind(conn: sqlite3.Connection, run_id: str) -> list[tuple[str, str, int]]:
    """
    Aggregate counts for reporting:
      [(collector, kind, count), ...]
    """
    try:
        rows = conn.execute(
            """
            SELECT collector, kind, COUNT(1) AS c
              FROM evidence
             WHERE run_id = ?
             GROUP BY collector, kind
             ORDER BY collector ASC, kind ASC
            """,
            (run_id,),
        ).fetchall()
        return [(r["collector"], r["kind"], int(r["c"])) for r in rows]
    except sqlite3.Error as e:
        raise EvidenceStoreError(f"Failed to count evidence groups: {e}") from e