"""Writes the static site: index.html, assets, data.json and an RSS feed of top stories."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from importlib import resources
from pathlib import Path

from . import __version__
from .config import Config

ASSETS = ("app.js", "style.css", "favicon.svg")


def public_item(it: dict, cluster_of: dict) -> dict:
    text = it["text"]
    if len(text) > 420:
        text = text[:420].rsplit(" ", 1)[0] + "…"
    return {
        "id": it["id"],
        "source": it["source"],
        "source_name": it["source_name"],
        "kind": it["kind"],
        "url": it["url"],
        "title": it["title"],
        "text": text,
        "author": it["author"],
        "published": it["published"],
        "engagement": {k: v for k, v in it["engagement"].items() if v},
        "relevance": it["relevance"],
        "score": it["score"],
        "cluster": cluster_of.get(it["id"]),
    }


def build_site(cfg: Config, data: dict) -> Path:
    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    site = resources.files("fieldfeed") / "site"

    html = (site / "index.html").read_text()
    html = (html.replace("{{title}}", escape(cfg.title))
                .replace("{{tagline}}", escape(cfg.tagline))
                .replace("{{version}}", __version__)
                .replace("{{build}}", datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")))
    (out / "index.html").write_text(html)
    for name in ASSETS:
        with resources.as_file(site / name) as src:
            shutil.copy(src, out / name)

    (out / "data.json").write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    (out / "feed.xml").write_text(rss(cfg, data))
    (out / ".nojekyll").write_text("")
    return out


def rss(cfg: Config, data: dict) -> str:
    """Top stories of the shortest window as RSS, so people can subscribe to the front page."""
    window = data["meta"]["windows"][0]
    items = {it["id"]: it for it in data["items"]}
    entries = []
    for s in data["windows"][window]["stories"][:15]:
        rep = items.get(s["items"][0])
        if not rep:
            continue
        links = "".join(
            f'<li><a href="{escape(items[i]["url"])}">{escape(items[i]["source_name"])}: '
            f'{escape(items[i]["title"])}</a></li>'
            for i in s["items"][:8] if i in items
        )
        body = f"<p>{escape(s['summary'])}</p><ul>{links}</ul>"
        pub = datetime.fromisoformat(rep["published"])
        entries.append(
            "<item>"
            f"<title>{escape(s['headline'])}</title>"
            f"<link>{escape(rep['url'])}</link>"
            f'<guid isPermaLink="false">{s["id"]}</guid>'
            f"<pubDate>{format_datetime(pub)}</pubDate>"
            f"<description>{escape(body)}</description>"
            "</item>"
        )
    link = cfg.site_url or "https://example.com/"
    now = format_datetime(datetime.now(timezone.utc))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>{escape(cfg.title)}: top stories</title>"
        f"<link>{escape(link)}</link>"
        f"<description>{escape(cfg.tagline)}</description>"
        f"<lastBuildDate>{now}</lastBuildDate>"
        + "".join(entries)
        + "</channel></rss>\n"
    )
