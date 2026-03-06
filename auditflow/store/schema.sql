-- AuditFlow Lite - SQLite Schema (v0.1)
-- Key goals:
-- - evidence/chain/artifacts: append-only ENFORCED (no UPDATE/DELETE)
-- - runs: allow restricted UPDATE only for sealing/finalization fields
-- - chain uses per-run seq for deterministic replay

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- runs: one row per run (RUNNING -> terminal)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
  run_id            TEXT PRIMARY KEY,
  started_at        TEXT NOT NULL,                 -- ISO8601
  finished_at       TEXT,                          -- ISO8601
  status            TEXT NOT NULL,                 -- RUNNING | OK | WARN | FAIL | ABORTED

  profile           TEXT NOT NULL,
  targets_json      TEXT NOT NULL,                 -- canonical JSON array
  tags_json         TEXT NOT NULL,                 -- canonical JSON object/array
  notes             TEXT,

  env_fingerprint   TEXT NOT NULL,                 -- sha256(canonical env dict)
  run_seed_hash     TEXT NOT NULL,                 -- sha256(canonical seed dict)

  warnings_count    INTEGER NOT NULL DEFAULT 0,
  errors_count      INTEGER NOT NULL DEFAULT 0,

  evidence_count    INTEGER,                       -- set at sealing
  final_chain_hash  TEXT                           -- set at sealing
);

CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_profile    ON runs(profile);

-- ---------------------------------------------------------------------------
-- evidence: append-only evidence records
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id        TEXT PRIMARY KEY,
  run_id             TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,

  collector          TEXT NOT NULL,
  kind               TEXT NOT NULL,                -- e.g., filesystem_summary, scheduled_task, collector_error, artifact_manifest
  ts                 TEXT NOT NULL,                -- ISO8601

  payload_json       TEXT NOT NULL,                -- canonical JSON
  attachment_path    TEXT,                         -- relative or absolute; prefer relative under artifacts/<run_id>/
  attachment_sha256  TEXT,
  attachment_size    INTEGER,

  payload_hash       TEXT NOT NULL                 -- sha256(payload_json || attachment_sha256 || attachment_size)
);

CREATE INDEX IF NOT EXISTS idx_evidence_run_collector ON evidence(run_id, collector);
CREATE INDEX IF NOT EXISTS idx_evidence_kind          ON evidence(kind);
CREATE INDEX IF NOT EXISTS idx_evidence_ts            ON evidence(ts);

-- ---------------------------------------------------------------------------
-- chain: tamper-evident chain, per-run sequencing
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chain (
  run_id       TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
  seq          INTEGER NOT NULL,                     -- per-run sequence number starting from 1
  evidence_id  TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE RESTRICT,

  prev_hash    TEXT NOT NULL,
  this_hash    TEXT NOT NULL,
  created_at   TEXT NOT NULL,                        -- ISO8601

  PRIMARY KEY (run_id, seq),
  UNIQUE (run_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_chain_run_seq ON chain(run_id, seq);

-- ---------------------------------------------------------------------------
-- artifacts: append-only artifacts metadata (report/diff/attachments indexes)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id  TEXT PRIMARY KEY,
  run_id       TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,

  type         TEXT NOT NULL,                        -- report_md | diff_md | report_html | attachment_index ...
  path         TEXT NOT NULL,                        -- filesystem path (prefer relative to AUDITFLOW_HOME)
  sha256       TEXT NOT NULL,
  created_at   TEXT NOT NULL                         -- ISO8601
);

CREATE INDEX IF NOT EXISTS idx_artifacts_run  ON artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(type);

-- ---------------------------------------------------------------------------
-- Append-only enforcement: evidence / chain / artifacts
-- ---------------------------------------------------------------------------

-- evidence: forbid UPDATE
CREATE TRIGGER IF NOT EXISTS trg_evidence_no_update
BEFORE UPDATE ON evidence
BEGIN
  SELECT RAISE(ABORT, 'append-only: evidence cannot be updated');
END;

-- evidence: forbid DELETE
CREATE TRIGGER IF NOT EXISTS trg_evidence_no_delete
BEFORE DELETE ON evidence
BEGIN
  SELECT RAISE(ABORT, 'append-only: evidence cannot be deleted');
END;

-- chain: forbid UPDATE
CREATE TRIGGER IF NOT EXISTS trg_chain_no_update
BEFORE UPDATE ON chain
BEGIN
  SELECT RAISE(ABORT, 'append-only: chain cannot be updated');
END;

-- chain: forbid DELETE
CREATE TRIGGER IF NOT EXISTS trg_chain_no_delete
BEFORE DELETE ON chain
BEGIN
  SELECT RAISE(ABORT, 'append-only: chain cannot be deleted');
END;

-- artifacts: forbid UPDATE
CREATE TRIGGER IF NOT EXISTS trg_artifacts_no_update
BEFORE UPDATE ON artifacts
BEGIN
  SELECT RAISE(ABORT, 'append-only: artifacts cannot be updated');
END;

-- artifacts: forbid DELETE
CREATE TRIGGER IF NOT EXISTS trg_artifacts_no_delete
BEFORE DELETE ON artifacts
BEGIN
  SELECT RAISE(ABORT, 'append-only: artifacts cannot be deleted');
END;

-- ---------------------------------------------------------------------------
-- runs restrictions:
-- - forbid DELETE always
-- - allow UPDATE only for sealing fields:
--   finished_at, status, warnings_count, errors_count, evidence_count, final_chain_hash
-- ---------------------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS trg_runs_no_delete
BEFORE DELETE ON runs
BEGIN
  SELECT RAISE(ABORT, 'runs cannot be deleted');
END;

-- Allow only restricted updates: any change to immutable fields aborts.
CREATE TRIGGER IF NOT EXISTS trg_runs_restricted_update
BEFORE UPDATE ON runs
BEGIN
  -- immutable fields must not change
  SELECT
    CASE
      WHEN NEW.run_id          != OLD.run_id          THEN RAISE(ABORT, 'runs: run_id immutable')
      WHEN NEW.started_at      != OLD.started_at      THEN RAISE(ABORT, 'runs: started_at immutable')
      WHEN NEW.profile         != OLD.profile         THEN RAISE(ABORT, 'runs: profile immutable')
      WHEN NEW.targets_json    != OLD.targets_json    THEN RAISE(ABORT, 'runs: targets_json immutable')
      WHEN NEW.tags_json       != OLD.tags_json       THEN RAISE(ABORT, 'runs: tags_json immutable')
      WHEN IFNULL(NEW.notes,'')!= IFNULL(OLD.notes,'')THEN RAISE(ABORT, 'runs: notes immutable')
      WHEN NEW.env_fingerprint != OLD.env_fingerprint THEN RAISE(ABORT, 'runs: env_fingerprint immutable')
      WHEN NEW.run_seed_hash   != OLD.run_seed_hash   THEN RAISE(ABORT, 'runs: run_seed_hash immutable')
      ELSE 0
    END;

  -- sealing fields are allowed to change (finished_at/status/warnings_count/errors_count/evidence_count/final_chain_hash)
END;