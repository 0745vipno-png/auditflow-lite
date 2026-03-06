# AuditFlow Lite

AuditFlow Lite is a local-first, one-shot evidence collection and reporting system.

It is designed to capture **operational evidence** from a system in a deterministic,
append-only manner, producing **human-readable reports** and a **tamper-evident hash chain**.

The tool is intentionally simple:

- No background agents
- No automatic decisions
- No destructive actions
- No cloud dependency

AuditFlow Lite focuses on one thing:

> Collect verifiable evidence and produce readable reports.


---

# Design Philosophy

AuditFlow Lite follows several core principles.

### One-shot execution

Each command execution performs a single audit run.


auditflow run --profile windows_basic --target D:\Work


The run collects evidence, writes it to the local store, produces a report,
and then exits.

No background service is required.

---

### Append-only evidence store

All evidence is stored in SQLite in an **append-only** manner.

Evidence is never modified or deleted.

Every run records:

- inputs
- environment fingerprint
- collected evidence
- artifacts (reports, attachments)

This ensures that historical runs remain reproducible and inspectable.

---

### Tamper-evident chain

Every evidence record is linked through a **hash chain**.


GENESIS (run_seed_hash)
↓
Evidence #1
↓
Evidence #2
↓
...
↓
final_chain_hash


Verification can replay the chain to detect tampering.


auditflow verify <run_id>


This does not make the system tamper-proof,
but it does make tampering **detectable**.

---

### Human-readable outputs

The system produces reports in Markdown format.

Reports summarize:

- run metadata
- environment fingerprint
- evidence counts
- warnings / errors
- artifact references

Example:


artifacts/<run_id>/report.md


---

# Architecture

High-level architecture:


User
│
▼
auditflow CLI
│
▼
Run Orchestrator
│
├─ Collectors (read-only)
│ ├─ filesystem_snapshot
│ └─ ...
│
├─ Evidence Store (SQLite)
│ ├─ runs
│ ├─ evidence
│ ├─ chain
│ └─ artifacts
│
└─ Report Engine
└─ Markdown reports


Optional components (future versions):

- Web read-only UI
- Diff engine
- Additional collectors

---

# Project Structure


auditflow-lite/
│
├─ auditflow/
│ ├─ cli.py
│ ├─ core/
│ │ ├─ runner.py
│ │ └─ canonical.py
│ │
│ ├─ collectors/
│ │ ├─ base.py
│ │ └─ filesystem_snapshot.py
│ │
│ ├─ config/
│ │ └─ loader.py
│ │
│ ├─ report/
│ │ └─ md.py
│ │
│ ├─ store/
│ │ ├─ schema.sql
│ │ ├─ db.py
│ │ ├─ runs.py
│ │ ├─ evidence.py
│ │ ├─ chain.py
│ │ └─ artifacts.py
│ │
│ └─ util/
│ └─ hashing.py
│
├─ profiles/
│ └─ windows_basic.yaml
│
└─ tests/


---

# Installation

Requires:

- Python 3.11+
- SQLite (bundled with Python)

Clone the repository and run directly:


python -m auditflow init


This creates the runtime directory:


~/.auditflow/


---

# Quick Start

Initialize the environment:


python -m auditflow init


Run an audit:


python -m auditflow run
--profile windows_basic
--target D:\Work


Output example:


[INFO] Run ID: 2026-03-06T02-56-26Z
[INFO] Status: OK
[INFO] Report: artifacts/<run_id>/report.md
[INFO] Evidence Count: 2
[INFO] Final Chain Hash: ...


---

# Verify Integrity

To verify the hash chain:


python -m auditflow verify <run_id>


Example:


[PASS] Chain integrity verified


---

# Profiles

Profiles define which collectors run and their configuration.

Example:

```yaml
name: windows_basic

collectors:
  - type: filesystem_snapshot
    options:
      targets: "{{TARGETS}}"
      include_hidden: false
      hash_files: false

Variables such as {{TARGETS}} are injected by the CLI.

Evidence Model

Evidence records have the following structure:

collector
kind
timestamp
payload_json
payload_hash
attachment (optional)

Payloads are stored as canonical JSON.

Hash computation:

payload_hash = sha256(payload_json || attachment_hash || attachment_size)
Security Notes

AuditFlow Lite is tamper-evident, not tamper-proof.

The system detects:

evidence modification

chain manipulation

record removal

However, it cannot prevent:

database deletion

full disk rollback

attacker with full system control

It is designed as an evidence recorder, not a hardened security system.

Roadmap

Planned improvements:

Diff Engine

Compare two runs:

auditflow diff <runA> <runB>

Detect:

file additions

file removals

file modifications

Additional Collectors

Examples:

Windows Task Scheduler

process snapshot

event logs

Read-only Web UI

Local web interface for browsing:

runs

evidence

reports