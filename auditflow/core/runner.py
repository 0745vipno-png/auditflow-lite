# auditflow/core/runner.py
from __future__ import annotations

import socket
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from auditflow.collectors.base import CollectorSpec, EvidenceRecord, RunContext
from auditflow.collectors.filesystem_snapshot import FilesystemSnapshotCollector
from auditflow.config.loader import (
    build_collector_specs,
    get_report_config,
    load_and_resolve_profile,
)
from auditflow.core.canonical import canonical_json_dumps, compute_payload_hash
from auditflow.report.md import (
    ArtifactItem,
    EvidenceSummaryItem,
    RunReportData,
    render_and_write_markdown_report,
)
from auditflow.store.artifacts import insert_artifact
from auditflow.store.chain import ChainInput, append_chain_batch
from auditflow.store.db import DBPaths, open_db
from auditflow.store.evidence import EvidenceInsert, count_evidence_by_collector_and_kind, insert_evidence_batch
from auditflow.store.runs import (
    STATUS_ABORTED,
    STATUS_FAIL,
    STATUS_OK,
    STATUS_RUNNING,
    STATUS_WARN,
    count_evidence,
    create_run,
    get_last_chain_hash,
    get_run,
    mark_aborted_if_running,
    seal_run,
)
from auditflow.util.hashing import sha256_file, sha256_text


class RunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    report_path: Path
    warnings_count: int
    errors_count: int
    evidence_count: int
    final_chain_hash: str


@dataclass
class RunnerCounters:
    warnings_count: int = 0
    errors_count: int = 0
    highlights: list[str] = field(default_factory=list)


