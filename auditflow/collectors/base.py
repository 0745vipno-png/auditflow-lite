# auditflow/collectors/base.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Protocol, Sequence


class CollectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class CollectorSpec:
    """
    Resolved collector configuration from profile.

    Example:
        CollectorSpec(
            type="filesystem_snapshot",
            options={
                "targets": ["D:\\Work"],
                "include_hidden": False,
                "hash_files": False,
            },
        )
    """
    type: str
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunContext:
    """
    Runtime context passed into collectors.

    Notes:
    - Collector must treat this as read-only input.
    - temp_dir is reserved for temporary files generated during this run.
    - artifacts_run_dir is the stable directory for this run's attachments/artifacts.
    """
    run_id: str
    profile: str
    targets: Sequence[str]
    tags: Mapping[str, Any]
    notes: Optional[str]

    env_info: Mapping[str, Any]
    temp_dir: Path
    artifacts_run_dir: Path

    collector_name: str
    collector_options: Mapping[str, Any]


@dataclass(frozen=True)
class EvidenceRecord:
    """
    Collector output before canonicalization / hashing / DB insertion.

    Required:
    - collector
    - kind
    - ts
    - payload (Python dict/list/scalar JSON-compatible object)

    Optional:
    - attachment_path: filesystem path to a generated attachment file
      (e.g. JSONL with many filesystem entries)
    """
    collector: str
    kind: str
    ts: str
    payload: Any

    attachment_path: Optional[Path] = None


class Collector(Protocol):
    """
    Collector protocol:
      collect(context) -> iterable of EvidenceRecord

    Constraints:
    - must be read-only with respect to user data
    - should produce deterministic output order
    - payload should be structured and JSON-serializable
    - may raise CollectorError for fatal collector-specific failures
    """
    name: str

    def collect(self, context: RunContext) -> Iterable[EvidenceRecord]:
        ...


class BaseCollector:
    """
    Convenience base class for concrete collectors.

    Subclasses should override:
      - name
      - collect()

    They may also use helper methods here.
    """
    name: str = "base"

    def collect(self, context: RunContext) -> Iterable[EvidenceRecord]:
        raise NotImplementedError("Collector must implement collect(context)")

    def require_option(self, context: RunContext, key: str) -> Any:
        if key not in context.collector_options:
            raise CollectorError(f"[{self.name}] missing required option: {key}")
        return context.collector_options[key]

    def option(self, context: RunContext, key: str, default: Any = None) -> Any:
        return context.collector_options.get(key, default)

    def ensure_artifacts_dir(self, context: RunContext, subdir: Optional[str] = None) -> Path:
        """
        Return a collector-owned directory under artifacts/<run_id>/.
        Creates it if needed.

        Example:
            artifacts/<run_id>/filesystem_snapshot/
        """
        base = context.artifacts_run_dir / self.name
        if subdir:
            base = base / subdir
        base.mkdir(parents=True, exist_ok=True)
        return base

    def ensure_temp_dir(self, context: RunContext, subdir: Optional[str] = None) -> Path:
        """
        Return a collector-owned temporary directory under temp_dir.
        Creates it if needed.
        """
        base = context.temp_dir / self.name
        if subdir:
            base = base / subdir
        base.mkdir(parents=True, exist_ok=True)
        return base

    def iter_targets(self, context: RunContext) -> Iterator[Path]:
        """
        Yield normalized target paths from context.targets.
        """
        for target in context.targets:
            yield Path(target)

    def build_evidence(
        self,
        *,
        kind: str,
        ts: str,
        payload: Any,
        attachment_path: Optional[Path] = None,
    ) -> EvidenceRecord:
        return EvidenceRecord(
            collector=self.name,
            kind=kind,
            ts=ts,
            payload=payload,
            attachment_path=attachment_path,
        )