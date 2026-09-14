from __future__ import annotations

import json
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(without_marks.split())


def _keyword_text(item: Any) -> str:
    if isinstance(item, dict):
        item = item.get("keyword", "")
    return str(item).strip()


def _keywords(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key, [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _keyword_text(item))]


def _middle_items(metadata: dict[str, Any]) -> list[Any]:
    value = metadata.get("key_middle", [])
    if isinstance(value, str):
        return [value]
    return value if isinstance(value, list) else []


def _unique_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        key = normalize_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def _retriever_anchors(metadata: dict[str, Any]) -> list[str]:
    anchors: list[str] = []
    for item in _middle_items(metadata):
        if isinstance(item, dict) and item.get("in_citation_path"):
            text = _keyword_text(item)
            if text:
                anchors.append(text)
    citations = metadata.get("citations", [])
    if isinstance(citations, list):
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            blob_path = citation.get("blob_path")
            if not blob_path:
                continue
            stem = PurePosixPath(str(blob_path)).stem.strip()
            if stem:
                anchors.append(stem)
    return _unique_preserve(anchors)


def _tool_keywords(metadata: dict[str, Any]) -> list[str]:
    keywords: list[str] = []
    for item in _middle_items(metadata):
        if isinstance(item, dict) and item.get("in_citation_path"):
            continue
        text = _keyword_text(item)
        if text:
            keywords.append(text)
    return _unique_preserve(keywords)


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


def _tool_part(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        return {}
    part = (payload.get("properties") or {}).get("part") or {}
    return part if isinstance(part, dict) and part.get("type") == "tool" else {}


def _is_tool_event(event: dict[str, Any]) -> bool:
    kind = str(event.get("kind", ""))
    if "tool" in kind:
        return True
    return bool(_tool_part(event))


def _tool_trace_text(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    values: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or not _is_tool_event(event):
                continue
            part = _tool_part(event)
            if part:
                state = part.get("state") or {}
                chunks: list[str] = []
                if isinstance(state, dict):
                    for key in ("output", "error", "input"):
                        value = state.get(key)
                        if value is None:
                            continue
                        if isinstance(value, str):
                            chunks.append(value)
                        else:
                            chunks.append(json.dumps(value, ensure_ascii=False))
                chunks.append(json.dumps(part, ensure_ascii=False))
                values.append("\n".join(chunks))
            else:
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

    tool_text = _tool_trace_text(trace_path)
    retriever_anchors = _retriever_anchors(metadata)
    matched_retriever = _matched(retriever_anchors, tool_text)
    tool_keys = _tool_keywords(metadata)
    matched_tool = _matched(tool_keys, tool_text)

    return {
        "success_rate": len(matched_answer) / len(answer_keys) if answer_keys else None,
        "progress_rate": len(matched_all) / len(all_keys) if all_keys else None,
        "matched_key_answer": matched_answer,
        "matched_key_middle": [
            keyword for keyword in middle_keys if keyword in matched_all
        ],
        "retriever_score": (
            len(matched_retriever) / len(retriever_anchors) if retriever_anchors else None
        ),
        "tool_score": len(matched_tool) / len(tool_keys) if tool_keys else None,
        "matched_retriever_anchors": matched_retriever,
        "matched_tool_keywords": matched_tool,
    }
