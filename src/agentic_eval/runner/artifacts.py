from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from agentic_eval.config import redact
from agentic_eval.datasets import file_sha256
from agentic_eval.domain import AttemptResult, RunSpec, TraceEvent


def case_artifact_key(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:12]
    readable = "".join(character if character.isalnum() or character in "-_" else "-" for character in case_id)
    return f"{readable[:48] or 'case'}-{digest}"


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class TraceWriter:
    def __init__(self, final_path: Path, max_payload_bytes: int) -> None:
        self.final_path = final_path
        self.max_payload_bytes = max_payload_bytes
        self.temporary_path = final_path.with_suffix(final_path.suffix + ".tmp")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.temporary_path.open("w", encoding="utf-8")

    async def write(self, event: TraceEvent) -> None:
        raw_payload = json.dumps(event.payload, ensure_ascii=False, default=str).encode()
        if len(raw_payload) > self.max_payload_bytes:
            digest = hashlib.sha256(raw_payload).hexdigest()
            event = event.model_copy(
                update={
                    "payload": {
                        "truncated_preview": raw_payload[: self.max_payload_bytes].decode(
                            "utf-8", errors="replace"
                        ),
                        "original_bytes": len(raw_payload),
                    },
                    "truncated": True,
                    "original_sha256": digest,
                }
            )
        self.handle.write(event.model_dump_json() + "\n")
        self.handle.flush()

    def commit(self) -> None:
        if not self.handle.closed:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()
        os.replace(self.temporary_path, self.final_path)

    def abort(self) -> None:
        if not self.handle.closed:
            self.handle.close()
        self.temporary_path.unlink(missing_ok=True)


def initialize_run_directory(
    run_dir: Path,
    spec: RunSpec,
    config_path: Path,
    *,
    run_id: str,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "attempts").mkdir(exist_ok=True)
    (run_dir / "runtime").mkdir(exist_ok=True)
    dataset_copy = run_dir / "dataset.jsonl"
    if not dataset_copy.exists():
        shutil.copy2(spec.dataset.path, dataset_copy)
    source_profile_manifest = None
    if spec.task.source_profile_path:
        profile_copy = run_dir / "source-profile.yaml"
        if not profile_copy.exists():
            shutil.copy2(spec.task.source_profile_path, profile_copy)
        source_profile_manifest = {
            "id": spec.task.source_profile.id if spec.task.source_profile else None,
            "source": str(spec.task.source_profile_path),
            "snapshot": profile_copy.name,
            "sha256": file_sha256(spec.task.source_profile_path),
        }
    manifest = {
        "run_id": run_id,
        "config_source": str(config_path.resolve()),
        "dataset_source": str(spec.dataset.path),
        "dataset_sha256": file_sha256(spec.dataset.path),
        "source_profile": source_profile_manifest,
        "resolved_config": redact(spec.model_dump(mode="json")),
    }
    atomic_write_json(run_dir / "manifest.json", manifest)
    (run_dir / "resolved-config.redacted.yaml").write_text(
        yaml.safe_dump(manifest["resolved_config"], sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def write_attempt_result(run_dir: Path, result: AttemptResult) -> Path:
    path = (
        run_dir
        / "attempts"
        / case_artifact_key(result.case_id)
        / f"attempt-{result.attempt:03d}.result.json"
    )
    atomic_write_json(path, result.model_dump(mode="json"))
    return path
