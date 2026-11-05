"""Command line entry point.

    fieldfeed run            fetch, rank, summarize, build (what the cron job runs)
    fieldfeed build          rank and rebuild the site from what is already stored
    fieldfeed serve          serve the built site on localhost
    fieldfeed check          test every source in field.yaml and report what works
    fieldfeed init "field"   draft a field.yaml for any field with your model, verify every feed
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .build import build_site, public_item
from .config import WINDOW_DAYS, Config, load
from .digest import fallback_story, score_relevance, write_briefs, write_stories
from .fetchers import fetch_all
from .llm import LLM
from .rank import cluster, mentions, rank_window, score_items
from .store import Store

TOP = {"24h": 8, "7d": 10, "14d": 12, "30d": 12}


def log(msg: str) -> None:
    print(msg, flush=True)


def do_fetch(cfg: Config, store: Store) -> list[dict]:
    last = store.meta_get("last_fetch")
    days = cfg.max_days
    if last and store.count():
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days
        days = min(cfg.max_days, max(7, age + 2))  # refresh a week of engagement numbers
    since = datetime.now(timezone.utc) - timedelta(days=days)
    log(f"Fetching {len(cfg.sources)} sources, last {days} days")
    results = fetch_all(cfg.sources, since)
    status = []
    for r in results:
        new = store.upsert(r.items) if r.items else 0
        mark = "✗" if r.error else "✓"
        detail = r.error or f"{len(r.items)} item{'' if len(r.items) == 1 else 's'}, {new} new"
        log(f"  {mark} {r.name:<32} {detail}  ({r.seconds:.1f}s)")
        status.append({"key": r.key, "name": r.name, "fetched": len(r.items), "error": r.error})
    store.meta_set("last_fetch", datetime.now(timezone.utc).isoformat())
    store.meta_set("source_status", status)
    store.prune(cfg.max_days + 2)
    return status


def do_build(cfg: Config, store: Store, llm: LLM) -> Path:
    items = store.items_since(cfg.max_days)
    if llm.enabled:
        log(f"Model: {llm.reason}")
        score_relevance(cfg, store, llm, items, log=log)
    else:
        log(f"Model: off ({llm.reason}). Ranking on engagement and cross-source signal only.")

    score_items(items)
    by_id = {it["id"]: it for it in items}
    groups = cluster(items)
    mention_map = mentions(items)
    windows = {w: rank_window(items, groups, mention_map, WINDOW_DAYS[w]) for w in cfg.windows}

    stories = write_stories(cfg, store, llm, windows, by_id, TOP, log=log)
    briefs = write_briefs(cfg, store, llm, windows, stories, TOP, log=log)

    cluster_of = {}
    for g in groups:
        if len(g) > 1:
            cid = items[g[0]]["id"]
            for i in g:
                cluster_of[items[i]["id"]] = cid

    out_windows = {}
    for w, clusters in windows.items():
        front = []
        for c in clusters[: TOP[w]]:
            story = stories.get(c["id"]) or fallback_story(by_id[c["rep"]])
            front.append({**c, **story, "generated": c["id"] in stories})
        out_windows[w] = {"brief": briefs.get(w, []), "stories": front}

    status = store.meta_get("source_status", [])
    counts = {}
    for it in items:
        counts[it["source"]] = counts.get(it["source"], 0) + 1
    sources = []
    for s in cfg.sources:
        st = next((x for x in status if x["key"] == s["key"]), {})
        sources.append({"key": s["key"], "name": s["name"], "type": s["type"],
                        "count": counts.get(s["key"], 0), "error": st.get("error")})

    data = {
        "meta": {
            "title": cfg.title,
            "field": cfg.field,
            "tagline": cfg.tagline,
            "description": cfg.description,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "windows": cfg.windows,
            "llm": llm.describe(),
            "sources": sources,
            "version": __version__,
        },
        "windows": out_windows,
        "items": [public_item(it, cluster_of) for it in items],
    }
    out = build_site(cfg, data)
    log(f"Built {out}/ with {len(items)} items, {sum(len(v['stories']) for v in out_windows.values())} "
        f"front-page stories ({llm.calls} model calls, {llm.input_tokens:,} in / {llm.output_tokens:,} out tokens)")
    return out


def cmd_run(args) -> None:
    cfg = load(args.config)
    store = Store(cfg.db_path)
    if not args.no_fetch:
        do_fetch(cfg, store)
    do_build(cfg, store, LLM(cfg.llm))


def cmd_build(args) -> None:
    cfg = load(args.config)
    do_build(cfg, Store(cfg.db_path), LLM(cfg.llm))


def cmd_serve(args) -> None:
    cfg = load(args.config)
    root = Path(cfg.out_dir)
    if not (root / "index.html").exists():
        sys.exit(f"Nothing built in {root}/ yet. Run `fieldfeed run` first.")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    with socketserver.ThreadingTCPServer(("127.0.0.1", args.port), handler) as httpd:
        log(f"Serving {root}/ at http://127.0.0.1:{args.port}")
        httpd.serve_forever()


def cmd_check(args) -> None:
    cfg = load(args.config)
    since = datetime.now(timezone.utc) - timedelta(days=cfg.max_days)
    bad = 0
    for r in fetch_all(cfg.sources, since):
        ok = not r.error and r.items
        bad += not ok
        detail = r.error or (f"{len(r.items)} items in the last {cfg.max_days} days" if r.items else "reachable, but nothing recent")
        log(f"  {'✓' if ok else '✗'} {r.name:<32} {detail}")
    llm = LLM(cfg.llm)
    log(f"\nModel: {llm.reason if llm.enabled else 'off (' + llm.reason + ')'}")
    if bad:
        log(f"{bad} source(s) returned nothing. Fix or remove them in {args.config}.")


def cmd_init(args) -> None:
    from .init import init_field
    init_field(args.field, args.config, args.provider, args.force, args.command, log=log)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="fieldfeed", description="One page for everything happening in your field.")
    p.add_argument("--version", action="version", version=f"fieldfeed {__version__}")
    p.add_argument("-c", "--config", default="field.yaml", help="path to field.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="fetch, rank, summarize and build the site")
    r.add_argument("--no-fetch", action="store_true", help="skip fetching, rebuild from stored items")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("build", help="rebuild the site from stored items").set_defaults(fn=cmd_build)
    s = sub.add_parser("serve", help="serve the built site locally")
    s.add_argument("--port", type=int, default=8321)
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("check", help="test every source and the model config").set_defaults(fn=cmd_check)
    i = sub.add_parser("init", help="draft a field.yaml for any field")
    i.add_argument("field", help='e.g. "computational biology"')
    i.add_argument("--provider", default=None, help="model provider to draft with (default: anthropic)")
    i.add_argument("--command", default=None, help='draft with an agent CLI instead, e.g. "claude -p"')
    i.add_argument("--force", action="store_true", help="overwrite an existing field.yaml")
    i.set_defaults(fn=cmd_init)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
