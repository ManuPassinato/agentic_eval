from pathlib import Path

from agentic_eval.config import load_run_spec
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
