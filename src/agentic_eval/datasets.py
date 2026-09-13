from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agentic_eval.domain import DatasetSpec, QuestionCase


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional(record: dict[str, Any], field: str | None, default: Any = None) -> Any:
    return record.get(field, default) if field else default


def agent_visible_fields(spec: DatasetSpec) -> list[str]:
    fields = [spec.id_field, spec.question_field]
    for field in (spec.tags_field, spec.timeout_field):
        if field:
            fields.append(field)
    return fields


def agent_visible_record(record: dict[str, Any], spec: DatasetSpec) -> dict[str, Any]:
    return {
        field: record[field]
        for field in agent_visible_fields(spec)
        if field in record
    }


_SCORING_KEY_ALIASES = {
    "key_answer": ("key_answer", "answer_keywords"),
    "key_middle": ("key_middle", "middle_keywords"),
}


def scoring_metadata(record: dict[str, Any], metadata_field: str | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if metadata_field:
        value = record.get(metadata_field, {})
        if isinstance(value, dict):
            metadata.update(value)
    for dest, sources in _SCORING_KEY_ALIASES.items():
        for source in sources:
            if source in record:
                metadata[dest] = record[source]
                break
    return metadata


def load_cases(spec: DatasetSpec) -> list[QuestionCase]:
    cases: list[QuestionCase] = []
    seen: set[str] = set()
    with spec.path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                case = QuestionCase(
                    id=str(record[spec.id_field]),
                    question=str(record[spec.question_field]),
                    reference_answer=_optional(record, spec.reference_answer_field),
                    tags=_optional(record, spec.tags_field, []) or [],
                    metadata=_optional(record, spec.metadata_field, {}) or {},
                    timeout_seconds=_optional(record, spec.timeout_field),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid JSONL record at {spec.path}:{line_number}: {exc}") from exc
            if case.id in seen:
                raise ValueError(f"Duplicate case id {case.id!r} at {spec.path}:{line_number}")
            seen.add(case.id)
            cases.append(case)
    return cases


def select_cases(
    cases: Iterable[QuestionCase],
    *,
    case_ids: set[str],
    tags: set[str],
    shard_index: int,
    shard_count: int,
) -> list[QuestionCase]:
    if shard_index >= shard_count:
        raise ValueError("shard_index must be smaller than shard_count")
    selected = []
    for case in cases:
        if case_ids and case.id not in case_ids:
            continue
        if tags and not tags.intersection(case.tags):
            continue
        stable_bucket = int(hashlib.sha256(case.id.encode()).hexdigest(), 16) % shard_count
        if stable_bucket == shard_index:
            selected.append(case)
    return selected
