"""field.yaml loading. The file is the whole customization surface."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

WINDOW_DAYS = {"24h": 1, "7d": 7, "14d": 14, "30d": 30}


@dataclass
class Config:
    field: str
    description: str
    interest_profile: str = ""
    title: str = ""
    tagline: str = ""
    windows: list[str] = field(default_factory=lambda: ["24h", "7d", "14d", "30d"])
    llm: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    db_path: str = "data/fieldfeed.db"
    out_dir: str = "public"
    site_url: str = ""
    path: Path | None = None

    @property
    def max_days(self) -> int:
        return max(WINDOW_DAYS[w] for w in self.windows)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "source"


def load(path: str | Path = "field.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"No config at {path}. Copy one from packs/ or run `fieldfeed init \"your field\"`.")
    raw = yaml.safe_load(path.read_text()) or {}
    if "field" not in raw:
        raise SystemExit(f"{path}: `field` is required.")

    windows = raw.get("windows") or ["24h", "7d", "14d", "30d"]
    bad = [w for w in windows if w not in WINDOW_DAYS]
    if bad:
        raise SystemExit(f"{path}: unknown windows {bad}. Use any of {list(WINDOW_DAYS)}.")

    sources = []
    seen = set()
    for i, s in enumerate(raw.get("sources") or []):
        if not isinstance(s, dict) or "type" not in s:
            raise SystemExit(f"{path}: source #{i + 1} needs a `type`.")
        if s.get("enabled") is False:
            continue
        s = dict(s)
        s.setdefault("name", s["type"].title())
        key = s.get("key") or slug(s["name"])
        while key in seen:
            key += "-2"
        seen.add(key)
        s["key"] = key
        sources.append(s)

    return Config(
        field=raw["field"],
        description=(raw.get("description") or "").strip(),
        interest_profile=(raw.get("interest_profile") or "").strip(),
        title=raw.get("title") or raw["field"],
        tagline=raw.get("tagline") or f"Everything happening in {raw['field']}, on one page.",
        windows=windows,
        llm=raw.get("llm") or {},
        sources=sources,
        db_path=raw.get("db_path", "data/fieldfeed.db"),
        out_dir=raw.get("out_dir", "public"),
        site_url=os.environ.get("FIELDFEED_SITE_URL") or raw.get("site_url", ""),
        path=path,
    )
