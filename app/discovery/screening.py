"""Rule-based pre-screening and optional AI title/abstract classification."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Pattern

from app.config import ScreeningConfig
from app.models import PaperCluster, PaperRecord, RuleResult

LABEL_LIKELY = "likely_AlScN_PVD"
LABEL_UNCLEAR = "unclear"
LABEL_NOT_RELEVANT = "not_relevant"


class RuleScreener:
    """Conservative, deterministic title/abstract screen.

    The rule stage is intentionally biased toward ``unclear`` rather than
    dropping an AlScN paper. It removes clearly off-topic records and records
    that are explicitly non-PVD, review articles, or possible electrode-only
    papers. The LLM stage, when enabled, can only promote a rule-unclear paper
    if the material gate already passed.
    """

    def __init__(self, config: ScreeningConfig) -> None:
        self.config = config
        self._material = _compile(config.material_patterns)
        self._method = _compile(config.method_patterns)
        self._review = _compile(config.review_patterns)
        self._electrode = _compile(config.electrode_patterns)
        self._functional = _compile(config.functional_layer_patterns)
        self._non_pvd = _compile(config.non_pvd_method_patterns)
        self._sputter = _compile((r"\bsputter(?:ing|ed)?\b", r"\bmagnetron\b", r"\bco[\s\-]?sputter(?:ing|ed)?\b"))
        self._pld = _compile((r"\bpld\b", r"\bpulsed[\s\-]+laser\s+deposition\b"))

    def screen(self, paper: PaperRecord | PaperCluster) -> RuleResult:
        title = paper.title or ""
        abstract = paper.abstract or ""
        text = f"{title}\n{abstract}"
        title_text = title

        material = _matches(self._material, text)
        method = _matches(self._method, text)
        review = _matches(self._review, title_text) or (paper.item_type or "").casefold() == "review"
        electrode = _matches(self._electrode, text)
        functional = _matches(self._functional, text)
        non_pvd = _matches(self._non_pvd, text)
        sputter = _matches(self._sputter, text)
        pld = _matches(self._pld, text)

        flags: list[str] = []
        reasons: list[str] = []
        if not abstract:
            flags.append("title_only")
            reasons.append("abstract_missing")
        if review:
            flags.append("review_article")
            reasons.append("review_article_reference_only")
        if non_pvd:
            flags.append("non_pvd_method")
            reasons.append("non_pvd_deposition_method_mentioned")
        if electrode:
            flags.append("mentions_electrode")
        possible_electrode_only = bool(electrode) and not bool(functional)
        if possible_electrode_only:
            flags.append("possible_electrode_only")
            reasons.append("AlScN may be only an electrode; confirm functional layer in full text")
        if pld and not sputter:
            flags.append("other_pvd")
            reasons.append("PLD/other PVD requires separate flag; not core sputtering set")

        if not material:
            return RuleResult(
                label=LABEL_NOT_RELEVANT,
                confidence=0.95,
                matched_material=[],
                matched_method=method,
                flags=flags,
                reasons=reasons + ["no AlScN/ScAlN material name found"],
                review_required=False,
                eligible_for_confirmation=False,
            )
        if review:
            return RuleResult(
                label=LABEL_NOT_RELEVANT,
                confidence=0.90,
                matched_material=material,
                matched_method=method,
                flags=flags,
                reasons=reasons,
                review_required=False,
                eligible_for_confirmation=False,
            )

        if not method:
            if non_pvd:
                reasons.append("only a non-PVD deposition method was detected")
            else:
                flags.append("deposition_method_not_found")
                reasons.append("AlScN found but no PVD/sputtering method in title/abstract")
            return RuleResult(
                label=LABEL_UNCLEAR,
                confidence=0.55,
                matched_material=material,
                matched_method=[],
                flags=flags,
                reasons=reasons,
                review_required=True,
                eligible_for_confirmation=False,
            )

        if possible_electrode_only:
            return RuleResult(
                label=LABEL_UNCLEAR,
                confidence=0.55,
                matched_material=material,
                matched_method=method,
                flags=flags,
                reasons=reasons,
                review_required=True,
                eligible_for_confirmation=False,
            )

        eligible = "other_pvd" not in flags
        confidence = 0.90 if not flags or flags == ["title_only"] else 0.82
        reasons.append("AlScN/ScAlN material name and PVD/sputtering method both found")
        return RuleResult(
            label=LABEL_LIKELY,
            confidence=confidence,
            matched_material=material,
            matched_method=method,
            flags=flags,
            reasons=reasons,
            review_required=False,
            eligible_for_confirmation=eligible,
        )


def combine_rule_and_ai(
    rule: RuleResult,
    ai_label: str,
    *,
    confidence: float = 0.0,
    classifier: str = "ai",
    classifier_version: str = "phase2-llm-v1",
) -> RuleResult:
    """Combine the conservative rule result with an AI label.

    The AI may promote an ``unclear`` record only when the material gate has
    already passed and the record is not flagged as a review, non-PVD-only or
    possible electrode-only paper. Conflicts are never auto-resolved.
    """

    if ai_label not in {LABEL_LIKELY, LABEL_UNCLEAR, LABEL_NOT_RELEVANT}:
        raise ValueError(f"unknown AI label: {ai_label!r}")

    flags = list(rule.flags)

    if rule.label == LABEL_NOT_RELEVANT:
        return replace(rule, classifier=classifier, classifier_version=classifier_version)

    if ai_label == LABEL_LIKELY:
        if rule.label == LABEL_LIKELY:
            flags.append("ai_confirmed")
            return replace(
                rule,
                flags=_unique(flags),
                confidence=max(rule.confidence, confidence),
                classifier=classifier,
                classifier_version=classifier_version,
            )
        if any(flag in flags for flag in ("possible_electrode_only", "non_pvd_method", "review_article", "other_pvd")):
            flags.append("ai_conflict")
            return replace(
                rule,
                label=LABEL_UNCLEAR,
                flags=_unique(flags),
                reasons=rule.reasons + ["AI suggested AlScN-PVD but a conservative rule blocked promotion"],
                review_required=True,
                eligible_for_confirmation=False,
                classifier=classifier,
                classifier_version=classifier_version,
            )
        if rule.matched_material:
            flags.append("ai_promoted")
            return replace(
                rule,
                label=LABEL_LIKELY,
                flags=_unique(flags),
                reasons=rule.reasons + ["AI title/abstract classifier promoted an unclear record"],
                confidence=max(rule.confidence, confidence),
                review_required=False,
                eligible_for_confirmation=True,
                classifier=classifier,
                classifier_version=classifier_version,
            )
        return replace(rule, classifier=classifier, classifier_version=classifier_version)

    if ai_label == LABEL_NOT_RELEVANT:
        if rule.label == LABEL_LIKELY:
            flags.append("ai_conflict")
            return replace(
                rule,
                label=LABEL_UNCLEAR,
                flags=_unique(flags),
                reasons=rule.reasons + ["AI and rule screen disagree; human review required"],
                review_required=True,
                eligible_for_confirmation=False,
                classifier=classifier,
                classifier_version=classifier_version,
            )
        return replace(
            rule,
            label=LABEL_UNCLEAR,
            flags=_unique(flags + ["ai_conflict"]),
            reasons=rule.reasons + ["AI did not confirm the unclear record"],
            review_required=True,
            eligible_for_confirmation=False,
            classifier=classifier,
            classifier_version=classifier_version,
        )

    flags.append("ai_unclear")
    return replace(
        rule,
        label=LABEL_UNCLEAR,
        flags=_unique(flags),
        reasons=rule.reasons + ["AI returned unclear"],
        review_required=True,
        eligible_for_confirmation=False,
        classifier=classifier,
        classifier_version=classifier_version,
    )


def _compile(patterns: tuple[str, ...]) -> list[Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


def _matches(patterns: list[Pattern[str]], text: str) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            if value and value not in found:
                found.append(value)
    return found


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
