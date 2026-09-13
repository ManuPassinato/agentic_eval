# Claude Code harness

Research snapshot: 5 September 2026.

Official documentation is unversioned. The production Claude Code
executable is proprietary. The public
[anthropics/claude-code](https://github.com/anthropics/claude-code)
repository holds plugins, documentation links, and issue tracking, not
the harness implementation. The
[Python Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)
exposes wrapper source and bundles the Claude Code executable.

This note records how Claude Code builds context, designs prompts,
exposes tools, and runs its agent loop. Claims are tagged as
**verified**, **inference**, or **unavailable**.

This repository reserves `claude_code` in
[`src/agentic_eval/harnesses/claude_code.py`](../src/agentic_eval/harnesses/claude_code.py)
and [`src/agentic_eval/harnesses/registry.py`](../src/agentic_eval/harnesses/registry.py).
No Claude Code adapter is implemented.

## Model and provider abstraction

**Verified.** Claude Code is a harness around Claude models, not a
model-neutral orchestration layer. Anthropic states that it does not
support routing Claude Code to non-Claude models.

Supported backends include Anthropic’s API and subscriptions, Amazon
Bedrock, Google Cloud’s Agent Platform / Vertex integration, Microsoft
Foundry, Claude Platform on AWS, and compatible gateways. Cloud-specific
model identifiers map to Claude model families. Custom gateways must
expose a supported Anthropic-compatible API and route Claude model
names.

The Agent SDK launches a bundled Claude Code native executable. It is
not an independent reimplementation of the loop.

**Inference.** The architecture is a Claude-specific orchestration
runtime over an Anthropic Messages-style tool protocol.

## Context assembly and compaction

**Verified.** Each request’s working context accumulates:

- System prompt and tool definitions.
- CLAUDE.md, rules, and auto memory.
- User and assistant conversation.
- Tool inputs and results.
- Loaded skills and MCP definitions.
- File contents and command output returned by tools.

Stable prefixes are automatically prompt-cached. MCP schemas are
normally deferred: Claude initially sees tool names and server
instructions, while full schemas load through `ToolSearch` when needed.

Near the model limit, Claude Code:

1. Clears older tool outputs.
2. Summarizes older conversation history if more space is needed.
3. Re-injects persistent material.

Project-root CLAUDE.md, unscoped rules, auto memory, and eligible skill
bodies survive compaction. Nested CLAUDE.md and path-scoped rules reload
when matching files are read. Conversation-only instructions can be
lost.

`/compact`, `/autocompact`, `PreCompact`, `PostCompact`, and `/context`
expose control or visibility. A `SessionStart` hook with a `compact`
matcher can re-inject critical context after compaction. If a single
file or tool output is so large that context refills immediately after
each summary, auto-compaction stops after a few attempts instead of
looping.

Sessions start with a fresh context window. Auto memory and CLAUDE.md
are the durable cross-session channels, not prior conversation history.

## Prompt roles and instruction hierarchy

**Verified.** The built-in `claude_code` system-prompt preset contains
tool guidance, coding and formatting conventions, response style,
security guidance, and dynamic environment information such as working
directory, OS, shell, git status, and memory paths.

The CLI and SDK can preserve and append to the default prompt, replace
it entirely, or move dynamic sections into the first user message for
better cross-machine cache reuse.

CLAUDE.md is project context, not part of the system prompt, and is
advisory rather than enforcement.

Loading order:

1. Managed organization instructions.
2. User `~/.claude/CLAUDE.md`.
3. Ancestor and project files, ordered filesystem root → working
   directory.
4. `CLAUDE.local.md` after `CLAUDE.md` at each directory.
5. Descendant instructions on demand when files there are read.

All discovered files are concatenated. There is no hard
conflict-resolution or semantic precedence mechanism. Imports use
`@path`, with up to four recursive hops. `AGENTS.md` is not read
automatically but can be imported.

Auto memory loads the first 200 lines or 25 KB of `MEMORY.md`, whichever
comes first, at session start.

The `InstructionsLoaded` hook fires when a CLAUDE.md or
`.claude/rules/*.md` file is loaded.

## Agent loop

**Verified.** Documented loop:

1. Combine prompt, system and context material, tools, and history.
2. Claude returns text and/or one or more tool-use blocks.
3. The harness evaluates permissions and hooks, executes approved tools,
   and returns results as user or tool-result messages.
4. Repeat until Claude emits no tool calls.
5. Emit a terminal result containing status, text where successful,
   token usage, cost, turn count, stop reason, and session ID.

Multiple tool calls may execute in parallel. Rejected calls return a
rejection result to Claude, allowing it to retry or choose another
approach.

The SDK streams `SystemMessage`, `AssistantMessage`, `UserMessage`, raw
stream events, and `ResultMessage`.

Operators can interrupt with `Esc` or queue a correction without
stopping the current tool. Those interactive controls are part of the
product loop and would need a non-interactive policy in a benchmark
adapter.

## Built-in tools

**Verified, configuration-dependent.** Stable core tools include `Read`,
`Edit`, `Write`, `Glob`, `Grep`, `Bash`, `PowerShell`, `NotebookEdit`,
`WebFetch`, `WebSearch`, `LSP`, `AskUserQuestion`, and plan/worktree
controls.

Orchestration and runtime tools include:

- `Agent`, `Workflow`.
- `TaskCreate`, `TaskGet`, `TaskList`, `TaskUpdate`, `TaskStop`.
- `Monitor`.
- `ToolSearch`, `WaitForMcpServers`.
- `ListMcpResourcesTool`, `ReadMcpResourceTool`.
- `Skill`, `SendMessage`, `ListAgents`.
- Session, scheduling, reporting, artifact, and remote-delivery tools.

Availability depends on model, interface, configuration, feature
rollout, authentication, and provider. `TodoWrite` is disabled by
default in favor of the newer Task tools.

Background subagents keep MCP tools but only a reduced built-in set
(`Read`, `Grep`, `Glob`, and related read-only tools). Explore and Plan
are special one-shot agents that omit CLAUDE.md and git status.

## Permissions and sandboxing

**Verified.** Permission rules support `deny`, `ask`, and `allow`. Deny
is evaluated first. Modes include Manual/default, Accept Edits, Plan,
Auto classifier mode, `dontAsk`, and `bypassPermissions`.

The sandbox is separate from permissions:

- Permissions govern whether a tool may run.
- The OS sandbox constrains Bash and child processes after launch.
- Filesystem and network restrictions are OS-enforced.
- Unsandboxed retries can return to the permission flow unless disabled.
- Sandbox availability is macOS, Linux, and WSL2. Linux uses
  `bubblewrap`.

Hooks run outside the model context and can inspect, modify, block, or
react to lifecycle events. Important events include `UserPromptSubmit`,
`PreToolUse`, `PermissionRequest`, `PostToolUse`, `PostToolBatch`,
`PreCompact`, `PostCompact`, `Stop`, session events, and subagent
events.

A `PreToolUse` denial can block execution even under
`bypassPermissions` or `--dangerously-skip-permissions`.

File checkpoints snapshot contents before edits and are separate from
git. They do not cover remote side effects.

## MCP

**Verified.** Claude Code supports MCP tools, resources, and prompts
over local stdio servers and remote HTTP servers, with OAuth for remote
services. SSE transport is deprecated. Scopes include project, user,
local, and managed. The Agent SDK can host in-process MCP servers.

Tool schemas are deferred through `ToolSearch` by default. Some
gateways, providers, or older models disable this, causing complete
schemas to consume context on every request.

MCP tools are named with a `mcp__` prefix. Plugin-provided servers use
a scoped segment that includes the plugin name. Hook matchers must use
the same scoped name.

## Sessions, events, and observability

**Verified.** Sessions are locally persisted JSONL conversations under
`~/.claude/projects/`, tied to a project directory. Resume continues the
same history. Branch and fork copy conversation history under a new
session ID. Session persistence does not snapshot the entire
filesystem; file checkpointing is separate.

Normal subagents have fresh isolated context and their own system
prompt. They receive a delegation prompt, tools, and usually project
context. They do not receive parent conversation history or the parent
system prompt. They return only their final result.

Forked subagents inherit the parent’s conversation, prompt, model, and
tools, but their intermediate work remains isolated.

Experimental agent teams are independent Claude Code instances with
separate contexts, shared task state, and mailbox messaging.

Observability:

- Optional OpenTelemetry metrics (sessions, tokens, cost, active time,
  code changes, tool decisions) and structured events.
- Beta traces for interactions, model requests, tools, and hooks.
- Prompt text, tool inputs, and file content are excluded by default
  and require explicit content-export settings.
- The SDK exposes per-run messages, token usage, cost, session IDs,
  tool progress, hook events, and task notifications.
- Operational inspection includes `--debug`, `/status`, `/context`,
  `/mcp`, and cost/usage results.

## Implications for this benchmark

- Claude Code is not a candidate for a model-agnostic harness. It can
  evaluate Claude models only. Use it as a Claude-specific baseline, not
  as the shared adapter.
- The production loop lives in a proprietary binary. An adapter should
  treat the Agent SDK or CLI as the integration surface and must not
  claim to reimplement the loop.
- Default system prompt, CLAUDE.md, auto memory, and organization
  instructions all enter context. A closed-corpus run must isolate
  `~/.claude`, disable memory, and replace or append a benchmark-owned
  system prompt.
- Deferred MCP schemas via `ToolSearch` change the token cost of a
  90-tool corpus versus OpenCode, which exposes full schemas up front.
  Record whether deferred loading is active.
- Compaction can drop conversation-only instructions. Put the closed
  contract (evidence IDs, `finish`, no external tools) in CLAUDE.md or a
  surviving hook, not only in the first user message.
- Hooks can enforce deny-by-default even when the user permission mode
  is bypassed. That is the strongest deterministic control for a
  benchmark adapter.
- Subagents and agent teams create hidden extra context windows. A
  comparable evaluation should disable them unless the task explicitly
  measures delegation.
- The reserved `CAPABILITIES` in `claude_code.py` currently advertise
  `web_search=True` and `structured_output=False`. Confirm both against
  the chosen Claude model and settings before implementing the adapter.

## Sources

Official documentation:

- [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)
- [Extend Claude Code](https://docs.anthropic.com/en/docs/claude-code/features-overview)
- [Context window](https://code.claude.com/docs/en/context-window)
- [Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)
- [Modify system prompts](https://code.claude.com/docs/en/agent-sdk/modifying-system-prompts)
- [CLAUDE.md](https://code.claude.com/docs/en/claude-md)
- [Complete tools reference](https://code.claude.com/docs/en/tools-reference)
- [Settings and permissions](https://code.claude.com/docs/en/settings)
- [Sandboxing](https://code.claude.com/docs/en/sandboxing)
- [Hooks](https://code.claude.com/docs/en/hooks)
- [Hooks guide](https://docs.anthropic.com/en/docs/claude-code/hooks-guide)
- [MCP](https://code.claude.com/docs/en/mcp)
- [Sessions](https://code.claude.com/docs/en/sessions)
- [Subagents](https://code.claude.com/docs/en/subagents)
- [Create custom subagents](https://docs.anthropic.com/en/docs/claude-code/sub-agents)
- [Monitoring](https://code.claude.com/docs/en/monitoring-usage/)
- [Model configuration](https://code.claude.com/docs/en/model-config)
- [LLM gateways](https://code.claude.com/docs/en/llm-gateway)

Related source:

- [Python Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)
- [Public Claude Code repository](https://github.com/anthropics/claude-code)

## What this note cannot verify

The exact production system prompt, compaction summarizer prompt,
model-routing heuristics, internal safety classifiers, scheduler
implementation, and complete wire protocol are **unavailable**.
Anthropic documents external behavior; it does not publish the CLI
engine source.
