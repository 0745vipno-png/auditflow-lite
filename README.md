# AuditFlow Lite

AuditFlow Lite is a **local-first evidence collection and reporting system** designed for reproducible operational audits.

It collects system evidence in a **read-only, one-shot execution model** and stores results in an **append-only SQLite store with a tamper-evident hash chain**.

The goal is simple:

> Turn observable system state into verifiable evidence and human-readable reports.

---

## Key Features

- **One-shot CLI execution**
  - No background service
  - No long-running agents

- **Read-only collectors**
  - Filesystem snapshots
  - Task Scheduler inspection
  - Extensible collector framework

- **Append-only evidence store**
  - SQLite-based
  - Immutable evidence records

- **Tamper-evident hash chain**
  - Each evidence record links to the previous
  - Enables integrity verification

- **Human-readable reports**
  - Markdown reports
  - Reproducible run summaries

---

## Example

Run a simple audit:

```bash
python -m auditflow.cli init

python -m auditflow.cli run --profile test --target .

Example output:

[INFO] Run ID: 2026-03-06T02-56-26Z
[INFO] Status: OK
[INFO] Evidence Count: 1421
[INFO] Warnings: 0

Report location:

artifacts/<run_id>/report.md
Architecture Overview

AuditFlow Lite follows a simple pipeline:

CLI
  ↓
Run Orchestrator
  ↓
Collectors
  ↓
Evidence Store (SQLite)
  ↓
Hash Chain
  ↓
Report Engine

Design principles:

Append-only evidence

Deterministic collection

Local-first operation

Human-auditable reports

Project Structure
auditflow-lite/
├─ auditflow/        # core implementation
├─ profiles/         # collector profiles
├─ docs/             # architecture & design docs
├─ tests/            # test suite
├─ artifacts/        # generated reports
└─ logs/             # runtime logs
Use Cases

AuditFlow Lite can be used for:

Operational auditing

Change tracking

Evidence collection

System inspection

Reproducible diagnostics

Security Model

AuditFlow Lite focuses on tamper-evident logging, not tamper-proof storage.

Key properties:

append-only evidence records

hash-chain integrity verification

deterministic collectors

The system assumes local trust but verifiable history.

Roadmap

Planned improvements:

Evidence diffing between runs

Anomaly signal detection

Web-based read-only viewer

Collector plugin system

License

Apache License 2.0


---

# 為什麼這樣寫

這個 README 結構是 **典型開源專案 landing page**：

順序是：


Title
↓
Project explanation
↓
Key features
↓
Example usage
↓
Architecture
↓
Project structure
↓
Use cases
↓
Security model
↓
Roadmap
↓
License
