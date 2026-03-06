# auditflow/collectors/filesystem_snapshot.py
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from auditflow.collectors.base import BaseCollector, CollectorError, EvidenceRecord, RunContext
from auditflow.core.canonical import canonical_json_dumps, stable_sort_records
from auditflow.util.hashing import sha256_file


class FilesystemSnapshotCollector(BaseCollector):
    """
    Read-only filesystem snapshot collector.

    Output strategy (v0.1):
    - One summary EvidenceRecord per run:
        kind = "filesystem_summary"
    - One attachment file (JSONL):
        artifacts/<run_id>/filesystem_snapshot/entries.jsonl

    Supported options:
    - targets: list[str]               (required; usually injected from profile)
    - include_hidden: bool             (default False)
    - hash_files: bool                 (default False)
    - exclude_globs: list[str]         (default [])
    - max_files: int | None            (default None)
    - follow_symlinks: bool            (default False)
    """
    name = "filesystem_snapshot"

    def collect(self, context: RunContext) -> Iterable[EvidenceRecord]:
        targets = self.option(context, "targets")
        if not targets:
            raise CollectorError(f"[{self.name}] missing or empty 'targets' option")

        include_hidden = bool(self.option(context, "include_hidden", False))
        hash_files = bool(self.option(context, "hash_files", False))
        exclude_globs = list(self.option(context, "exclude_globs", []))
        max_files = self.option(context, "max_files", None)
        follow_symlinks = bool(self.option(context, "follow_symlinks", False))

        if max_files is not None:
            try:
                max_files = int(max_files)
                if max_files <= 0:
                    raise ValueError("max_files must be > 0")
            except Exception as e:
                raise CollectorError(f"[{self.name}] invalid max_files: {max_files}") from e

        out_dir = self.ensure_artifacts_dir(context)
        entries_path = out_dir / "entries.jsonl"

        all_entries: List[Dict[str, Any]] = []

        scanned_targets = 0
        file_count = 0
        dir_count = 0
        skipped_hidden_count = 0
        skipped_excluded_count = 0
        skipped_error_count = 0
        truncated = False

        for raw_target in targets:
            target = Path(raw_target)
            if not target.exists():
                raise CollectorError(f"[{self.name}] target does not exist: {target}")

            scanned_targets += 1

            if target.is_file():
                entry = self._build_entry(
                    path=target,
                    root=target.parent,
                    hash_files=hash_files,
                )
                if self._should_skip(
                    entry_path=entry["path"],
                    rel_path=entry["path_rel"],
                    name=target.name,
                    include_hidden=include_hidden,
                    exclude_globs=exclude_globs,
                ):
                    if self._is_hidden_name(target.name) and not include_hidden:
                        skipped_hidden_count += 1
                    else:
                        skipped_excluded_count += 1
                else:
                    all_entries.append(entry)
                    file_count += 1

                    if max_files is not None and file_count >= max_files:
                        truncated = True
                        break
                continue

            if target.is_dir():
                try:
                    for entry in self._walk_target(
                        target=target,
                        include_hidden=include_hidden,
                        hash_files=hash_files,
                        exclude_globs=exclude_globs,
                        follow_symlinks=follow_symlinks,
                    ):
                        if entry["__skip_reason"] == "hidden":
                            skipped_hidden_count += 1
                            continue
                        if entry["__skip_reason"] == "excluded":
                            skipped_excluded_count += 1
                            continue
                        if entry["__skip_reason"] == "error":
                            skipped_error_count += 1
                            continue

                        entry.pop("__skip_reason", None)
                        all_entries.append(entry)

                        if entry["type"] == "file":
                            file_count += 1
                            if max_files is not None and file_count >= max_files:
                                truncated = True
                                break
                        elif entry["type"] == "dir":
                            dir_count += 1

                    if truncated:
                        break

                except OSError as e:
                    raise CollectorError(f"[{self.name}] failed to walk target {target}: {e}") from e
                continue

            raise CollectorError(f"[{self.name}] unsupported target type: {target}")

        # Deterministic ordering
        all_entries = stable_sort_records(all_entries, key="path_norm")

        # Write JSONL attachment
        try:
            with entries_path.open("w", encoding="utf-8", newline="\n") as f:
                for row in all_entries:
                    f.write(canonical_json_dumps(row))
                    f.write("\n")
        except OSError as e:
            raise CollectorError(f"[{self.name}] failed to write attachment {entries_path}: {e}") from e

        summary_payload = {
            "collector": self.name,
            "targets": [str(Path(t)) for t in targets],
            "include_hidden": include_hidden,
            "hash_files": hash_files,
            "exclude_globs": exclude_globs,
            "follow_symlinks": follow_symlinks,
            "max_files": max_files,
            "scanned_targets": scanned_targets,
            "file_count": file_count,
            "dir_count": dir_count,
            "entry_count": len(all_entries),
            "skipped_hidden_count": skipped_hidden_count,
            "skipped_excluded_count": skipped_excluded_count,
            "skipped_error_count": skipped_error_count,
            "truncated": truncated,
            "attachment_format": "jsonl",
            "attachment_filename": entries_path.name,
        }

        ts = context.env_info.get("now_iso")
        if not isinstance(ts, str) or not ts:
            raise CollectorError(f"[{self.name}] context.env_info['now_iso'] missing")

        yield self.build_evidence(
            kind="filesystem_summary",
            ts=ts,
            payload=summary_payload,
            attachment_path=entries_path,
        )

    def _walk_target(
        self,
        *,
        target: Path,
        include_hidden: bool,
        hash_files: bool,
        exclude_globs: List[str],
        follow_symlinks: bool,
    ) -> Iterator[Dict[str, Any]]:
        """
        Walk one directory target and yield raw entry dicts.

        Special internal field:
        - __skip_reason: None | "hidden" | "excluded" | "error"
        """
        # include the root directory itself as a directory entry
        root_entry = self._build_entry(path=target, root=target.parent, hash_files=False)
        root_skip = self._classify_skip(
            entry_path=root_entry["path"],
            rel_path=root_entry["path_rel"],
            name=target.name,
            include_hidden=include_hidden,
            exclude_globs=exclude_globs,
        )
        root_entry["__skip_reason"] = root_skip
        yield root_entry

        for dirpath, dirnames, filenames in os.walk(str(target), topdown=True, followlinks=follow_symlinks):
            current_dir = Path(dirpath)

            # Filter dirnames in-place so os.walk won't descend into skipped dirs
            kept_dirnames: List[str] = []
            for dirname in sorted(dirnames):
                child = current_dir / dirname
                rel_path = self._safe_rel_path(child, target.parent)

                skip_reason = self._classify_skip(
                    entry_path=str(child),
                    rel_path=rel_path,
                    name=dirname,
                    include_hidden=include_hidden,
                    exclude_globs=exclude_globs,
                )

                if skip_reason is not None:
                    skipped_entry = {
                        "path": str(child),
                        "path_norm": self._norm_path(child),
                        "path_rel": rel_path,
                        "type": "dir",
                        "size": 0,
                        "mtime_epoch": None,
                        "mtime_iso": None,
                        "sha256": None,
                        "is_symlink": child.is_symlink(),
                        "__skip_reason": skip_reason,
                    }
                    yield skipped_entry
                    continue

                kept_dirnames.append(dirname)

                try:
                    dir_entry = self._build_entry(path=child, root=target.parent, hash_files=False)
                    dir_entry["__skip_reason"] = None
                    yield dir_entry
                except OSError:
                    yield {
                        "path": str(child),
                        "path_norm": self._norm_path(child),
                        "path_rel": rel_path,
                        "type": "dir",
                        "size": 0,
                        "mtime_epoch": None,
                        "mtime_iso": None,
                        "sha256": None,
                        "is_symlink": child.is_symlink(),
                        "__skip_reason": "error",
                    }

            dirnames[:] = sorted(kept_dirnames)

            for filename in sorted(filenames):
                child = current_dir / filename
                rel_path = self._safe_rel_path(child, target.parent)

                skip_reason = self._classify_skip(
                    entry_path=str(child),
                    rel_path=rel_path,
                    name=filename,
                    include_hidden=include_hidden,
                    exclude_globs=exclude_globs,
                )
                if skip_reason is not None:
                    yield {
                        "path": str(child),
                        "path_norm": self._norm_path(child),
                        "path_rel": rel_path,
                        "type": "file",
                        "size": None,
                        "mtime_epoch": None,
                        "mtime_iso": None,
                        "sha256": None,
                        "is_symlink": child.is_symlink(),
                        "__skip_reason": skip_reason,
                    }
                    continue

                try:
                    file_entry = self._build_entry(path=child, root=target.parent, hash_files=hash_files)
                    file_entry["__skip_reason"] = None
                    yield file_entry
                except OSError:
                    yield {
                        "path": str(child),
                        "path_norm": self._norm_path(child),
                        "path_rel": rel_path,
                        "type": "file",
                        "size": None,
                        "mtime_epoch": None,
                        "mtime_iso": None,
                        "sha256": None,
                        "is_symlink": child.is_symlink(),
                        "__skip_reason": "error",
                    }

    def _build_entry(self, *, path: Path, root: Path, hash_files: bool) -> Dict[str, Any]:
        stat_result = path.stat()

        entry_type = "dir" if path.is_dir() else "file"
        size = 0 if entry_type == "dir" else int(stat_result.st_size)
        mtime_epoch = int(stat_result.st_mtime)
        mtime_iso = self._epoch_to_iso(mtime_epoch)

        sha256_value: Optional[str] = None
        if entry_type == "file" and hash_files:
            sha256_value = sha256_file(path)

        return {
            "path": str(path),
            "path_norm": self._norm_path(path),
            "path_rel": self._safe_rel_path(path, root),
            "type": entry_type,
            "size": size,
            "mtime_epoch": mtime_epoch,
            "mtime_iso": mtime_iso,
            "sha256": sha256_value,
            "is_symlink": path.is_symlink(),
        }

    def _should_skip(
        self,
        *,
        entry_path: str,
        rel_path: str,
        name: str,
        include_hidden: bool,
        exclude_globs: List[str],
    ) -> bool:
        return self._classify_skip(
            entry_path=entry_path,
            rel_path=rel_path,
            name=name,
            include_hidden=include_hidden,
            exclude_globs=exclude_globs,
        ) is not None

    def _classify_skip(
        self,
        *,
        entry_path: str,
        rel_path: str,
        name: str,
        include_hidden: bool,
        exclude_globs: List[str],
    ) -> Optional[str]:
        if not include_hidden and self._is_hidden_name(name):
            return "hidden"

        normalized_full = self._slash_norm(entry_path)
        normalized_rel = self._slash_norm(rel_path)

        for pattern in exclude_globs:
            p = self._slash_norm(pattern)
            if fnmatch.fnmatch(normalized_full, p) or fnmatch.fnmatch(normalized_rel, p):
                return "excluded"

        return None

    @staticmethod
    def _is_hidden_name(name: str) -> bool:
        """
        MVP hidden rule:
        - dotfiles / dotdirs count as hidden
        - Windows hidden attribute is not checked in v0.1
        """
        return name.startswith(".")

    @staticmethod
    def _norm_path(path: Path) -> str:
        """
        Deterministic path representation for sorting/diff.
        Current rule:
        - absolute path
        - normalized separators
        - lowercase on Windows-style environments to reduce case noise
        """
        p = str(path.resolve())
        p = p.replace("\\", "/")
        return p.lower()

    @staticmethod
    def _slash_norm(value: str) -> str:
        return value.replace("\\", "/")

    @staticmethod
    def _safe_rel_path(path: Path, root: Path) -> str:
        try:
            rel = path.resolve().relative_to(root.resolve())
            return str(rel).replace("\\", "/")
        except Exception:
            return str(path).replace("\\", "/")

    @staticmethod
    def _epoch_to_iso(epoch_seconds: int) -> str:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()