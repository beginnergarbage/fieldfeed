"""Source fetchers. Each one turns a `sources:` entry into Items.

A fetcher is a function `fetch(src: dict, ctx: Ctx) -> list[Item]`. Fetching is
plain code on purpose: it is deterministic, free, and easy to debug. The model
only sees what comes out of here.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

import httpx

from ..models import Item
from . import arxiv, bluesky, hackernews, reddit, rss, x

FETCHERS = {
    "rss": rss.fetch,
    "arxiv": arxiv.fetch,
    "bluesky": bluesky.fetch,
    "reddit": reddit.fetch,
    "hackernews": hackernews.fetch,
    "x": x.fetch,
}

USER_AGENT = "fieldfeed/0.1 (open-source field dashboard; RSS reader)"


@dataclass
class Ctx:
    client: httpx.Client
    since: datetime


@dataclass
class Result:
    key: str
    name: str
    items: list[Item]
    error: str | None
    seconds: float


def run_one(src: dict, ctx: Ctx) -> Result:
    start = time.monotonic()
    fn = FETCHERS.get(src["type"])
    if fn is None:
        return Result(src["key"], src["name"], [], f"unknown source type {src['type']!r}", 0.0)
    try:
        items = fn(src, ctx)
        for it in items:
            it.source = src["key"]
            it.source_name = src["name"]
            it.trusted = it.trusted or bool(src.get("trusted"))
        items = [it for it in items if it.published is None or it.published >= ctx.since]
        return Result(src["key"], src["name"], items, None, time.monotonic() - start)
    except Exception as e:  # one broken source never takes the run down
        msg = f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else e}"
        return Result(src["key"], src["name"], [], msg[:300], time.monotonic() - start)


def fetch_all(sources: list[dict], since: datetime, workers: int = 6) -> list[Result]:
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as client:
        ctx = Ctx(client=client, since=since)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda s: run_one(s, ctx), sources))
