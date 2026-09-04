import json

from agentic_eval.domain import TraceEvent
from agentic_eval.runner.artifacts import TraceWriter, case_artifact_key


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
