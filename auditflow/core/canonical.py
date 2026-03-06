# auditflow/core/canonical.py
from __future__ import annotations

import json
from typing import Any, Iterable, List, Dict, Optional

from auditflow.util.hashing import sha256_text


class CanonicalError(RuntimeError):
    pass


# ----------------------------------------------------------------------
# Canonical JSON
# ----------------------------------------------------------------------

def canonical_json_dumps(obj: Any) -> str:
    """
    Convert Python object -> canonical JSON string.

    Rules:
    - sort_keys=True
    - compact separators
    - ensure_ascii=False
    - deterministic output
    """
    try:
        return json.dumps(
            obj,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except Exception as e:
        raise CanonicalError(f"Failed to canonicalize JSON: {e}") from e


def canonical_json_loads(text: str) -> Any:
    """
    Load JSON text.
    Mostly used in tests / debug / reporting.
    """
    try:
        return json.loads(text)
    except Exception as e:
        raise CanonicalError(f"Invalid JSON payload: {e}") from e


# ----------------------------------------------------------------------
# Stable Sorting Utilities
# ----------------------------------------------------------------------

def stable_sort_records(
    records: Iterable[Dict[str, Any]],
    *,
    key: str
) -> List[Dict[str, Any]]:
    """
    Deterministically sort list of dict records by key.

    Example:
        stable_sort_records(files, key="path")
    """
    try:
        return sorted(records, key=lambda r: r.get(key, ""))
    except Exception as e:
        raise CanonicalError(f"Failed to sort records by {key}: {e}") from e


def stable_sort_multi(
    records: Iterable[Dict[str, Any]],
    *,
    keys: List[str],
) -> List[Dict[str, Any]]:
    """
    Sort by multiple keys deterministically.

    Example:
        stable_sort_multi(tasks, keys=["folder", "task_name"])
    """
    try:
        return sorted(
            records,
            key=lambda r: tuple(r.get(k, "") for k in keys),
        )
    except Exception as e:
        raise CanonicalError(f"Failed to multi-sort records: {e}") from e


# ----------------------------------------------------------------------
# Payload Hash
# ----------------------------------------------------------------------

def compute_payload_hash(
    payload_json: str,
    attachment_sha256: Optional[str],
    attachment_size: Optional[int],
) -> str:
    """
    Compute payload hash used in evidence table.

    Spec (from architecture):
        payload_hash =
            sha256(payload_json || attachment_sha256 || attachment_size)

    Rules:
    - None values become empty string
    - attachment_size converted to str
    """
    a_hash = attachment_sha256 or ""
    a_size = "" if attachment_size is None else str(attachment_size)

    material = "||".join([
        payload_json,
        a_hash,
        a_size,
    ])

    return sha256_text(material)


# ----------------------------------------------------------------------
# Canonicalize Evidence Payload
# ----------------------------------------------------------------------

def canonicalize_payload(obj: Any) -> tuple[str, str]:
    """
    Convert Python object payload -> canonical JSON + payload_hash
    (without attachments).

    Returns:
        (payload_json, payload_hash)

    Note:
        attachment hash must be applied separately using compute_payload_hash()
        when attachments exist.
    """
    payload_json = canonical_json_dumps(obj)
    payload_hash = compute_payload_hash(payload_json, None, None)
    return payload_json, payload_hash