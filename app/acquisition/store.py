"""Content-addressed raw file storage for acquired documents."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


@dataclass
class StoredFile:
    path: str
    sha256: str
    byte_size: int
    filename: str


class RawStore:
    def __init__(self, root: str | Path = "data/raw") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, paper_key: str, body: bytes, *, filename: str = "", source_url: str = "") -> StoredFile:
        digest = hashlib.sha256(body).hexdigest()
        safe_key = _safe(paper_key) or "unknown"
        safe_name = _safe_filename(filename or _filename_from_url(source_url) or "document.bin")
        directory = self.root / safe_key
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest[:16]}_{safe_name}"
        if not path.exists():
            path.write_bytes(body)
        return StoredFile(path=str(path), sha256=digest, byte_size=len(body), filename=safe_name)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")


def _safe_filename(value: str) -> str:
    name = unquote(value)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    return name[:120] or "document.bin"


def _filename_from_url(url: str) -> str:
    if not url:
        return ""
    path = urlparse(url).path
    return Path(unquote(path)).name
