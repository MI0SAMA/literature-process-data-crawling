"""Shared test helpers."""

from __future__ import annotations

from app.models import Author, PaperRecord


def make_record(
    *,
    source: str = "openalex",
    source_id: str = "W1",
    title: str = "AlScN thin films by reactive magnetron sputtering",
    doi: str | None = "10.1063/1.5084945",
    abstract: str | None = "AlScN films were grown by reactive magnetron sputtering.",
    authors: list[Author] | None = None,
    year: int | None = 2019,
    venue: str | None = "Journal of Applied Physics",
    item_type: str | None = "article",
    landing_url: str | None = None,
    pdf_url: str | None = None,
) -> PaperRecord:
    return PaperRecord(
        source=source,
        source_id=source_id,
        title=title,
        doi=doi,
        abstract=abstract,
        authors=authors if authors is not None else [Author(name="Oliver Fichtner", family="Fichtner", given="Oliver")],
        publication_year=year,
        venue=venue,
        item_type=item_type,
        landing_url=landing_url,
        pdf_url=pdf_url,
        raw={},
    )
