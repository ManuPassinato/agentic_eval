import json

import pytest

from agentic_eval.datasets import load_cases, select_cases
from agentic_eval.domain import DatasetSpec


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
