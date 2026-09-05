from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(without_marks.split())


def _keywords(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key, [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _matched(keywords: list[str], text: str) -> list[str]:
    normalized = normalize_text(text)
    return [keyword for keyword in keywords if normalize_text(keyword) in normalized]


def _trace_text(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    values: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            values.append(json.dumps(event.get("payload", {}), ensure_ascii=False))
    return "\n".join(values)


def keyword_scores(
    final_answer: str | None,
    metadata: dict[str, Any],
    *,
    trace_path: Path | None = None,
) -> dict[str, Any]:
    answer_keys = _keywords(metadata, "key_answer")
    middle_keys = _keywords(metadata, "key_middle")
    matched_answer = _matched(answer_keys, final_answer or "")
    trajectory = f"{_trace_text(trace_path)}\n{final_answer or ''}"
    all_keys = answer_keys + middle_keys
    matched_all = _matched(all_keys, trajectory)
    return {
        "success_rate": len(matched_answer) / len(answer_keys) if answer_keys else None,
        "progress_rate": len(matched_all) / len(all_keys) if all_keys else None,
        "matched_key_answer": matched_answer,
        "matched_key_middle": [
            keyword for keyword in middle_keys if keyword in matched_all
        ],
    }
