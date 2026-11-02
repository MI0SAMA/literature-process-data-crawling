"""Crossref Works API adapter."""

from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import urlencode

from app.clients.base import SearchClient, split_author_name
from app.identifiers import normalize_doi
from app.models import Author, PaperRecord, QuerySpec, SearchPage

_SELECT = ",".join(
    (
        "DOI",
        "title",
        "abstract",
        "author",
        "issued",
        "published",
        "published-print",
        "published-online",
        "container-title",
        "type",
        "URL",
        "resource",
        "link",
        "event",
    )
)
_SKIP_TYPES = {"component", "dataset", "peer-review", "grant", "database"}
_TAG_RE = re.compile(r"<[^>]+>")


class CrossrefClient(SearchClient):
    source = "crossref"

    def __init__(self, http, *, base_url: str = "https://api.crossref.org", mailto: str = "", api_key: str = "") -> None:
        super().__init__(http, base_url=base_url, mailto=mailto, api_key=api_key)

    def search(self, spec: QuerySpec, *, page: int = 1) -> SearchPage:
        offset = max(0, (page - 1) * spec.page_size)
        params = {
            "query.bibliographic": spec.query,
            "rows": spec.page_size,
            "offset": offset,
            "select": _SELECT,
        }
        filters: list[str] = []
        if spec.from_year:
            filters.append(f"from-pub-date:{spec.from_year}-01-01")
        if spec.to_year:
            filters.append(f"until-pub-date:{spec.to_year}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        if self.mailto:
            params["mailto"] = self.mailto

        url = f"{self.base_url}/works?{urlencode(params)}"
        response = self.http.get_json(url, source=self.source)
        message = response.data.get("message") or {}
        total = _int_or_none(message.get("total-results"))
        items = message.get("items") or []
        records: list[PaperRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") in _SKIP_TYPES:
                continue
            records.append(self.parse_record(item))
        next_page = None
        if total is not None and offset + spec.page_size < total:
            next_page = page + 1
        return SearchPage(
            records=records,
            total=total,
            page=page,
            next_page=next_page,
            raw=response.data,
            cache_hit=response.cache_hit,
            retries=response.retries,
        )

    def parse_record(self, item: dict) -> PaperRecord:
        doi = normalize_doi(item.get("DOI"))
        title = _first_text(item.get("title")) or "Untitled"
        authors: list[Author] = []
        for author in item.get("author") or []:
            name = author.get("name") or " ".join(
                part for part in (author.get("given"), author.get("family")) if part
            )
            if not name:
                continue
            family = author.get("family")
            given = author.get("given")
            if not family:
                family, derived_given = split_author_name(name)
                given = given or derived_given
            authors.append(Author(name=name, family=family, given=given, orcid=author.get("ORCID")))

        resource = item.get("resource") or {}
        primary = resource.get("primary") or {}
        pdf_url = None
        for link in item.get("link") or []:
            if "pdf" in str(link.get("content-type", "")).lower():
                pdf_url = link.get("URL")
                break
        event = item.get("event") or {}
        return PaperRecord(
            source=self.source,
            source_id=doi or str(item.get("URL") or _hash_id(title)),
            title=title,
            doi=doi,
            abstract=strip_jats(item.get("abstract")),
            authors=authors,
            publication_year=_publication_year(item),
            publication_date=None,
            venue=_first_text(item.get("container-title")) or event.get("name"),
            item_type=item.get("type"),
            landing_url=primary.get("URL") or item.get("URL"),
            pdf_url=pdf_url,
            is_oa=None,
            oa_status=None,
            language=None,
            raw=item,
        )


def strip_jats(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _publication_year(item: dict) -> int | None:
    for key in ("published-print", "published-online", "issued", "published", "created"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if not parts:
            continue
        for value in parts[0]:
            if isinstance(value, int) and 1800 <= value <= 2200:
                return value
    return None


def _first_text(value: object) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return " ".join(item.split())
    return ""


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
