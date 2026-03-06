# auditflow/cli.py
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from auditflow.core.runner import run_auditflow
from auditflow.store.chain import replay_verify
from auditflow.store.db import init_db, open_db
from auditflow.store.runs import get_run, list_runs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auditflow",
        description="AuditFlow Lite - one-shot, read-only evidence collection and reporting",
    )
    parser.add_argument(
        "--home",
        dest="auditflow_home",
        default=None,
        help="Override AUDITFLOW_HOME runtime directory",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Initialize AUDITFLOW_HOME and SQLite DB")
    p_init.set_defaults(func=cmd_init)

    # run
    p_run = subparsers.add_parser("run", help="Execute one AuditFlow run")
    p_run.add_argument("--profile", required=True, help="Profile name or profile file path")
    p_run.add_argument(
        "--target",
        dest="targets",
        action="append",
        required=True,
        help="Target path (may be specified multiple times)",
    )
    p_run.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=[],
        help='Tag in k=v format (may be repeated), e.g. --tag project=X',
    )
    p_run.add_argument(
        "--notes",
        default=None,
        help="Optional human note for this run",
    )
    p_run.set_defaults(func=cmd_run)

    # verify
    p_verify = subparsers.add_parser("verify", help="Verify chain integrity for a run")
    p_verify.add_argument("run_id", help="Run ID to verify")
    p_verify.set_defaults(func=cmd_verify)

    # list-runs
    p_list = subparsers.add_parser("list-runs", help="List recent runs")
    p_list.add_argument("--limit", type=int, default=20, help="Max rows to return")
    p_list.add_argument("--status", default=None, help="Filter by status")
    p_list.add_argument("--profile", default=None, help="Filter by profile")
    p_list.add_argument("--since", dest="since_iso", default=None, help="Filter started_at >= ISO8601")
    p_list.set_defaults(func=cmd_list_runs)

    # show-run
    p_show = subparsers.add_parser("show-run", help="Show one run")
    p_show.add_argument("run_id", help="Run ID to show")
    p_show.set_defaults(func=cmd_show_run)

    return parser


def parse_tags(tag_args: list[str]) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    for raw in tag_args:
        if "=" not in raw:
            raise ValueError(f"Invalid --tag format: {raw!r} (expected k=v)")
        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            raise ValueError(f"Invalid --tag key in: {raw!r}")
        tags[k] = v
    return tags


def cmd_init(args: argparse.Namespace) -> int:
    paths = init_db(args.auditflow_home)

    print("[OK] AuditFlow initialized")
    print(f"home:      {paths.home}")
    print(f"db:        {paths.db_path}")
    print(f"artifacts: {paths.artifacts_dir}")
    print(f"logs:      {paths.logs_dir}")
    print(f"profiles:  {paths.profiles_dir}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    try:
        tags = parse_tags(args.tags)
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    try:
        result = run_auditflow(
            profile_name_or_path=args.profile,
            targets=args.targets,
            tags=tags,
            notes=args.notes,
            auditflow_home=args.auditflow_home,
        )
    except Exception as e:
        print(f"[ERROR] run failed: {e}", file=sys.stderr)
        return 1

    print(f"[INFO] Run ID: {result.run_id}")
    print(f"[INFO] Status: {result.status}")
    print(f"[INFO] Report: {result.report_path}")
    print(f"[INFO] Evidence Count: {result.evidence_count}")
    print(f"[INFO] Warnings: {result.warnings_count}")
    print(f"[INFO] Errors: {result.errors_count}")
    print(f"[INFO] Final Chain Hash: {result.final_chain_hash}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        _, conn = open_db(args.auditflow_home)
        try:
            result = replay_verify(conn, args.run_id)
        finally:
            conn.close()
    except Exception as e:
        print(f"[ERROR] verify failed: {e}", file=sys.stderr)
        return 1

    if result.ok:
        print("[PASS] Chain integrity verified")
        print(f"run_id: {result.run_id}")
        print(f"checked_chain_rows: {result.checked_chain_rows}")
        print(f"evidence_count: {result.actual_evidence_count}")
        print(f"final_chain_hash: {result.actual_final_chain_hash}")
        return 0

    print("[FAIL] Verification failed", file=sys.stderr)
    print(f"run_id: {result.run_id}", file=sys.stderr)
    print(f"reason: {result.failure_reason}", file=sys.stderr)
    if result.mismatch_seq is not None:
        print(f"mismatch_seq: {result.mismatch_seq}", file=sys.stderr)
    if result.mismatch_evidence_id is not None:
        print(f"mismatch_evidence_id: {result.mismatch_evidence_id}", file=sys.stderr)
    print(f"expected_final_chain_hash: {result.expected_final_chain_hash}", file=sys.stderr)
    print(f"actual_final_chain_hash: {result.actual_final_chain_hash}", file=sys.stderr)
    print(f"expected_evidence_count: {result.expected_evidence_count}", file=sys.stderr)
    print(f"actual_evidence_count: {result.actual_evidence_count}", file=sys.stderr)
    return 3


def cmd_list_runs(args: argparse.Namespace) -> int:
    try:
        _, conn = open_db(args.auditflow_home)
        try:
            rows = list_runs(
                conn,
                limit=args.limit,
                status=args.status,
                profile=args.profile,
                since_iso=args.since_iso,
            )
        finally:
            conn.close()
    except Exception as e:
        print(f"[ERROR] list-runs failed: {e}", file=sys.stderr)
        return 1

    if not rows:
        print("(no runs)")
        return 0

    print("started_at | status | profile | run_id")
    print("-" * 100)
    for row in rows:
        print(f"{row.started_at} | {row.status} | {row.profile} | {row.run_id}")
    return 0


def cmd_show_run(args: argparse.Namespace) -> int:
    try:
        _, conn = open_db(args.auditflow_home)
        try:
            row = get_run(conn, args.run_id)
        finally:
            conn.close()
    except Exception as e:
        print(f"[ERROR] show-run failed: {e}", file=sys.stderr)
        return 1

    if row is None:
        print(f"[ERROR] run not found: {args.run_id}", file=sys.stderr)
        return 2

    print(f"run_id: {row.run_id}")
    print(f"status: {row.status}")
    print(f"started_at: {row.started_at}")
    print(f"finished_at: {row.finished_at}")
    print(f"profile: {row.profile}")
    print(f"targets_json: {row.targets_json}")
    print(f"tags_json: {row.tags_json}")
    print(f"notes: {row.notes}")
    print(f"env_fingerprint: {row.env_fingerprint}")
    print(f"run_seed_hash: {row.run_seed_hash}")
    print(f"warnings_count: {row.warnings_count}")
    print(f"errors_count: {row.errors_count}")
    print(f"evidence_count: {row.evidence_count}")
    print(f"final_chain_hash: {row.final_chain_hash}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())