from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from agentic_eval.datasets import scoring_metadata
from agentic_eval.runner.ledger import RunLedger
from agentic_eval.scoring import keyword_scores


def _mean(records: list[dict[str, object]], key: str) -> float | None:
    values = [value for record in records if isinstance((value := record.get(key)), (int, float))]
    return sum(values) / len(values) if values else None


def _metadata_dataset_path(run_dir: Path, manifest: dict[str, object]) -> Path | None:
    source = manifest.get("dataset_source")
    if isinstance(source, str) and source:
        original = Path(source)
        if original.is_file():
            return original
    snapshot = run_dir / "dataset.jsonl"
    return snapshot if snapshot.is_file() else None


def _case_metadata(run_dir: Path) -> dict[str, dict[str, object]]:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_path = _metadata_dataset_path(run_dir, manifest)
    if dataset_path is None:
        return {}
    dataset_spec = (manifest.get("resolved_config") or {}).get("dataset") or {}
    id_field = dataset_spec.get("id_field", "id")
    metadata_field = dataset_spec.get("metadata_field", "metadata")
    cases: dict[str, dict[str, object]] = {}
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            cases[str(record[id_field])] = scoring_metadata(record, metadata_field)
    return cases


def export_run(run_dir: Path) -> tuple[Path, Path]:
    ledger = RunLedger(run_dir / "ledger.sqlite3")
    try:
        rows = ledger.final_rows()
    finally:
        ledger.close()

    metadata_by_case = _case_metadata(run_dir)
    records: list[dict[str, object]] = []
    for row in rows:
        result: dict[str, object] = {}
        result_path = row["result_path"]
        resolved_result_path = run_dir / result_path if result_path else None
        if resolved_result_path and resolved_result_path.exists():
            result = json.loads(resolved_result_path.read_text(encoding="utf-8"))
        source_matches = result.get("source_matches", []) or []
        preferred_matches = [
            match for match in source_matches if isinstance(match, dict) and match.get("preferred")
        ]
        trace_path = run_dir / row["trace_path"] if row["trace_path"] else None
        scores = keyword_scores(
            result.get("final_answer") if isinstance(result.get("final_answer"), str) else None,
            metadata_by_case.get(str(row["case_id"]), {}),
            trace_path=trace_path,
        )
        records.append(
            {
                "case_id": row["case_id"],
                "question": row["question"],
                "status": row["status"],
                "attempt": row["final_attempt"],
                "failure_kind": result.get("failure_kind"),
                "final_answer": result.get("final_answer"),
                "sources": result.get("sources", []),
                "source_matches": source_matches,
                "preferred_source_count": len(preferred_matches),
                "preferred_domains": sorted(
                    {
                        match["matched_domain"]
                        for match in preferred_matches
                        if match.get("matched_domain")
                    }
                ),
                **scores,
                "duration_seconds": row["duration_seconds"],
                "worker_id": row["worker_id"],
                "error": row["error"],
                "result_path": result_path,
                "trace_path": row["trace_path"],
            }
        )

    export_dir = run_dir / "exports"
    export_dir.mkdir(exist_ok=True)
    jsonl_path = export_dir / "results.jsonl"
    jsonl_tmp = jsonl_path.with_suffix(".jsonl.tmp")
    with jsonl_tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(jsonl_tmp, jsonl_path)

    csv_path = export_dir / "results.csv"
    csv_tmp = csv_path.with_suffix(".csv.tmp")
    fields = list(records[0]) if records else [
        "case_id",
        "question",
        "status",
        "attempt",
        "failure_kind",
        "final_answer",
        "sources",
        "source_matches",
        "preferred_source_count",
        "preferred_domains",
        "success_rate",
        "progress_rate",
        "matched_key_answer",
        "matched_key_middle",
        "retriever_score",
        "tool_score",
        "matched_retriever_anchors",
        "matched_tool_keywords",
        "duration_seconds",
        "worker_id",
        "error",
        "result_path",
        "trace_path",
    ]
    with csv_tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    **record,
                    "sources": json.dumps(record["sources"], ensure_ascii=False),
                    "source_matches": json.dumps(
                        record["source_matches"], ensure_ascii=False
                    ),
                    "preferred_domains": json.dumps(
                        record["preferred_domains"], ensure_ascii=False
                    ),
                    "matched_key_answer": json.dumps(
                        record["matched_key_answer"], ensure_ascii=False
                    ),
                    "matched_key_middle": json.dumps(
                        record["matched_key_middle"], ensure_ascii=False
                    ),
                    "matched_retriever_anchors": json.dumps(
                        record["matched_retriever_anchors"], ensure_ascii=False
                    ),
                    "matched_tool_keywords": json.dumps(
                        record["matched_tool_keywords"], ensure_ascii=False
                    ),
                }
            )
    os.replace(csv_tmp, csv_path)

    summary = {
        "n_cases": len(records),
        # Table 4.2: SR column is the with-tools success_rate.
        "success_rate_mean": _mean(records, "success_rate"),
        "progress_rate_mean": _mean(records, "progress_rate"),
        "retriever_score_mean": _mean(records, "retriever_score"),
        "tool_score_mean": _mean(records, "tool_score"),
        "duration_seconds_mean": _mean(records, "duration_seconds"),
    }
    summary_path = export_dir / "summary.json"
    summary_tmp = summary_path.with_suffix(".json.tmp")
    summary_tmp.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    os.replace(summary_tmp, summary_path)
    return jsonl_path, csv_path
