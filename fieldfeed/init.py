"""`fieldfeed init "some field"`: draft a field.yaml with a model, then keep only
the sources that actually answer.

Models know which forums, newsletters and people matter in a field, but they
misremember feed URLs. So every proposed source is fetched before it is
written, and dead ones are dropped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

from .fetchers import USER_AGENT, Ctx, run_one
from .llm import LLM, LLMError

SYSTEM = """You set up a news dashboard that tracks one research or professional field. You know the
field's communities well: its forums, newsletters, lab and org blogs, podcasts, the arXiv categories its
papers land in, the subreddits where practitioners talk, and the people and organisations worth following
on Bluesky and X. Prefer sources that publish often and are taken seriously by people in the field."""

PROMPT = """Field: {field}

Propose sources. Give real, specific feed URLs (Substack feeds end in /feed; many blogs have /feed,
/rss or /feed.xml). Bluesky handles look like name.bsky.social or a domain. X handles without @.

Return JSON:
{{
  "title": "short name for the dashboard, 2-4 words",
  "tagline": "one sentence",
  "description": "2-3 sentences: what the field covers, what counts as on topic, what does not",
  "rss": [{{"name": "...", "url": "...", "kind": "newsletter|blog|podcast|news"}}],
  "arxiv": {{"categories": ["..."], "keywords": ["..."]}},
  "subreddits": ["..."],
  "hn_keywords": ["..."],
  "bluesky": ["..."],
  "x": ["..."]
}}

Aim for 15-30 RSS feeds, 10-20 arXiv keywords (only if the field publishes on arXiv), 2-5 subreddits,
10-25 Bluesky accounts and 20-40 X accounts."""


def init_field(field: str, path: str, provider: str | None, force: bool, command: str | None = None,
               log=print) -> None:
    target = Path(path)
    if target.exists() and not force:
        raise SystemExit(f"{target} exists. Pass --force to overwrite it, or -c to write somewhere else.")

    llm_cfg = {"provider": provider or "anthropic"}
    if command:
        llm_cfg = {"provider": "command", "command": command}
    llm = LLM(llm_cfg)
    if not llm.enabled:
        raise SystemExit(f"`init` needs a model: {llm.reason}. "
                         "Set ANTHROPIC_API_KEY, pick --provider, or pass --command \"claude -p\".")

    log(f"Asking {llm.reason} for sources in {field}…")
    try:
        draft = llm.complete_json(SYSTEM, PROMPT.format(field=field), "smart", 16000)
    except LLMError as e:
        raise SystemExit(f"Model call failed: {e}")

    sources = _candidate_sources(draft)
    log(f"Checking {len(sources)} proposed sources…")
    since = datetime.now(timezone.utc) - timedelta(days=60)
    kept = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True) as client:
        ctx = Ctx(client=client, since=since)
        for src in sources:
            if src["type"] == "x":
                kept.append(src)  # can't verify without a paid key; ships disabled
                continue
            if src["type"] == "bluesky":
                src["accounts"] = _live_bluesky(client, src["accounts"])
                if not src["accounts"]:
                    log("  ✗ Bluesky: none of the proposed handles exist")
                    continue
            r = run_one({**src, "key": src["name"]}, ctx)
            if r.error or not r.items:
                log(f"  ✗ {src['name']}: {r.error or 'no posts in the last 60 days'}")
                continue
            log(f"  ✓ {src['name']}: {len(r.items)} recent items")
            kept.append(src)

    config = {
        "field": field,
        "title": draft.get("title") or field.title(),
        "tagline": draft.get("tagline") or f"Everything happening in {field}, on one page.",
        "description": draft.get("description") or "",
        "interest_profile": "",
        "windows": ["24h", "7d", "14d", "30d"],
        "llm": {"provider": "anthropic", "fast_model": "claude-haiku-5-5", "smart_model": "claude-opus-5-5"},
        "sources": kept,
    }
    header = (f"# fieldfeed pack: {field}\n# Drafted by `fieldfeed init` on {datetime.now():%Y-%m-%d}. "
              "Every source except X was fetched and returned recent posts.\n"
              "# Edit freely, then run `fieldfeed check` and `fieldfeed run`.\n\n")
    target.write_text(header + yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=100))
    working = sum(1 for s in kept if s["type"] != "x")
    log(f"\nWrote {target}: {working} working sources (of {len(sources)} proposed).")


def _candidate_sources(d: dict) -> list[dict]:
    out = []
    for r in d.get("rss") or []:
        if r.get("url"):
            out.append({"type": "rss", "name": r.get("name") or r["url"], "url": r["url"],
                        "kind": r.get("kind") if r.get("kind") in ("newsletter", "blog", "podcast", "news") else "blog"})
    ax = d.get("arxiv") or {}
    if ax.get("categories") or ax.get("keywords"):
        out.append({"type": "arxiv", "name": "arXiv", "categories": ax.get("categories") or [],
                    "keywords": ax.get("keywords") or [], "max_results": 500})
    if d.get("bluesky"):
        out.append({"type": "bluesky", "name": "Bluesky", "accounts": [h.lstrip("@") for h in d["bluesky"]]})
    if d.get("x"):
        out.append({"type": "x", "name": "X", "enabled": False, "backend": "twitterapi_io",
                    "max_per_account": 15, "accounts": [h.lstrip("@") for h in d["x"]]})
    if d.get("subreddits"):
        out.append({"type": "reddit", "name": "Reddit",
                    "subreddits": [s.removeprefix("r/") for s in d["subreddits"]]})
    if d.get("hn_keywords"):
        out.append({"type": "hackernews", "name": "Hacker News", "keywords": d["hn_keywords"], "min_points": 30})
    return out


def _live_bluesky(client: httpx.Client, handles: list[str]) -> list[str]:
    live = []
    for h in handles:
        r = client.get("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile", params={"actor": h})
        if r.status_code == 200 and r.json().get("postsCount", 0) > 5:
            live.append(h)
    return live
