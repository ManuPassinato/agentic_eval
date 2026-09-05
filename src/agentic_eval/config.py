from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from agentic_eval.domain import RunSpec
from agentic_eval.sources import load_source_profile

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_SECRET_KEYS = {"api_key", "password", "token", "secret", "authorization"}


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ValueError(f"Environment variable {name} is required")

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def load_run_spec(path: Path) -> RunSpec:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    spec = RunSpec.model_validate(_expand_env(raw))
    if not spec.dataset.path.is_absolute():
        spec.dataset.path = (path.parent / spec.dataset.path).resolve()
    if not spec.output_dir.is_absolute():
        spec.output_dir = (path.parent / spec.output_dir).resolve()
    if spec.environment.database_path and not spec.environment.database_path.is_absolute():
        spec.environment.database_path = (
            path.parent / spec.environment.database_path
        ).resolve()
    if spec.environment.manifest_path and not spec.environment.manifest_path.is_absolute():
        spec.environment.manifest_path = (
            path.parent / spec.environment.manifest_path
        ).resolve()
    if spec.task.source_profile_path:
        if not spec.task.source_profile_path.is_absolute():
            spec.task.source_profile_path = (
                path.parent / spec.task.source_profile_path
            ).resolve()
        spec.task.source_profile = load_source_profile(spec.task.source_profile_path)
    return spec


def redact(value: Any) -> Any:
    if hasattr(value, "get_secret_value"):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {
            key: ("***REDACTED***" if key.lower() in _SECRET_KEYS else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
