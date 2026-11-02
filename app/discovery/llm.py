"""Optional OpenAI-compatible title/abstract classifier.

Only public title and abstract text are sent. Internal 147-sample process
values are never included, and API keys are read from the environment and
never written to logs or the database.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from app.config import LLMConfig
from app.discovery.screening import LABEL_LIKELY, LABEL_NOT_RELEVANT, LABEL_UNCLEAR
from app.http import CachedHttpClient
from app.models import PaperCluster


@dataclass
class AIClassification:
    label: str
    confidence: float
    reason: str = ""
    raw: dict[str, Any] | None = None
    classifier: str = "llm"
    version: str = "phase2-title-abstract-v1"


class LLMClassifier:
    def __init__(
        self,
        http: CachedHttpClient,
        config: LLMConfig,
        *,
        api_key: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self.http = http
        self.config = config
        self.api_key = api_key if api_key is not None else os.environ.get(config.api_key_env, "")
        self.version = prompt_version or config.prompt_version
        if not config.base_url or not config.model:
            raise ValueError(
                "LLM classifier is not configured; set ALSCN_LLM_BASE_URL and ALSCN_LLM_MODEL"
            )
        if not self.api_key:
            raise ValueError(f"LLM classifier has no API key; set {config.api_key_env}")

    def classify(self, paper: PaperCluster) -> AIClassification:
        url = self.config.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        prompt = build_prompt(paper)
        payload = {
            "model": self.config.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You classify public paper titles and abstracts for a materials-science "
                        "literature database. Return only JSON with keys label, confidence, reason. "
                        "label must be one of likely_AlScN_PVD, unclear, not_relevant. "
                        "AlScN must be the functional layer prepared by PVD/sputtering; papers where "
                        "AlScN is only an electrode are not likely. Reviews are not original experiments."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        response = self.http.post_json(
            url,
            payload,
            source="llm",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        content = _extract_content(response.data)
        parsed = _parse_json_content(content)
        label = str(parsed.get("label", "")).strip()
        if label not in {LABEL_LIKELY, LABEL_UNCLEAR, LABEL_NOT_RELEVANT}:
            raise ValueError(f"LLM returned invalid label: {label!r}")
        confidence = _clamp(parsed.get("confidence", 0.0))
        return AIClassification(
            label=label,
            confidence=confidence,
            reason=str(parsed.get("reason", ""))[:1000],
            raw=response.data,
            version=self.version,
        )


def build_prompt(paper: PaperCluster) -> str:
    title = (paper.title or "")[:500]
    abstract = (paper.abstract or "")[:4000]
    return (
        f"TITLE:\n{title}\n\n"
        f"ABSTRACT:\n{abstract or '[no abstract available]'}\n\n"
        "Classify whether this is an original research paper where an AlScN/ScAlN "
        "functional layer was deposited by PVD/sputtering. Return JSON only."
    )


def _extract_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("LLM response contains no choices")
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response has no message content")
    return content


def _parse_json_content(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON is not an object")
    return parsed


def _clamp(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))
