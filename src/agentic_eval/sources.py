from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import yaml

from agentic_eval.domain import (
    QuestionCase,
    SeedSource,
    SourceMatch,
    SourceProfile,
    TrustedDomain,
)


def load_source_profile(path: Path) -> SourceProfile:
    with path.open("r", encoding="utf-8") as handle:
        return SourceProfile.model_validate(yaml.safe_load(handle) or {})


def select_seed_sources(profile: SourceProfile, case: QuestionCase) -> list[SeedSource]:
    case_topics = {tag.strip().lower() for tag in case.tags}
    return [
        source
        for source in profile.sources
        if source.always_include
        or bool(case_topics.intersection(topic.lower() for topic in source.topics))
    ]


def _domain_target(domain: TrustedDomain) -> str:
    suffix = domain.path_prefix.rstrip("/")
    return f"https://{domain.domain}{suffix}"


def render_source_guidance(profile: SourceProfile, case: QuestionCase) -> str:
    domains = "\n".join(
        f"- {_domain_target(domain)}"
        + (f": {domain.description}" if domain.description else "")
        for domain in profile.trusted_domains
    )
    selected_sources = select_seed_sources(profile, case)
    source_lines = "\n".join(
        f"- {source.url}" + (f": {source.description}" if source.description else "")
        for source in selected_sources
    )
    instructions = "\n".join(f"- {instruction}" for instruction in profile.instructions)
    sections = [
        "Preferred source profile",
        profile.description.strip(),
        "Prioritize these official locations before broader web search:",
        domains,
    ]
    if source_lines:
        sections.extend(
            [
                "Useful discovery pages for this question:",
                source_lines,
                (
                    "You must call the websearch tool to investigate the question before "
                    "answering, even though discovery URLs are provided. Treat discovery "
                    "pages as navigation hints, not as presumed answers."
                ),
            ]
        )
    if instructions:
        sections.extend(["Research instructions:", instructions])
    sections.append(
        "This is a preference, not a restriction. Use other reliable web sources when "
        "the preferred sources are insufficient, and cite the primary sources used."
    )
    return "\n".join(section for section in sections if section)


def _matches_domain(url: str, trusted: TrustedDomain) -> bool:
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if hostname != trusted.domain and not hostname.endswith(f".{trusted.domain}"):
        return False
    prefix = trusted.path_prefix.rstrip("/")
    if not prefix:
        return True
    path = parsed.path.rstrip("/")
    return path == prefix or path.startswith(f"{prefix}/")


def _same_source(url: str, source: SeedSource) -> bool:
    try:
        candidate = urlparse(url)
        expected = urlparse(source.url)
        candidate_hostname = (candidate.hostname or "").lower()
        expected_hostname = (expected.hostname or "").lower()
    except ValueError:
        return False
    return (
        candidate.scheme.lower(),
        candidate_hostname,
        candidate.path.rstrip("/"),
    ) == (
        expected.scheme.lower(),
        expected_hostname,
        expected.path.rstrip("/"),
    )


def classify_sources(urls: list[str], profile: SourceProfile | None) -> list[SourceMatch]:
    if profile is None:
        return []
    matches: list[SourceMatch] = []
    for url in urls:
        seed = next((source for source in profile.sources if _same_source(url, source)), None)
        domain = next(
            (
                trusted
                for trusted in profile.trusted_domains
                if _matches_domain(url, trusted)
            ),
            None,
        )
        matches.append(
            SourceMatch(
                url=url,
                preferred=domain is not None or seed is not None,
                matched_domain=domain.domain if domain else None,
                source_id=seed.id if seed else None,
            )
        )
    return matches
