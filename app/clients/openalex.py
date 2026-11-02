"""OpenAlex Works API adapter."""

from __future__ import annotations

import hashlib
from urllib.parse import urlencode

from app.clients.base import SearchClient, split_author_name
from app.identifiers import normalize_doi
from app.models import Author, PaperRecord, QuerySpec, SearchPage

_SELECT = ",".join(
    (
        "id",
        "doi",
        "title",
        "display_name",
        "publication_year",
        "publication_date",
        "type",
        "authorships",
        "primary_location",
        "best_oa_location",
        "locations",
        "open_access",
        "abstract_inverted_index",
        "ids",
        "language",
    )
)


class OpenAlexClient(SearchClient):
    source = "openalex"

    def __init__(self, http, *, base_url: str = "https://api.openalex.org", mailto: str = "", api_key: str = "") -> None:
        super().__init__(http, base_url=base_url, mailto=mailto, api_key=api_key)

    def search(self, spec: QuerySpec, *, page: int = 1) -> SearchPage:
        params = {
            "search": spec.query,
            "per-page": spec.page_size,
            "page": page,
            "select": _SELECT,
        }
        filters: list[str] = []
        if spec.from_year:
            filters.append(f"from_publication_date:{spec.from_year}-01-01")
        if spec.to_year:
            filters.append(f"to_publication_date:{spec.to_year}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        if self.mailto:
            params["mailto"] = self.mailto
        if self.api_key:
            params["api_key"] = self.api_key

        url = f"{self.base_url}/works?{urlencode(params)}"
        response = self.http.get_json(url, source=self.source)
        payload = response.data
        meta = payload.get("meta") or {}
        total = _int_or_none(meta.get("count"))
        results = payload.get("results") or []
        records: list[PaperRecord] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            records.append(self.parse_record(item))
        next_page = None
        if total is not None and page * spec.page_size < total:
            next_page = page + 1
        return SearchPage(
            records=records,
            total=total,
            page=page,
            next_page=next_page,
            raw=payload,
            cache_hit=response.cache_hit,
            retries=response.retries,
        )

    def get_work_by_doi(self, doi: str) -> PaperRecord | None:
        normalized = normalize_doi(doi)
        if not normalized:
            return None
        params = {
            "filter": f"doi:{normalized}",
            "per-page": 1,
            "select": _SELECT,
        }
        if self.mailto:
            params["mailto"] = self.mailto
        if self.api_key:
            params["api_key"] = self.api_key
        url = f"{self.base_url}/works?{urlencode(params)}"
        response = self.http.get_json(url, source=self.source)
        results = response.data.get("results") or []
        if not results:
            return None
        return self.parse_record(results[0])

    def parse_record(self, item: dict) -> PaperRecord:
        title = _first_text(item.get("title"), item.get("display_name")) or "Untitled"
        doi = normalize_doi(item.get("doi"))
        openalex_id = str(item.get("id") or "")
        source_id = openalex_id.rsplit("/", 1)[-1] if openalex_id else (doi or _hash_id(title))

        authors: list[Author] = []
        for authorship in item.get("authorships") or []:
            author = authorship.get("author") or {}
            name = author.get("display_name") or authorship.get("raw_author_name") or ""
            if not name:
                continue
            family, given = split_author_name(name)
            authors.append(
                Author(
                    name=name,
                    family=family,
                    given=given,
                    orcid=author.get("orcid"),
                )
            )

        primary = item.get("primary_location") or {}
        best_oa = item.get("best_oa_location") or {}
        source = primary.get("source") or {}
        open_access = item.get("open_access") or {}
        ids = item.get("ids") or {}
        pdf_url = primary.get("pdf_url") or best_oa.get("pdf_url") or open_access.get("oa_url")
        return PaperRecord(
            source=self.source,
            source_id=source_id,
            title=title,
            doi=doi,
            abstract=reconstruct_abstract(item.get("abstract_inverted_index")),
            authors=authors,
            publication_year=_int_or_none(item.get("publication_year")),
            publication_date=item.get("publication_date"),
            venue=source.get("display_name") or item.get("raw_source_name"),
            item_type=item.get("type"),
            landing_url=primary.get("landing_page_url") or best_oa.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None),
            pdf_url=pdf_url,
            is_oa=open_access.get("is_oa") if open_access.get("is_oa") is not None else best_oa.get("is_oa"),
            oa_status=open_access.get("oa_status"),
            oa_url=open_access.get("oa_url"),
            pmcid=ids.get("pmcid"),
            license=best_oa.get("license") or primary.get("license"),
            language=item.get("language"),
            raw=item,
        )


def reconstruct_abstract(inverted_index: object) -> str | None:
    if not isinstance(inverted_index, dict) or not inverted_index:
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                positioned.append((position, str(word)))
    if not positioned:
        return None
    positioned.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in positioned)


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
