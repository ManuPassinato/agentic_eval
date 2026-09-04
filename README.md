# Agentic Eval

A trace-first evaluation pipeline for running web-search QA datasets through
multiple agent harnesses. The first adapter manages isolated OpenCode workers;
vLLM remains an external OpenAI-compatible model server.

## Requirements

- Python 3.10+
- `uv`
- A pinned `opencode` executable
- A running vLLM server with tool calling enabled for the selected model

## Quick start

```bash
cp .env.example .env
uv sync --extra dev
source .env
uv run agentic-eval doctor --config configs/run.example.yaml
uv run agentic-eval run --config configs/run.example.yaml
```

Start with one case, then a small pilot, before scheduling the full dataset.
Change `dataset.path` and its field mapping in the run configuration for your
500-question JSONL file.

## Commands

- `doctor`: validate the OpenCode binary, vLLM model, web tools, and a live case.
- `run`: create a new immutable run directory.
- `resume RUN_DIR`: continue only unfinished cases after checking the dataset hash.
- `status RUN_DIR`: summarize ledger states.
- `export RUN_DIR`: write flat JSONL and CSV summaries from normalized results.

## Preferred source profiles

Tasks can point to a harness-neutral YAML profile:

```yaml
task:
  source_profile_path: sources/aneel.yaml
```

The profile adds trusted domains and relevant discovery pages to every harness
prompt while still allowing broader web search. Sources marked
`always_include` are always shown; other seed pages are included only when
their topics match the question's JSONL `tags`.

The included `configs/sources/aneel.yaml` treats Brazilian electrical systems
as the default domain, prioritizes official ANEEL locations, and uses the ANEEL
organization page as a discovery hub rather than a presumed answer. Result
artifacts classify captured URLs as preferred or unmatched, but do not score
answer quality.

## Artifact model

Each run records:

- `manifest.json`: dataset hash, source paths, and redacted resolved configuration.
- `dataset.jsonl`: immutable input snapshot.
- `source-profile.yaml`: exact preferred-source profile snapshot, when configured.
- `qualification.json`: preflight evidence.
- `ledger.sqlite3`: transactional case and attempt state.
- `attempts/<case>/`: one atomic raw trace and normalized result per attempt.
- `runtime/`: isolated OpenCode worker state and process logs.
- `exports/`: generated JSONL and CSV summaries.

No answer scoring is performed in v1. Raw traces and plain final answers remain
the source of truth for later deterministic or judge-based evaluation.

See [docs/architecture.md](docs/architecture.md) for process isolation, failure
semantics, and instructions for adding another harness.
