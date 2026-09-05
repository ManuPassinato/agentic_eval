from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from typing_extensions import Self

_TERM = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)


def _fts_query(query: str) -> str:
    terms = _TERM.findall(query)
    if not terms:
        raise ValueError("query must contain at least one searchable term")
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:32])


def _source_url(blob_path: str) -> str:
    return f"hf://datasets/cemig-ceia/biblioteca-aneel-categorizado-4/{quote(blob_path)}"


def _decode_document(row: sqlite3.Row, *, include_text: bool = False) -> dict[str, Any]:
    record = dict(row)
    record["categories"] = json.loads(record.pop("categories_json"))
    record["category_paths"] = json.loads(record.pop("category_paths_json"))
    record["source_uri"] = _source_url(record["blob_path"])
    if not include_text:
        record.pop("text", None)
    return record


class AneelCorpus:
    def __init__(self, database_path: Path) -> None:
        if not database_path.is_file():
            raise FileNotFoundError(f"ANEEL corpus database not found: {database_path}")
        uri = f"file:{quote(str(database_path.resolve()))}?mode=ro"
        self.connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only=ON")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _filters(
        *,
        family: str | None,
        issuer: str | None,
        role: str | None,
        year: int | None,
        alias: str = "d",
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (("family", family), ("issuer", issuer), ("role", role)):
            if value is not None:
                clauses.append(f"{alias}.{column} = ?")
                values.append(value)
        if year is not None:
            clauses.append(f"{alias}.act_year = ?")
            values.append(year)
        return clauses, values

    def search(
        self,
        query: str,
        *,
        family: str | None = None,
        issuer: str | None = None,
        year: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 20)
        clauses, values = self._filters(
            family=family,
            issuer=issuer,
            role=None,
            year=year,
        )
        where = ["documents_fts MATCH ?", *clauses]
        sql = f"""
            SELECT d.document_id, d.source_idx, d.blob_path, d.title, d.origin,
                   d.categories_json, d.category_paths_json, d.family, d.prefix,
                   d.role, d.issuer, d.act_number, d.act_year, d.text_length,
                   bm25(documents_fts, 4.0, 1.0) AS score,
                   snippet(documents_fts, 1, '[', ']', ' … ', 32) AS snippet
            FROM documents_fts
            JOIN documents AS d ON d.rowid = documents_fts.rowid
            WHERE {" AND ".join(where)}
            ORDER BY score
            LIMIT ?
        """
        rows = self.connection.execute(
            sql,
            [_fts_query(query), *values, limit],
        ).fetchall()
        return [_decode_document(row) for row in rows]

    def get_document(
        self,
        document_id: str,
        *,
        offset: int = 0,
        max_chars: int = 12_000,
    ) -> dict[str, Any] | None:
        if offset < 0:
            raise ValueError("offset must be nonnegative")
        max_chars = min(max(max_chars, 1), 50_000)
        row = self.connection.execute(
            "SELECT * FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        record = _decode_document(row, include_text=True)
        text = record.pop("text")
        record["offset"] = offset
        record["content"] = text[offset : offset + max_chars]
        record["next_offset"] = (
            offset + max_chars if offset + max_chars < len(text) else None
        )
        return record

    def get_act(
        self,
        number: str,
        year: int,
        *,
        family: str | None = None,
        role: str | None = "Ato principal",
        issuer: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_number = re.sub(r"\D", "", number).lstrip("0") or "0"
        clauses = [
            """
            ltrim(
                replace(
                    replace(
                        CASE
                            WHEN instr(d.act_number, '/') > 0
                            THEN substr(d.act_number, 1, instr(d.act_number, '/') - 1)
                            ELSE d.act_number
                        END,
                        '.',
                        ''
                    ),
                    '-',
                    ''
                ),
                '0'
            ) = ?
            """,
            "d.act_year = ?",
        ]
        values: list[Any] = [normalized_number, year]
        if family is not None:
            clauses.append("d.family = ?")
            values.append(family)
        if role is not None:
            clauses.append("d.role = ?")
            values.append(role)
        if issuer is not None:
            clauses.append("d.issuer = ?")
            values.append(issuer)
        rows = self.connection.execute(
            f"""
            SELECT document_id, source_idx, blob_path, title, origin,
                   categories_json, category_paths_json, family, prefix, role,
                   issuer, act_number, act_year, text_length
            FROM documents AS d
            WHERE {" AND ".join(clauses)}
            ORDER BY source_idx
            LIMIT 20
            """,
            values,
        ).fetchall()
        return [_decode_document(row) for row in rows]

    def get_family_act(
        self,
        family: str,
        number: str,
        year: int,
        *,
        issuer: str | None = None,
        role: str | None = "Ato principal",
        offset: int = 0,
        max_chars: int = 12_000,
    ) -> dict[str, Any]:
        candidates = self.get_act(
            number,
            year,
            family=family,
            role=role,
            issuer=issuer,
        )
        identity = {
            "family": family,
            "number": number,
            "year": year,
            "issuer": issuer,
            "role": role,
        }
        if not candidates:
            return {"status": "not_found", **identity, "candidates": []}
        if len(candidates) > 1:
            return {
                "status": "ambiguous",
                **identity,
                "candidates": candidates,
            }
        document = self.get_document(
            candidates[0]["document_id"],
            offset=offset,
            max_chars=max_chars,
        )
        if document is None:
            raise RuntimeError(
                f"Corpus index references missing document {candidates[0]['document_id']}"
            )
        return {"status": "found", **identity, "document": document}

    def list_documents(
        self,
        *,
        family: str | None = None,
        issuer: str | None = None,
        role: str | None = None,
        year: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 100)
        clauses, values = self._filters(
            family=family,
            issuer=issuer,
            role=role,
            year=year,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"""
            SELECT document_id, source_idx, blob_path, title, origin,
                   categories_json, category_paths_json, family, prefix, role,
                   issuer, act_number, act_year, text_length
            FROM documents AS d
            {where}
            ORDER BY source_idx
            LIMIT ?
            """,
            [*values, limit],
        ).fetchall()
        return [_decode_document(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        total = self.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        def counts(column: str) -> dict[str, int]:
            rows = self.connection.execute(
                f"""
                SELECT COALESCE({column}, '(null)') AS value, COUNT(*) AS count
                FROM documents GROUP BY {column} ORDER BY count DESC
                """
            ).fetchall()
            return {str(row["value"]): int(row["count"]) for row in rows}

        metadata = {
            row["key"]: json.loads(row["value"])
            for row in self.connection.execute("SELECT key, value FROM corpus_meta")
        }
        distributions = metadata.pop("distributions", None)
        if isinstance(distributions, dict):
            return {
                **metadata,
                "document_count": total,
                "origin": distributions.get("origin", {}),
                "family": distributions.get("family", {}),
                "issuer": distributions.get("issuer", {}),
            }
        return {
            **metadata,
            "document_count": total,
            "origin": counts("origin"),
            "family": counts("family"),
            "issuer": counts("issuer"),
        }

    def family_counts(self) -> tuple[int, dict[str, int]]:
        metadata_row = self.connection.execute(
            "SELECT value FROM corpus_meta WHERE key = 'document_count'"
        ).fetchone()
        if metadata_row is None:
            raise RuntimeError("Corpus metadata is missing document_count")
        rows = self.connection.execute(
            """
            SELECT COALESCE(family, '(null)') AS family, COUNT(*) AS count
            FROM documents
            GROUP BY family
            """
        ).fetchall()
        return (
            int(json.loads(metadata_row["value"])),
            {str(row["family"]): int(row["count"]) for row in rows},
        )
