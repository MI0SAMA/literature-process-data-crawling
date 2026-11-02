"""Deterministic cross-source deduplication for Phase 2 candidates."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from app.identifiers import normalize_doi, normalize_person_name, normalize_title, title_similarity
from app.models import Author, DedupResult, PaperCluster, PaperRecord, ReviewItem


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size
        self.methods: list[set[str]] = [set() for _ in range(size)]
        self.review_required = [False] * size
        self.review_reason: list[str | None] = [None] * size

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int, *, method: str, review: bool = False, reason: str | None = None) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            if method:
                self.methods[root_left].add(method)
            if review and not self.review_required[root_left]:
                self.review_required[root_left] = True
                self.review_reason[root_left] = reason
            return
        if self.rank[root_left] < self.rank[root_right]:
            root_left, root_right = root_right, root_left
        self.parent[root_right] = root_left
        if self.rank[root_left] == self.rank[root_right]:
            self.rank[root_left] += 1
        self.methods[root_left].update(self.methods[root_right])
        if method:
            self.methods[root_left].add(method)
        self.review_required[root_left] = self.review_required[root_left] or self.review_required[root_right] or review
        if self.review_reason[root_left] is None:
            self.review_reason[root_left] = reason or self.review_reason[root_right]


@dataclass
class _Pair:
    left: int
    right: int
    similarity: float


class Deduplicator:
    """Merge records by DOI first, then by title + first author + year.

    Exact DOI matches are always merged. Records without a usable DOI are
    matched on normalized title, first-author family and publication year.
    Fuzzy matches above the auto-merge threshold are merged but flagged for
    review. Pairs in the review band are not merged and are written to the
    dedup review queue.
    """

    def __init__(
        self,
        *,
        fuzzy_auto_merge_threshold: float = 0.94,
        fuzzy_review_threshold: float = 0.55,
    ) -> None:
        if not 0.0 <= fuzzy_review_threshold <= fuzzy_auto_merge_threshold <= 1.0:
            raise ValueError("dedup thresholds must satisfy 0 <= review <= auto <= 1")
        self.fuzzy_auto_merge_threshold = fuzzy_auto_merge_threshold
        self.fuzzy_review_threshold = fuzzy_review_threshold

    def deduplicate(self, records: Iterable[PaperRecord]) -> DedupResult:
        items = list(records)
        if not items:
            return DedupResult(clusters=[], review_items=[], stats={
                "records_in": 0,
                "clusters_out": 0,
                "duplicates_removed": 0,
                "review_items": 0,
                "doi_merges": 0,
                "title_merges": 0,
                "fuzzy_merges": 0,
            })

        uf = _UnionFind(len(items))
        doi_groups: dict[str, list[int]] = defaultdict(list)
        for index, record in enumerate(items):
            doi = normalize_doi(record.doi)
            if doi:
                record.doi = doi
                doi_groups[doi].append(index)

        doi_merge_count = 0
        for indices in doi_groups.values():
            for other in indices[1:]:
                uf.union(indices[0], other, method="doi")
                doi_merge_count += 1

        title_groups: dict[str, list[int]] = defaultdict(list)
        author_year_groups: dict[tuple[str, int | None], list[int]] = defaultdict(list)
        for index, record in enumerate(items):
            title_key = normalize_title(record.title)
            if title_key:
                title_groups[title_key].append(index)
            family = normalize_person_name(record.first_author_family)
            if family:
                author_year_groups[(family, record.publication_year)].append(index)

        pairs: list[_Pair] = []
        seen: set[tuple[int, int]] = set()

        def add_pair(left: int, right: int) -> None:
            if left == right:
                return
            key = (left, right) if left < right else (right, left)
            if key in seen:
                return
            seen.add(key)
            left_record, right_record = items[left], items[right]
            if left_record.doi and right_record.doi and left_record.doi != right_record.doi:
                return
            similarity = title_similarity(left_record.title, right_record.title)
            if similarity <= 0.0:
                return
            pairs.append(_Pair(left=left, right=right, similarity=similarity))

        # Exact title candidates catch records whose first author is missing.
        for indices in title_groups.values():
            for position, left in enumerate(indices):
                for right in indices[position + 1 :]:
                    add_pair(left, right)

        # Author/year blocking catches near-identical titles with formatting
        # differences. ±1 year tolerates online-first vs issue-year metadata.
        for (family, year), indices in author_year_groups.items():
            buckets = [(family, year)]
            if year is not None:
                buckets.extend([(family, year - 1), (family, year + 1)])
            candidate_indices: list[int] = []
            for bucket in buckets:
                candidate_indices.extend(author_year_groups.get(bucket, ()))
            for left in indices:
                for right in candidate_indices:
                    add_pair(left, right)

        review_items: list[ReviewItem] = []
        title_merge_count = 0
        fuzzy_merge_count = 0
        review_pairs: set[tuple[int, int]] = set()

        for pair in pairs:
            left = items[pair.left]
            right = items[pair.right]
            if uf.find(pair.left) == uf.find(pair.right):
                continue
            left_family = normalize_person_name(left.first_author_family)
            right_family = normalize_person_name(right.first_author_family)
            same_author = bool(left_family and right_family and left_family == right_family)
            years_compatible = (
                left.publication_year is None
                or right.publication_year is None
                or left.publication_year == right.publication_year
            )
            fuzzy_years_compatible = (
                left.publication_year is not None
                and right.publication_year is not None
                and abs(left.publication_year - right.publication_year) <= 1
            )

            if pair.similarity >= 0.999:
                if same_author and years_compatible:
                    uf.union(pair.left, pair.right, method="title_author_year")
                    title_merge_count += 1
                    continue
                if (not left_family or not right_family) and years_compatible:
                    # Exact title + year is strong evidence, but a missing
                    # author means the identity cannot be fully confirmed.
                    # Merge to avoid a duplicate paper_key, and flag/list it
                    # for human confirmation.
                    uf.union(
                        pair.left,
                        pair.right,
                        method="title_author_year",
                        review=True,
                        reason="exact title/year match but author metadata is missing",
                    )
                    self._add_review(
                        review_items,
                        review_pairs,
                        left,
                        right,
                        pair.similarity,
                        "exact_title_missing_author",
                    )
                    title_merge_count += 1
                    continue
                continue

            if (
                same_author
                and fuzzy_years_compatible
                and pair.similarity >= self.fuzzy_auto_merge_threshold
            ):
                uf.union(
                    pair.left,
                    pair.right,
                    method="fuzzy_title_author_year",
                    review=True,
                    reason=f"fuzzy title match ({pair.similarity:.3f}) requires review",
                )
                fuzzy_merge_count += 1
                continue

            if (
                same_author
                and fuzzy_years_compatible
                and pair.similarity >= self.fuzzy_review_threshold
            ):
                self._add_review(
                    review_items, review_pairs, left, right, pair.similarity,
                    "fuzzy_title_author_year",
                )

        clusters: list[PaperCluster] = []
        for root in sorted({uf.find(index) for index in range(len(items))}):
            indices = [index for index in range(len(items)) if uf.find(index) == root]
            cluster = self._build_cluster(items, indices, uf, root)
            clusters.append(cluster)

        clusters.sort(key=lambda cluster: (cluster.doi is None, (cluster.title or "").casefold()))
        review_items.sort(key=lambda item: item.similarity, reverse=True)
        stats = {
            "records_in": len(items),
            "clusters_out": len(clusters),
            "duplicates_removed": len(items) - len(clusters),
            "review_items": len(review_items),
            "doi_merges": doi_merge_count,
            "title_merges": title_merge_count,
            "fuzzy_merges": fuzzy_merge_count,
        }
        return DedupResult(clusters=clusters, review_items=review_items, stats=stats)

    @staticmethod
    def _add_review(
        review_items: list[ReviewItem],
        seen: set[tuple[int, int]],
        left: PaperRecord,
        right: PaperRecord,
        similarity: float,
        reason: str,
    ) -> None:
        key = tuple(sorted((id(left), id(right))))
        if key in seen:
            return
        seen.add(key)
        review_items.append(
            ReviewItem(
                left_source=left.source,
                left_source_id=left.source_id,
                right_source=right.source,
                right_source_id=right.source_id,
                left_title=left.title,
                right_title=right.title,
                similarity=similarity,
                reason=reason,
            )
        )

    def _build_cluster(
        self,
        records: list[PaperRecord],
        indices: list[int],
        uf: _UnionFind,
        root: int,
    ) -> PaperCluster:
        members = [records[index] for index in indices]
        ranked = sorted(members, key=self._completeness_score, reverse=True)
        doi = next((record.doi for record in ranked if record.doi), None)
        title = next((record.title for record in ranked if record.title), "Untitled")
        normalized_title = normalize_title(title)
        abstract = max((record.abstract or "" for record in members), key=len, default="")
        authors = max((record.authors for record in members), key=len, default=[])
        first_author = ranked[0].first_author_family
        year = next((record.publication_year for record in ranked if record.publication_year), None)
        venue = next((record.venue for record in ranked if record.venue), None)
        item_type = next((record.item_type for record in ranked if record.item_type), None)
        landing_url = next((record.landing_url for record in ranked if record.landing_url), None)

        methods = uf.methods[root]
        if len(members) == 1:
            method = "single"
        elif "doi" in methods:
            method = "doi"
        elif "fuzzy_title_author_year" in methods:
            method = "fuzzy_title_author_year"
        elif "title_author_year" in methods:
            method = "title_author_year"
        else:
            method = "doi"

        paper_key = self._paper_key(doi, normalized_title, year, first_author)
        return PaperCluster(
            paper_key=paper_key,
            title=title,
            normalized_title=normalized_title,
            doi=doi,
            abstract=abstract or None,
            authors=list(authors),
            publication_year=year,
            venue=venue,
            item_type=item_type,
            landing_url=landing_url,
            records=ranked,
            method=method,
            review_required=uf.review_required[root],
            review_reason=uf.review_reason[root],
        )

    @staticmethod
    def _completeness_score(record: PaperRecord) -> tuple[int, int]:
        score = 0
        if record.doi:
            score += 8
        if record.abstract:
            score += 4
        if record.authors:
            score += 2
        if record.title:
            score += 2
        if record.publication_year:
            score += 1
        if record.venue:
            score += 1
        return score, len(record.abstract or "")

    @staticmethod
    def _paper_key(doi: str | None, normalized_title: str, year: int | None, first_author: str | None) -> str:
        if doi:
            return f"doi:{doi}"
        digest = hashlib.sha256(
            f"{normalized_title}|{year or ''}|{normalize_person_name(first_author)}".encode("utf-8")
        ).hexdigest()[:20]
        return f"title:{digest}"
