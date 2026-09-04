"""Codex adapter extension point.

Implement the HarnessAdapter contract in this module. Keep Codex-specific
sandbox, process, and event semantics here; return only normalized domain
objects to the runner.
"""

from agentic_eval.domain import HarnessCapabilities

CAPABILITIES = HarnessCapabilities(
    web_search=True,
    streaming=True,
    structured_output=False,
    usage_reporting=True,
    cancellation=True,
)
