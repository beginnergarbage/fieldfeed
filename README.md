# fieldfeed

A research field happens in a dozen places at once. In computational biology that means bioRxiv and arXiv, journal feeds, lab blogs, a few newsletters, Bluesky, X, Reddit and Hacker News. Keeping up means checking all of them, and the important thing on any given day is usually the one that shows up in several at once. fieldfeed pulls those sources into one page, groups posts that are about the same story, ranks the stories, and has a model of your choice write a headline and a two-sentence summary for each. The original posts are listed under every summary so you can always click through to the source.

It runs as a static site. Fork the repo, edit one YAML file, add an API key, and GitHub Actions rebuilds the page every four hours on GitHub Pages. There's no server and no database to host.

## Quick start

```bash
git clone https://github.com/<you>/fieldfeed && cd fieldfeed
uv sync                                  # or: pip install .
cp packs/computational-biology.yaml field.yaml
export ANTHROPIC_API_KEY=sk-ant-...      # optional, see "Models"
uv run fieldfeed run
uv run fieldfeed serve                   # http://127.0.0.1:8321
```

Without a model key the site still builds. Stories are ranked on engagement and cross-source links, and the front page shows original titles and excerpts instead of written summaries.

## Deploy on GitHub Pages

1. Fork this repo (or use it as a template).
2. Put your config in `field.yaml` at the repo root.
3. In Settings, Pages, set Source to **GitHub Actions**.
4. In Settings, Secrets and variables, Actions, add `ANTHROPIC_API_KEY` (or the key for whichever provider you configure).
5. Run the "Update dashboard" workflow once from the Actions tab.

The workflow in [`.github/workflows/update.yml`](.github/workflows/update.yml) runs every four hours. It keeps the item database between runs in the Actions cache, so scores and summaries are not recomputed from scratch. If the cache is evicted, the next run refetches the last 30 days.

The site also publishes `feed.xml`, an RSS feed of the top stories, so readers can subscribe to the front page.

## Your own field

```bash
uv run fieldfeed init "computational biology"
```

`init` asks your model for the forums, newsletters, journals, subreddits, arXiv categories and accounts that matter in a field, then fetches every proposed source and keeps only the ones that return recent posts. Models are good at naming the right places but often get feed URLs wrong: for computational biology it proposed 34 sources and 19 survived the check. The result is in [`packs/computational-biology.yaml`](packs/computational-biology.yaml). Review the file, add what it missed, and run `fieldfeed check` to test every source.

To draft with an agent CLI instead of an API key:

```bash
uv run fieldfeed init "climate science" --command "claude -p"
```

## Configuration

Everything lives in `field.yaml`:

```yaml
field: computational biology
title: Computational Biology Feed
tagline: One sentence under the title.
description: >
  What the field covers and what counts as off topic. The relevance model reads this.
interest_profile: >
  Optional. Your own focus, ranked higher. "I work on single-cell methods."
windows: [24h, 7d, 14d, 30d]

llm:
  provider: anthropic
  fast_model: claude-haiku-5-5
  smart_model: claude-opus-5-5

sources:
  - type: rss
    name: bioRxiv Genomics
    url: https://connect.biorxiv.org/biorxiv_xml.php?subject=genomics
    kind: news
  - type: arxiv
    name: arXiv
    categories: [q-bio.GN, q-bio.QM]
```

### Source types

| `type` | What it reads | Options | Needs a key |
|---|---|---|---|
| `rss` | Any RSS or Atom feed | `url`, `kind` (newsletter, blog, podcast, news), `keywords`, `max_items` | no |
| `arxiv` | arXiv API | `categories`, `keywords`, `max_results` | no |
| `bluesky` | Public Bluesky AppView | `accounts`, `lists` | no |
| `reddit` | Subreddit RSS | `subreddits` | no |
| `hackernews` | HN via Algolia search | `keywords`, `min_points` | no |
| `x` | X timelines or a list | `backend` (twitterapi_io or official), `accounts`, `list_id`, `max_per_account` | yes |

