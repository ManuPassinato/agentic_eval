import json
from datetime import datetime, timezone
from pathlib import Path

from agentic_eval.domain import AttemptResult, FailureKind, QuestionCase
from agentic_eval.runner.exports import export_run
from agentic_eval.runner.ledger import RunLedger


def test_export_ablation_scores_from_benchmark_annotations(tmp_path):
    dataset = Path("examples/benchmark_smoke2.jsonl")
    record = json.loads(dataset.read_text(encoding="utf-8").splitlines()[0])
    case_id = record["task_id"]

    run_dir = tmp_path / "run"
    attempts = run_dir / "attempts" / case_id
    attempts.mkdir(parents=True)
    trace_rel = Path("attempts") / case_id / "1.trace.jsonl"
    result_rel = Path("attempts") / case_id / "1.result.json"
    (run_dir / trace_rel).write_text(
        json.dumps(
            {
                "kind": "message.part.updated",
                "payload": {
                    "properties": {
                        "part": {
                            "type": "tool",
                            "tool": "aneel_search_resolucao_normativa",
                            "state": {
                                "output": (
                                    f"blob={record['citations'][0]['blob_path']} "
                                    "SMF sistema de medição"
                                )
                            },
                        }
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    final_answer = (
        "Contratação com teto de 49%, sistema de medição para faturamento, "
        "e registro do CCEI na CCEE."
    )
    (run_dir / result_rel).write_text(
        json.dumps(
            {
                "failure_kind": "success",
                "final_answer": final_answer,
                "sources": [],
                "source_matches": [],
            }
        ),
        encoding="utf-8",
    )

    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_source": str(dataset.resolve()),
                "resolved_config": {
                    "dataset": {
                        "id_field": "task_id",
                        "question_field": "question",
                        "metadata_field": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    ledger = RunLedger(run_dir / "ledger.sqlite3")
    try:
        now = datetime.now(timezone.utc)
        ledger.register_cases(
            [QuestionCase(id=case_id, question=record["question"])]
        )
        ledger.record_attempt(
            "test-0",
            AttemptResult(
                case_id=case_id,
                attempt=1,
                failure_kind=FailureKind.SUCCESS,
                final_answer=final_answer,
                started_at=now,
                finished_at=now,
                duration_seconds=1.5,
            ),
            result_path=result_rel,
            trace_path=trace_rel,
            final=True,
        )
    finally:
        ledger.close()

    jsonl_path, _csv_path = export_run(run_dir)
    exported = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
    summary = json.loads((run_dir / "exports" / "summary.json").read_text(encoding="utf-8"))

    assert exported["success_rate"] == 1.0
    assert exported["retriever_score"] == 1.0
    assert "ren2006247" in exported["matched_retriever_anchors"]
    assert exported["tool_score"] is not None
    assert summary["success_rate_mean"] == 1.0
    assert summary["retriever_score_mean"] == 1.0
