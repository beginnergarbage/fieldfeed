from datetime import datetime, timedelta, timezone

from fieldfeed.rank import cluster, mentions, rank_window, score_items
from fieldfeed.urls import canonical

NOW = datetime.now(timezone.utc)


def item(i, source, url, title, links=(), kind="blog", eng=None, hours=2):
    return {
        "id": f"i{i}", "source": source, "source_name": source, "kind": kind, "url": url,
        "canon": canonical(url), "title": title, "text": "", "links": list(links),
        "engagement": eng or {}, "relevance": None, "trusted": False,
        "published": (NOW - timedelta(hours=hours)).isoformat(),
        "published_dt": NOW - timedelta(hours=hours),
    }


def test_posts_linking_the_same_paper_merge_and_rank_first():
    paper = "https://arxiv.org/abs/2610.00001"
    items = [
        item(0, "arXiv", paper, "Protein language models at scale", kind="paper"),
        item(1, "Bluesky", "https://bsky.app/profile/a.b/post/xyz", "new paper", [paper], "social", {"likes": 40}),
        item(2, "Hacker News", "https://news.ycombinator.com/item?id=41000001", "Protein language models",
             [paper], "discussion", {"points": 80}),
        item(3, "Blog", "https://example.com/unrelated/post-about-other-things", "Unrelated news"),
    ]
    score_items(items)
    groups = cluster(items)
    ranked = rank_window(items, groups, mentions(items), days=7, now=NOW)
    assert set(ranked[0]["items"]) == {"i0", "i1", "i2"}
    assert ranked[0]["rep"] == "i0"  # the paper itself, not a post about it


def test_roundups_do_not_glue_stories_together():
    links = [f"https://example.com/story/number-{n}-long-slug" for n in range(10)]
    items = [item(0, "Newsletter", "https://news.example.com/p/weekly-roundup", "Roundup", links, "newsletter")]
    items += [item(n + 1, "Blog", links[n], f"Story {n}") for n in range(3)]
    score_items(items)
    assert len(cluster(items)) == 4
    assert mentions(items)[canonical(links[0])] == {"Newsletter"}


def test_off_topic_clusters_are_dropped():
    items = [item(0, "Blog", "https://example.com/a/long-post-slug-here", "Off topic")]
    items[0]["relevance"] = 0
    score_items(items)
    assert rank_window(items, cluster(items), {}, days=7, now=NOW) == []
