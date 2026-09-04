from agentic_eval.domain import TraceEvent
from agentic_eval.harnesses.opencode import (
    _extract_answer,
    _extract_urls,
    _is_idle_event,
    _is_tool_event,
    _session_id,
)


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
