"""Clustering and scoring. Plain code, no model.

The strongest signal of "this mattered" is the same thing showing up in many
places: a paper on arXiv that gets linked from Bluesky, posted to Reddit and
discussed on HN. So items are grouped into story clusters by shared canonical
URLs (and near-identical titles), and clusters that span sources rank higher.

Roundup posts (a newsletter linking to forty things) are not allowed to merge
clusters, or one weekly digest would glue the whole week together. Their links count
as mentions instead, which give the linked stories a boost.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .urls import canonical

ENGAGEMENT_WEIGHTS = {"score": 1.0, "points": 1.0, "likes": 1.0, "reposts": 2.0, "comments": 0.5, "replies": 0.3}
RELEVANCE_MULT = {0: 0.15, 1: 0.6, 2: 1.0, 3: 1.35}
ROUNDUP_LINKS = 4  # items with more outbound links than this count as roundups

_STOP = set("""a an and are as at be by for from has have how in is it its new of on or our that the
their this to was we what when why will with you your via vs using towards toward""".split())


def engagement_value(eng: dict) -> float | None:
    if not eng:
        return None
    return sum(ENGAGEMENT_WEIGHTS.get(k, 0) * (v or 0) for k, v in eng.items())


def _tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", title.lower()) if t not in _STOP and len(t) > 2}


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def score_items(items: list[dict]) -> None:
    """Attach `score` in [0, ~1.35] to each item: engagement percentile within
    its own source, times a relevance multiplier."""
    by_source = defaultdict(list)
    for it in items:
        v = engagement_value(it["engagement"])
        it["_eng"] = v
        if v is not None:
            by_source[it["source"]].append(v)
    for vals in by_source.values():
        vals.sort()
    for it in items:
        vals = by_source.get(it["source"])
        if it["_eng"] is None or not vals:
            # No engagement numbers. Editorial sources (papers, blogs, newsletters)
            # sit mid-pack; unscored social and Reddit posts start low.
            pct = 0.15 if it["kind"] in ("social", "discussion") else 0.4
        elif len(vals) == 1:
            pct = 0.5
        else:
            # midpoint rank so ties share a percentile
            lo = _bisect(vals, it["_eng"], left=True)
            hi = _bisect(vals, it["_eng"], left=False)
            pct = ((lo + hi - 1) / 2) / (len(vals) - 1)
        base = 0.25 + 0.75 * pct
        rel = it.get("relevance")
        if it.get("trusted") and rel is None:
            rel = 2
        mult = RELEVANCE_MULT.get(rel, 0.9 if it["kind"] == "paper" else 1.0)
        it["score"] = round(base * mult, 4)


def _bisect(vals: list[float], x: float, left: bool) -> int:
    lo, hi = 0, len(vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if vals[mid] < x or (not left and vals[mid] == x):
            lo = mid + 1
        else:
            hi = mid
    return lo


def cluster(items: list[dict]) -> list[list[int]]:
    """Group item indices that are about the same story."""
    n = len(items)
    uf = _UF(n)
    owner: dict[str, int] = {}

    def claim(key: str, i: int) -> None:
        if key in owner:
            uf.union(owner[key], i)
        else:
            owner[key] = i

    for i, it in enumerate(items):
        claim(it["canon"], i)
        if len(it["links"]) <= ROUNDUP_LINKS:
            for link in it["links"]:
                claim(canonical(link), i)

    # Near-identical titles (same paper posted as an arXiv entry and a blog post).
    token_sets = {}
    index = defaultdict(list)
    for i, it in enumerate(items):
        if it["kind"] == "social":
            continue
        toks = _tokens(it["title"])
        if len(toks) < 4:
            continue
        token_sets[i] = toks
        for t in toks:
            index[t].append(i)
    for i, toks in token_sets.items():
        rare = sorted(toks, key=lambda t: len(index[t]))[:2]
        for t in rare:
            for j in index[t]:
                if j <= i:
                    continue
                other = token_sets[j]
                if len(toks & other) / len(toks | other) >= 0.75:
                    uf.union(i, j)

    groups = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    return list(groups.values())


def mentions(items: list[dict]) -> dict[str, set[str]]:
    """canonical URL -> sources of roundup posts that linked to it."""
    out = defaultdict(set)
    for it in items:
        if len(it["links"]) > ROUNDUP_LINKS:
            for link in it["links"]:
                out[canonical(link)].add(it["source_name"])
    return out


def _representative(members: list[dict], linked: set[str]) -> dict:
    """The item that *is* the story: the thing others link to, else the best
    non-social item, else the best post."""
    targets = [m for m in members if m["canon"] in linked]
    pool = targets or [m for m in members if m["kind"] != "social"] or members
    return max(pool, key=lambda m: (m["score"], m["published"]))


def rank_window(items: list[dict], groups: list[list[int]], mention_map: dict, days: float,
                now: datetime | None = None, limit: int = 30) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    linked_targets = {canonical(l) for it in items if len(it["links"]) <= ROUNDUP_LINKS for l in it["links"]}
    clusters = []
    for g in groups:
        members = [items[i] for i in g if items[i]["published_dt"] >= cutoff]
        if not members:
            continue
        if all(m.get("relevance") == 0 for m in members):
            continue
        sources = sorted({m["source_name"] for m in members})
        canons = {m["canon"] for m in members} | {
            canonical(l) for m in members if len(m["links"]) <= ROUNDUP_LINKS for l in m["links"]
        }
        mentioned_by = set().union(*(mention_map.get(c, set()) for c in canons)) - set(sources)
        best = max(m["score"] for m in members)
        newest = max(m["published_dt"] for m in members)
        freshness = max(0.0, 1 - (now - newest).total_seconds() / (days * 86400))
        score = (
            best
            + 0.30 * min(4, len(sources) - 1)
            + 0.08 * min(6, len(members) - 1)
            + 0.12 * min(5, len(mentioned_by))
        ) * (0.7 + 0.3 * freshness)
        rep = _representative(members, linked_targets)
        members.sort(key=lambda m: (m["id"] != rep["id"], -m["score"]))
        clusters.append({
            "id": hashlib.sha1("|".join(sorted(m["id"] for m in members)).encode()).hexdigest()[:12],
            "score": round(score, 4),
            "rep": rep["id"],
            "items": [m["id"] for m in members],
            "sources": sources,
            "mentioned_by": sorted(mentioned_by),
        })
    clusters.sort(key=lambda c: -c["score"])
    return clusters[:limit]