Every source also takes `name`, `enabled: false` to switch it off, and `trusted: true` to skip the relevance pass for sources that are always on topic.

### X costs money

X has charged per read since February 2026, about $0.005 per post on the official API. Thirty accounts at 15 posts each is 450 reads a run, roughly $2.25, and six runs a day add up. Third-party readers such as twitterapi.io cost a small fraction of that but operate outside X's terms. The X source ships disabled. To turn it on, set `TWITTERAPI_IO_KEY` or `X_BEARER_TOKEN` and set `enabled: true`. Bluesky is on by default because it is free to read.

## Models

The model never fetches anything. Fetching is plain Python, and the model only gets a prompt and returns text, so you can swap providers without touching the fetchers.

There are two passes:

- **Relevance** (`fast_model`): scores every new item 0 to 3 against your field description, in batches of 60. Each item is scored once and the score is stored.
- **Stories** (`smart_model`): writes a headline and summary for the top clusters in each window, plus a short brief. Summaries are cached per cluster, so a steady run makes a few calls.

| `provider` | Key read from | Notes |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | Defaults to `claude-haiku-5-5` and `claude-opus-5-5` |
| `openai` | `OPENAI_API_KEY` | |
| `xai` | `XAI_API_KEY` | Grok |
| `openrouter` | `OPENROUTER_API_KEY` | Any model OpenRouter serves |
| `gemini` | `GEMINI_API_KEY` | Through Google's OpenAI-compatible endpoint |
| `groq`, `deepseek`, `mistral` | `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY` | |
| `ollama` | none | Local models at `localhost:11434` |
| `openai-compatible` | `LLM_API_KEY` | Set `base_url` |
| `command` | none | Any CLI: prompt on stdin, reply on stdout |

For every provider except `anthropic`, set `fast_model` and `smart_model` to model ids your provider currently serves.

### Bring your own harness

`provider: command` runs any program that reads a prompt on stdin and prints a reply. That covers agent CLIs, local scripts, and your own wrapper around anything else:

```yaml
llm:
  provider: command
  fast_command: claude -p --model haiku
  smart_command: claude -p --model sonnet
```

## How ranking works

1. **Grouping.** Posts are merged into one story when they share a canonical URL. arXiv abs and pdf links, twitter.com and x.com, and tracking parameters all normalize to the same key. Near-identical titles also merge.
2. **Roundups do not merge.** A newsletter that links to forty things would otherwise glue a week of news into one story. Posts with more than four links count as mentions instead, which raise the linked stories' rank.
3. **Engagement** is turned into a percentile within each source, so Hacker News points and Bluesky likes are comparable.
4. **Story score** is the best item's score plus bonuses for each extra source, each extra post and each roundup mention, decayed by age within the window.

The ranking is about 200 lines in [`fieldfeed/rank.py`](fieldfeed/rank.py) if you want to change the weights.

## Commands

```
fieldfeed run              fetch, rank, summarize, build into public/
fieldfeed run --no-fetch   rebuild from stored items
fieldfeed build            same as run --no-fetch
fieldfeed serve            serve public/ at http://127.0.0.1:8321
fieldfeed check            test every source and the model settings
fieldfeed init "field"     draft and verify a field.yaml
```

All commands take `-c path/to/field.yaml`.

## Limits

- Reddit's public feeds carry no scores, so Reddit posts rank on relevance and cross-source links only.
- Some sites rate-limit by IP. When a source fails, fieldfeed reports it and carries on with the others.
- Summaries are written from excerpts, not full papers. Read the originals before citing anything.

## Team

Built by [@beginnergarbage](https://github.com/beginnergarbage), [@24bec063](https://github.com/24bec063), [@24bec040](https://github.com/24bec040), [@Sowjithn](https://github.com/Sowjithn) and [@sathvik20005](https://github.com/sathvik20005). Issues and PRs welcome, especially new source types and packs for other fields.

## License

MIT
