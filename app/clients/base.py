"""Common search-client interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.http import CachedHttpClient
from app.models import PaperRecord, QuerySpec, SearchPage


class SearchClient(ABC):
    source: str

    def __init__(
        self,
        http: CachedHttpClient,
        *,
        base_url: str,
        mailto: str = "",
        api_key: str = "",
    ) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.mailto = mailto
        self.api_key = api_key

    @abstractmethod
    def search(self, spec: QuerySpec, *, page: int = 1) -> SearchPage:
        raise NotImplementedError

    @abstractmethod
    def parse_record(self, item: dict) -> PaperRecord:
        raise NotImplementedError


def split_author_name(name: str) -> tuple[str | None, str | None]:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        return None, None
    if "," in cleaned:
        family, given = cleaned.split(",", 1)
        return family.strip() or None, given.strip() or None
    parts = cleaned.split()
    if len(parts) == 1:
        return parts[0], None
    return parts[-1], " ".join(parts[:-1])
