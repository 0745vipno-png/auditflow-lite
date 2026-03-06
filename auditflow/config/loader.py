# auditflow/config/loader.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import yaml

from auditflow.collectors.base import CollectorSpec


class ProfileLoadError(RuntimeError):
    pass


def load_profile_file(profile_path: str | Path) -> dict[str, Any]:
    """
    Load a profile file from YAML or JSON.

    Supported:
    - .yaml / .yml
    - .json
    """
    path = Path(profile_path)
    if not path.exists():
        raise ProfileLoadError(f"Profile file not found: {path}")
    if not path.is_file():
        raise ProfileLoadError(f"Profile path is not a file: {path}")

    suffix = path.suffix.lower()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ProfileLoadError(f"Failed to read profile file {path}: {e}") from e

    try:
        if suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(text)
        elif suffix == ".json":
            data = json.loads(text)
        else:
            raise ProfileLoadError(f"Unsupported profile format: {path.suffix}")
    except Exception as e:
        raise ProfileLoadError(f"Failed to parse profile file {path}: {e}") from e

    if not isinstance(data, dict):
        raise ProfileLoadError("Profile root must be an object/dict")

    return data


def resolve_profile_path(
    profile_name_or_path: str,
    *,
    profiles_dir: Optional[str | Path] = None,
) -> Path:
    """
    Resolve a profile by either:
    - direct file path
    - profile name under profiles_dir (tries .yaml, .yml, .json)

    Examples:
      resolve_profile_path("windows_basic", profiles_dir="profiles")
      resolve_profile_path("profiles/windows_basic.yaml")
    """
    raw = Path(profile_name_or_path)

    # direct path
    if raw.exists():
        return raw.resolve()

    if profiles_dir is None:
        raise ProfileLoadError(
            f"Profile not found as direct path and no profiles_dir provided: {profile_name_or_path}"
        )

    base = Path(profiles_dir)
    candidates = [
        base / f"{profile_name_or_path}.yaml",
        base / f"{profile_name_or_path}.yml",
        base / f"{profile_name_or_path}.json",
    ]

    for c in candidates:
        if c.exists() and c.is_file():
            return c.resolve()

    raise ProfileLoadError(f"Profile not found: {profile_name_or_path}")


def substitute_variables(
    obj: Any,
    *,
    variables: Mapping[str, Any],
) -> Any:
    """
    Recursively substitute template variables in profile content.

    Supported patterns:
    - exact token replacement:
        "{{TARGET}}"  -> first target string
        "{{TARGETS}}" -> list[str]
    - string replacement inside larger strings:
        "prefix-{{TARGET}}-suffix"

    Important behavior:
    - if the whole value is exactly "{{TARGETS}}", and variables["TARGETS"] is a list,
      return the list object directly (not a string)
    - for partial string interpolation, non-string variables are JSON-stringified
    """
    if isinstance(obj, dict):
        return {k: substitute_variables(v, variables=variables) for k, v in obj.items()}

    if isinstance(obj, list):
        return [substitute_variables(v, variables=variables) for v in obj]

    if isinstance(obj, str):
        # exact token replacement first
        exact = _match_exact_token(obj)
        if exact is not None:
            if exact not in variables:
                raise ProfileLoadError(f"Undefined profile variable: {exact}")
            return variables[exact]

        # partial string replacement
        result = obj
        for key, value in variables.items():
            token = "{{" + key + "}}"
            if token in result:
                if isinstance(value, str):
                    replacement = value
                else:
                    replacement = json.dumps(value, ensure_ascii=False)
                result = result.replace(token, replacement)
        return result

    return obj


def _match_exact_token(value: str) -> Optional[str]:
    """
    Return token name if value is exactly like "{{NAME}}", otherwise None.
    """
    stripped = value.strip()
    if stripped.startswith("{{") and stripped.endswith("}}"):
        inner = stripped[2:-2].strip()
        if inner:
            return inner
    return None


def validate_profile_shape(profile: Mapping[str, Any]) -> None:
    """
    Lightweight profile shape validation for v0.1.

    Expected shape:
      {
        "name": "windows_basic",
        "collectors": [
          {"type": "filesystem_snapshot", "options": {...}},
          ...
        ],
        "report": {...}   # optional
      }
    """
    if "name" not in profile:
        raise ProfileLoadError("Profile missing required field: name")
    if not isinstance(profile["name"], str) or not profile["name"].strip():
        raise ProfileLoadError("Profile field 'name' must be a non-empty string")

    if "collectors" not in profile:
        raise ProfileLoadError("Profile missing required field: collectors")
    if not isinstance(profile["collectors"], list) or not profile["collectors"]:
        raise ProfileLoadError("Profile field 'collectors' must be a non-empty list")

    for i, c in enumerate(profile["collectors"]):
        if not isinstance(c, dict):
            raise ProfileLoadError(f"collector[{i}] must be an object/dict")
        if "type" not in c:
            raise ProfileLoadError(f"collector[{i}] missing required field: type")
        if not isinstance(c["type"], str) or not c["type"].strip():
            raise ProfileLoadError(f"collector[{i}].type must be a non-empty string")
        if "options" in c and not isinstance(c["options"], dict):
            raise ProfileLoadError(f"collector[{i}].options must be an object/dict if present")

    if "report" in profile and not isinstance(profile["report"], dict):
        raise ProfileLoadError("Profile field 'report' must be an object/dict if present")


def build_profile_variables(
    *,
    targets: Sequence[str],
) -> dict[str, Any]:
    """
    Build supported substitution variables for profile templates.

    Variables:
    - TARGET   : first target string (only valid when exactly one target exists)
    - TARGETS  : list[str]
    """
    target_list = [str(t) for t in targets]

    variables: dict[str, Any] = {
        "TARGETS": target_list,
    }

    if len(target_list) == 1:
        variables["TARGET"] = target_list[0]

    return variables


def load_and_resolve_profile(
    profile_name_or_path: str,
    *,
    targets: Sequence[str],
    profiles_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    """
    Resolve file path -> load -> validate -> substitute variables -> validate again.

    Returns resolved profile dict.
    """
    profile_path = resolve_profile_path(profile_name_or_path, profiles_dir=profiles_dir)
    raw_profile = load_profile_file(profile_path)
    validate_profile_shape(raw_profile)

    variables = build_profile_variables(targets=targets)
    resolved = substitute_variables(raw_profile, variables=variables)

    if not isinstance(resolved, dict):
        raise ProfileLoadError("Resolved profile must remain an object/dict")

    validate_profile_shape(resolved)
    return resolved


def build_collector_specs(profile: Mapping[str, Any]) -> List[CollectorSpec]:
    """
    Convert resolved profile dict -> CollectorSpec list.
    """
    validate_profile_shape(profile)

    out: List[CollectorSpec] = []
    for c in profile["collectors"]:
        out.append(
            CollectorSpec(
                type=c["type"],
                options=dict(c.get("options", {})),
            )
        )
    return out


def get_report_config(profile: Mapping[str, Any]) -> dict[str, Any]:
    """
    Return report config dict (empty dict if absent).
    """
    report = profile.get("report", {})
    if not isinstance(report, dict):
        raise ProfileLoadError("Resolved report config must be a dict")
    return dict(report)