import json

import pytest

from agentic_eval.datasets import agent_visible_record, load_cases, scoring_metadata, select_cases
from agentic_eval.domain import DatasetSpec, TaskSpec


def test_loads_configurable_jsonl_fields(tmp_path):
    path = tmp_path / "questions.jsonl"
    path.write_text(
        json.dumps({"key": 7, "prompt": "Question?", "labels": ["current"]}) + "\n",
        encoding="utf-8",
    )
    cases = load_cases(
        DatasetSpec(
            path=path,
            id_field="key",
            question_field="prompt",
            tags_field="labels",
        )
    )
    assert cases[0].id == "7"
    assert cases[0].tags == ["current"]


def test_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "questions.jsonl"
    path.write_text(
        '{"id":"same","question":"One"}\n{"id":"same","question":"Two"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate case id"):
        load_cases(DatasetSpec(path=path))


def test_shards_are_stable_and_disjoint(tmp_path):
    path = tmp_path / "questions.jsonl"
    path.write_text(
        "".join(json.dumps({"id": str(i), "question": f"Q{i}"}) + "\n" for i in range(20)),
        encoding="utf-8",
    )
    cases = load_cases(DatasetSpec(path=path))
    first = select_cases(cases, case_ids=set(), tags=set(), shard_index=0, shard_count=2)
    second = select_cases(cases, case_ids=set(), tags=set(), shard_index=1, shard_count=2)
    assert {case.id for case in first}.isdisjoint(case.id for case in second)
    assert len(first) + len(second) == len(cases)


def test_fused_answer_columns_are_not_loaded_or_rendered(tmp_path):
    path = tmp_path / "benchmark.jsonl"
    record = {
        "task_id": "TASK_1",
        "instruction": "Como regularizar a fatura?",
        "key_answer": ["quinze dias"],
        "key_middle": ["ren2006247"],
        "candidate_response": "SECRET GOLD ANSWER",
        "citations": [{"evidence_text": "SECRET EVIDENCE"}],
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    spec = DatasetSpec(
        path=path,
        id_field="task_id",
        question_field="instruction",
        reference_answer_field=None,
        tags_field=None,
        metadata_field=None,
        timeout_field=None,
    )
    case = load_cases(spec)[0]
    prompt = TaskSpec(system_prompt="Use as ferramentas ANEEL.").render(
        case, include_source_profile=False
    )

    assert case.question == "Como regularizar a fatura?"
    assert case.reference_answer is None
    assert case.metadata == {}
    assert agent_visible_record(record, spec) == {
        "task_id": "TASK_1",
        "instruction": "Como regularizar a fatura?",
    }
    assert scoring_metadata(record, spec.metadata_field) == {
        "key_answer": ["quinze dias"],
        "key_middle": ["ren2006247"],
        "citations": [{"evidence_text": "SECRET EVIDENCE"}],
    }
    assert scoring_metadata(
        {
            "answer_keywords": [{"keyword": "quinze dias"}],
            "middle_keywords": [{"keyword": "ren2006247"}],
            "citations": [{"blob_path": "legislacao/ren2006247.pdf"}],
        },
        None,
    ) == {
        "key_answer": [{"keyword": "quinze dias"}],
        "key_middle": [{"keyword": "ren2006247"}],
        "citations": [{"blob_path": "legislacao/ren2006247.pdf"}],
    }
    assert "Como regularizar a fatura?" in prompt
    assert "SECRET GOLD ANSWER" not in prompt
    assert "SECRET EVIDENCE" not in prompt
    assert "quinze dias" not in prompt
    assert "ren2006247" not in prompt
