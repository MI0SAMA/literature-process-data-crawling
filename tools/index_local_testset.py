"""Index a local PDF folder as an independent literature test set (read-only)."""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from pathlib import Path

from pypdf import PdfReader

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
ALSCN_RE = re.compile(r"Al\s*ScN|Sc\s*AlN|Al\s*1\s*[-−]\s*x\s*Sc|Sc\s*x\s*Al", re.I)
SPUTTER_RE = re.compile(r"sputter|PVD|magnetron|deposition", re.I)


def first_text(path: Path, pages: int = 2) -> tuple[str, int, str]:
    try:
        reader = PdfReader(str(path), strict=False)
        parts = []
        for page in reader.pages[:pages]:
            parts.append(page.extract_text() or "")
        return "\n".join(parts), len(reader.pages), "ok"
    except Exception as error:
        return "", 0, f"{type(error).__name__}: {error}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--db", default="data/structured/corpus.sqlite3")
    parser.add_argument("--output", default="docs/onedrive_testset_inventory.csv")
    parser.add_argument("--folders", default="AlScN,alscn aog,ferroelectric")
    args = parser.parse_args()
    root = Path(args.root)
    folders = [item.strip() for item in args.folders.split(",") if item.strip()]
    corpus = set()
    if Path(args.db).exists():
        con = sqlite3.connect(args.db)
        corpus = {str(row[0]).lower() for row in con.execute("SELECT doi FROM papers WHERE doi IS NOT NULL")}
        con.close()
    paths = []
    for folder in folders:
        base = root / folder
        if base.exists():
            paths.extend(sorted(base.rglob("*.pdf")))
    rows = []
    for index, path in enumerate(paths, 1):
        text, pages, status = first_text(path)
        flat = re.sub(r"\s+", " ", text)
        doi_match = DOI_RE.search(text)
        doi = doi_match.group(0).rstrip(".,;)]") if doi_match else ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        title = " ".join(lines[:3])[:240] if lines else path.stem
        rows.append({
            "path": str(path),
            "name": path.name,
            "folder": path.parent.name,
            "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
            "pages": pages,
            "doi": doi,
            "in_corpus": "yes" if doi.lower() in corpus else "no",
            "has_alscn": "yes" if ALSCN_RE.search(flat) else "no",
            "has_sputter": "yes" if SPUTTER_RE.search(flat) else "no",
            "parse_status": status,
            "title_guess": title,
        })
        if index % 20 == 0:
            print(f"indexed {index}/{len(paths)}", flush=True)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["path"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
