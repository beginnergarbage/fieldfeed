"""Bluesky through the public AppView. No account or key needed.

    - type: bluesky
      name: Bluesky
      accounts: [lpachter.bsky.social, mikelove.bsky.social]
      lists: [at://did:plc:.../app.bsky.graph.list/...]   # optional
"""

from __future__ import annotations

from ..models import Item, parse_iso
from ..urls import extract_links

API = "https://public.api.bsky.app/xrpc"


def _post_to_item(post: dict) -> Item | None:
    record = post.get("record") or {}
    text = record.get("text", "").strip()
    author = post.get("author") or {}
    handle = author.get("handle", "")
    rkey = post.get("uri", "").rsplit("/", 1)[-1]
    if not rkey or not handle:
        return None

    links = []
    for facet in record.get("facets") or []:
        for feat in facet.get("features") or []:
            if feat.get("uri"):
                links.append(feat["uri"])
    embed = post.get("embed") or {}
    ext = embed.get("external") or (embed.get("media") or {}).get("external") or {}
    if ext.get("uri"):
        links.append(ext["uri"])
    links = extract_links(" ".join(links)) if links else []

    title = text.split("\n", 1)[0][:140] or (ext.get("title") or "")
    if not text and not ext:
        return None
    return Item(
        source="", source_name="", kind="social",
        url=f"https://bsky.app/profile/{handle}/post/{rkey}",
        title=title,
        text=text if len(text) > len(title) else (ext.get("description") or text),
        author=f"{author.get('displayName') or handle} (@{handle})",
        published=parse_iso(record.get("createdAt") or post.get("indexedAt")),
        engagement={
            "likes": post.get("likeCount", 0),
            "reposts": post.get("repostCount", 0) + post.get("quoteCount", 0),
            "replies": post.get("replyCount", 0),
        },
        links=links,
    )


def fetch(src: dict, ctx) -> list[Item]:
    out, errors = [], []
    calls = [("app.bsky.feed.getAuthorFeed", {"actor": a, "limit": 50, "filter": "posts_no_replies"})
             for a in src.get("accounts", [])]
    calls += [("app.bsky.feed.getListFeed", {"list": l, "limit": 100}) for l in src.get("lists", [])]
    for method, params in calls:
        r = ctx.client.get(f"{API}/{method}", params=params)
        if r.status_code != 200:
            errors.append(params.get("actor") or params.get("list"))
            continue
        for entry in r.json().get("feed", []):
            if entry.get("reason"):  # skip reposts, keep authorship clean
                continue
            item = _post_to_item(entry.get("post") or {})
            if item and item.published and item.published >= ctx.since:
                out.append(item)
    if errors and not out:
        raise RuntimeError(f"no accounts resolved: {', '.join(errors[:5])}")
    return out
