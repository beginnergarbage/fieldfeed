"""URL canonicalization.

Two posts about the same thing usually point at the same URL in slightly
different spellings: arxiv.org/abs vs /pdf, twitter.com vs x.com,
tracking params. Canonical keys let the ranker see that
they are one story.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit

_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_TWEET = re.compile(r"(?:twitter\.com|x\.com)/[^/]+/status(?:es)?/(\d+)", re.I)
_BSKY = re.compile(r"bsky\.app/profile/([^/]+)/post/([a-z0-9]+)", re.I)
_YT = re.compile(r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/)([\w-]{11})", re.I)
_KEYED = re.compile(r"^(arxiv|x|bsky|yt):")
_HREF = re.compile(r"""href=["']([^"'#]+)["']""", re.I)
_BARE = re.compile(r"https?://[^\s<>\"')\]]+")
_TAG = re.compile(r"<[^>]*>")

_DROP_PARAMS = {
    "ref", "ref_src", "s", "t", "si", "source", "fbclid", "gclid", "mc_cid", "mc_eid", "commentid",
    "action", "view_token", "r", "triedredirect", "publication_id", "post_id", "isfreemail", "token",
}

# Hosts whose links never identify a story: profiles, share buttons, the
# newsletter's own subscribe page and so on.
_NOISE_HOSTS = {
    "substack.com", "twitter.com", "x.com", "bsky.app", "facebook.com", "linkedin.com",
    "instagram.com", "t.me", "discord.gg", "patreon.com", "buymeacoffee.com",
    "substackcdn.com", "cdn.substack.com", "gravatar.com", "feedburner.com", "w3.org",
    "schema.org", "purl.org", "ogp.me", "fonts.googleapis.com",
}


def canonical(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if m := _ARXIV.search(url):
        return f"arxiv:{m.group(1)}"
    if m := _TWEET.search(url):
        return f"x:{m.group(1)}"
    if m := _BSKY.search(url):
        return f"bsky:{m.group(1).lower()}/{m.group(2)}"
    if m := _YT.search(url):
        return f"yt:{m.group(1)}"
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    for prefix in ("www.", "m.", "mobile.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    path = re.sub(r"/+$", "", parts.path) or ""
    query = [
        (k, v) for k, v in parse_qsl(parts.query)
        if not k.lower().startswith("utm_") and k.lower() not in _DROP_PARAMS
    ]
    q = f"?{urlencode(sorted(query))}" if query else ""
    return f"{host}{path}{q}"


def is_story_link(url: str) -> bool:
    """True when a link plausibly points at a specific piece of content."""
    c = canonical(url)
    if not c:
        return False
    if _KEYED.match(c):
        return True
    host, _, rest = c.partition("/")
    if host in _NOISE_HOSTS:
        return False
    if host.endswith(".substack.com") and rest.startswith(("subscribe", "about", "archive")):
        return False
    # Bare domains and one-segment paths (profiles, home pages) are not stories.
    segments = [s for s in rest.split("?")[0].split("/") if s]
    if not segments:
        return False
    if len(segments) == 1 and len(segments[0]) < 12:
        return False
    if re.search(r"\.(png|jpe?g|gif|webp|svg|mp3|mp4|css|js)$", rest, re.I):
        return False
    return True


def extract_links(text: str) -> list[str]:
    # Bare URLs only from visible text, so attributes like xmlns are not read as links.
    found = [unescape(u) for u in _HREF.findall(text)] + _BARE.findall(unescape(_TAG.sub(" ", text)))
    seen, out = set(), []
    for url in found:
        url = url.rstrip(".,;:")
        if not url.startswith("http"):
            continue
        c = canonical(url)
        if c in seen or not is_story_link(url):
            continue
        seen.add(c)
        out.append(url)
    return out
