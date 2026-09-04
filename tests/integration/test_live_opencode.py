import os
from pathlib import Path

import pytest

from agentic_eval.config import load_run_spec
from agentic_eval.harnesses import create_adapter


@pytest.mark.skipif(
    not os.getenv("AGENTIC_EVAL_LIVE"),
    reason="Set AGENTIC_EVAL_LIVE=1 to run against OpenCode and vLLM",
)
async def test_live_stack_qualification(tmp_path):
    config = Path(os.getenv("AGENTIC_EVAL_CONFIG", "configs/run.example.yaml"))
    spec = load_run_spec(config)
    adapter = create_adapter(spec, tmp_path)
    try:
        result = await adapter.qualify(run_live_case=True)
    finally:
        await adapter.close()
    assert result["live_case"]["failure_kind"] == "success"
