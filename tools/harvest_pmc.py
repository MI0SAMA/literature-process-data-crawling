"""Harvest legal Europe PMC OA JATS full texts for AlScN/ScAlN PVD papers."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.acquisition.jats import parse_jats
from app.acquisition.sections import locate_deposition_sections
from app.acquisition.store import RawStore
from app.discovery.dedup import Deduplicator
from app.models import Author, PaperRecord, utc_now_iso
from app.storage import Database

CORPUS_DB = ROOT / "data/structured/corpus.sqlite3"
RAW_DIR = ROOT / "data/raw"
TARGET = 50
QUERIES = ["AlScN", "ScAlN", "scandium aluminum nitride", "Al1-xScxN"]


def search() -> list[dict]:
    seen = set()
    results = []
    session = requests.Session()
    for query in QUERIES:
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
            f"query={requests.utils.quote(query + ' AND OPEN_ACCESS:y')}&format=json&pageSize=100&resultType=core"
        )
        response = session.get(url, timeout=45, headers={"User-Agent": "AlScN-PVD-Literature/0.4"})
        response.raise_for_status()
        for item in response.json().get("resultList", {}).get("result") or []:
            pmcid = item.get("pmcid")
            if not pmcid or pmcid in seen:
                continue
            seen.add(pmcid)
            results.append(item)
        time.sleep(1)
    return results


def relevant(item: dict) -> bool:
    text = f"{item.get('title', '')} {item.get('abstractText', '')}".casefold()
    material = any(term in text for term in ("alscn", "scaln", "al1-xscxn", "scxal1-xn", "scandium aluminum nitride"))
    method = any(term in text for term in ("sputter", "magnetron", "pvd", "physical vapor", "hipims", "pld"))
    non_pvd_only = any(term in text for term in ("mocvd", "metalorganic chemical", "molecular beam epitaxy", "atomic layer deposition")) and not any(
        term in text for term in ("sputter", "magnetron", "pvd", "hipims", "pld")
    )
    return material and method and not non_pvd_only


def main() -> int:
    db = Database(CORPUS_DB)
    store = RawStore(RAW_DIR)
    session = requests.Session()
    try:
        successes = db.conn.execute("SELECT COUNT(*) FROM acquisition_runs WHERE status IN ('ok','partial')").fetchone()[0]
        successes = int(successes)
        items = search()
        filtered = [item for item in items if relevant(item)]
        print(json.dumps({"search_results": len(items), "relevant": len(filtered), "already": successes}), flush=True)
        results = []
        for item in filtered:
            if successes >= TARGET:
                break
            pmcid = item.get("pmcid")
            doi = (item.get("doi") or "").strip()
            if not doi:
                continue
            existing_paper = db.find_paper_by_doi(doi.lower())
            if existing_paper:
                acquired = db.conn.execute(
                    "SELECT 1 FROM acquisition_runs WHERE paper_key=? AND status IN ('ok','partial')",
                    (existing_paper["paper_key"],),
                ).fetchone()
                if acquired:
                    continue
            xml_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
            try:
                response = session.get(xml_url, timeout=45, headers={"User-Agent": "AlScN-PVD-Literature/0.4"})
                response.raise_for_status()
                parsed = parse_jats(response.content)
            except Exception as error:
                results.append({"pmcid": pmcid, "doi": doi, "status": "jats_failed", "error": f"{type(error).__name__}: {error}"})
                continue
            selected = locate_deposition_sections(parsed.sections, document_title=parsed.title)
            authors = []
            for author in item.get("authorList", {}).get("author") or []:
                name = " ".join(part for part in (author.get("firstName"), author.get("lastName")) if part) or author.get("collectiveName")
                if name:
                    authors.append(Author(name=name, family=author.get("lastName"), given=author.get("firstName")))
            paper = PaperRecord(
                source="europepmc",
                source_id=pmcid,
                title=item.get("title") or parsed.title,
                doi=doi,
                abstract=item.get("abstractText"),
                authors=authors,
                publication_year=int(item.get("pubYear")) if str(item.get("pubYear", "")).isdigit() else None,
                venue=item.get("journalTitle"),
                item_type="article",
                landing_url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
                pdf_url=None,
                is_oa=True,
                oa_status="open_access",
                oa_url=xml_url,
                pmcid=pmcid,
                license=item.get("license"),
                raw={
                    "open_access": {"is_oa": True, "oa_url": xml_url, "oa_status": "open_access"},
                    "best_oa_location": {"is_oa": True, "landing_page_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/", "version": "publishedVersion"},
                    "primary_location": {"landing_page_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/", "version": "publishedVersion"},
                },
            )
            cluster = Deduplicator().deduplicate([paper]).clusters[0]
            paper_key = db.save_cluster(cluster)
            stored = store.save(paper_key, response.content, filename="document.xml", source_url=xml_url)
            document_id = "doc_" + hashlib.sha256(f"{paper_key}|jats|{xml_url}".encode()).hexdigest()[:24]
            db.save_document(
                {
                    "document_id": document_id,
                    "paper_key": paper_key,
                    "doi": doi,
                    "document_type": "jats",
                    "version": "published",
                    "source_url": xml_url,
                    "final_url": xml_url,
                    "access_status": "open_access",
                    "http_status": 200,
                    "content_type": "application/xml",
                    "file_path": stored.path,
                    "file_hash": stored.sha256,
                    "byte_size": stored.byte_size,
                    "fetched_at": utc_now_iso(),
                    "parser": parsed.parser,
                    "parser_version": parsed.parser_version,
                    "license": item.get("license"),
                    "is_primary": 0,
                    "parent_document_id": None,
                    "error_kind": None,
                    "error_message": None,
                }
            )
            db.set_primary_document(paper_key, document_id)
            db.save_sections(document_id, [section.to_dict() for section in parsed.sections])
            deposition_id = None
            if selected:
                rows = db.get_sections(document_id=document_id)
                match = next((row for row in rows if row["locator"] == selected[0].locator), None)
                deposition_id = match["section_id"] if match else None
            status = "ok" if deposition_id else "partial"
            db.save_acquisition_run(
                {
                    "acquisition_id": f"acq_pmc_{hashlib.sha256(pmcid.encode()).hexdigest()[:16]}",
                    "paper_key": paper_key,
                    "doi": doi,
                    "started_at": utc_now_iso(),
                    "finished_at": utc_now_iso(),
                    "status": status,
                    "primary_document_id": document_id,
                    "deposition_section_id": deposition_id,
                    "metadata": {
                        "source": "europepmc",
                        "pmcid": pmcid,
                        "deposition_heading": selected[0].heading if selected else None,
                        "sections": len(parsed.sections),
                    },
                }
            )
            successes += 1
            result = {
                "pmcid": pmcid,
                "doi": doi,
                "status": status,
                "deposition_heading": selected[0].heading if selected else None,
                "sections": len(parsed.sections),
            }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

        evidence = {
            "source": "europepmc",
            "target": TARGET,
            "successes": successes,
            "search_results": len(items),
            "relevant": len(filtered),
            "results": results,
            "documents": db.count_documents(),
            "sections": db.count_sections(),
        }
        (ROOT / "docs/pmc_manifest.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: evidence[k] for k in ("target", "successes", "documents", "sections")}, indent=2))
        return 0 if successes >= TARGET else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
