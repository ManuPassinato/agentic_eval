from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .corpus import AneelCorpus
from .families import (
    FAMILIES,
    FAMILY_BY_SLUG,
    FamilyDefinition,
    validate_corpus_families,
)


def _register_family_tools(server: Any, corpus: AneelCorpus, family: FamilyDefinition) -> None:
    def search_family(
        query: str,
        issuer: str | None = None,
        year: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return corpus.search(
            query,
            family=family.name,
            issuer=issuer,
            year=year,
            limit=limit,
        )

    def get_family(
        number: str,
        year: int,
        issuer: str | None = None,
        role: str | None = "Ato principal",
        offset: int = 0,
        max_chars: int = 12_000,
    ) -> dict[str, Any]:
        return corpus.get_family_act(
            family.name,
            number,
            year,
            issuer=issuer,
            role=role,
            offset=offset,
            max_chars=max_chars,
        )

    def list_family(
        issuer: str | None = None,
        role: str | None = None,
        year: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return corpus.list_documents(
            family=family.name,
            issuer=issuer,
            role=role,
            year=year,
            limit=limit,
        )

    server.tool(
        name=f"search_{family.slug}",
        description=(
            f"Search only the ANEEL `{family.name}` corpus with BM25. "
            f"The full corpus contains {family.document_count} documents in this family."
        ),
    )(search_family)
    server.tool(
        name=f"get_{family.slug}",
        description=(
            f"Get one `{family.name}` by act number and year, including a bounded "
            "first content chunk. Returns candidates instead of guessing when ambiguous."
        ),
    )(get_family)
    server.tool(
        name=f"list_{family.slug}",
        description=(
            f"List metadata from only the ANEEL `{family.name}` corpus using "
            "optional issuer, role, and year filters."
        ),
    )(list_family)


def _select_families(family_slugs: list[str] | None) -> tuple[FamilyDefinition, ...]:
    if not family_slugs:
        return FAMILIES
    if len(set(family_slugs)) != len(family_slugs):
        raise ValueError("Selected ANEEL family slugs must be unique")
    unknown = sorted(set(family_slugs) - set(FAMILY_BY_SLUG))
    if unknown:
        raise ValueError(f"Unknown ANEEL family slugs: {unknown}")
    return tuple(FAMILY_BY_SLUG[slug] for slug in family_slugs)


def create_server(
    database_path: Path,
    family_slugs: list[str] | None = None,
) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP dependencies are missing; run `uv sync --extra corpus --extra dev`"
        ) from exc

    corpus = AneelCorpus(database_path)
    document_count, family_counts = corpus.family_counts()
    validate_corpus_families(family_counts, document_count)
    server = FastMCP("aneel")

    for family in _select_families(family_slugs):
        _register_family_tools(server, corpus, family)

    @server.tool()
    def get_aneel_document(
        document_id: str,
        offset: int = 0,
        max_chars: int = 12_000,
    ) -> dict[str, Any]:
        """Read a bounded chunk of one document returned by a search or listing."""
        document = corpus.get_document(
            document_id,
            offset=offset,
            max_chars=max_chars,
        )
        return document or {"error": "document_not_found", "document_id": document_id}

    @server.tool()
    def get_aneel_corpus_stats() -> dict[str, Any]:
        """Return corpus revision, row count, and metadata distributions."""
        return corpus.stats()

    @server.tool()
    def finish(answer: str, evidence_ids: list[str]) -> dict[str, Any]:
        """Complete the task with an answer and the ANEEL document IDs supporting it."""
        return {
            "finished": True,
            "answer": answer,
            "evidence_ids": evidence_ids,
        }

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local ANEEL corpus over MCP")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument(
        "--family",
        action="append",
        default=[],
        help="Expose only this registered family slug; repeat as needed.",
    )
    args = parser.parse_args()
    create_server(args.db, args.family).run(transport="stdio")


if __name__ == "__main__":
    main()
