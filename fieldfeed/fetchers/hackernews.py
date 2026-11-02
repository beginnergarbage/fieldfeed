"""Hacker News stories matching keywords, via the Algolia search API.

    - type: hackernews
      name: Hacker News
      keywords: [bioinformatics, genomics, protein folding]
      min_points: 20
"""

from __future__ import annotations

from ..models import Item, html_to_text, parse_iso

API = "https://hn.algolia.com/api/v1/search_by_date"


def fetch(src: dict, ctx) -> list[Item]:
    since = int(ctx.since.timestamp())
    min_points = int(src.get("min_points", 20))
    out, seen = [], set()
    for kw in src.get("keywords", []):
        params = {
            "query": kw,
            "tags": "story",
            "hitsPerPage": 100,
            "numericFilters": f"created_at_i>{since},points>{min_points}",
        }
        r = ctx.client.get(API, params=params)
        r.raise_for_status()
        for h in r.json().get("hits", []):
            oid = h.get("objectID")
            if not oid or oid in seen:
                continue
            seen.add(oid)
            ext = h.get("url")
            out.append(Item(
                source="", source_name="", kind="discussion",
                url=f"https://news.ycombinator.com/item?id={oid}",
                title=h.get("title") or "",
                text=html_to_text(h.get("story_text") or "", limit=600),
                author=h.get("author") or "",
                published=parse_iso(h.get("created_at")),
                engagement={"points": h.get("points") or 0, "comments": h.get("num_comments") or 0},
                links=[ext] if ext else [],
            ))
    return out