def run_auditflow(
    *,
    profile_name_or_path: str,
    targets: Sequence[str],
    tags: Optional[Mapping[str, Any]] = None,
    notes: Optional[str] = None,
    auditflow_home: Optional[str] = None,
) -> RunResult:
    """
    Execute one full AuditFlow run.

    v0.1:
    - supports resolved profile + collector registry
    - supports filesystem_snapshot collector
    - writes markdown report
    - seals run with final_chain_hash + evidence_count
    """
    tags = dict(tags or {})

    paths, conn = open_db(auditflow_home)
    run_id: Optional[str] = None
    temp_dir_obj = None

    try:
        # 1) Load/resolve profile
        profile = load_and_resolve_profile(
            profile_name_or_path,
            targets=targets,
            profiles_dir=paths.profiles_dir,
        )
        specs = build_collector_specs(profile)
        report_config = get_report_config(profile)

        profile_name = str(profile["name"])
        started_at = _now_iso()
        run_id = _build_run_id()

        env_info = _build_env_info(
            profile_name=profile_name,
            targets=targets,
            tool_version="0.1",
            now_iso=started_at,
        )
        env_fingerprint = sha256_text(canonical_json_dumps(env_info))

        run_seed_dict = {
            "profile": profile_name,
            "targets": list(targets),
            "env_fingerprint": env_fingerprint,
            "tool_version": "0.1",
        }
        run_seed_hash = sha256_text(canonical_json_dumps(run_seed_dict))

        # 2) Create run row
        create_run(
            conn,
            run_id=run_id,
            started_at=started_at,
            profile=profile_name,
            targets=list(targets),
            tags=tags,
            notes=notes,
            env_fingerprint=env_fingerprint,
            run_seed_hash=run_seed_hash,
        )

        # 3) Prepare runtime dirs
        run_artifacts_dir = paths.artifacts_dir / run_id
        run_artifacts_dir.mkdir(parents=True, exist_ok=True)

        temp_dir_obj = tempfile.TemporaryDirectory(prefix=f"auditflow_{run_id}_")
        temp_dir = Path(temp_dir_obj.name)

        counters = RunnerCounters()

        # 4) Execute collectors in deterministic order
        for spec in specs:
            collector = _resolve_collector(spec)

            context = RunContext(
                run_id=run_id,
                profile=profile_name,
                targets=list(targets),
                tags=tags,
                notes=notes,
                env_info=env_info,
                temp_dir=temp_dir,
                artifacts_run_dir=run_artifacts_dir,
                collector_name=collector.name,
                collector_options=spec.options,
            )

            try:
                raw_records = list(collector.collect(context))
            except Exception as e:
                counters.errors_count += 1
                counters.highlights.append(
                    f"Collector `{collector.name}` failed: {e}"
                )
                # handled degradation: continue run
                error_record = EvidenceRecord(
                    collector=collector.name,
                    kind="collector_error",
                    ts=started_at,
                    payload={
                        "collector": collector.name,
                        "error": str(e),
                        "severity": "error",
                    },
                    attachment_path=None,
                )
                raw_records = [error_record]

            prepared = _prepare_evidence_batch(
                run_id=run_id,
                raw_records=raw_records,
            )

            if not prepared:
                continue

            # evidence + chain in one transaction
            conn.execute("BEGIN")
            insert_evidence_batch(conn, prepared["evidence_rows"], commit=False)
            append_chain_batch(
                conn,
                run_id=run_id,
                run_seed_hash=run_seed_hash,
                items=prepared["chain_inputs"],
                created_at=_now_iso(),
                commit=False,
            )
            conn.commit()

        # 5) Build report
        evidence_summary = [
            EvidenceSummaryItem(collector=c, kind=k, count=n)
            for (c, k, n) in count_evidence_by_collector_and_kind(conn, run_id)
        ]

        pre_report_evidence_count = count_evidence(conn, run_id)
        pre_report_final_chain_hash = get_last_chain_hash(conn, run_id) or run_seed_hash

        report_data = RunReportData(
            run_id=run_id,
            status=STATUS_RUNNING,  # temporary, before sealing
            profile=profile_name,
            started_at=started_at,
            finished_at=None,
            targets=list(targets),
            tags=tags,
            notes=notes,
            env_fingerprint=env_fingerprint,
            env_info=env_info,
            warnings_count=counters.warnings_count,
            errors_count=counters.errors_count,
            evidence_count=pre_report_evidence_count,
            final_chain_hash=pre_report_final_chain_hash,
            evidence_summary=evidence_summary,
            artifacts=[],
            highlights=list(counters.highlights),
        )

        report_path = run_artifacts_dir / "report.md"
        render_and_write_markdown_report(report_path, report_data)
        report_sha256 = sha256_file(report_path)

        insert_artifact(
            conn,
            artifact_id=_build_artifact_id(run_id, "report_md"),
            run_id=run_id,
            artifact_type="report_md",
            path=str(report_path),
            sha256=report_sha256,
            created_at=_now_iso(),
        )

        # 6) Add artifact_manifest evidence and chain it
        artifact_manifest_payload = {
            "artifacts": [
                {
                    "type": "report_md",
                    "path": str(report_path),
                    "sha256": report_sha256,
                }
            ]
        }
        artifact_manifest_record = EvidenceRecord(
            collector="report_engine",
            kind="artifact_manifest",
            ts=_now_iso(),
            payload=artifact_manifest_payload,
            attachment_path=None,
        )

        prepared_manifest = _prepare_evidence_batch(
            run_id=run_id,
            raw_records=[artifact_manifest_record],
        )

        if prepared_manifest["evidence_rows"]:
            conn.execute("BEGIN")
            insert_evidence_batch(conn, prepared_manifest["evidence_rows"], commit=False)
            append_chain_batch(
                conn,
                run_id=run_id,
                run_seed_hash=run_seed_hash,
                items=prepared_manifest["chain_inputs"],
                created_at=_now_iso(),
                commit=False,
            )
            conn.commit()

        # 7) Finalize / seal
        finished_at = _now_iso()
        final_evidence_count = count_evidence(conn, run_id)
        final_chain_hash = get_last_chain_hash(conn, run_id) or run_seed_hash
        final_status = _decide_final_status(counters)

        seal_run(
            conn,
            run_id=run_id,
            finished_at=finished_at,
            status=final_status,
            warnings_count=counters.warnings_count,
            errors_count=counters.errors_count,
            evidence_count=final_evidence_count,
            final_chain_hash=final_chain_hash,
        )

        return RunResult(
            run_id=run_id,
            status=final_status,
            report_path=report_path,
            warnings_count=counters.warnings_count,
            errors_count=counters.errors_count,
            evidence_count=final_evidence_count,
            final_chain_hash=final_chain_hash,
        )

    except Exception as e:
        if run_id is not None:
            try:
                # best effort abort visibility
                mark_aborted_if_running(
                    conn,
                    run_id=run_id,
                    finished_at=_now_iso(),
                    warnings_count=0,
                    errors_count=1,
                )
            except Exception:
                pass
        raise RunnerError(str(e)) from e
    finally:
        try:
            conn.close()
        except Exception:
            pass
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()


