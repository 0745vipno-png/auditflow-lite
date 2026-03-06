# auditflow/report/md.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


class MarkdownReportError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceSummaryItem:
    collector: str
    kind: str
    count: int


@dataclass(frozen=True)
class ArtifactItem:
    type: str
    path: str
    sha256: str
    created_at: Optional[str] = None


@dataclass(frozen=True)
class RunReportData:
    """
    Normalized input for Markdown report generation.

    The report layer should not know about sqlite.Row directly.
    Feed it plain Python values / dataclasses.
    """
    run_id: str
    status: str
    profile: str
    started_at: str
    finished_at: Optional[str]

    targets: Sequence[str]
    tags: Mapping[str, Any]
    notes: Optional[str]

    env_fingerprint: str
    env_info: Mapping[str, Any]

    warnings_count: int
    errors_count: int
    evidence_count: Optional[int]
    final_chain_hash: Optional[str]

    evidence_summary: Sequence[EvidenceSummaryItem] = field(default_factory=list)
    artifacts: Sequence[ArtifactItem] = field(default_factory=list)

    highlights: Sequence[str] = field(default_factory=list)


def render_markdown_report(data: RunReportData) -> str:
    """
    Render report as Markdown string.
    """
    lines: list[str] = []

    # Title
    lines.append(f"# AuditFlow Lite Report")
    lines.append("")
    lines.append(f"- **Run ID:** `{data.run_id}`")
    lines.append(f"- **Status:** `{data.status}`")
    lines.append("")

    # Run summary
    lines.append("## Run Summary")
    lines.append("")
    lines.append(f"- **Profile:** `{data.profile}`")
    lines.append(f"- **Started At:** `{data.started_at}`")
    lines.append(f"- **Finished At:** `{data.finished_at or ''}`")
    lines.append(f"- **Warnings:** `{data.warnings_count}`")
    lines.append(f"- **Errors:** `{data.errors_count}`")
    lines.append(f"- **Evidence Count:** `{data.evidence_count if data.evidence_count is not None else ''}`")
    lines.append(f"- **Final Chain Hash:** `{data.final_chain_hash or ''}`")
    lines.append("")

    # Targets
    lines.append("## Targets")
    lines.append("")
    if data.targets:
        for target in data.targets:
            lines.append(f"- `{target}`")
    else:
        lines.append("- _(none)_")
    lines.append("")

    # Tags
    lines.append("## Tags")
    lines.append("")
    if data.tags:
        for k in sorted(data.tags.keys()):
            lines.append(f"- **{k}:** `{_stringify_inline(data.tags[k])}`")
    else:
        lines.append("- _(none)_")
    lines.append("")

    # Notes
    lines.append("## Notes")
    lines.append("")
    if data.notes:
        lines.append(data.notes)
    else:
        lines.append("_None_")
    lines.append("")

    # Environment
    lines.append("## Environment Fingerprint")
    lines.append("")
    lines.append(f"- **env_fingerprint:** `{data.env_fingerprint}`")
    if data.env_info:
        for k in sorted(data.env_info.keys()):
            lines.append(f"- **{k}:** `{_stringify_inline(data.env_info[k])}`")
    lines.append("")

    # Highlights
    lines.append("## Highlights")
    lines.append("")
    if data.highlights:
        for item in data.highlights:
            lines.append(f"- {item}")
    else:
        lines.append("- _(none)_")
    lines.append("")

    # Evidence summary
    lines.append("## Evidence Summary")
    lines.append("")
    if data.evidence_summary:
        lines.append("| Collector | Kind | Count |")
        lines.append("|---|---|---:|")
        for item in data.evidence_summary:
            lines.append(f"| `{item.collector}` | `{item.kind}` | {item.count} |")
    else:
        lines.append("_No evidence summary available._")
    lines.append("")

    # Artifacts
    lines.append("## Artifacts")
    lines.append("")
    if data.artifacts:
        lines.append("| Type | Path | SHA256 | Created At |")
        lines.append("|---|---|---|---|")
        for a in data.artifacts:
            lines.append(
                f"| `{a.type}` | `{a.path}` | `{a.sha256}` | `{a.created_at or ''}` |"
            )
    else:
        lines.append("_No artifacts recorded._")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_markdown_report(report_path: str | Path, content: str) -> Path:
    """
    Write markdown content to file, creating parent dirs if needed.
    """
    path = Path(report_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path
    except OSError as e:
        raise MarkdownReportError(f"Failed to write markdown report {path}: {e}") from e


def render_and_write_markdown_report(
    report_path: str | Path,
    data: RunReportData,
) -> Path:
    """
    Convenience wrapper:
      render -> write -> return path
    """
    content = render_markdown_report(data)
    return write_markdown_report(report_path, content)


def _stringify_inline(value: Any) -> str:
    """
    Stringify values safely for inline markdown usage.
    """
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)