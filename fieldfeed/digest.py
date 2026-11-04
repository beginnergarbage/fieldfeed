"""The two model passes.

1. Relevance: a cheap model scores every new item 0-3 against the field
   description. Scores are stored, so each item is scored once.
2. Stories: a stronger model writes a headline and a short summary for the top
   clusters, plus a few-bullet brief per time window. Cached by cluster.

Without a model both passes are skipped and the page falls back to titles and
excerpts, ranked by engagement and cross-source signal alone.
"""

from __future__ import annotations

import hashlib

from .config import Config
from .llm import LLM, LLMError
from .store import Store

RELEVANCE_SYSTEM = """You triage a news feed for people who work in {field}.

{description}

Score each item for how much it matters to someone working in this field:
3 = substantive work or news central to the field (new research, major org or policy news, an important argument)
2 = clearly on topic
1 = adjacent: general science or tech news with only a weak link to the field
0 = off topic, spam, or a homonym (e.g. "transformer" in a power electronics paper)

{profile}Judge by the content, not the source. Be strict with 3."""

RELEVANCE_PROMPT = """Items:

{items}

Return {{"scores": {{"<item number>": <0-3>, ...}}}} covering every item number above."""

STORIES_SYSTEM = """You write the front page of a dashboard that tracks {field}.

{description}

{profile}For each story cluster you get the original posts that make it up. Write:
- headline: at most 90 characters, plain and specific. Say what happened, name who did it. No clickbait, no colons-for-drama.
- summary: two or three sentences. What is new, and why it matters for people in the field. Use only facts present in the posts. If the posts disagree or react critically, say so. If the posts are thin, write one sentence rather than commenting on what is missing.

Write like a sharp colleague, not a press release. No hype words (groundbreaking, game-changing, revolutionary), no "this highlights", no em dashes."""

STORIES_PROMPT = """Story clusters:

{clusters}

Return {{"stories": {{"<cluster id>": {{"headline": "...", "summary": "..."}}, ...}}}} covering every cluster id above."""

BRIEF_SYSTEM = """You write the opening brief for a dashboard that tracks {field}. Given the top stories for
several time windows, write 3 to 5 bullets per window that tell a busy researcher what happened. Each bullet is
one sentence, concrete, and names who did what. Order by importance. No hype words, no em dashes."""

BRIEF_PROMPT = """{windows}

Return {{"briefs": {{"<window>": ["bullet", ...], ...}}}} for each window above."""


def _profile(cfg: Config) -> str:
    return f"The reader's own focus: {cfg.interest_profile}\n\n" if cfg.interest_profile else ""


def _fmt(cfg: Config, template: str) -> str:
    return template.format(field=cfg.field, description=cfg.description, profile=_profile(cfg))


def score_relevance(cfg: Config, store: Store, llm: LLM, items: list[dict], batch: int = 60,
                    log=print) -> int:
    pending = [it for it in items if it["relevance"] is None and not it["trusted"]]
    if not pending:
        return 0
    system = _fmt(cfg, RELEVANCE_SYSTEM)
    done = 0
    for start in range(0, len(pending), batch):
        chunk = pending[start:start + batch]
        lines = []
        for n, it in enumerate(chunk, 1):
            body = it["text"][:280].replace("\n", " ")
            lines.append(f"[{n}] ({it['source_name']}, {it['kind']}) {it['title']}\n    {body}")
        try:
            out = llm.complete_json(system, RELEVANCE_PROMPT.format(items="\n".join(lines)), "fast", 8000)
        except LLMError as e:
            log(f"  relevance batch {start // batch + 1} skipped: {e}")
            continue
        scores = {}
        for k, v in (out.get("scores") or {}).items():
            try:
                idx = int(str(k).strip("[]")) - 1
                val = max(0, min(3, int(v)))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(chunk):
                scores[chunk[idx]["id"]] = val
        store.set_relevance(scores)
        for it in chunk:
            if it["id"] in scores:
                it["relevance"] = scores[it["id"]]
        done += len(scores)
        log(f"  relevance: scored {done}/{len(pending)}")
    return done


