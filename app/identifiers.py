"""DOI and title identifiers used for deterministic deduplication."""

from __future__ import annotations

import difflib
import html
import re
import unicodedata
from urllib.parse import unquote

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_DOI_PREFIX_RE = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/", re.IGNORECASE)
_DOI_LABEL_RE = re.compile(r"^doi\s*:\s*", re.IGNORECASE)

_LEADING_ARTICLES = ("the ", "a ", "an ")


def normalize_doi(value: object) -> str | None:
    """Return a canonical lowercase DOI or None when the value is not a DOI.

    Handles DOI URLs, ``doi:`` prefixes, percent-encoded slashes, uppercase
    characters and the punctuation that commonly trails a DOI in reference
    lists. It does not invent a DOI from a title.
    """

    if value is None:
        return None
    text = html.unescape(str(value)).strip()
    if not text:
        return None
    text = unquote(text)
    text = _DOI_PREFIX_RE.sub("", text)
    text = _DOI_LABEL_RE.sub("", text)
    text = text.strip()

    # Strip wrappers such as "(10....)" or "[10....]" only when they wrap the
    # entire value; parentheses inside a DOI remain untouched.
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
        if text.startswith(opening) and text.endswith(closing):
            if text.count(opening) == 1 and text.count(closing) == 1:
                text = text[1:-1].strip()

    # Trailing punctuation is not part of a DOI. Keep a trailing ')' only when
    # it belongs to a balanced pair inside the DOI itself.
    while text and text[-1] in ".,;:":
        text = text[:-1].rstrip()
    while text.endswith(")") and text.count(")") > text.count("("):
        text = text[:-1].rstrip()
    while text.endswith("]") and text.count("]") > text.count("["):
        text = text[:-1].rstrip()

    if not _DOI_RE.match(text):
        return None
    return text.lower()


def normalize_title(value: object) -> str:
    """Normalize a title for exact and fuzzy comparison."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    # Normalize ampersands and common separators before removing punctuation.
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for article in _LEADING_ARTICLES:
        if text.startswith(article):
            text = text[len(article) :]
            break
    return text


def title_similarity(left: object, right: object) -> float:
    """Return a 0..1 similarity based on normalized titles."""

    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def normalize_person_name(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()
