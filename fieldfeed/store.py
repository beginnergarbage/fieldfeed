"""SQLite storage. One file, no server."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Item, parse_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    canon TEXT NOT NULL,
    source TEXT NOT NULL,
    source_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    published TEXT NOT NULL,
    fetched TEXT NOT NULL,
    engagement TEXT NOT NULL DEFAULT '{}',
    links TEXT NOT NULL DEFAULT '[]',
    trusted INTEGER NOT NULL DEFAULT 0,
    relevance INTEGER
);
CREATE INDEX IF NOT EXISTS items_published ON items(published);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # items ---------------------------------------------------------------

    def upsert(self, items: list[Item]) -> int:
        """Insert new items, refresh engagement and text on known ones.

        Returns the number of items that were not in the store before.
        """
        fetched = now().isoformat()
        new = 0
        for it in items:
            if not it.published:
                it.published = now()
            row = self.db.execute("SELECT 1 FROM items WHERE id = ?", (it.id,)).fetchone()
            if row is None:
                new += 1
            self.db.execute(
                """
                INSERT INTO items (id, canon, source, source_name, kind, url, title, text, author,
                                   published, fetched, engagement, links, trusted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    source_name = excluded.source_name,
                    kind = excluded.kind,
                    title = excluded.title,
                    text = CASE WHEN length(excluded.text) > 0 THEN excluded.text ELSE items.text END,
                    engagement = CASE WHEN excluded.engagement != '{}' THEN excluded.engagement ELSE items.engagement END,
                    links = excluded.links,
                    trusted = MAX(items.trusted, excluded.trusted),
                    fetched = excluded.fetched
                """,
                (
                    it.id, it.canon, it.source, it.source_name, it.kind, it.url, it.title,
                    it.text, it.author, it.published.isoformat(), fetched,
                    json.dumps(it.engagement), json.dumps(it.links), int(it.trusted),
                ),
            )
        self.db.commit()
        return new

    def items_since(self, days: float) -> list[dict]:
        cutoff = (now() - timedelta(days=days)).isoformat()
        rows = self.db.execute(
            "SELECT * FROM items WHERE published >= ? ORDER BY published DESC", (cutoff,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["engagement"] = json.loads(d["engagement"])
            d["links"] = json.loads(d["links"])
            d["published_dt"] = parse_iso(d["published"])
            out.append(d)
        return out

    def set_relevance(self, scores: dict[str, int]) -> None:
        self.db.executemany(
            "UPDATE items SET relevance = ? WHERE id = ?", [(v, k) for k, v in scores.items()]
        )
        self.db.commit()

    def prune(self, keep_days: int) -> int:
        cutoff = (now() - timedelta(days=keep_days)).isoformat()
        cur = self.db.execute("DELETE FROM items WHERE published < ?", (cutoff,))
        self.db.execute("DELETE FROM cache WHERE created < ?", (cutoff,))
        self.db.commit()
        return cur.rowcount

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    # cache and meta ------------------------------------------------------

    def cache_get(self, key: str):
        row = self.db.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_set(self, key: str, value) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cache (key, value, created) VALUES (?, ?, ?)",
            (key, json.dumps(value), now().isoformat()),
        )
        self.db.commit()

    def meta_get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def meta_set(self, key: str, value) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value))
        )
        self.db.commit()
