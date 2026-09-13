import json

from agentic_eval.domain import (
    DatasetSpec,
    EnvironmentSpec,
    ModelSpec,
    RunSpec,
    TaskSpec,
    TraceEvent,
)
from agentic_eval.runner.artifacts import (
    TraceWriter,
    case_artifact_key,
    initialize_run_directory,
)


async def test_trace_writer_truncates_and_atomically_commits(tmp_path):
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path, max_payload_bytes=20)
    await writer.write(TraceEvent(kind="tool", payload={"content": "x" * 200}))
    assert not path.exists()
    writer.commit()
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["truncated"] is True
    assert record["original_sha256"]
    assert not path.with_suffix(".jsonl.tmp").exists()


def test_case_artifact_key_cannot_escape_directory():
    key = case_artifact_key("../../outside")
    assert "/" not in key
    assert ".." not in key


def test_run_snapshots_small_corpus_manifest_not_database(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text('{"id":"q","question":"Q?"}\n', encoding="utf-8")
    database = tmp_path / "large.sqlite3"
    database.write_bytes(b"database")
    corpus_manifest = tmp_path / "corpus.manifest.json"
    corpus_manifest.write_text('{"document_count": 2}', encoding="utf-8")
    config = tmp_path / "run.yaml"
    config.write_text("test: true\n", encoding="utf-8")
    spec = RunSpec(
        dataset=DatasetSpec(path=dataset),
        model=ModelSpec(model_id="model", base_url="http://vllm/v1"),
        environment=EnvironmentSpec(
            kind="aneel_corpus",
            database_path=database,
            manifest_path=corpus_manifest,
        ),
        task=TaskSpec(system_prompt="Use corpus."),
    )
    run_dir = tmp_path / "run"

    initialize_run_directory(run_dir, spec, config, run_id="test")

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["corpus"]["snapshot"] == "corpus-manifest.json"
    assert (run_dir / "corpus-manifest.json").exists()
    assert not (run_dir / database.name).exists()


def test_run_dataset_snapshot_hides_answer_columns(tmp_path):
    dataset = tmp_path / "benchmark.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "task_id": "TASK_1",
                "instruction": "Como regularizar a fatura?",
                "key_answer": ["quinze dias"],
                "candidate_response": "SECRET GOLD ANSWER",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "run.yaml"
    config.write_text("test: true\n", encoding="utf-8")
    spec = RunSpec(
        dataset=DatasetSpec(
            path=dataset,
            id_field="task_id",
            question_field="instruction",
            reference_answer_field=None,
            metadata_field=None,
        ),
        model=ModelSpec(model_id="model", base_url="http://vllm/v1"),
        task=TaskSpec(system_prompt="Use corpus."),
    )
    run_dir = tmp_path / "run"

    initialize_run_directory(run_dir, spec, config, run_id="test")

    snapshot = json.loads((run_dir / "dataset.jsonl").read_text(encoding="utf-8"))
    assert snapshot == {
        "task_id": "TASK_1",
        "instruction": "Como regularizar a fatura?",
    }
    original = json.loads(dataset.read_text(encoding="utf-8"))
    assert original["candidate_response"] == "SECRET GOLD ANSWER"
