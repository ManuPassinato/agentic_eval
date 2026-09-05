from pathlib import Path

import pytest

from agentic_eval.config import load_run_spec
from agentic_eval.domain import (
    DatasetSpec,
    EnvironmentSpec,
    HarnessSpec,
    ModelSpec,
    RunSpec,
    TaskSpec,
    TraceEvent,
)
from agentic_eval.harnesses.opencode import (
    OpenCodeWorker,
    _classify_runtime_error,
    _extract_answer,
    _extract_document_ids,
    _extract_finish_answer,
    _extract_urls,
    _is_idle_event,
    _is_tool_event,
    _session_id,
)

PROJECT_ROOT = Path(__file__).parents[2]


def test_extracts_last_assistant_plain_text_and_usage():
    messages = [
        {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "old"}]},
        {
            "info": {"role": "assistant", "tokens": {"input": 10, "output": 4}},
            "parts": [
                {"type": "reasoning", "text": "hidden"},
                {"type": "text", "text": "answer https://example.com"},
            ],
        },
    ]
    answer, usage = _extract_answer(messages)
    assert answer == "answer https://example.com"
    assert usage["output"] == 4


def test_reads_session_id_from_event_variants():
    assert _session_id({"properties": {"sessionID": "abc"}}) == "abc"
    assert _session_id({"sessionId": "def"}) == "def"


def test_recognizes_current_status_and_tool_event_schemas():
    assert _is_idle_event(
        {
            "type": "session.status",
            "properties": {"sessionID": "abc", "status": {"type": "idle"}},
        }
    )
    assert _is_tool_event(
        TraceEvent(
            kind="message.part.updated",
            payload={"properties": {"part": {"type": "tool"}}},
        )
    )


def test_extracts_urls_without_json_escape_artifacts():
    payload = {
        "output": "URL: https://www.python.org/\nPublished: today",
        "answer": "See [Python](https://example.com/path).",
    }
    assert _extract_urls(payload) == {
        "https://www.python.org/",
        "https://example.com/path",
    }


def test_extracts_corpus_evidence_and_finish_answer():
    event = TraceEvent(
        kind="message.part.updated",
        payload={
            "properties": {
                "part": {
                    "id": "call-1",
                    "type": "tool",
                    "tool": "aneel_finish",
                    "state": {
                        "input": {
                            "answer": "Há 149004 documentos.",
                            "evidence_ids": ["aneel-0123456789abcdefabcd"],
                        }
                    },
                }
            }
        },
    )
    assert _extract_finish_answer(event) == "Há 149004 documentos."
    assert _extract_document_ids(event.payload) == {"aneel-0123456789abcdefabcd"}


def test_native_config_registers_local_corpus_mcp(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text('{"id":"q1","question":"Q?"}\n', encoding="utf-8")
    database = tmp_path / "corpus.sqlite3"
    database.touch()
    spec = RunSpec(
        dataset=DatasetSpec(path=dataset),
        model=ModelSpec(model_id="model", base_url="http://vllm/v1"),
        harness=HarnessSpec(tool_policy={"allow": ["aneel_*"], "deny": ["websearch"]}),
        environment=EnvironmentSpec(
            kind="aneel_corpus",
            database_path=database,
            families=["resolucao_normativa"],
        ),
        task=TaskSpec(system_prompt="Use the corpus."),
    )

    config = OpenCodeWorker(spec, Path(tmp_path), 0)._native_config()

    assert config["mcp"]["aneel"]["type"] == "local"
    assert str(database) in config["mcp"]["aneel"]["command"]
    assert config["mcp"]["aneel"]["command"][-2:] == [
        "--family",
        "resolucao_normativa",
    ]
    assert config["permission"]["aneel_*"] == "allow"


def test_rejects_unknown_or_duplicate_environment_families(tmp_path):
    with pytest.raises(ValueError, match="unknown ANEEL family"):
        EnvironmentSpec(
            kind="aneel_corpus",
            database_path=tmp_path / "corpus.sqlite3",
            families=["not_a_family"],
        )
    with pytest.raises(ValueError, match="must be unique"):
        EnvironmentSpec(
            kind="aneel_corpus",
            database_path=tmp_path / "corpus.sqlite3",
            families=["despacho", "despacho"],
        )


def test_public_run_native_config_is_closed_and_narrowed(tmp_path):
    spec = load_run_spec(PROJECT_ROOT / "configs" / "run.public-tasks-closed.yaml")
    config = OpenCodeWorker(spec, tmp_path, 0)._native_config()

    command = config["mcp"]["aneel"]["command"]
    assert command[-2:] == ["--family", "resolucao_normativa"]
    assert config["permission"]["*"] == "deny"
    assert config["permission"]["aneel_*"] == "allow"
    assert all(
        config["permission"][tool] == "deny"
        for tool in ("websearch", "webfetch", "bash", "read", "write", "edit")
    )
    assert "exa" not in str(config).lower()


def test_classifies_corpus_and_step_limit_failures():
    from agentic_eval.domain import FailureKind

    assert _classify_runtime_error("tool step limit exceeded") == FailureKind.STEP_LIMIT
    assert (
        _classify_runtime_error("ANEEL MCP tool failed")
        == FailureKind.CORPUS_TOOL_FAILURE
    )
