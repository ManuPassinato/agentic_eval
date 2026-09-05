from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from agentic_eval.domain import (
    AttemptResult,
    FailureKind,
    HarnessCapabilities,
    QuestionCase,
    RunSpec,
    TraceEvent,
)
from agentic_eval.harnesses.base import EventSink

_URL_RE = re.compile(r"https?://[^\s<>\[\](){}\"']+")
_DOCUMENT_ID_RE = re.compile(r"\baneel-[0-9a-f]{20}\b")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _session_id(event: dict[str, Any]) -> str | None:
    properties = event.get("properties") or {}
    return (
        properties.get("sessionID")
        or properties.get("sessionId")
        or event.get("sessionID")
        or event.get("sessionId")
    )


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("type") or event.get("event") or "unknown")


def _extract_urls(value: Any) -> set[str]:
    if isinstance(value, str):
        return {
            match.rstrip(".,;:!?)]}")
            for match in _URL_RE.findall(value)
            if match.rstrip(".,;:!?)]}")
        }
    if isinstance(value, dict):
        return set().union(*(_extract_urls(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_extract_urls(item) for item in value), set())
    return set()


def _is_idle_event(event: dict[str, Any]) -> bool:
    kind = _event_type(event)
    if kind == "session.idle":
        return True
    status = (event.get("properties") or {}).get("status") or {}
    return kind == "session.status" and status.get("type") == "idle"


def _is_tool_event(event: TraceEvent) -> bool:
    if "tool" in event.kind:
        return True
    part = (event.payload.get("properties") or {}).get("part") or {}
    return event.kind == "message.part.updated" and part.get("type") == "tool"


def _tool_part(event: TraceEvent) -> dict[str, Any]:
    part = (event.payload.get("properties") or {}).get("part") or {}
    return part if isinstance(part, dict) and part.get("type") == "tool" else {}


def _tool_name(event: TraceEvent) -> str | None:
    part = _tool_part(event)
    name = part.get("tool") or part.get("name")
    return str(name) if name else None


def _tool_call_id(event: TraceEvent) -> str | None:
    part = _tool_part(event)
    value = part.get("callID") or part.get("callId") or part.get("id")
    return str(value) if value else None


def _extract_document_ids(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_DOCUMENT_ID_RE.findall(value))
    if isinstance(value, dict):
        return set().union(*(_extract_document_ids(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_extract_document_ids(item) for item in value), set())
    return set()


def _extract_finish_answer(event: TraceEvent) -> str | None:
    name = _tool_name(event)
    if not name or not name.endswith("finish"):
        return None
    part = _tool_part(event)
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    for candidate in (state.get("output"), state.get("input"), part.get("output"), part.get("input")):
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError:
                continue
        if isinstance(candidate, dict) and candidate.get("answer"):
            return str(candidate["answer"]).strip() or None
    return None


def _classify_runtime_error(error: str) -> FailureKind:
    lowered = error.lower()
    if "tool step limit" in lowered:
        return FailureKind.STEP_LIMIT
    if "malformed" in lowered and ("tool" in lowered or "json" in lowered):
        return FailureKind.MALFORMED_TOOL_CALL
    if "aneel" in lowered and ("tool" in lowered or "mcp" in lowered or "corpus" in lowered):
        return FailureKind.CORPUS_TOOL_FAILURE
    if any(tool in lowered for tool in ("websearch", "webfetch", "web search", "web fetch")):
        return FailureKind.WEB_TOOL_FAILURE
    if any(term in lowered for term in ("provider", "model", "completion", "context length")):
        return FailureKind.MODEL_ERROR
    return FailureKind.HARNESS_CRASH


def _extract_answer(messages: Any) -> tuple[str | None, dict[str, Any]]:
    if isinstance(messages, dict):
        messages = messages.get("messages") or messages.get("data") or [messages]
    if not isinstance(messages, list):
        return None, {}
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        info = message.get("info") or {}
        role = info.get("role") or message.get("role")
        if role != "assistant":
            continue
        texts: list[str] = []
        for part in message.get("parts") or []:
            if isinstance(part, dict) and part.get("type") in {"text", "reasoning"}:
                text = part.get("text")
                if text and part.get("type") == "text":
                    texts.append(str(text))
        if not texts and isinstance(message.get("content"), str):
            texts.append(message["content"])
        answer = "\n".join(texts).strip()
        if answer:
            usage = info.get("tokens") or info.get("usage") or {}
            return answer, usage if isinstance(usage, dict) else {}
    return None, {}


class OpenCodeWorker:
    def __init__(
        self,
        run_spec: RunSpec,
        runtime_root: Path,
        worker_index: int,
        attach_url: str | None = None,
    ) -> None:
        self.run_spec = run_spec
        self.worker_id = f"opencode-{worker_index:02d}"
        self.root = runtime_root / self.worker_id
        self.workspace = self.root / "workspace"
        self.shared_cache = Path.home() / ".cache" / "agentic-eval" / "opencode"
        self.attach_url = attach_url
        self.process: asyncio.subprocess.Process | None = None
        self._log_handle: Any = None
        self.password = secrets.token_urlsafe(24)
        self.base_url = attach_url
        self._auth: httpx.BasicAuth | None = None
        self.client: httpx.AsyncClient | None = None

    def _native_config(self) -> dict[str, Any]:
        model = self.run_spec.model
        permission = {"*": "deny"}
        permission.update({tool: "deny" for tool in self.run_spec.harness.tool_policy.deny})
        permission.update({tool: "allow" for tool in self.run_spec.harness.tool_policy.allow})
        config: dict[str, Any] = {
            "$schema": "https://opencode.ai/config.json",
            "model": f"{model.provider_id}/{model.model_id}",
            "small_model": f"{model.provider_id}/{model.model_id}",
            "enabled_providers": [model.provider_id],
            "permission": permission,
            "provider": {
                model.provider_id: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Evaluation vLLM",
                    "options": {
                        "baseURL": model.base_url.rstrip("/"),
                        "apiKey": "{env:AGENTIC_EVAL_VLLM_API_KEY}",
                        "timeout": int(model.request_timeout_seconds * 1000),
                        "chunkTimeout": int(model.chunk_timeout_seconds * 1000),
                    },
                    "models": {
                        model.model_id: {
                            "name": model.model_id,
                            "limit": {
                                "context": model.context_limit,
                                "output": model.output_limit,
                            },
                        }
                    },
                }
            },
        }
        environment = self.run_spec.environment
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
        return config

    async def start(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.workspace.mkdir(parents=True, exist_ok=True)
            self.shared_cache.mkdir(parents=True, exist_ok=True)
            auth: httpx.BasicAuth | None = None
            if self.attach_url is None:
                port = _free_port(self.run_spec.harness.host)
                self.base_url = f"http://{self.run_spec.harness.host}:{port}"
                config_path = self.root / "opencode.json"
                config_path.write_text(
                    json.dumps(self._native_config(), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                env = os.environ.copy()
                for key in (
                    "OPENCODE_CONFIG_CONTENT",
                    "OPENCODE_CONFIG_DIR",
                    "OPENCODE_PERMISSION",
                ):
                    env.pop(key, None)
                env.update(
                    {
                        "AGENTIC_EVAL_VLLM_API_KEY": (
                            self.run_spec.model.api_key.get_secret_value()
                        ),
                        "OPENCODE_CONFIG": str(config_path),
                        "OPENCODE_SERVER_PASSWORD": self.password,
                        "XDG_CONFIG_HOME": str(self.root / "xdg-config"),
                        "XDG_DATA_HOME": str(self.root / "xdg-data"),
                        "XDG_CACHE_HOME": str(self.shared_cache),
                    }
                )
                if self.run_spec.environment.kind == "web":
                    env["OPENCODE_ENABLE_EXA"] = "1"
                else:
                    env.pop("OPENCODE_ENABLE_EXA", None)
                log_path = self.root / "opencode.log"
                self._log_handle = log_path.open("ab")
                self.process = await asyncio.create_subprocess_exec(
                    self.run_spec.harness.binary,
                    "serve",
                    "--hostname",
                    self.run_spec.harness.host,
                    "--port",
                    str(port),
                    cwd=self.workspace,
                    env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=self._log_handle,
                    stderr=asyncio.subprocess.STDOUT,
                )
                auth = httpx.BasicAuth("opencode", self.password)
            elif self.run_spec.harness.attach_password is not None:
                auth = httpx.BasicAuth(
                    self.run_spec.harness.attach_username,
                    self.run_spec.harness.attach_password.get_secret_value(),
                )
            self._auth = auth
            self.client = httpx.AsyncClient(
                base_url=self.base_url,
                auth=auth,
                timeout=httpx.Timeout(30),
            )
            deadline = time.monotonic() + self.run_spec.harness.startup_timeout_seconds
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                if self.process and self.process.returncode is not None:
                    raise RuntimeError(
                        f"{self.worker_id} exited during startup with {self.process.returncode}"
                    )
                try:
                    await self.health()
                    return
                except (httpx.HTTPError, RuntimeError) as exc:
                    last_error = exc
                    await asyncio.sleep(0.2)
            raise TimeoutError(
                f"{self.worker_id} did not become healthy; last error: {last_error}"
            )
        except BaseException:
            await self.stop()
            raise

    async def health(self) -> dict[str, object]:
        if not self.client:
            raise RuntimeError("worker is not started")
        response = await self.client.get("/global/health", timeout=2)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("healthy") is False:
            raise RuntimeError(f"unhealthy OpenCode worker: {data}")
        return data if isinstance(data, dict) else {"response": data}

    async def _events_until_idle(
        self,
        session_id: str,
        event_sink: EventSink,
        ready: asyncio.Event,
    ) -> None:
        if not self.client or not self.base_url:
            raise RuntimeError("worker is not started")
        async with httpx.AsyncClient(
            base_url=self.base_url,
            auth=self._auth,
            headers={"x-opencode-directory": str(self.workspace)},
            timeout=httpx.Timeout(30, read=None),
        ) as event_client, event_client.stream("GET", "/event") as response:
            response.raise_for_status()
            ready.set()
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                    continue
                if line or not data_lines:
                    continue
                raw = "\n".join(data_lines)
                data_lines.clear()
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event_session_id = _session_id(event)
                if event_session_id is not None and event_session_id != session_id:
                    continue
                kind = _event_type(event)
                await event_sink(TraceEvent(kind=kind, session_id=session_id, payload=event))
                if event_session_id == session_id and _is_idle_event(event):
                    return
                if kind in {"session.error", "server.error"}:
                    raise RuntimeError(json.dumps(event, ensure_ascii=False))

    async def _abort(self, session_id: str) -> None:
        if not self.client:
            return
        try:
            await self.client.post(
                f"/session/{session_id}/abort",
                headers={"x-opencode-directory": str(self.workspace)},
            )
        except httpx.HTTPError:
            pass

    async def _delete(self, session_id: str) -> None:
        if not self.client:
            return
        try:
            await self.client.delete(
                f"/session/{session_id}",
                headers={"x-opencode-directory": str(self.workspace)},
            )
        except httpx.HTTPError:
            pass

    async def _terminate_process(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            wait_task = asyncio.create_task(self.process.wait())
            done, _ = await asyncio.wait(
                {wait_task},
                timeout=self.run_spec.harness.shutdown_timeout_seconds,
            )
            if not done and self.process.returncode is None:
                self.process.kill()
                await wait_task
        self.process = None

    async def execute(
        self,
        case: QuestionCase,
        prompt: str,
        attempt: int,
        timeout_seconds: float,
        event_sink: EventSink,
    ) -> AttemptResult:
        if not self.client:
            raise RuntimeError("worker is not started")
        started_at = _now()
        started = time.monotonic()
        session_id: str | None = None
        event_task: asyncio.Task[None] | None = None
        operation_task: asyncio.Task[
            tuple[str | None, dict[str, Any], list[str], FailureKind, str | None]
        ] | None = None
        observed_sources: set[str] = set()
        observed_document_ids: set[str] = set()
        seen_tool_calls: set[str] = set()
        finish_answer: str | None = None
        timed_out = False

        async def capture(event: TraceEvent) -> None:
            nonlocal finish_answer
            if _is_tool_event(event):
                observed_sources.update(_extract_urls(event.payload))
                observed_document_ids.update(_extract_document_ids(event.payload))
                call_id = _tool_call_id(event) or json.dumps(
                    _tool_part(event),
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                )
                if (
                    self.run_spec.environment.kind == "aneel_corpus"
                    and call_id not in seen_tool_calls
                ):
                    seen_tool_calls.add(call_id)
                    if len(seen_tool_calls) > self.run_spec.environment.max_tool_steps:
                        raise RuntimeError(
                            "tool step limit exceeded: "
                            f"{len(seen_tool_calls)} > "
                            f"{self.run_spec.environment.max_tool_steps}"
                        )
                finish_answer = _extract_finish_answer(event) or finish_answer
            await event_sink(event)

        async def run_attempt() -> tuple[str | None, dict[str, Any], list[str], FailureKind, str | None]:
            nonlocal event_task, session_id
            response = await self.client.post(
                "/session",
                json={"title": f"eval:{case.id}:attempt:{attempt}"},
                headers={"x-opencode-directory": str(self.workspace)},
            )
            response.raise_for_status()
            session = response.json()
            session_id = str(session.get("id") or session.get("sessionID"))
            if not session_id or session_id == "None":
                raise RuntimeError(f"OpenCode returned no session id: {session}")

            ready = asyncio.Event()
            event_task = asyncio.create_task(self._events_until_idle(session_id, capture, ready))
            await asyncio.wait_for(ready.wait(), timeout=10)
            prompt_response = await self.client.post(
                f"/session/{session_id}/prompt_async",
                json={"parts": [{"type": "text", "text": prompt}]},
                headers={"x-opencode-directory": str(self.workspace)},
            )
            prompt_response.raise_for_status()
            await asyncio.wait_for(event_task, timeout=timeout_seconds)

            messages_response = await self.client.get(
                f"/session/{session_id}/message",
                headers={"x-opencode-directory": str(self.workspace)},
            )
            messages_response.raise_for_status()
            messages = messages_response.json()
            await event_sink(
                TraceEvent(kind="session.messages", session_id=session_id, payload={"data": messages})
            )
            assistant_answer, usage = _extract_answer(messages)
            final_answer = finish_answer or assistant_answer
            failure = FailureKind.SUCCESS if final_answer else FailureKind.MISSING_ANSWER
            sources = sorted(
                observed_sources
                .union(observed_document_ids)
                .union(_extract_urls(final_answer or ""))
            )
            error = None if final_answer else "No assistant text found in completed session"
            if (
                self.run_spec.environment.kind == "aneel_corpus"
                and case.id != "qualification"
            ):
                if not seen_tool_calls:
                    failure = FailureKind.CORPUS_TOOL_FAILURE
                    error = "Closed-corpus case completed without an ANEEL tool call"
                elif finish_answer is None:
                    failure = FailureKind.MISSING_ANSWER
                    error = "Closed-corpus case completed without calling finish"
            return final_answer, usage, sources, failure, error

        try:
            operation_task = asyncio.create_task(run_attempt())
            done, _ = await asyncio.wait({operation_task}, timeout=timeout_seconds)
            if not done:
                raise asyncio.TimeoutError
            final_answer, usage, sources, failure, error = await operation_task
        except asyncio.TimeoutError:
            timed_out = True
            if operation_task:
                operation_task.cancel()
            if event_task:
                event_task.cancel()
            if session_id:
                abort_task = asyncio.create_task(self._abort(session_id))
                await asyncio.wait({abort_task}, timeout=5)
                abort_task.cancel()
            if self.attach_url is None:
                await self._terminate_process()
            final_answer, usage, sources = None, {}, []
            failure, error = FailureKind.TIMEOUT, f"case exceeded {timeout_seconds}s"
        except asyncio.CancelledError:
            if operation_task:
                operation_task.cancel()
            if event_task:
                event_task.cancel()
            if session_id:
                abort_task = asyncio.create_task(self._abort(session_id))
                await asyncio.shield(asyncio.wait({abort_task}, timeout=5))
                abort_task.cancel()
            raise
        except httpx.HTTPStatusError as exc:
            final_answer, usage, sources = None, {}, []
            body = exc.response.text[:2000]
            failure = FailureKind.MODEL_ERROR if exc.response.status_code < 500 else FailureKind.HARNESS_CRASH
            error = f"HTTP {exc.response.status_code}: {body}"
        except RuntimeError as exc:
            final_answer, usage, sources = (
                None,
                {},
                sorted(observed_sources.union(observed_document_ids)),
            )
            error = str(exc)
            failure = _classify_runtime_error(error)
        except (httpx.HTTPError, OSError) as exc:
            final_answer, usage, sources = None, {}, []
            failure, error = FailureKind.HARNESS_CRASH, str(exc)
        finally:
            if event_task and not event_task.done():
                event_task.cancel()
            pending_tasks = {
                task
                for task in (operation_task, event_task)
                if task is not None and not task.done()
            }
            if pending_tasks:
                await asyncio.wait(pending_tasks, timeout=2)
            if session_id and not timed_out:
                delete_task = asyncio.create_task(self._delete(session_id))
                await asyncio.wait({delete_task}, timeout=5)
                delete_task.cancel()

        finished_at = _now()
        return AttemptResult(
            case_id=case.id,
            attempt=attempt,
            failure_kind=failure,
            final_answer=final_answer,
            sources=sources,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=time.monotonic() - started,
            session_id=session_id,
            usage=usage,
            error=error,
        )

    async def stop(self) -> None:
        await self._terminate_process()
        if self.client:
            close_task = asyncio.create_task(self.client.aclose())
            await asyncio.wait({close_task}, timeout=2)
            close_task.cancel()
            self.client = None
        self._auth = None
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None


class OpenCodeAdapter:
    name = "opencode"
    capabilities = HarnessCapabilities(
        web_search=True,
        streaming=True,
        structured_output=True,
        usage_reporting=True,
        cancellation=True,
    )

    def __init__(self, run_spec: RunSpec, runtime_root: Path) -> None:
        self.run_spec = run_spec
        self.runtime_root = runtime_root
        self._workers: list[OpenCodeWorker] = []

    async def _binary_version(self) -> str:
        binary = self.run_spec.harness.binary
        if not shutil.which(binary) and not Path(binary).exists():
            raise RuntimeError(f"OpenCode binary not found: {binary}")
        process = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        if process.returncode:
            raise RuntimeError(f"Unable to get OpenCode version: {output.decode().strip()}")
        version = output.decode().strip()
        expected = self.run_spec.harness.version
        if expected and expected not in version:
            raise RuntimeError(f"Expected OpenCode {expected}, found {version}")
        return version

    async def _vllm_health(self) -> dict[str, Any]:
        model = self.run_spec.model
        headers = {"Authorization": f"Bearer {model.api_key.get_secret_value()}"}
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            response = await client.get(f"{model.base_url.rstrip('/')}/models")
            response.raise_for_status()
            data = response.json()
        ids = {entry.get("id") for entry in data.get("data", [])}
        if model.model_id not in ids:
            raise RuntimeError(f"Model {model.model_id!r} not returned by vLLM; available={ids}")
        return {"model_id": model.model_id, "available_models": sorted(item for item in ids if item)}

    async def qualify(self, run_live_case: bool = True) -> dict[str, object]:
        version = await self._binary_version()
        vllm = await self._vllm_health()
        corpus_summary: dict[str, Any] | None = None
        if self.run_spec.environment.kind == "aneel_corpus":
            from agentic_eval.environments.aneel import AneelCorpus

            database_path = self.run_spec.environment.database_path
            if database_path is None:
                raise RuntimeError("ANEEL corpus database path is not configured")
            with AneelCorpus(database_path) as corpus:
                stats = corpus.stats()
            corpus_summary = {
                "database": str(database_path),
                "document_count": stats["document_count"],
                "revision": stats.get("revision"),
            }
        worker = await self.create_worker(-1)
        try:
            health = await worker.health()
            result: dict[str, object] = {
                "opencode_version": version,
                "opencode_health": health,
                "vllm": vllm,
            }
            if corpus_summary is not None:
                result["corpus"] = corpus_summary
            if run_live_case:
                events: list[TraceEvent] = []

                async def capture(event: TraceEvent) -> None:
                    events.append(event)

                case = QuestionCase(id="qualification", question=self.run_spec.task.qualification_question)
                attempt = await worker.execute(
                    case,
                    self.run_spec.task.render_qualification(),
                    1,
                    min(self.run_spec.timeout_seconds, 120),
                    capture,
                )
                if not attempt.successful:
                    raise RuntimeError(
                        f"OpenCode live qualification failed: {attempt.failure_kind}: {attempt.error}"
                    )
                if not any(_is_tool_event(event) for event in events):
                    raise RuntimeError("Live qualification completed without an observable tool event")
                if self.run_spec.environment.kind == "aneel_corpus" and not any(
                    "aneel" in (_tool_name(event) or "").lower()
                    for event in events
                ):
                    raise RuntimeError(
                        "Corpus qualification completed without an observable ANEEL tool event"
                    )
                result["live_case"] = attempt.model_dump(mode="json")
            return result
        finally:
            await worker.stop()

    async def create_worker(self, worker_index: int) -> OpenCodeWorker:
        attach_url = None
        if self.run_spec.harness.mode == "attach":
            try:
                attach_url = self.run_spec.harness.attach_urls[worker_index]
            except IndexError as exc:
                raise ValueError("attach mode requires one attach_url per worker") from exc
        worker = OpenCodeWorker(self.run_spec, self.runtime_root, worker_index, attach_url)
        await worker.start()
        self._workers.append(worker)
        return worker

    async def close(self) -> None:
        await asyncio.gather(*(worker.stop() for worker in self._workers), return_exceptions=True)
        self._workers.clear()
