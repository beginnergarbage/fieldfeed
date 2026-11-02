"""Core data types shared by fetchers, ranking and the site builder."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape

from .urls import canonical, extract_links

# What kind of thing an item is. Drives the badge colour on the site and a few
# ranking decisions (social posts have no real title, papers have no engagement).
KINDS = ("forum", "paper", "newsletter", "blog", "social", "discussion", "podcast", "news")


@dataclass
class Item:
    source: str            # source key from field.yaml, e.g. "biorxiv"
    source_name: str       # display name, e.g. "bioRxiv"
    kind: str
    url: str
    title: str
    text: str = ""
    author: str = ""
    published: datetime | None = None
    engagement: dict = field(default_factory=dict)   # e.g. {"score": 120, "comments": 14}
    links: list[str] = field(default_factory=list)   # outbound links found in the body
    trusted: bool = False  # skip the relevance pass, treat as on-topic

    @property
    def canon(self) -> str:
        return canonical(self.url)

    @property
    def id(self) -> str:
        return hashlib.sha1(self.canon.encode()).hexdigest()[:16]


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(html: str, limit: int = 1200) -> str:
    """Strip tags and collapse whitespace. Good enough for excerpts."""
    if not html:
        return ""
    text = _TAG.sub(" ", html)
    text = _WS.sub(" ", unescape(text)).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def links_from_html(html: str) -> list[str]:
    return extract_links(html or "")


def utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None