def _prepare_evidence_batch(
    *,
    run_id: str,
    raw_records: Sequence[EvidenceRecord],
) -> dict[str, list[Any]]:
    evidence_rows: list[EvidenceInsert] = []
    chain_inputs: list[ChainInput] = []

    for idx, record in enumerate(raw_records, start=1):
        payload_json = canonical_json_dumps(record.payload)

        attachment_path_str: Optional[str] = None
        attachment_sha256: Optional[str] = None
        attachment_size: Optional[int] = None

        if record.attachment_path is not None:
            attachment_path = Path(record.attachment_path)
            attachment_path_str = str(attachment_path)
            attachment_sha256 = sha256_file(attachment_path)
            attachment_size = attachment_path.stat().st_size

        payload_hash = compute_payload_hash(
            payload_json,
            attachment_sha256,
            attachment_size,
        )

        evidence_id = _build_evidence_id(
            run_id=run_id,
            collector=record.collector,
            index=idx,
            kind=record.kind,
        )

        evidence_rows.append(
            EvidenceInsert(
                evidence_id=evidence_id,
                run_id=run_id,
                collector=record.collector,
                kind=record.kind,
                ts=record.ts,
                payload_json=payload_json,
                payload_hash=payload_hash,
                attachment_path=attachment_path_str,
                attachment_sha256=attachment_sha256,
                attachment_size=attachment_size,
            )
        )
        chain_inputs.append(
            ChainInput(
                evidence_id=evidence_id,
                payload_hash=payload_hash,
            )
        )

    return {
        "evidence_rows": evidence_rows,
        "chain_inputs": chain_inputs,
    }


def _resolve_collector(spec: CollectorSpec):
    registry = {
        "filesystem_snapshot": FilesystemSnapshotCollector,
    }

    if spec.type not in registry:
        raise RunnerError(f"Unsupported collector type: {spec.type}")

    return registry[spec.type]()


def _build_env_info(
    *,
    profile_name: str,
    targets: Sequence[str],
    tool_version: str,
    now_iso: str,
) -> dict[str, Any]:
    return {
        "now_iso": now_iso,
        "hostname": socket.gethostname(),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "profile": profile_name,
        "targets": list(targets),
        "tool_version": tool_version,
    }


def _decide_final_status(counters: RunnerCounters) -> str:
    if counters.errors_count > 0:
        return STATUS_FAIL
    if counters.warnings_count > 0:
        return STATUS_WARN
    return STATUS_OK


def _build_run_id() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _build_evidence_id(*, run_id: str, collector: str, index: int, kind: str) -> str:
    safe_collector = collector.replace(" ", "_")
    safe_kind = kind.replace(" ", "_")
    return f"{run_id}:{safe_collector}:{safe_kind}:{index:06d}"


def _build_artifact_id(run_id: str, artifact_type: str) -> str:
    safe_type = artifact_type.replace(" ", "_")
    return f"{run_id}:{safe_type}"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()