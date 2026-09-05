from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from agentic_eval.datasets import file_sha256, load_cases, select_cases
from agentic_eval.domain import AttemptResult, FailureKind, RunSpec
from agentic_eval.harnesses import create_adapter
from agentic_eval.runner.artifacts import (
    TraceWriter,
    atomic_write_json,
    case_artifact_key,
    initialize_run_directory,
    write_attempt_result,
)
from agentic_eval.runner.ledger import RunLedger
from agentic_eval.sources import classify_sources


class EvaluationRunner:
    def __init__(
        self,
        spec: RunSpec,
        config_path: Path,
        run_dir: Path,
        *,
        run_id: str,
        resume: bool = False,
    ) -> None:
        self.spec = spec
        self.config_path = config_path
        self.run_dir = run_dir
        self.run_id = run_id
        self.resume = resume

    def _prepare(self) -> tuple[list, RunLedger]:
        manifest_path = self.run_dir / "manifest.json"
        if self.resume:
            if not manifest_path.exists():
                raise ValueError(f"Cannot resume: {manifest_path} does not exist")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = manifest["dataset_sha256"]
            actual = file_sha256(self.spec.dataset.path)
            if actual != expected:
                raise ValueError(
                    f"Dataset changed since run creation: expected {expected}, found {actual}"
                )
            corpus_manifest = manifest.get("corpus")
            if corpus_manifest and self.spec.environment.manifest_path:
                expected_corpus = corpus_manifest["sha256"]
                actual_corpus = file_sha256(self.spec.environment.manifest_path)
                if actual_corpus != expected_corpus:
                    raise ValueError(
                        "Corpus manifest changed since run creation: "
                        f"expected {expected_corpus}, found {actual_corpus}"
                    )
        else:
            if manifest_path.exists():
                raise ValueError(f"Run directory already exists: {self.run_dir}")
            initialize_run_directory(
                self.run_dir,
                self.spec,
                self.config_path,
                run_id=self.run_id,
            )

        cases = select_cases(
            load_cases(self.spec.dataset),
            case_ids=set(self.spec.case_ids),
            tags=set(self.spec.tags),
            shard_index=self.spec.shard_index,
            shard_count=self.spec.shard_count,
        )
        ledger = RunLedger(self.run_dir / "ledger.sqlite3")
        ledger.register_cases(cases)
        pending = ledger.pending_case_ids()
        return [case for case in cases if case.id in pending], ledger

    async def run(self) -> dict[str, int]:
        cases, ledger = self._prepare()
        if not cases:
            summary = ledger.summary()
            ledger.close()
            return summary

        adapter = create_adapter(self.spec, self.run_dir / "runtime")
        try:
            try:
                qualification = await adapter.qualify(run_live_case=True)
            except Exception as exc:
                atomic_write_json(
                    self.run_dir / "qualification.json",
                    {
                        "failure_kind": FailureKind.QUALIFICATION_FAILURE.value,
                        "error": str(exc),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                raise
            atomic_write_json(self.run_dir / "qualification.json", qualification)

            worker_count = min(
                len(cases),
                max(1, self.spec.concurrency),
                max(1, self.spec.harness.workers),
            )
            queue: asyncio.Queue = asyncio.Queue()
            for case in cases:
                queue.put_nowait(case)
            for _ in range(worker_count):
                queue.put_nowait(None)

            async def worker_loop(index: int) -> None:
                worker = await adapter.create_worker(index)
                while True:
                    case = await queue.get()
                    try:
                        if case is None:
                            return
                        attempt = ledger.next_attempt(case.id)
                        retries_left = self.spec.infrastructure_retries
                        while True:
                            artifact_dir = self.run_dir / "attempts" / case_artifact_key(case.id)
                            trace_path = artifact_dir / f"attempt-{attempt:03d}.trace.jsonl"
                            trace = TraceWriter(trace_path, self.spec.max_tool_payload_bytes)
                            timeout = case.timeout_seconds or self.spec.timeout_seconds
                            try:
                                result = await worker.execute(
                                    case,
                                    self.spec.task.render(case),
                                    attempt,
                                    timeout,
                                    trace.write,
                                )
                            except asyncio.CancelledError:
                                trace.abort()
                                raise
                            # Adapter plugins are an isolation boundary; preserve unknown
                            # failures as retryable harness-crash attempts.
                            except Exception as exc:  # noqa: BLE001
                                now = datetime.now(timezone.utc)
                                result = AttemptResult(
                                    case_id=case.id,
                                    attempt=attempt,
                                    failure_kind=FailureKind.HARNESS_CRASH,
                                    started_at=now,
                                    finished_at=now,
                                    duration_seconds=0,
                                    error=f"Unhandled adapter error: {exc}",
                                )
                            result.source_matches = classify_sources(
                                result.sources,
                                self.spec.task.source_profile,
                            )
                            trace.commit()
                            should_retry = result.infrastructure_failure and retries_left > 0
                            result.retry_class = (
                                "infrastructure" if result.infrastructure_failure else "agent"
                            )
                            result_path = write_attempt_result(self.run_dir, result)
                            ledger.record_attempt(
                                worker.worker_id,
                                result,
                                result_path.relative_to(self.run_dir),
                                trace_path.relative_to(self.run_dir),
                                final=not should_retry,
                            )
                            if not should_retry:
                                break
                            retries_left -= 1
                            attempt += 1
                            await worker.stop()
                            await worker.start()
                    finally:
                        queue.task_done()

            tasks = [asyncio.create_task(worker_loop(index)) for index in range(worker_count)]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            return ledger.summary()
        finally:
            await adapter.close()
            ledger.close()
