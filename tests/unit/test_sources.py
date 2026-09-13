from pathlib import Path

from agentic_eval.config import load_run_spec
from agentic_eval.datasets import load_cases
from agentic_eval.domain import QuestionCase
from agentic_eval.sources import (
    classify_sources,
    load_source_profile,
    render_source_guidance,
    select_seed_sources,
)

PROJECT_ROOT = Path(__file__).parents[2]
PROFILE_PATH = PROJECT_ROOT / "configs" / "sources" / "aneel.yaml"


def test_profile_routes_global_and_topic_sources():
    profile = load_source_profile(PROFILE_PATH)
    regulation = QuestionCase(id="regulation", question="REN 1000", tags=["regulacao"])
    bdgd = QuestionCase(id="bdgd", question="BDGD", tags=["bdgd"])

    assert [source.id for source in select_seed_sources(profile, regulation)] == [
        "aneel-open-data-organization"
    ]
    assert [source.id for source in select_seed_sources(profile, bdgd)] == [
        "aneel-open-data-organization",
        "aneel-bdgd",
    ]


def test_guidance_is_preferred_not_restricted():
    profile = load_source_profile(PROFILE_PATH)
    case = QuestionCase(id="q", question="REN 1000", tags=["regulacao"])
    guidance = render_source_guidance(profile, case)

    assert "agencia-nacional-de-energia-eletrica" in guidance
    assert "base-de-dados-geografica" not in guidance
    assert "must call the websearch tool" in guidance
    assert "not a restriction" in guidance


def test_classifies_domain_path_and_exact_seed():
    profile = load_source_profile(PROFILE_PATH)
    matches = classify_sources(
        [
            "https://dadosabertos.aneel.gov.br/organization/agencia-nacional-de-energia-eletrica",
            "https://www.gov.br/aneel/pt-br/assuntos",
            "https://www.gov.br/saude/pt-br",
            "https://[broken",
        ],
        profile,
    )

    assert matches[0].preferred and matches[0].source_id == "aneel-open-data-organization"
    assert matches[1].preferred and matches[1].matched_domain == "www.gov.br"
    assert not matches[2].preferred
    assert not matches[3].preferred


def test_live_config_loads_profile_and_keeps_qualification_neutral():
    spec = load_run_spec(PROJECT_ROOT / "configs" / "run.live.yaml")
    case = QuestionCase(id="q", question="REM 1000", tags=["regulacao"])

    assert spec.task.source_profile
    assert spec.task.source_profile.id == "aneel-brazil"
    prompt = spec.task.render(case)
    assert "sistemas elétricos" in prompt
    assert "agencia-nacional-de-energia-eletrica" in prompt
    assert prompt.endswith("Pergunta: REM 1000")
    assert "ANEEL" not in spec.task.render_qualification()


def test_closed_corpus_config_resolves_local_paths_and_denies_web():
    spec = load_run_spec(PROJECT_ROOT / "configs" / "run.aneel-closed.example.yaml")

    assert spec.environment.kind == "aneel_corpus"
    assert spec.environment.database_path == (
        PROJECT_ROOT / "data" / "processed" / "aneel-corpus.sqlite3"
    )
    assert spec.environment.max_tool_steps == 10
    assert "aneel_*" in spec.harness.tool_policy.allow
    assert "websearch" in spec.harness.tool_policy.deny
    assert spec.task.source_profile is None


def test_public_tasks_config_loads_only_safe_question_fields():
    spec = load_run_spec(PROJECT_ROOT / "configs" / "run.public-tasks-closed.yaml")
    cases = load_cases(spec.dataset)

    assert len(cases) == 30
    assert spec.dataset.id_field == "task_id"
    assert spec.dataset.question_field == "question"
    assert spec.dataset.reference_answer_field is None
    assert spec.dataset.tags_field is None
    assert spec.dataset.metadata_field is None
    assert spec.dataset.timeout_field is None
    assert spec.environment.families == ["resolucao_normativa"]
    assert all(case.reference_answer is None for case in cases)
    assert all(case.tags == [] and case.metadata == {} for case in cases)
    prompt = spec.task.render(cases[0], include_source_profile=False)
    assert cases[0].question in prompt
    assert "candidate_response" not in prompt
    assert "answer_keywords" not in prompt
    assert "key_answer" not in prompt
    assert prompt.endswith(f"Pergunta: {cases[0].question}")
