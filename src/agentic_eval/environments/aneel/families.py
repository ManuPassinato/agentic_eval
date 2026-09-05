from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FamilyDefinition:
    name: str
    slug: str
    document_count: int


FAMILIES = (
    FamilyDefinition("Despacho", "despacho", 87_002),
    FamilyDefinition("Resolução Autorizativa", "resolucao_autorizativa", 30_465),
    FamilyDefinition("Portaria", "portaria", 11_791),
    FamilyDefinition("Resolução Homologatória", "resolucao_homologatoria", 8_738),
    FamilyDefinition(
        "Resolução — classificação anterior a 2004",
        "resolucao_anterior_2004",
        3_291,
    ),
    FamilyDefinition("Resolução Normativa", "resolucao_normativa", 2_358),
    FamilyDefinition("Ofício", "oficio", 1_209),
    FamilyDefinition("Audiência Pública", "audiencia_publica", 1_111),
    FamilyDefinition("Consulta Pública", "consulta_publica", 567),
    FamilyDefinition("Contrato de Concessão", "contrato_concessao", 537),
    FamilyDefinition("Decreto", "decreto", 363),
    FamilyDefinition("Leilão", "leilao", 288),
    FamilyDefinition("Comunicado", "comunicado", 280),
    FamilyDefinition("Nota Técnica avulsa", "nota_tecnica_avulsa", 235),
    FamilyDefinition("Aviso", "aviso", 199),
    FamilyDefinition("Extrato de Compromisso", "extrato_compromisso", 114),
    FamilyDefinition(
        "Aviso de Tomada de Subsídios",
        "aviso_tomada_subsidios",
        100,
    ),
    FamilyDefinition("Edital/Convocação", "edital_convocacao", 84),
    FamilyDefinition(
        "Portaria Interministerial",
        "portaria_interministerial",
        58,
    ),
    FamilyDefinition(
        "Instrução Administrativa",
        "instrucao_administrativa",
        48,
    ),
    FamilyDefinition(
        "Extrato de Convênio de Cooperação",
        "extrato_convenio_cooperacao",
        10,
    ),
    FamilyDefinition("Ato de outra agência", "ato_outra_agencia", 6),
    FamilyDefinition(
        "Extrato de Contrato de Metas",
        "extrato_contrato_metas",
        5,
    ),
    FamilyDefinition("Exposição de Motivos", "exposicao_motivos", 2),
    FamilyDefinition("Decisão Judicial", "decisao_judicial", 1),
    FamilyDefinition("Manual", "manual", 1),
    FamilyDefinition("Pleito", "pleito", 1),
    FamilyDefinition("Relatório de Comissão", "relatorio_comissao", 1),
    FamilyDefinition("Súmula", "sumula", 1),
)

FAMILY_BY_SLUG = {family.slug: family for family in FAMILIES}


def validate_family_registry() -> None:
    if len(FAMILIES) != 29:
        raise RuntimeError(f"Expected 29 ANEEL families, found {len(FAMILIES)}")
    if len(FAMILY_BY_SLUG) != len(FAMILIES):
        raise RuntimeError("ANEEL family slugs must be unique")
    if len({family.name for family in FAMILIES}) != len(FAMILIES):
        raise RuntimeError("ANEEL family names must be unique")
    if any(
        not family.slug.isascii() or not family.slug.replace("_", "").isalnum()
        for family in FAMILIES
    ):
        raise RuntimeError("ANEEL family slugs must contain only ASCII letters, numbers, or underscores")
    if any(family.document_count < 1 for family in FAMILIES):
        raise RuntimeError("ANEEL family document counts must be positive")


def validate_corpus_families(counts: dict[str, int], document_count: int) -> None:
    expected = {family.name: family.document_count for family in FAMILIES}
    actual = {name: count for name, count in counts.items() if name != "(null)"}
    unknown = sorted(set(actual) - set(expected))
    if unknown:
        raise RuntimeError(f"Corpus contains unregistered ANEEL families: {unknown}")
    if document_count == 149_004 and actual != expected:
        differences = {
            name: {"expected": expected.get(name), "actual": actual.get(name)}
            for name in sorted(set(expected) | set(actual))
            if expected.get(name) != actual.get(name)
        }
        raise RuntimeError(f"Full corpus family counts do not match registry: {differences}")


validate_family_registry()
