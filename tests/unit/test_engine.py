import json
from datetime import datetime, timezone

from agentic_eval.domain import (
    AttemptResult,
    DatasetSpec,
    FailureKind,
    HarnessSpec,
    ModelSpec,
    RunSpec,
    TaskSpec,
    TraceEvent,
)
from agentic_eval.runner.engine import EvaluationRunner
from agentic_eval.runner.exports import export_run
from agentic_eval.sources import load_source_profile


class FakeWorker:
    worker_id = "fake-0"

    def __init__(self):
        self.calls = 0
        self.prompts = []

    async def start(self):
        return None

    async def stop(self):
        return None

    async def execute(self, case, prompt, attempt, timeout_seconds, event_sink):
        self.calls += 1
        self.prompts.append(prompt)
        await event_sink(TraceEvent(kind="test", payload={"prompt": prompt}))
        now = datetime.now(timezone.utc)
        if self.calls == 1:
            return AttemptResult(
                case_id=case.id,
                attempt=attempt,
                failure_kind=FailureKind.HARNESS_CRASH,
                started_at=now,
                finished_at=now,
                duration_seconds=0,
                error="transient",
            )
        return AttemptResult(
            case_id=case.id,
            attempt=attempt,
            failure_kind=FailureKind.SUCCESS,
            final_answer="done",
            sources=["https://official.example/source"],
            started_at=now,
            finished_at=now,
            duration_seconds=0,
        )


class FakeAdapter:
    def __init__(self):
        self.worker = FakeWorker()

    async def qualify(self, run_live_case=True):
        return {"healthy": True}

    async def create_worker(self, worker_index):
        return self.worker

    async def close(self):
        return None


async def test_runner_preserves_retry_attempts(monkeypatch, tmp_path):
    dataset = tmp_path / "questions.jsonl"
    dataset.write_text(
        '{"id":"q/1","question":"What?","metadata":'
        '{"key_answer":["done"],"key_middle":["Search."]}}\n',
        encoding="utf-8",
    )
    config = tmp_path / "run.yaml"
    config.write_text("test: true\n", encoding="utf-8")
    profile_path = tmp_path / "sources.yaml"
    profile_path.write_text(
        """
id: test-sources
description: Test preferred sources
trusted_domains:
  - domain: official.example
sources:
  - id: official-home
    url: https://official.example/
    always_include: true
""",
        encoding="utf-8",
    )
    run_dir = tmp_path / "runs" / "test"
    spec = RunSpec(
        dataset=DatasetSpec(path=dataset),
        model=ModelSpec(model_id="model", base_url="http://vllm/v1"),
        harness=HarnessSpec(workers=1),
        task=TaskSpec(
            system_prompt="Search.",
            source_profile_path=profile_path,
            source_profile=load_source_profile(profile_path),
        ),
        output_dir=tmp_path / "runs",
        infrastructure_retries=1,
    )
    adapter = FakeAdapter()
    monkeypatch.setattr(
        "agentic_eval.runner.engine.create_adapter",
        lambda run_spec, runtime_root: adapter,
    )

    summary = await EvaluationRunner(
        spec,
        config,
        run_dir,
        run_id="test",
    ).run()

    assert summary == {"success": 1}
    results = list((run_dir / "attempts").glob("**/*.result.json"))
    traces = list((run_dir / "attempts").glob("**/*.trace.jsonl"))
    assert len(results) == 2
    assert len(traces) == 2
    final_result = json.loads(results[-1].read_text(encoding="utf-8"))
    assert final_result["source_matches"][0]["preferred"] is True
    assert "official.example" in adapter.worker.prompts[-1]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_profile"]["id"] == "test-sources"
    assert (run_dir / "source-profile.yaml").exists()
    jsonl_path, csv_path = export_run(run_dir)
    assert '"final_answer": "done"' in jsonl_path.read_text(encoding="utf-8")
    assert '"preferred_source_count": 1' in jsonl_path.read_text(encoding="utf-8")
    assert '"success_rate": 1.0' in jsonl_path.read_text(encoding="utf-8")
    assert '"progress_rate": 1.0' in jsonl_path.read_text(encoding="utf-8")
    assert csv_path.exists()

    resumed = await EvaluationRunner(
        spec,
        config,
        run_dir,
        run_id="test",
        resume=True,
    ).run()
    assert resumed == {"success": 1}
    assert len(list((run_dir / "attempts").glob("**/*.result.json"))) == 2
