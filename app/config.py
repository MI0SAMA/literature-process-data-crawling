"""Application configuration loading for Phase 2."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0


@dataclass(frozen=True)
class Defaults:
    databases: tuple[str, ...] = ("openalex", "crossref")
    max_results_per_query: int = 100
    page_size: int = 50
    from_year: int | None = 2005
    to_year: int | None = None
    cache_ttl_hours: float = 168.0
    request_timeout_seconds: float = 20.0
    rate_limit_seconds: dict[str, float] = field(default_factory=lambda: {"openalex": 0.2, "crossref": 0.6})
    retry: RetryConfig = field(default_factory=RetryConfig)
    user_agent: str = "AlScN-PVD-Literature/0.2"
    mailto: str = ""


@dataclass(frozen=True)
class QueryConfig:
    query_id: str
    query: str


@dataclass(frozen=True)
class ScreeningConfig:
    material_patterns: tuple[str, ...]
    method_patterns: tuple[str, ...]
    review_patterns: tuple[str, ...]
    electrode_patterns: tuple[str, ...]
    functional_layer_patterns: tuple[str, ...]
    non_pvd_method_patterns: tuple[str, ...]
    fuzzy_auto_merge_threshold: float = 0.94
    fuzzy_review_threshold: float = 0.55


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "openai_compatible"
    base_url: str = ""
    model: str = ""
    api_key_env: str = "ALSCN_LLM_API_KEY"
    timeout_seconds: float = 45.0
    prompt_version: str = "phase2-title-abstract-v1"


@dataclass(frozen=True)
class AppConfig:
    path: Path
    project: dict[str, Any]
    defaults: Defaults
    queries: tuple[QueryConfig, ...]
    screening: ScreeningConfig
    llm: LLMConfig


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def load_app_config(path: str | Path = "config/search_queries.yaml") -> AppConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    raw = _expand_env(raw)

    defaults_raw = raw.get("defaults") or {}
    retry_raw = defaults_raw.get("retry") or {}
    defaults = Defaults(
        databases=tuple(defaults_raw.get("databases") or ("openalex", "crossref")),
        max_results_per_query=int(defaults_raw.get("max_results_per_query", 100)),
        page_size=int(defaults_raw.get("page_size", 50)),
        from_year=_optional_int(defaults_raw.get("from_year")),
        to_year=_optional_int(defaults_raw.get("to_year")),
        cache_ttl_hours=float(defaults_raw.get("cache_ttl_hours", 168)),
        request_timeout_seconds=float(defaults_raw.get("request_timeout_seconds", 20)),
        rate_limit_seconds={
            str(key): float(value)
            for key, value in (defaults_raw.get("rate_limit_seconds") or {}).items()
        },
        retry=RetryConfig(
            max_attempts=int(retry_raw.get("max_attempts", 3)),
            base_delay_seconds=float(retry_raw.get("base_delay_seconds", 1.0)),
            max_delay_seconds=float(retry_raw.get("max_delay_seconds", 8.0)),
        ),
        user_agent=str(defaults_raw.get("user_agent", "AlScN-PVD-Literature/0.2")),
        mailto=str(defaults_raw.get("mailto") or ""),
    )

    queries = tuple(
        QueryConfig(query_id=str(item["id"]), query=str(item["query"]).strip())
        for item in (raw.get("queries") or [])
        if item.get("id") and item.get("query")
    )
    if not queries:
        raise ValueError("config contains no search queries")

    screening_raw = raw.get("screening") or {}
    screening = ScreeningConfig(
        material_patterns=tuple(screening_raw.get("material_patterns") or ()),
        method_patterns=tuple(screening_raw.get("method_patterns") or ()),
        review_patterns=tuple(screening_raw.get("review_patterns") or ()),
        electrode_patterns=tuple(screening_raw.get("electrode_patterns") or ()),
        functional_layer_patterns=tuple(screening_raw.get("functional_layer_patterns") or ()),
        non_pvd_method_patterns=tuple(screening_raw.get("non_pvd_method_patterns") or ()),
        fuzzy_auto_merge_threshold=float(screening_raw.get("fuzzy_auto_merge_threshold", 0.94)),
        fuzzy_review_threshold=float(screening_raw.get("fuzzy_review_threshold", 0.55)),
    )
    if not screening.material_patterns or not screening.method_patterns:
        raise ValueError("screening material/method patterns must not be empty")

    llm_raw = raw.get("llm") or {}
    llm = LLMConfig(
        provider=str(llm_raw.get("provider", "openai_compatible")),
        base_url=str(llm_raw.get("base_url") or ""),
        model=str(llm_raw.get("model") or ""),
        api_key_env=str(llm_raw.get("api_key_env", "ALSCN_LLM_API_KEY")),
        timeout_seconds=float(llm_raw.get("timeout_seconds", 45)),
        prompt_version=str(llm_raw.get("prompt_version", "phase2-title-abstract-v1")),
    )

    return AppConfig(
        path=config_path,
        project=dict(raw.get("project") or {}),
        defaults=defaults,
        queries=queries,
        screening=screening,
        llm=llm,
    )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)
