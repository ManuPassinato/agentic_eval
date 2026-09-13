import json

from agentic_eval.scoring.keyword import keyword_scores, normalize_text


def test_normalizes_case_accents_and_spacing():
    assert normalize_text("  Resolução   NORMATIVA ") == "resolucao normativa"


def test_scores_final_answer_and_intermediate_trace(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps({"payload": {"output": "Resolução Normativa 1000"}}) + "\n",
        encoding="utf-8",
    )

    scores = keyword_scores(
        "A norma permanece vigente.",
        {
            "key_answer": ["vigente"],
            "key_middle": ["resolucao normativa", "1000"],
        },
        trace_path=trace,
    )

    assert scores["success_rate"] == 1.0
    assert scores["progress_rate"] == 1.0
    assert scores["matched_key_middle"] == ["resolucao normativa", "1000"]


def test_returns_none_when_no_gold_keywords():
    scores = keyword_scores("answer", {})
    assert scores["success_rate"] is None
    assert scores["progress_rate"] is None


def test_unwraps_keyword_objects():
    scores = keyword_scores(
        "O CCEI deve ser registrado na CCEE.",
        {
            "key_answer": [{"keyword": "CCEI"}, {"keyword": "CCEE"}],
            "key_middle": [{"keyword": "ren2006247"}],
        },
    )
    assert scores["success_rate"] == 1.0
    assert scores["matched_key_answer"] == ["CCEI", "CCEE"]