def _cluster_block(c: dict, by_id: dict) -> str:
    lines = [f"## cluster {c['id']}  (sources: {', '.join(c['sources'])}"
             + (f"; also linked by {', '.join(c['mentioned_by'])}" if c["mentioned_by"] else "") + ")"]
    for iid in c["items"][:5]:
        it = by_id[iid]
        eng = ", ".join(f"{k} {v}" for k, v in it["engagement"].items() if v)
        lines.append(f"- [{it['source_name']}] {it['author']}: {it['title']}" + (f"  ({eng})" if eng else ""))
        if it["text"] and it["text"] != it["title"]:
            lines.append(f"  {it['text'][:600]}")
    return "\n".join(lines)


def write_stories(cfg: Config, store: Store, llm: LLM, windows: dict[str, list[dict]], by_id: dict,
                  top: dict[str, int], log=print) -> dict[str, dict]:
    """Return cluster id -> {headline, summary} for every cluster on any front page."""
    wanted = {}
    for w, clusters in windows.items():
        for c in clusters[: top[w]]:
            wanted[c["id"]] = c
    stories = {}
    missing = []
    for cid, c in wanted.items():
        cached = store.cache_get(f"story:{cid}")
        if cached:
            stories[cid] = cached
        else:
            missing.append(c)
    if missing and llm.enabled:
        system = _fmt(cfg, STORIES_SYSTEM)
        for start in range(0, len(missing), 12):
            chunk = missing[start:start + 12]
            prompt = STORIES_PROMPT.format(clusters="\n\n".join(_cluster_block(c, by_id) for c in chunk))
            try:
                out = llm.complete_json(system, prompt, "smart", 16000)
            except LLMError as e:
                log(f"  stories batch skipped: {e}")
                continue
            for cid, s in (out.get("stories") or {}).items():
                if cid in wanted and isinstance(s, dict) and s.get("headline"):
                    story = {"headline": str(s["headline"]).strip(), "summary": str(s.get("summary", "")).strip()}
                    stories[cid] = story
                    store.cache_set(f"story:{cid}", story)
            log(f"  stories: wrote {min(start + 12, len(missing))}/{len(missing)}")
    return stories


def write_briefs(cfg: Config, store: Store, llm: LLM, windows: dict[str, list[dict]], stories: dict,
                 top: dict[str, int], log=print) -> dict[str, list[str]]:
    if not llm.enabled:
        return {}
    blocks = []
    for w, clusters in windows.items():
        heads = [f"- {stories[c['id']]['headline']}: {stories[c['id']]['summary']}"
                 for c in clusters[: top[w]] if c["id"] in stories]
        if heads:
            blocks.append(f"### window {w}\n" + "\n".join(heads))
    if not blocks:
        return {}
    text = "\n\n".join(blocks)
    key = "brief:" + hashlib.sha1(text.encode()).hexdigest()[:16]
    if cached := store.cache_get(key):
        return cached
    try:
        out = llm.complete_json(BRIEF_SYSTEM.format(field=cfg.field), BRIEF_PROMPT.format(windows=text), "smart", 8000)
    except LLMError as e:
        log(f"  brief skipped: {e}")
        return {}
    briefs = {w: [str(b).strip() for b in v][:5] for w, v in (out.get("briefs") or {}).items()
              if w in windows and isinstance(v, list)}
    store.cache_set(key, briefs)
    return briefs


def fallback_story(rep: dict) -> dict:
    text = rep["text"]
    if text == rep["title"] or text.startswith(rep["title"]):
        text = text[len(rep["title"]):].strip()
    if len(text) > 280:
        text = text[:280].rsplit(" ", 1)[0] + "…"
    return {"headline": rep["title"], "summary": text, "generated": False}
