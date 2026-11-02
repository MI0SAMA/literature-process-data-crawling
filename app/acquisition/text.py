"""Text normalization helpers for document parsers."""

from __future__ import annotations

import re
from typing import Iterable

from lxml import etree


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def element_text(element: etree._Element | None) -> str:
    if element is None:
        return ""
    return normalize_text(" ".join(element.itertext()))


def local_name(element: etree._Element) -> str:
    tag = element.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return str(tag)


def first_text(root: etree._Element, local: str) -> str:
    for element in root.iter():
        if local_name(element) == local:
            text = element_text(element)
            if text:
                return text
    return ""


def join_blocks(blocks: Iterable[str]) -> str:
    return normalize_text(" ".join(block for block in blocks if block))
