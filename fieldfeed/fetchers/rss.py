"""Any RSS or Atom feed: newsletters, lab blogs, podcasts.

    - type: rss
      name: bioRxiv Genomics
      url: https://connect.biorxiv.org/biorxiv_xml.php?subject=genomics
      kind: newsletter     # newsletter | blog | podcast | news (default blog)
      keywords: [single-cell] # optional: keep only entries mentioning one of these
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone

import feedparser

from ..models import Item, html_to_text, links_from_html


def _when(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def parse(content: bytes, kind: str = "blog", keywords: list[str] | None = None) -> list[Item]:
    feed = feedparser.parse(content)
    kws = [k.lower() for k in keywords or []]
    out = []
    for e in feed.entries:
        link = e.get("link") or ""
        if not link:
            continue
        html = ""
        if e.get("content"):
            html = e.content[0].get("value", "")
        summary = e.get("summary", "") or html
        text = html_to_text(summary, limit=900)
        title = html_to_text(e.get("title", ""), limit=300) or text[:120]
        if kws and not any(k in f"{title} {text}".lower() for k in kws):
            continue
        out.append(Item(
            source="", source_name="", kind=kind,
            url=link,
            title=title,
            text=text,
            author=e.get("author", "") or feed.feed.get("title", ""),
            published=_when(e),
            links=links_from_html(html or summary)[:200],
        ))
    return out


def fetch(src: dict, ctx) -> list[Item]:
    r = ctx.client.get(src["url"])
    r.raise_for_status()
    items = parse(r.content, src.get("kind", "blog"), src.get("keywords"))
    items = [it for it in items if it.published and it.published >= ctx.since]
    return items[: int(src.get("max_items", 60))]
