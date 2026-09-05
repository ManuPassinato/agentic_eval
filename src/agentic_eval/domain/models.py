from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FailureKind(str, Enum):
    SUCCESS = "success"
    QUALIFICATION_FAILURE = "qualification_failure"
    TIMEOUT = "timeout"
    HARNESS_CRASH = "harness_crash"
    MODEL_ERROR = "model_error"
    MALFORMED_TOOL_CALL = "malformed_tool_call"
    WEB_TOOL_FAILURE = "web_tool_failure"
    CORPUS_TOOL_FAILURE = "corpus_tool_failure"
    STEP_LIMIT = "step_limit"
    MISSING_ANSWER = "missing_answer"
    CANCELLATION = "cancellation"


class QuestionCase(BaseModel):
    id: str
    question: str
    reference_answer: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = None

    @field_validator("id", "question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class DatasetSpec(BaseModel):
    path: Path
    id_field: str = "id"
    question_field: str = "question"
    reference_answer_field: str | None = "reference_answer"
    tags_field: str | None = "tags"
    metadata_field: str | None = "metadata"
    timeout_field: str | None = "timeout_seconds"


class ModelSpec(BaseModel):
    provider_id: str = "vllm"
    model_id: str
    base_url: str
    api_key: SecretStr = SecretStr("not-required")
    context_limit: int = 32768
    output_limit: int = 8192
    request_timeout_seconds: float = 600
    chunk_timeout_seconds: float = 120


class ToolPolicy(BaseModel):
    allow: list[str] = Field(default_factory=lambda: ["websearch", "webfetch"])
    deny: list[str] = Field(
        default_factory=lambda: ["bash", "edit", "write", "read", "glob", "grep"]
    )


class HarnessSpec(BaseModel):
    name: str = "opencode"
    mode: str = "managed"
    binary: str = "opencode"
    version: str | None = None
    workers: int = 1
    host: str = "127.0.0.1"
    startup_timeout_seconds: float = 30
    shutdown_timeout_seconds: float = 5
    attach_urls: list[str] = Field(default_factory=list)
    attach_username: str = "opencode"
    attach_password: SecretStr | None = None
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in {"managed", "attach"}:
            raise ValueError("mode must be 'managed' or 'attach'")
        return value


class EnvironmentSpec(BaseModel):
    kind: Literal["web", "aneel_corpus"] = "web"
    database_path: Path | None = None
    manifest_path: Path | None = None
    server_name: str = "aneel"
    max_tool_steps: int = Field(default=10, ge=1)
    families: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def corpus_paths_required(self) -> EnvironmentSpec:
        if self.kind == "aneel_corpus" and self.database_path is None:
            raise ValueError("database_path is required for an aneel_corpus environment")
        if self.kind == "web" and self.families:
            raise ValueError("families can only be selected for an aneel_corpus environment")
        if self.families:
            from agentic_eval.environments.aneel.families import FAMILY_BY_SLUG

            if len(set(self.families)) != len(self.families):
                raise ValueError("environment families must be unique")
            unknown = sorted(set(self.families) - set(FAMILY_BY_SLUG))
            if unknown:
                raise ValueError(f"unknown ANEEL family slugs: {unknown}")
        return self


class TrustedDomain(BaseModel):
    domain: str
    path_prefix: str = "/"
    description: str | None = None

    @field_validator("domain")
    @classmethod
    def valid_domain(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if not value or "://" in value or "/" in value:
            raise ValueError("domain must be a hostname without scheme or path")
        return value

    @field_validator("path_prefix")
    @classmethod
    def valid_path_prefix(cls, value: str) -> str:
        value = value.strip() or "/"
        return value if value.startswith("/") else f"/{value}"


class SeedSource(BaseModel):
    id: str
    url: str
    description: str | None = None
    topics: list[str] = Field(default_factory=list)
    always_include: bool = False

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an absolute HTTP(S) URL")
        return value


class SourceProfile(BaseModel):
    id: str
    description: str
    mode: Literal["preferred"] = "preferred"
    trusted_domains: list[TrustedDomain]
    sources: list[SeedSource] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)


class SourceMatch(BaseModel):
    url: str
    preferred: bool
    matched_domain: str | None = None
    source_id: str | None = None


class TaskSpec(BaseModel):
    system_prompt: str
    question_template: str = "{question}"
    source_profile_path: Path | None = None
    source_profile: SourceProfile | None = None
    qualification_system_prompt: str = (
        "You must call the websearch tool before answering. A response without a "
        "websearch tool call fails qualification. Answer the question directly after "
        "the search."
    )
    qualification_question: str = (
        "Use web search to identify the official Python website and answer with its URL."
    )

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

    def render_qualification(self) -> str:
        return (
            f"{self.qualification_system_prompt.strip()}\n\n"
            f"{self.qualification_question.strip()}"
        )


class RunSpec(BaseModel):
    name: str = "web-qa"
    dataset: DatasetSpec
    model: ModelSpec
    harness: HarnessSpec = Field(default_factory=HarnessSpec)
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    task: TaskSpec
    output_dir: Path = Path("runs")
    concurrency: int = 1
    timeout_seconds: float = 300
    infrastructure_retries: int = 1
    case_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    shard_index: int = 0
    shard_count: int = 1
    max_tool_payload_bytes: int = 1_000_000

    @field_validator("shard_count")
    @classmethod
    def positive_shards(cls, value: int) -> int:
        if value < 1:
            raise ValueError("shard_count must be positive")
        return value

    @field_validator("shard_index")
    @classmethod
    def nonnegative_shard(cls, value: int) -> int:
        if value < 0:
            raise ValueError("shard_index must be nonnegative")
        return value


class HarnessCapabilities(BaseModel):
    web_search: bool
    streaming: bool
    structured_output: bool
    usage_reporting: bool
    cancellation: bool


class TraceEvent(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    kind: str
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    original_sha256: str | None = None


class AttemptResult(BaseModel):
    case_id: str
    attempt: int
    failure_kind: FailureKind
    final_answer: str | None = None
    sources: list[str] = Field(default_factory=list)
    source_matches: list[SourceMatch] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    session_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    retry_class: str = "none"

    @property
    def successful(self) -> bool:
        return self.failure_kind == FailureKind.SUCCESS

    @property
    def infrastructure_failure(self) -> bool:
        return self.failure_kind in {
            FailureKind.TIMEOUT,
            FailureKind.HARNESS_CRASH,
            FailureKind.CANCELLATION,
        }
