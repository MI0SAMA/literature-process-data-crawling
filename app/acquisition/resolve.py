"""DOI resolution and OpenAlex work lookup for Phase 3."""

from __future__ import annotations

import urllib.parse

from app.acquisition.models import FetchedResource
from app.clients.openalex import OpenAlexClient
from app.identifiers import normalize_doi
from app.models import PaperRecord, utc_now_iso


class OpenAlexDoiResolver:
    def __init__(self, client: OpenAlexClient) -> None:
        self.client = client

    def resolve(self, doi: str) -> PaperRecord | None:
        return self.client.get_work_by_doi(doi)


class DoiHandleResolver:
    """Resolve a DOI handle without bypassing publisher access controls."""

    def __init__(self, transport, *, timeout: float = 30.0) -> None:
        self.transport = transport
        self.timeout = timeout

    def resolve(self, doi: str) -> dict:
        normalized = normalize_doi(doi)
        if not normalized:
            return {"doi": doi, "status": 0, "url": "", "final_url": "", "error": "invalid_doi"}
        url = f"https://doi.org/{urllib.parse.quote(normalized, safe='/')}"
        try:
            response = self.transport.request(
                "HEAD",
                url,
                {"User-Agent": "AlScN-PVD-Literature/0.3 (DOI resolution)", "Accept": "text/html"},
                self.timeout,
                None,
            )
            return {
                "doi": normalized,
                "status": response.status,
                "url": url,
                "final_url": response.url,
                "content_type": response.headers.get("content-type", ""),
                "resolved_at": utc_now_iso(),
                "error": None,
            }
        except Exception as error:
            return {
                "doi": normalized,
                "status": 0,
                "url": url,
                "final_url": "",
                "resolved_at": utc_now_iso(),
                "error": f"{type(error).__name__}: {error}",
            }
