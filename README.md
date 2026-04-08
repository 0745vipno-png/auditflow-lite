AuditFlow Lite

AuditFlow Lite is a local-first operational auditing tool designed to turn live system state into verifiable evidence and reproducible reports.

It collects system evidence using a read-only, one-shot execution model and stores results in an append-only SQLite evidence store with a tamper-evident hash chain.

The goal is simple:

Turn observable system state into verifiable evidence and human-readable reports.


AuditFlow Lite converts live system state into verifiable audit evidence.

Key properties:

• One-shot CLI execution
• Read-only system inspection
• Append-only SQLite evidence store
• Tamper-evident hash chain
• Human-readable Markdown reports

No agents.
No background services.
No hidden runtime behavior.

Example

Run a simple audit:

python -m auditflow.cli init

python -m auditflow.cli run --profile test --target .

Example output:

[INFO] Run ID: 2026-03-06T02-56-26Z
[INFO] Status: OK
[INFO] Evidence Count: 1421
[INFO] Warnings: 0

Generated report:

artifacts/<run_id>/report.md

The report contains a reproducible snapshot of system state with tamper-evident evidence records.

Why AuditFlow Lite?

Many operational audits rely on manual inspection or ad-hoc scripts.

This often results in:

inconsistent results

missing evidence

difficult verification

unclear system state

AuditFlow Lite provides a reproducible and auditable approach to system inspection by collecting deterministic evidence and generating structured reports.

Key Features
One-shot CLI execution

• No background services
• No long-running agents
• Explicit execution boundaries

Read-only collectors

• Filesystem snapshots
• Task Scheduler inspection
• Extensible collector framework

Collectors inspect system state without modifying it.

Append-only evidence store

• SQLite-based
• Immutable evidence records
• Deterministic run storage

Evidence collected during execution is stored as append-only records.

Tamper-evident hash chain

Each evidence record links to the previous record using a hash chain.

This enables integrity verification and makes evidence manipulation detectable.

Human-readable reports

• Markdown reports
• Reproducible run summaries
• Traceable evidence references

Reports are designed to be readable by both engineers and auditors.

Architecture Overview

AuditFlow Lite follows a simple execution pipeline:

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
Design Principles

AuditFlow Lite is built around several core principles:

Append-only evidence

Evidence records are never modified once written.

Deterministic collection

Given the same target state and configuration, collectors should produce reproducible results.

Local-first operation

The system operates entirely on the local machine.

No external services or cloud dependencies are required.

Human-auditable reports

Generated reports prioritize readability and traceability.

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

• Operational auditing
• Change tracking
• Evidence collection
• System inspection
• Reproducible diagnostics

Security Model

AuditFlow Lite focuses on tamper-evident logging, not tamper-proof storage.

Key properties:

• append-only evidence records
• hash-chain integrity verification
• deterministic collectors

The system assumes local trust but verifiable history.

Roadmap

Planned improvements include:

• Evidence diffing between runs
• Anomaly signal detection
• Web-based read-only report viewer
• Collector plugin system

License

This project is licensed under the GNU General Public License v3.0 (GPLv3).

What this means

• Commercial Use
You may use this software for commercial purposes.

• Source Code Requirement
If you modify or distribute this software, your derivative work must also be released under GPLv3.

• Copyleft Protection
This ensures that improvements remain open and prevents proprietary forks.

• No Warranty
This software is provided “as is” without warranty of any kind.

For full details, see the LICENSE file in this repository.

Author

Created and maintained by Zhi-Cheng Wang.
