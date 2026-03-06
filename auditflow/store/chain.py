# auditflow/store/chain.py
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Optional, Sequence


class ChainStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChainInput:
    """
    Input required to append one chain row.

    payload_hash should come from the evidence row already inserted into DB.
    """
    evidence_id: str
    payload_hash: str


@dataclass(frozen=True)
class ChainRow:
    run_id: str
    seq: int
    evidence_id: str
    prev_hash: str
    this_hash: str
    created_at: str


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    run_id: str
    expected_final_chain_hash: Optional[str]
    actual_final_chain_hash: Optional[str]
    expected_evidence_count: Optional[int]
    actual_evidence_count: int
    checked_chain_rows: int
    failure_reason: Optional[str] = None
    mismatch_seq: Optional[int] = None
    mismatch_evidence_id: Optional[str] = None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chain_material(
    *,
    prev_hash: str,
    run_id: str,
    seq: int,
    evidence_id: str,
    payload_hash: str,
) -> str:
    """
    Canonical chain material for hashing.
    Deliberately does not depend on ts.
    """
    return "||".join(
        [
            prev_hash,
            run_id,
            str(seq),
            evidence_id,
            payload_hash,
        ]
    )


def compute_chain_hash(
    *,
    prev_hash: str,
    run_id: str,
    seq: int,
    evidence_id: str,
    payload_hash: str,
) -> str:
    material = _chain_material(
        prev_hash=prev_hash,
        run_id=run_id,
        seq=seq,
        evidence_id=evidence_id,
        payload_hash=payload_hash,
    )
    return _sha256_text(material)


def get_last_seq_and_hash(conn: sqlite3.Connection, run_id: str) -> tuple[int, Optional[str]]:
    """
    Return (last_seq, last_hash) for a run.
    If no chain rows exist, returns (0, None).
    """
    try:
        row = conn.execute(
            """
            SELECT seq, this_hash
              FROM chain
             WHERE run_id = ?
             ORDER BY seq DESC
             LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if not row:
            return 0, None
        return int(row["seq"]), str(row["this_hash"])
    except sqlite3.Error as e:
        raise ChainStoreError(f"Failed to get last seq/hash: {e}") from e


def append_chain_batch(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    run_seed_hash: str,
    items: Sequence[ChainInput],
    created_at: str,
    commit: bool = False,
) -> list[ChainRow]:
    """
    Append chain rows for a batch of already-inserted evidence rows.

    Rules:
    - genesis prev_hash is run_seed_hash when the run has no chain rows yet
    - per-run seq starts at 1
    - one chain row per evidence row

    Caller typically does:
      BEGIN
      insert_evidence_batch(..., commit=False)
      append_chain_batch(..., commit=False)
      COMMIT
    """
    if not items:
        return []

    try:
        last_seq, last_hash = get_last_seq_and_hash(conn, run_id)
        prev_hash = last_hash if last_hash is not None else run_seed_hash

        out: list[ChainRow] = []
        seq = last_seq

        for item in items:
            seq += 1
            this_hash = compute_chain_hash(
                prev_hash=prev_hash,
                run_id=run_id,
                seq=seq,
                evidence_id=item.evidence_id,
                payload_hash=item.payload_hash,
            )
            out.append(
                ChainRow(
                    run_id=run_id,
                    seq=seq,
                    evidence_id=item.evidence_id,
                    prev_hash=prev_hash,
                    this_hash=this_hash,
                    created_at=created_at,
                )
            )
            prev_hash = this_hash

        conn.executemany(
            """
            INSERT INTO chain (
              run_id, seq, evidence_id, prev_hash, this_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.run_id,
                    row.seq,
                    row.evidence_id,
                    row.prev_hash,
                    row.this_hash,
                    row.created_at,
                )
                for row in out
            ],
        )

        if commit:
            conn.commit()

        return out
    except sqlite3.IntegrityError as e:
        if commit:
            conn.rollback()
        raise ChainStoreError(f"append_chain_batch integrity error: {e}") from e
    except sqlite3.Error as e:
        if commit:
            conn.rollback()
        raise ChainStoreError(f"append_chain_batch sqlite error: {e}") from e


