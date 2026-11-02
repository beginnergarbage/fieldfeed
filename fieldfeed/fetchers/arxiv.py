"""arXiv papers by category and keyword.

    - type: arxiv
      name: arXiv
      categories: [q-bio.GN, q-bio.BM, cs.LG]
      keywords: [single-cell, "protein design"]
      max_results: 300

Keywords cast a wide net. The relevance pass (when a model is configured)
filters out the "transformer" papers that are about power grids.
"""

from __future__ import annotations

import re

import feedparser

from ..models import Item, html_to_text
from .rss import _when

API = "https://export.arxiv.org/api/query"


def build_query(categories: list[str], keywords: list[str]) -> str:
    cats = " OR ".join(f"cat:{c}" for c in categories)
    q = f"({cats})" if cats else ""
    if keywords:
        terms = []
        for k in keywords:
            k = k.replace('"', "")
            terms.append(f'abs:"{k}"' if " " in k else f"abs:{k}")
            terms.append(f'ti:"{k}"' if " " in k else f"ti:{k}")
        kw = " OR ".join(terms)
        q = f"{q} AND ({kw})" if q else f"({kw})"
    return q


def fetch(src: dict, ctx) -> list[Item]:
    params = {
        "search_query": build_query(src.get("categories", []), src.get("keywords", [])),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": int(src.get("max_results", 300)),
    }
    r = ctx.client.get(API, params=params, timeout=90)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        published = _when(e)
        if published and published < ctx.since:
            break
        authors = [a.get("name", "") for a in e.get("authors", [])]
        author = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        url = re.sub(r"v\d+$", "", e.get("id", e.get("link", "")))
        out.append(Item(
            source="", source_name="", kind="paper",
            url=url,
            title=re.sub(r"\s+", " ", e.get("title", "")).strip(),
            text=html_to_text(e.get("summary", ""), limit=1200),
            author=author,
            published=published,
        ))
    return out
