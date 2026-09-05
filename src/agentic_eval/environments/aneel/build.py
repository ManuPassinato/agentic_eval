from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .download import REPO_ID, REVISION, file_sha256

_ACT_NUMBER = re.compile(
    r"\b(?:N[º°o.]?|NÚMERO)\s*[:.]?\s*([0-9][0-9./-]{0,20})",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_SPACE = re.compile(r"\s+")


def document_id(blob_path: str) -> str:
    return f"aneel-{hashlib.sha256(blob_path.encode('utf-8')).hexdigest()[:20]}"


def derive_title(text: str, blob_path: str) -> str:
    for raw_line in text[:4000].splitlines():
        line = _SPACE.sub(" ", raw_line).strip(" -\t")
        if len(line) >= 8:
            return line[:300]
    return Path(blob_path).stem[:300]


def derive_act_identity(
    text: str,
    blob_path: str,
    family: str | None,
) -> tuple[str | None, int | None]:
    sample = text[:6000]
    number_match = _ACT_NUMBER.search(sample)
    number = number_match.group(1).strip("./-") if number_match else None
    years = [int(value) for value in _YEAR.findall(sample[:2500])]
    year = years[0] if years else None
    if number is None:
        stem = Path(blob_path).stem
        compact = re.search(r"(?:19|20)(\d{2})(\d{1,6})(?:[a-z]+)?$", stem, re.IGNORECASE)
        if compact:
            year = year or int(compact.group(0)[:4])
            number = str(int(compact.group(2)))
    if family is None and number is None:
        return None, year
    return number, year


def _json_list(value: Any) -> str:
    return json.dumps(list(value or []), ensure_ascii=False)


def _rows_from_parquet(paths: Iterable[Path], batch_size: int = 512) -> Iterable[dict[str, Any]]:
    try:
        from pyarrow import parquet
    except ImportError as exc:
        raise RuntimeError(
            "Corpus dependencies are missing; run `uv sync --extra corpus --extra dev`"
        ) from exc
    for path in paths:
        parquet_file = parquet.ParquetFile(path)
        for batch in parquet_file.iter_batches(batch_size=batch_size):
            yield from batch.to_pylist()


_SCHEMA = """
CREATE TABLE corpus_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE documents (
    rowid INTEGER PRIMARY KEY,
    document_id TEXT NOT NULL UNIQUE,
    source_idx INTEGER NOT NULL UNIQUE,
    bucket_name TEXT NOT NULL,
    blob_path TEXT NOT NULL UNIQUE,
    format TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    text_length INTEGER NOT NULL,
    origin TEXT NOT NULL,
    categories_json TEXT NOT NULL,
    category_paths_json TEXT NOT NULL,
    family TEXT,
    prefix TEXT,
    role TEXT,
    issuer TEXT,
    act_number TEXT,
    act_year INTEGER
);
CREATE INDEX documents_family_idx ON documents(family);
CREATE INDEX documents_issuer_idx ON documents(issuer);
CREATE INDEX documents_role_idx ON documents(role);
CREATE INDEX documents_act_idx ON documents(act_year, act_number);
CREATE VIRTUAL TABLE documents_fts USING fts5(
    title,
    text,
    content='documents',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
"""


def build_corpus(
    raw_directory: Path,
    output_path: Path,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    parquet_paths = sorted((raw_directory / "data").glob("train-*.parquet"))
    if not parquet_paths:
        raise FileNotFoundError(f"No train Parquet shards found under {raw_directory / 'data'}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)

    origin_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    issuer_counts: Counter[str] = Counter()
    count = 0
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;"
        )
        connection.executescript(_SCHEMA)
        insert = """
            INSERT INTO documents (
                document_id, source_idx, bucket_name, blob_path, format, title, text,
                text_length, origin, categories_json, category_paths_json, family,
                prefix, role, issuer, act_number, act_year
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        for row in _rows_from_parquet(parquet_paths):
            text = str(row["text"])
            blob_path = str(row["blob_path"])
            family = row.get("family")
            act_number, act_year = derive_act_identity(text, blob_path, family)
            connection.execute(
                insert,
                (
                    document_id(blob_path),
                    int(row["idx"]),
                    str(row["bucket_name"]),
                    blob_path,
                    str(row["format"]),
                    derive_title(text, blob_path),
                    text,
                    len(text),
                    str(row["origin"]),
                    _json_list(row.get("categories")),
                    _json_list(row.get("category_paths")),
                    family,
                    row.get("prefix"),
                    row.get("role"),
                    row.get("issuer"),
                    act_number,
                    act_year,
                ),
            )
            count += 1
            origin_counts[str(row["origin"])] += 1
            family_counts[str(family or "(null)")] += 1
            issuer_counts[str(row.get("issuer") or "(null)")] += 1
            if count % 5000 == 0:
                connection.commit()
            if limit is not None and count >= limit:
                break

        connection.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
        metadata = {
            "schema_version": 1,
            "repo_id": REPO_ID,
            "revision": REVISION,
            "document_count": count,
            "distributions": {
                "origin": dict(origin_counts.most_common()),
                "family": dict(family_counts.most_common()),
                "issuer": dict(issuer_counts.most_common()),
            },
        }
        connection.executemany(
            "INSERT INTO corpus_meta(key, value) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    except BaseException:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        connection.close()

    temporary.replace(output_path)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repo_id": REPO_ID,
        "revision": REVISION,
        "database": str(output_path.resolve()),
        "database_bytes": output_path.stat().st_size,
        "database_sha256": file_sha256(output_path),
        "document_count": count,
        "limited": limit is not None,
        "source_shards": [
            {
                "path": str(path.relative_to(raw_directory)),
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
            for path in parquet_paths
        ],
        "distributions": {
            "origin": dict(origin_counts.most_common()),
            "family": dict(family_counts.most_common()),
            "issuer": dict(issuer_counts.most_common()),
        },
    }
    manifest_path = output_path.with_suffix(".manifest.json")
    manifest_tmp = manifest_path.with_suffix(".json.tmp")
    manifest_tmp.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest_tmp.replace(manifest_path)
    return manifest
