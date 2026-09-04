from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from agentic_eval.runner.ledger import RunLedger


def export_run(run_dir: Path) -> tuple[Path, Path]:
    ledger = RunLedger(run_dir / "ledger.sqlite3")
    try:
        rows = ledger.final_rows()
    finally:
        ledger.close()

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
                }
            )
    os.replace(csv_tmp, csv_path)
    return jsonl_path, csv_path
