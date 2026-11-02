"""Subreddits through their public RSS feeds. No OAuth app needed.

    - type: reddit
      name: Reddit
      subreddits: [ControlProblem, mlscaling]

Reddit's JSON endpoints refuse unauthenticated servers, the RSS feeds do not,
but they carry no scores. Reddit posts are ranked by relevance and by whether
other sources discuss the same link.
"""

from __future__ import annotations

import time

from ..models import Item
from .rss import parse


def fetch(src: dict, ctx) -> list[Item]:
    out, seen, failures = [], set(), []
    for sub in src.get("subreddits", []):
        for path in ("new/.rss", "top/.rss?t=week"):
            r = ctx.client.get(f"https://www.reddit.com/r/{sub}/{path}")
            if r.status_code != 200:
                failures.append(f"r/{sub} {r.status_code}")
                time.sleep(2)
                continue
            for it in parse(r.content, kind="discussion"):
                if it.url in seen:
                    continue
                seen.add(it.url)
                it.author = f"r/{sub} · {it.author.removeprefix('/u/')}"
                # The "[link]" anchor in the body is the submitted URL, which is the story.
                it.links = [l for l in it.links if "reddit.com" not in l][:3]
                out.append(it)
            time.sleep(1)  # be polite, Reddit rate limits unauthenticated readers hard
    if failures and not out:
        raise RuntimeError("; ".join(failures[:4]))
    return out
