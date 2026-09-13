# Codex CLI harness

Research snapshot: 5 September 2026.

Source pin: OpenAI Codex `main` at
[`7dc7c7a`](https://github.com/openai/codex/tree/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0).
Live documentation URLs are unversioned and can drift from that commit.

This note records how Codex CLI builds context, designs prompts, exposes
tools, and runs its agent loop. Claims are tagged as **verified**,
**inference**, or **unavailable**.

This repository reserves `codex` in
[`src/agentic_eval/harnesses/codex.py`](../src/agentic_eval/harnesses/codex.py)
and [`src/agentic_eval/harnesses/registry.py`](../src/agentic_eval/harnesses/registry.py).
No Codex adapter is implemented.

## Model and provider abstraction

**Verified.** Codex is a Rust event-driven agent runtime with a
`ModelProvider` trait for provider metadata, capabilities,
authentication, model catalogs, error mapping, and preferred auxiliary
models. `ConfiguredModelProvider` covers OpenAI-compatible configured
providers. Amazon Bedrock has a dedicated implementation.

Custom providers configure base URL, wire API (`responses` or Chat
Completions), authentication, headers, and retries. Built-in OSS mode
supports Ollama and LM Studio.

Capabilities gate namespace tools, image generation, web search, external
web access, and remote compaction. Remote compaction is enabled
selectively, principally for OpenAI and Azure Responses, not uniformly
across providers.

**Inference.** Codex is provider-pluggable, not protocol-neutral. Its
internal data model follows OpenAI Responses items, tool calls,
reasoning, and streaming semantics. “Model-agnostic” here means adapter
portability, not arbitrary-backend compatibility.

## Context assembly and compaction

**Verified.** Each request contains base instructions, model-visible tool
specifications, an output schema, and ordered history.
`parallel_tool_calls` is enabled.

Initial context is assembled from developer-policy and capability
fragments plus contextual user fragments. Durable “world state” sections
include model, environment, permissions, tools, plugins/apps, AGENTS
instructions, personality, collaboration mode, and context-window
guidance.

World-state snapshots are persisted. Later turns emit only changed
sections using snapshot comparison and RFC 7386-style merge patches.
Turn-local skill, plugin, hook, environment, and user-input fragments
can also be added.

Automatic compaction triggers at the model or configured token
threshold. Providers that support it use remote compaction; others
perform local summarization. Compacted history gets the canonical
initial context re-injected before the last real user message or
summary. `/compact` provides manual compaction. `compact_prompt` and
`model_auto_compact_token_limit` are configurable.

**Inference.** This is a state-synchronization layer over conversation
history: unchanged runtime context is not resent, but governing state
survives compaction and resume.

## Prompt roles and instruction hierarchy

**Verified.** Base instructions resolve in this order: explicit config
override, persisted session metadata, then the selected model’s
instruction template or personality rendering.

For normal Responses requests, base instructions use the API’s
top-level `instructions` field. Responses Lite prepends them as a
developer fragment. Additional policy, permission, tool, collaboration,
and configured instructions are developer-role messages.

AGENTS and project guidance is encoded as a contextual user-role
message headed `# AGENTS.md instructions`, separate from the actual
user task. The task remains ordinary user input.

Instruction-file discovery:

1. Global: `$CODEX_HOME/AGENTS.override.md`, otherwise `AGENTS.md`.
2. Project: project root through the current directory.
3. Per directory: `AGENTS.override.md`, then `AGENTS.md`, then configured
   fallback filenames. At most one file per directory.
4. Root-first concatenation: nearer files appear later and take
   precedence.
5. Empty files are skipped. Default aggregate limit is 32 KiB
   (`project_doc_max_bytes`).

`model_instructions_file` can replace built-in instructions. The
`instructions` config key is reserved; prefer `AGENTS.md` or
`model_instructions_file`.

## Agent loop

**Verified.** One turn:

1. Capture turn and world-state context and append user input.
2. Build tools and submit a streaming model request.
3. Convert streamed Responses items into messages, reasoning, or tool
   calls.
4. Parse function, custom, shell, hosted, or search calls through
   `ToolRouter`.
5. Dispatch handlers through `ToolRegistry`. Tool futures may run
   concurrently; results keep deterministic ordering.
6. Record tool-call outputs into history and sample again.
7. Stop when the model no longer requests follow-up and no queued user
   input remains. Compact or retry when required.

The loop consumes `ResponseEvent` variants such as creation, item-added,
item-completed, deltas, errors, and completion. `end_turn=false`, tool
output, or pending input causes another sampling cycle.

## Built-in tools

**Verified, configuration-dependent.** Tools are assembled per step from
core handlers, MCP, hosted model tools, extensions, and dynamic
registrations. There is no single fixed universal tool list.

Typical surfaces include:

- Command execution and resumable stdin.
- `apply_patch`, image viewing, planning, user-input and permission
  requests.
- MCP resource listing, template listing, and read.
- Hosted or standalone web search and image generation.
- Multi-agent spawn, message, wait, resume, interrupt, and list
  operations.
- Context-window and token-budget, current-time and sleep, environment
  readiness, plugin discovery and install, tool search, and dynamically
  supplied tools.

## Permissions and sandboxing

**Verified.** Sandbox and approval policy are independent controls.

Sandbox modes: `read-only`, `workspace-write`, and
`danger-full-access`. Network is disabled by default in
`workspace-write`.

Approval policies: `untrusted`, `on-request`, `never`, or granular per
prompt category. Reviewers may be the user or an automatic reviewer.
Auto-review does not enlarge the sandbox.

OS enforcement:

- macOS: Seatbelt.
- Linux and WSL2: bubblewrap with namespaces, read-only root bindings,
  writable-root overlays, `no_new_privs`, and seccomp network
  filtering. Legacy Landlock remains available.
- Native Windows: restricted-token and AppContainer mechanisms.

MCP and app tools can require approval from annotations or configured
approval mode. Destructive annotations require approval even when
conflicting read-only hints exist.

**Inference.** Prompted permission guidance is advisory. The security
boundary is the tool broker plus OS sandbox and approval state.

## MCP

**Verified.** Codex acts as an MCP client over stdio or Streamable HTTP,
with bearer tokens and OAuth. It exposes MCP tools and resources in the
same registry as internal tools. It supports deferred exposure and tool
search, startup requirements, per-server and per-tool approval modes,
elicitations, and resource reads.

`required=true` makes startup or resume fail if the server cannot
initialize.

Codex can also run as an MCP server (`codex mcp-server`), exposing
session-start and session-reply tools.

## Sessions, events, and observability

**Verified.** The local thread store is documented in source as rollout
JSONL plus SQLite metadata. Ephemeral threads remain memory-only.

Resume reconstructs history, world-state baselines, token usage,
compaction windows, and retained settings. Threads can be forked from
all or bounded history.

App-server exposes JSON-RPC operations including `thread/start`,
`thread/resume`, `thread/fork`, `turn/start`, steer, interrupt, and
compact. Live notifications include `turn/started`, `item/started`,
deltas, tool progress, `item/completed`, token updates, and
`turn/completed`.

Core’s typed `EventMsg` additionally covers reasoning, command, patch,
and MCP lifecycle, approvals, hooks, warnings, environment state, and
usage.

OpenTelemetry export is opt-in. It reports conversations, API, SSE, and
WebSocket activity, prompts, tool decisions and results, counters,
histograms, and traces. Prompt content is redacted unless explicitly
enabled.

## Implications for this benchmark

- Codex can talk to OpenAI-compatible and selected OSS backends, but a
  fair adapter must pin wire API (`responses` vs Chat Completions),
  capability flags, and whether remote compaction is active.
- `AGENTS.md` is user-role context, not system prompt. A closed-corpus
  run must isolate `$CODEX_HOME` and the worker workspace so host
  instruction files do not leak into cases.
- World-state snapshots and compaction re-injection are part of
  reproducibility. Resume and fork reconstruct more than raw chat
  history.
- Tool inventory is dynamic. Capture the request-visible tool list per
  attempt rather than assuming a fixed Codex tool surface.
- Sandbox and approval are separate from tool existence. Deny-by-default
  evaluation still needs an OS sandbox and a non-interactive approval
  policy (`never` or granular auto-approve of the allowed MCP tools
  only).
- App-server JSON-RPC plus typed `EventMsg` is the natural mapping onto
  this repository’s `TraceEvent` and `AttemptResult` contract.
- The reserved `CAPABILITIES` in `codex.py` currently advertise
  `web_search=True` and `structured_output=False`. Confirm both against
  the chosen provider before implementing the adapter.

## Sources

Official documentation:

- [Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced)
- [MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Sandboxing](https://learn.chatgpt.com/docs/sandboxing)
- [Agent approvals and security](https://learn.chatgpt.com/docs/agent-approvals-security)

Commit-pinned source (`7dc7c7a`):

- [Provider implementation](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/model-provider/src/provider.rs)
- [Session context assembly](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/core/src/session/mod.rs)
- [World state](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/core/src/context/world_state/mod.rs)
- [History manager](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/core/src/context_manager/history.rs)
- [Compaction](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/core/src/compact.rs)
- [AGENTS.md discovery](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/core/src/agents_md.rs)
- [Turn loop](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/core/src/session/turn.rs)
- [Tool plan](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/core/src/tools/spec_plan.rs)
- [App-server protocol](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/app-server/README.md)
- [Core events](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/protocol/src/protocol.rs)
- [Linux sandbox notes](https://github.com/openai/codex/blob/7dc7c7a7566a970f6d4d09e1384f854aebaf39e0/codex-rs/linux-sandbox/README.md)

## What this note cannot verify

The exact production base-instruction templates, compaction summarizer
prompt, model-routing heuristics, and any unpublished safety
classifiers are not independently inspectable from public docs plus the
pinned commit’s public source. Treat those as **unavailable**.
