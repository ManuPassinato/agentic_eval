from __future__ import annotations

from pathlib import Path

from agentic_eval.domain import RunSpec
from agentic_eval.harnesses.base import HarnessAdapter


def create_adapter(spec: RunSpec, runtime_root: Path) -> HarnessAdapter:
    if spec.harness.name == "opencode":
        from agentic_eval.harnesses.opencode import OpenCodeAdapter

        return OpenCodeAdapter(spec, runtime_root)
    if spec.harness.name in {"claude_code", "codex"}:
        raise NotImplementedError(
            f"{spec.harness.name} is reserved but not implemented; add a HarnessAdapter"
        )
    raise ValueError(f"Unknown harness {spec.harness.name!r}")