def get_chain_rows(conn: sqlite3.Connection, run_id: str) -> list[ChainRow]:
    try:
        rows = conn.execute(
            """
            SELECT run_id, seq, evidence_id, prev_hash, this_hash, created_at
              FROM chain
             WHERE run_id = ?
             ORDER BY seq ASC
            """,
            (run_id,),
        ).fetchall()

        return [
            ChainRow(
                run_id=r["run_id"],
                seq=int(r["seq"]),
                evidence_id=r["evidence_id"],
                prev_hash=r["prev_hash"],
                this_hash=r["this_hash"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
    except sqlite3.Error as e:
        raise ChainStoreError(f"Failed to load chain rows: {e}") from e


def replay_verify(conn: sqlite3.Connection, run_id: str) -> VerifyResult:
    """
    Verify a run by replaying chain hashes from genesis (= run_seed_hash).

    PASS criteria:
    - every recomputed this_hash matches stored chain.this_hash
    - recomputed last hash == runs.final_chain_hash
    - evidence row count == runs.evidence_count

    Notes:
    - evidence rows are joined by evidence_id to retrieve payload_hash
    - if no chain rows exist, actual_final_chain_hash will be run_seed_hash only in-memory,
      but PASS still depends on runs.final_chain_hash/evidence_count matching expectations
    """
    try:
        run_row = conn.execute(
            """
            SELECT run_id, run_seed_hash, final_chain_hash, evidence_count
              FROM runs
             WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if not run_row:
            raise ChainStoreError(f"Run not found: {run_id}")

        run_seed_hash = str(run_row["run_seed_hash"])
        expected_final_chain_hash = run_row["final_chain_hash"]
        expected_evidence_count = run_row["evidence_count"]

        actual_evidence_count_row = conn.execute(
            "SELECT COUNT(1) AS c FROM evidence WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        actual_evidence_count = int(actual_evidence_count_row["c"]) if actual_evidence_count_row else 0

        rows = conn.execute(
            """
            SELECT c.seq, c.evidence_id, c.prev_hash, c.this_hash, e.payload_hash
              FROM chain c
              JOIN evidence e
                ON e.evidence_id = c.evidence_id
             WHERE c.run_id = ?
             ORDER BY c.seq ASC
            """,
            (run_id,),
        ).fetchall()

        prev_hash = run_seed_hash
        checked = 0
        actual_final_chain_hash: Optional[str] = None

        for r in rows:
            seq = int(r["seq"])
            evidence_id = str(r["evidence_id"])
            stored_prev_hash = str(r["prev_hash"])
            stored_this_hash = str(r["this_hash"])
            payload_hash = str(r["payload_hash"])

            # Check stored prev_hash consistency first
            if stored_prev_hash != prev_hash:
                return VerifyResult(
                    ok=False,
                    run_id=run_id,
                    expected_final_chain_hash=expected_final_chain_hash,
                    actual_final_chain_hash=actual_final_chain_hash,
                    expected_evidence_count=expected_evidence_count,
                    actual_evidence_count=actual_evidence_count,
                    checked_chain_rows=checked,
                    failure_reason="prev_hash mismatch during replay",
                    mismatch_seq=seq,
                    mismatch_evidence_id=evidence_id,
                )

            recomputed = compute_chain_hash(
                prev_hash=prev_hash,
                run_id=run_id,
                seq=seq,
                evidence_id=evidence_id,
                payload_hash=payload_hash,
            )

            if recomputed != stored_this_hash:
                return VerifyResult(
                    ok=False,
                    run_id=run_id,
                    expected_final_chain_hash=expected_final_chain_hash,
                    actual_final_chain_hash=recomputed,
                    expected_evidence_count=expected_evidence_count,
                    actual_evidence_count=actual_evidence_count,
                    checked_chain_rows=checked,
                    failure_reason="this_hash mismatch during replay",
                    mismatch_seq=seq,
                    mismatch_evidence_id=evidence_id,
                )

            prev_hash = recomputed
            actual_final_chain_hash = recomputed
            checked += 1

        # If the run has zero chain rows, the replay endpoint is effectively the seed.
        if actual_final_chain_hash is None:
            actual_final_chain_hash = run_seed_hash

        if expected_final_chain_hash != actual_final_chain_hash:
            return VerifyResult(
                ok=False,
                run_id=run_id,
                expected_final_chain_hash=expected_final_chain_hash,
                actual_final_chain_hash=actual_final_chain_hash,
                expected_evidence_count=expected_evidence_count,
                actual_evidence_count=actual_evidence_count,
                checked_chain_rows=checked,
                failure_reason="final_chain_hash mismatch",
            )

        if expected_evidence_count is None or int(expected_evidence_count) != actual_evidence_count:
            return VerifyResult(
                ok=False,
                run_id=run_id,
                expected_final_chain_hash=expected_final_chain_hash,
                actual_final_chain_hash=actual_final_chain_hash,
                expected_evidence_count=expected_evidence_count,
                actual_evidence_count=actual_evidence_count,
                checked_chain_rows=checked,
                failure_reason="evidence_count mismatch",
            )

        return VerifyResult(
            ok=True,
            run_id=run_id,
            expected_final_chain_hash=expected_final_chain_hash,
            actual_final_chain_hash=actual_final_chain_hash,
            expected_evidence_count=int(expected_evidence_count),
            actual_evidence_count=actual_evidence_count,
            checked_chain_rows=checked,
            failure_reason=None,
        )
    except sqlite3.Error as e:
        raise ChainStoreError(f"Failed to replay verify: {e}") from e