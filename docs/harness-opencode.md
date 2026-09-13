# OpenCode harness

Research snapshot: 5 September 2026.

Implementation pin: OpenCode `v1.18.21`, tag commit
[`826d9ad`](https://github.com/anomalyco/opencode/commit/826d9ad46a22bef0294998e08daa3c4904fea28f),
release [v1.18.21](https://github.com/anomalyco/opencode/releases/tag/v1.18.21).
This repository pins the same version in
[`configs/harnesses/opencode.yaml`](../configs/harnesses/opencode.yaml)
and `doctor` rejects a different binary.

Current [opencode.ai docs](https://opencode.ai/docs/agents/) are
unversioned and already drift from the tag (for example they document a
`scout` subagent that 1.18.21 does not ship). Benchmark claims must cite
the tag, not live docs.

This note covers native OpenCode behavior and how
[`src/agentic_eval/harnesses/opencode.py`](../src/agentic_eval/harnesses/opencode.py)
uses it. Claims are tagged as **verified (1.18.21)**, **repository
observation**, **inference**, or **live-docs drift**.

## Model and provider abstraction

**Verified (1.18.21).** OpenCode normalizes providers and models into
internal records containing provider and model IDs, AI SDK package and
API model ID, endpoint, headers and options, context/input/output
limits, pricing, capabilities, and variants.

The initial catalog comes from Models.dev. Config-defined models merge
over catalog entries. Providers activate from environment credentials,
stored auth, config, plugins, or provider-specific loaders.
`enabled_providers` is an allowlist. `disabled_providers` removes
providers.

AI SDK implementations are bundled for major providers. Unknown or
custom models default to `@ai-sdk/openai-compatible`. Language-model
instances are cached by provider, AI SDK package, and resolved options.
Model and agent options are merged before each request; model variants
merge last.

**Repository observation.** The adapter registers vLLM as a custom
OpenAI-compatible provider, sets both `model` and `small_model` to
`{provider_id}/{model_id}`, and supplies explicit context and output
limits. That avoids Models.dev for the evaluated model. All LLM traffic
goes through OpenCode; the runner never calls the model directly.

## Context assembly and compaction

**Verified (1.18.21).** Each normal turn constructs context in this
order:

1. Agent-specific prompt, if configured; otherwise a model-family
   OpenCode prompt.
2. Environment metadata: exact model ID, working directory, workspace
   root, git status, platform, and current date.
3. Instruction files, including `AGENTS.md`.
4. MCP server instructions.
5. Available skill descriptions.
6. Structured-output instruction, if requested.
7. Per-message `system` override.
8. Filtered session history and current user content.
9. Tool definitions and schemas in a separate request field.

Plugins can transform system prompts, messages, schemas, parameters, and
headers.

Compaction:

- Triggers automatically from reported token usage at the usable-context
  threshold.
- Uses a hidden `compaction` agent with no tools.
- Summarizes older history and retains a recent tail bounded by 25% of
  usable context, clamped to 2,000–15,000 tokens.
- Prunes old tool outputs only after accumulating 40,000 protected
  tokens and at least 20,000 removable tokens. `skill` output is
  protected.
- Individual tool outputs are independently truncated at 2,000 lines or
  50 KiB, with full output saved externally.

For this benchmark’s 20,480 context and 2,048 output limit, the
effective overflow threshold is approximately 18,432 tokens and the
default retained recent-tail budget is 4,608 tokens.

**Repository observation.** `TaskSpec.system_prompt` is concatenated
into the ordinary text submitted in `parts` and does **not** populate
OpenCode’s `system` request field. Benchmark instructions therefore sit
in the user role beneath OpenCode’s native system prompt and any
AGENTS/config instructions.

```188:198:src/agentic_eval/domain/models.py
    def render(self, case: QuestionCase, *, include_source_profile: bool = True) -> str:
        sections = [self.system_prompt.strip()]
        if include_source_profile and self.source_profile:
            from agentic_eval.sources import render_source_guidance

            sections.append(render_source_guidance(self.source_profile, case))
        sections.append(
            "Question to answer:\n"
            f"{self.question_template.format(question=case.question).strip()}"
        )
        return "\n\n".join(sections)
```

```478:481:src/agentic_eval/harnesses/opencode.py
            prompt_response = await self.client.post(
                f"/session/{session_id}/prompt_async",
                json={"parts": [{"type": "text", "text": prompt}]},
```

The empty per-worker workspace and isolated `XDG_CONFIG_HOME` prevent
ordinary host `AGENTS.md` and global-config leakage.

## Prompt roles and instruction hierarchy

**Verified (1.18.21).** Provider or model-family prompts are selected by
API model ID. An agent’s explicit prompt replaces that provider prompt.

Global instructions consider `~/.config/opencode/AGENTS.md`, then Claude
compatibility. Project discovery prefers `AGENTS.md`, then `CLAUDE.md`,
then deprecated `CONTEXT.md`. `config.instructions` adds local globs or
HTTP(S) content; remote retrieval has a five-second timeout.

Reading files can dynamically attach nested instruction files near those
files once per assistant message. OpenCode prefixes loaded content with
its source path.

Default primary agents in 1.18.21: `build` (full tools) and `plan`
(restricted). Built-in subagents: `general` and `explore`. Hidden
agents: `compaction`, `title`, and `summary`.

**Live-docs drift.** Current docs also list a `scout` subagent. The
1.18.21 tagged agent registry does not include it.

**Repository observation.** Managed workers use the default `build`
agent. Qualification and case prompts are rendered by `TaskSpec` and
sent as user text. Source profiles are task configuration, not OpenCode
skills.

## Agent loop

**Verified (1.18.21).** The loop:

1. Loads compacted history.
2. Handles pending subtask or compaction operations.
3. Resolves agent, model, permissions, tools, instructions, and history.
4. Streams through AI SDK by default. An experimental native runtime can
   replace it.
5. Normalizes reasoning, text, tool-input, tool-call, tool-result,
   usage, finish, and provider-error events into persisted message
   parts.
6. Executes tools through typed JSON schemas.
7. Feeds tool results back into the next model iteration.
8. Stops on a terminal finish with no outstanding tool calls,
   error/denial, structured result, cancellation, or step limit.
9. Continues on `tool-calls` and `unknown`.
10. Detects three identical repeated calls through `doom_loop`
    permission.

Iterations are unlimited unless an agent `steps` limit is configured.
The 1.18.21 release continues when a provider reports an unknown finish
reason.

**Repository observation.** The adapter’s ten-call limit is external: it
counts observed SSE tool-call IDs and fails the attempt. OpenCode itself
remains unbounded. The worker-level deadline and forced process restart
are necessary because the default agent loop does not stop on its own.

Each case gets a new session titled `eval:{case.id}:attempt:{attempt}`.
The adapter waits for SSE idle, then fetches `/session/{id}/message` and
extracts the answer. Idle alone is not treated as proof of success.

Closed-corpus cases fail unless they call an ANEEL tool and
`aneel_finish`. Qualification skips that enforcement.

## Built-in tools

**Verified (1.18.21).** Tagged registry includes:

- `bash`, `read`, `glob`, `grep`
- `edit`, `write`, or model-dependent `apply_patch`
- `task`
- `webfetch`, and conditionally available `websearch`
- `todowrite`
- `skill`
- optional `question`
- experimental `lsp`, code-mode execution, and plan-transition tools
- internal `invalid`, used to repair malformed or unknown calls
- MCP tools and MCP resource operations

For GPT-family models, `apply_patch` can replace `edit` and `write`.
Web search availability depends on provider or Exa/Parallel flags.

**Repository observation.** Open-web runs set `OPENCODE_ENABLE_EXA=1`
and allow only `websearch` and `webfetch`. Closed-corpus runs unset Exa
and allow only `aneel_*`. Built-ins remain in the binary; a catch-all
deny plus a later allow rule removes them from the model-visible
schema.

## Permissions and sandboxing

**Verified (1.18.21).** Actions are `allow`, `ask`, or `deny`. Rules use
wildcard matching; the last matching rule wins. No match defaults to
`ask`, but built-in agent defaults broadly allow tools.

Agent, global, and session rules are concatenated, so later layers
override earlier ones. A catch-all wildcard denial can remove tools from
the model-visible schema entirely.

Runtime `ask` emits a permission event and blocks on a deferred
response. Replies are `once`, `always`, or `reject`. Remembered
approvals last for the running instance.

`edit` gates `edit`, `write`, and `apply_patch`. MCP tools use their
generated tool IDs directly (`server_tool`).

OpenCode’s permission layer is in-process policy, not an OS sandbox
comparable to Codex Seatbelt or Claude `bubblewrap`.

**Repository observation.** `_native_config()` writes `"*": "deny"`,
then explicit denies, then allows. The closed-corpus order
(`"*": "deny"` then `"aneel_*": "allow"`) is correct because the later
specific rule wins.

```192:196:src/agentic_eval/harnesses/opencode.py
        permission = {"*": "deny"}
        permission.update({tool: "deny" for tool in self.run_spec.harness.tool_policy.deny})
        permission.update({tool: "allow" for tool in self.run_spec.harness.tool_policy.allow})
```

Interactive `ask` would hang a headless run. The adapter therefore uses
only `allow` and `deny`.

## MCP

**Verified (1.18.21).** OpenCode supports:

- Local stdio servers with command, arguments, cwd, environment, enable
  flag, and timeout.
- Remote Streamable HTTP with SSE fallback.
- OAuth, dynamic client registration, stored credentials, and explicit
  OAuth disablement.
- Tool, prompt, resource, resource-template, server-instruction, and
  roots capabilities.
- Paginated tool discovery.
- Tool naming as sanitized `server_tool`.
- JSON Schema adaptation and per-call timeout that resets on progress.
- Text, image, and selected binary resource results, with a 10 MiB
  attachment cap.

**Repository observation.** Closed-corpus workers launch one local
Python MCP process per OpenCode worker:

```226:243:src/agentic_eval/harnesses/opencode.py
        if environment.kind == "aneel_corpus":
            command = [
                sys.executable,
                "-m",
                "agentic_eval.environments.aneel.mcp_server",
                "--db",
                str(environment.database_path),
            ]
            for family in environment.families:
                command.extend(["--family", family])
            config["mcp"] = {
                environment.server_name: {
                    "type": "local",
                    "command": command,
                    "enabled": True,
                    "timeout": 30_000,
                }
            }
```

The server name `aneel` produces tools such as
`aneel_search_resolucao_normativa` and `aneel_finish`. Default inventory
is 90 tools (29 families × 3 plus 3 shared). Public-task runs narrow
that to 6 via `--family resolucao_normativa`. Full schemas are visible
up front; there is no Codex/Claude-style deferred `ToolSearch`.

## Sessions, events, and observability

**Verified (1.18.21).** OpenCode is client/server internally.
`opencode serve` exposes an authenticated HTTP API.

Relevant operations: create, get, list, delete, and fork sessions;
synchronous message or asynchronous `prompt_async`; fetch complete
message and part history; abort, summarize/compact, revert, share, and
inspect status; respond to permission requests; subscribe to `/event`
SSE.

Instance state is routed by `directory`, `x-opencode-directory`, or
session directory. Basic authentication uses
`OPENCODE_SERVER_PASSWORD`.

The event stream starts with `server.connected`, emits `type` plus
`properties`, adds ten-second heartbeats, filters by directory, emits
`session.status` and a separate `session.idle` event, and terminates
when the instance is disposed.

**Repository observation.** The adapter:

- Spawns a managed `opencode serve` (or attaches to an existing URL).
- Isolates config, XDG dirs, empty workspace, and logs under
  `{run_dir}/runtime/opencode-{NN}/`.
- Streams `/event` until idle or error, writing normalized `TraceEvent`
  JSONL.
- Extracts `finish` answers first, then last assistant text (skipping
  reasoning).
- Collects URLs and `aneel-*` document IDs as provenance.
- Deletes the session after a completed attempt and terminates the
  process on timeout.

Workers are reused across cases and restarted after infrastructure
failures. One case runs at a time per worker.

## Implications for this benchmark

- Reproducibility must include the OpenCode tag, native prompt family,
  provider package versions, generated tool schemas, permissions,
  config/environment, AGENTS.md state, and compaction settings—not only
  model ID and task prompt.
- Rename or separately represent `TaskSpec.system_prompt`. It currently
  has user-role authority under OpenCode’s native system prompt.
- Capture the exact request-visible tool inventory and schema-token size
  per run. The 90-tool closed corpus is part of the evaluated harness,
  not just corpus access overhead.
- Record compaction and truncation events. Later turns may silently use
  summaries or cleared tool outputs.
- Prefer typed OpenAPI or SDK models, or pin event fixtures. The adapter
  already tolerates multiple event and session ID shapes.
- Keep one case per fresh session. Reusing sessions would contaminate
  history, remembered permission approvals, compaction, and model
  choice.
- Continue using `prompt_async` plus SSE and explicit message retrieval.
- Keep server authentication and `x-opencode-directory`; both are
  isolation boundaries.
- OpenCode is the only implemented adapter today and the most
  model-portable of the three studied harnesses, because custom
  OpenAI-compatible providers are first-class.

## Sources

Version-pinned:

- [v1.18.21 release](https://github.com/anomalyco/opencode/releases/tag/v1.18.21)
- [Tag commit 826d9ad](https://github.com/anomalyco/opencode/commit/826d9ad46a22bef0294998e08daa3c4904fea28f)
- [Provider](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/provider/provider.ts)
- [LLM request](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/session/llm/request.ts)
- [System prompt assembly](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/session/system.ts)
- [Instruction discovery](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/session/instruction.ts)
- [Prompt loop](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/session/prompt.ts)
- [Compaction](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/session/compaction.ts)
- [Overflow](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/session/overflow.ts)
- [Tool registry](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/tool/registry.ts)
- [Permissions](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/permission/index.ts)
- [MCP](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/mcp/index.ts)
- [Default native prompt](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.21/packages/opencode/src/session/prompt/default.txt)

Live docs (may drift):

- [Agents](https://opencode.ai/docs/agents/)
- [Permissions](https://opencode.ai/docs/permissions/)
- [Models](https://opencode.ai/docs/models/)
- [MCP servers](https://opencode.ai/docs/mcp-servers/)
- [Server](https://opencode.ai/docs/server/)
- [Rules](https://opencode.ai/docs/rules/)

Local adapter:

- [`src/agentic_eval/harnesses/opencode.py`](../src/agentic_eval/harnesses/opencode.py)
- [`src/agentic_eval/harnesses/base.py`](../src/agentic_eval/harnesses/base.py)
- [`docs/architecture.md`](architecture.md)

## What this note cannot verify

Exact default prompt text for every model family, plugin-transformed
request payloads at runtime, and future event-schema changes after
1.18.21 are **unavailable** unless captured from a live worker’s
`opencode.json` and traces.
