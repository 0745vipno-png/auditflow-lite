# auditflow/util/hashing.py
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, Optional


class HashingError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    """
    Return SHA-256 hex digest for raw bytes.
    """
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str, encoding: str = "utf-8") -> str:
    """
    Return SHA-256 hex digest for text.
    """
    return sha256_bytes(text.encode(encoding))


def sha256_stream(stream: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
    """
    Return SHA-256 hex digest for a binary stream.

    Notes:
    - Reads from current stream position to EOF.
    - Caller is responsible for stream lifecycle and seek position.
    """
    hasher = hashlib.sha256()

    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        hasher.update(chunk)

    return hasher.hexdigest()


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """
    Return SHA-256 hex digest for a file.

    Uses chunked reading to avoid loading large files into memory.
    """
    file_path = Path(path)

    if not file_path.exists():
        raise HashingError(f"File not found: {file_path}")
    if not file_path.is_file():
        raise HashingError(f"Not a regular file: {file_path}")

    try:
        with file_path.open("rb") as f:
            return sha256_stream(f, chunk_size=chunk_size)
    except OSError as e:
        raise HashingError(f"Failed to hash file {file_path}: {e}") from e


def sha256_optional_text(value: Optional[str], encoding: str = "utf-8") -> str:
    """
    Convenience helper:
    - None -> empty string hash
    - str  -> SHA-256 of the string
    """
    return sha256_text("" if value is None else value, encoding=encoding)