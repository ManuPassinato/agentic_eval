from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ID = "cemig-ceia/biblioteca-aneel-categorizado-4"
REVISION = "6d32fdfc85d7f9b007085a50afe059fbb90e8b49"
REQUIRED_FILES = (
    "README.md",
    "coverage_audit_v4.json",
    "page_observations_v4.json",
    "relevant_acts_snapshot.json",
    "scripts/relevant_acts.json",
    "scripts/categorize_aneel_v4.py",
    "scripts/normative_parser.py",
    "data/train-00000-of-00003.parquet",
    "data/train-00001-of-00003.parquet",
    "data/train-00002-of-00003.parquet",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_download(directory: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    missing: list[str] = []
    for relative in REQUIRED_FILES:
        path = directory / relative
        if not path.is_file():
            missing.append(relative)
            continue
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    return {
        "repo_id": REPO_ID,
        "revision": REVISION,
        "directory": str(directory.resolve()),
        "complete": not missing,
        "missing": missing,
        "files": files,
        "total_bytes": sum(item["bytes"] for item in files),
    }


def download_dataset(directory: Path, *, token: str | None = None) -> dict[str, Any]:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "Corpus dependencies are missing; run `uv sync --extra corpus --extra dev`"
        ) from exc

    directory.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        revision=REVISION,
        local_dir=directory,
        allow_patterns=list(REQUIRED_FILES),
        token=token,
    )
    audit = audit_download(directory)
    if not audit["complete"]:
        raise RuntimeError(f"Dataset download incomplete: {audit['missing']}")
    audit_path = directory / "download-manifest.json"
    temporary = audit_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(audit_path)
    return audit
