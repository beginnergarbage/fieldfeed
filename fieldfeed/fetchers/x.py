"""X (Twitter). Off by default because every read costs money.

    - type: x
      name: X
      enabled: true
      backend: twitterapi_io    # or: official
      accounts: [lpachter, mikelove]
      list_id: "1234567890"     # optional, official backend reads lists cheaply
      max_per_account: 20

Keys come from the environment: TWITTERAPI_IO_KEY for twitterapi.io,
X_BEARER_TOKEN for the official API. The official API bills about $0.005 per
post read; third-party readers are far cheaper but sit outside X's terms.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..models import Item, parse_iso
from ..urls import extract_links


def _item(tid, handle, name, text, created, likes, rts, replies, urls) -> Item:
    first = text.split("\n", 1)[0]
    return Item(
        source="", source_name="", kind="social",
        url=f"https://x.com/{handle}/status/{tid}",
        title=first[:140],
        text=text,
        author=f"{name or handle} (@{handle})",
        published=created,
        engagement={"likes": likes or 0, "reposts": rts or 0, "replies": replies or 0},
        links=extract_links(" ".join(urls)) if urls else [],
    )


def _twitterapi_io(src: dict, ctx) -> list[Item]:
    key = os.environ.get(src.get("api_key_env", "TWITTERAPI_IO_KEY"))
    if not key:
        raise RuntimeError("TWITTERAPI_IO_KEY is not set")
    headers = {"X-API-Key": key}
    base = "https://api.twitterapi.io/twitter"
    calls = [("user/last_tweets", {"userName": a}) for a in src.get("accounts", [])]
    if src.get("list_id"):
        calls.append(("list/tweets", {"listId": src["list_id"]}))
    out = []
    for path, params in calls:
        r = ctx.client.get(f"{base}/{path}", params=params, headers=headers)
        if r.status_code != 200:
            continue
        body = r.json()
        tweets = (body.get("data") or {}).get("tweets") or body.get("tweets") or []
        for t in tweets[: int(src.get("max_per_account", 20))]:
            try:
                created = parsedate_to_datetime(t["createdAt"]).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            if created < ctx.since:
                continue
            author = t.get("author") or {}
            urls = [u.get("expanded_url", "") for u in (t.get("entities") or {}).get("urls", [])]
            out.append(_item(t["id"], author.get("userName", ""), author.get("name", ""),
                             t.get("text", ""), created, t.get("likeCount"), t.get("retweetCount"),
                             t.get("replyCount"), urls))
    return out


def _official(src: dict, ctx) -> list[Item]:
    token = os.environ.get(src.get("api_key_env", "X_BEARER_TOKEN"))
    if not token:
        raise RuntimeError("X_BEARER_TOKEN is not set")
    headers = {"Authorization": f"Bearer {token}"}
    api = "https://api.x.com/2"
    fields = {
        "tweet.fields": "created_at,public_metrics,entities,author_id",
        "expansions": "author_id",
        "user.fields": "username,name",
        "max_results": 100,
    }
    timelines = []
    if src.get("list_id"):
        timelines.append(f"{api}/lists/{src['list_id']}/tweets")
    accounts = src.get("accounts", [])
    for i in range(0, len(accounts), 100):
        r = ctx.client.get(f"{api}/users/by", params={"usernames": ",".join(accounts[i:i + 100])},
                           headers=headers)
        r.raise_for_status()
        for u in r.json().get("data", []):
            timelines.append(f"{api}/users/{u['id']}/tweets")
    out = []
    for url in timelines:
        params = dict(fields)
        if "/users/" in url:
            params.update({"exclude": "replies,retweets", "max_results": int(src.get("max_per_account", 20)),
                           "start_time": ctx.since.strftime("%Y-%m-%dT%H:%M:%SZ")})
        r = ctx.client.get(url, params=params, headers=headers)
        if r.status_code != 200:
            continue
        body = r.json()
        users = {u["id"]: u for u in (body.get("includes") or {}).get("users", [])}
        for t in body.get("data", []):
            created = parse_iso(t.get("created_at")) or datetime.now(timezone.utc)
            if created < ctx.since:
                continue
            u = users.get(t.get("author_id"), {})
            m = t.get("public_metrics") or {}
            urls = [x.get("expanded_url", "") for x in (t.get("entities") or {}).get("urls", [])]
            out.append(_item(t["id"], u.get("username", "i"), u.get("name", ""), t.get("text", ""),
                             created, m.get("like_count"), m.get("retweet_count", 0) + m.get("quote_count", 0),
                             m.get("reply_count"), urls))
    return out


def fetch(src: dict, ctx) -> list[Item]:
    backend = src.get("backend", "twitterapi_io")
    if backend == "official":
        return _official(src, ctx)
    if backend == "twitterapi_io":
        return _twitterapi_io(src, ctx)
    raise RuntimeError(f"unknown X backend {backend!r}")
