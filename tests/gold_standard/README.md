# Phase 2 gold standard

## `known_alscn_pvd_papers.csv`

20 manually prepared original research papers where an AlScN/ScAlN functional
layer is prepared by PVD/sputtering. Selection spans:

- 2008–2026;
- single-recipe, multi-recipe, co-sputtering, single alloy target;
- RF / DC / pulsed DC / HiPIMS / reactive / magnetron sputter epitaxy;
- ferroelectric, piezoelectric, SAW/BAW/MEMS, energy harvesting, residual stress.

All 20 DOIs were checked against the OpenAlex Works API on 2026-10-29 with a
DOI filter (`filter=doi:...`) and matched title/year metadata. This verifies the
identifiers; it does not replace full-text verification of the deposition method
in Phase 3.

The current recorded-page replay finds 18/20. Missing:

- `10.1002/adma.200802611`
- `10.1016/j.jre.2022.03.009`

They rank below the top 25 of the three recorded queries. Do not add them to the
candidate database by hand; re-run the live default 14-query plan after the
network switch.

## `screening_labels.csv`

9 title-level labels used to check the rule screener's three-way output:

- 5 `likely_AlScN_PVD` (sputtering/magnetron/reactive/co-sputtering in title);
- 3 `unclear` (no deposition method in title, or MOCVD-only);
- 1 `not_relevant` (review type).

The labels are intentionally title-level, matching what Phase 2 can see. Phase 3
must confirm the actual functional-layer deposition method from full text.

## `../fixtures/live/recorded_openalex.json`

Public OpenAlex metadata captured through the harness `web_fetch` path on
2026-10-29. The local CLI proxy returned 407, so the CLI could not make these
calls directly. Only `doi`, `title`, `publication_year`, `type` are stored.
