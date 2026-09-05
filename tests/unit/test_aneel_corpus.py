import json

import pyarrow as pa
import pyarrow.parquet as pq

from agentic_eval.environments.aneel.build import (
    build_corpus,
    derive_act_identity,
    document_id,
)
from agentic_eval.environments.aneel.corpus import AneelCorpus
from agentic_eval.environments.aneel.download import audit_download
from agentic_eval.environments.aneel.families import (
    FAMILIES,
    FAMILY_BY_SLUG,
    validate_corpus_families,
)


def _raw_fixture(tmp_path):
    raw = tmp_path / "raw"
    data = raw / "data"
    data.mkdir(parents=True)
    rows = [
        {
            "bucket_name": "crawler-nlp-new",
            "blob_path": "legislacao/ren20211000.pdf",
            "format": "pdf",
            "text": (
                "RESOLUÇÃO NORMATIVA ANEEL Nº 1.000, DE 7 DE DEZEMBRO DE 2021\n"
                "Estabelece as Regras de Prestação do Serviço Público de Distribuição."
            ),
            "idx": 0,
            "origin": "Legislação ANEEL",
            "categories": ["Outros"],
            "category_paths": ["Outros"],
            "family": "Resolução Normativa",
            "prefix": "ren",
            "role": "Ato principal",
            "issuer": "aneel",
        },
        {
            "bucket_name": "crawler-nlp-new",
            "blob_path": "acervo/ata-01.pdf",
            "format": "pdf",
            "text": "ATA DA PRIMEIRA REUNIÃO PÚBLICA DA DIRETORIA DA ANEEL",
            "idx": 1,
            "origin": "Acervo ANEEL",
            "categories": ["Outros"],
            "category_paths": ["Outros"],
            "family": None,
            "prefix": None,
            "role": None,
            "issuer": None,
        },
        {
            "bucket_name": "crawler-nlp-new",
            "blob_path": "legislacao/ren20211000cnpe.pdf",
            "format": "pdf",
            "text": "RESOLUÇÃO NORMATIVA Nº 1.000, DE 2021\nOutro emissor.",
            "idx": 2,
            "origin": "Legislação ANEEL",
            "categories": ["Outros"],
            "category_paths": ["Outros"],
            "family": "Resolução Normativa",
            "prefix": "ren",
            "role": "Ato principal",
            "issuer": "cnpe",
        },
    ]
    pq.write_table(pa.Table.from_pylist(rows), data / "train-00000-of-00003.parquet")
    return raw


def test_build_search_list_and_chunk_document(tmp_path):
    raw = _raw_fixture(tmp_path)
    database = tmp_path / "aneel.sqlite3"

    manifest = build_corpus(raw, database)

    assert manifest["document_count"] == 3
    assert manifest["distributions"]["origin"]["Legislação ANEEL"] == 2
    with AneelCorpus(database) as corpus:
        results = corpus.search("serviço público distribuição", issuer="aneel")
        assert len(results) == 1
        assert results[0]["act_year"] == 2021
        assert results[0]["act_number"] == "1.000"
        assert results[0]["categories"] == ["Outros"]
        listed = corpus.list_documents(
            family="Resolução Normativa",
            issuer="aneel",
        )
        assert [item["document_id"] for item in listed] == [
            document_id("legislacao/ren20211000.pdf")
        ]
        acts = corpus.get_act(
            "1000",
            2021,
            family="Resolução Normativa",
            issuer="aneel",
        )
        assert [item["document_id"] for item in acts] == [
            document_id("legislacao/ren20211000.pdf")
        ]
        document = corpus.get_document(results[0]["document_id"], max_chars=20)
        assert document is not None
        assert len(document["content"]) == 20
        assert document["next_offset"] == 20
        exact = corpus.get_family_act(
            "Resolução Normativa",
            "1000",
            2021,
            issuer="aneel",
            max_chars=20,
        )
        assert exact["status"] == "found"
        assert len(exact["document"]["content"]) == 20
        ambiguous = corpus.get_family_act("Resolução Normativa", "1000", 2021)
        assert ambiguous["status"] == "ambiguous"
        assert len(ambiguous["candidates"]) == 2
        missing = corpus.get_family_act("Resolução Normativa", "9999", 2021)
        assert missing["status"] == "not_found"
        assert corpus.stats()["document_count"] == 3


def test_identity_falls_back_to_blob_name():
    number, year = derive_act_identity(
        "texto sem cabeçalho reconhecível",
        "biblioteca/ren20211000.pdf",
        "Resolução Normativa",
    )
    assert (number, year) == ("1000", 2021)


def test_download_audit_reports_missing_files(tmp_path):
    audit = audit_download(tmp_path)
    assert audit["complete"] is False
    assert "README.md" in audit["missing"]
    assert json.dumps(audit)


def test_family_registry_covers_all_non_null_documents():
    assert len(FAMILIES) == len(FAMILY_BY_SLUG) == 29
    assert sum(family.document_count for family in FAMILIES) == 148_866
    assert FAMILY_BY_SLUG["resolucao_normativa"].name == "Resolução Normativa"
    validate_corpus_families({"Resolução Normativa": 1}, document_count=1)
