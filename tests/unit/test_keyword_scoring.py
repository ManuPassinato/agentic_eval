import json, pytest

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


def test_retriever_and_tool_scores_use_tool_observations_only(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "message.part.updated",
                        "payload": {
                            "properties": {
                                "part": {
                                    "type": "tool",
                                    "tool": "aneel_search_resolucao_normativa",
                                    "state": {
                                        "output": (
                                            "document_id=aneel-abc path=ren2006247.pdf "
                                            "trecho sobre SMF e § 1o"
                                        )
                                    },
                                }
                            }
                        },
                    }
                ),
                json.dumps(
                    {
                        "kind": "assistant",
                        "payload": {
                            "text": "resposta final com ren2021926 e FNDCT sem tool"
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    scores = keyword_scores(
        "resposta final com ren2021926 e FNDCT sem tool",
        {
            "key_answer": ["49%"],
            "key_middle": [
                {"keyword": "ren2006247", "in_citation_path": True},
                {"keyword": "SMF", "in_citation_path": False},
                {"keyword": "§ 1o", "in_citation_path": False},
                {"keyword": "FNDCT", "in_citation_path": False},
            ],
            "citations": [
                {"blob_path": "biblioteca_aneel_gov_br/legislacao/ren2006247.pdf"},
                {"blob_path": "biblioteca_aneel_gov_br/legislacao/ren2021926.pdf"},
            ],
        },
        trace_path=trace,
    )

    # Retriever = citation-path middles ∪ citation blob stems, matched in tool output only.
    assert scores["retriever_score"] == 0.5  # ren2006247 yes; ren2021926 only in answer
    assert scores["matched_retriever_anchors"] == ["ren2006247"]
    # Tool = remaining middle keywords in tool output only.
    assert scores["tool_score"] == pytest.approx(2 / 3)
    assert scores["matched_tool_keywords"] == ["SMF", "§ 1o"]


def test_ablation_scores_none_without_gold_anchors():
    scores = keyword_scores("anything", {"key_answer": ["x"]})
    assert scores["retriever_score"] is None
    assert scores["tool_score"] is None
