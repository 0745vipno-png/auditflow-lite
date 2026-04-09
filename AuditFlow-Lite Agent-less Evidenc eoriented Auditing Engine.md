# AuditFlow-Lite: Agent-less Evidence-oriented Auditing Engine

> **Turning observable system state into immutable, verifiable evidence.**

[![License: GPLv3](https://shields.io)](https://gnu.org)

AuditFlow-Lite 是一款專為「高合規需求環境」設計的系統稽核工具。它能在不安裝任何 Agent、不侵入現有服務的前提下，將動態的系統狀態轉換為具備加密保護的不可竄改證據。

### 🏗️ Engineering Layout (System Architecture)

```text

auditflow-lite/
├── auditflow/
│   ├── core/         # 🧠 數據規範化 (Canonicalization) 與 Pipeline
│   ├── collectors/   # 🔍 唯讀式系統證據收集 (Filesystem, Task Scheduler)
│   ├── store/        # 🗄️ 加密審計鏈 (Append-only SQLite & Hash Chain)
│   └── report/       # 📄 自動化產出可重現 (Reproducible) 的 MD 報告
└── profiles/         # ⚙️ 稽核規則配置 (Yaml-based)