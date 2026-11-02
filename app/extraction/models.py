"""Data objects for Phase 4 extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedField:
    field_key: str
    value: Any
    raw_value: str
    raw_unit: str
    normalized_value: float | None = None
    normalized_unit: str | None = None
    evidence_text: str = ""
    section: str = ""
    locator: str = ""
    status: str = "reported"
    confidence: float = 0.7
    entity_type: str = "recipe"
    entity_id: str = ""
    derivation_note: str | None = None


@dataclass
class RecipeExtraction:
    record_id: str
    paper_key: str
    doi: str
    recipe_id: str
    sample_id: str
    data: dict[str, Any]
    fields: list[ExtractedField] = field(default_factory=list)
    targets: list[dict[str, Any]] = field(default_factory=list)
    review_items: list[dict[str, Any]] = field(default_factory=list)
    source_document_id: str | None = None
    source_url: str | None = None
    status: str = "extracted"
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    paper_key: str
    doi: str
    status: str
    recipes: list[RecipeExtraction] = field(default_factory=list)
    measurements: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    review_items: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_key": self.paper_key,
            "doi": self.doi,
            "status": self.status,
            "recipes": [
                {
                    "record_id": recipe.record_id,
                    "recipe_id": recipe.recipe_id,
                    "sample_id": recipe.sample_id,
                    "status": recipe.status,
                    "data": recipe.data,
                    "fields": [
                        {
                            "field_key": field.field_key,
                            "value": field.value,
                            "raw_value": field.raw_value,
                            "raw_unit": field.raw_unit,
                            "normalized_value": field.normalized_value,
                            "normalized_unit": field.normalized_unit,
                            "evidence_text": field.evidence_text,
                            "section": field.section,
                            "locator": field.locator,
                            "status": field.status,
                            "confidence": field.confidence,
                        }
                        for field in recipe.fields
                    ],
                    "targets": recipe.targets,
                    "review_items": recipe.review_items,
                    "warnings": recipe.warnings,
                }
                for recipe in self.recipes
            ],
            "evidence": self.evidence,
            "measurements": self.measurements,
            "review_items": self.review_items,
            "warnings": self.warnings,
            "metadata": self.metadata,
        }
