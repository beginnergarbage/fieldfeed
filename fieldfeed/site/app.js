/* fieldfeed front end. Reads data.json, renders everything client side. */
(() => {
  "use strict";

  const KIND_LABEL = {
    forum: "Forums", paper: "Papers", newsletter: "Newsletters", blog: "Labs & blogs",
    social: "Social", discussion: "Discussion", podcast: "Podcasts", news: "News",
  };
  const WINDOW_LABEL = { "24h": "24 hours", "7d": "7 days", "14d": "14 days", "30d": "30 days" };
  const WINDOW_DAYS = { "24h": 1, "7d": 7, "14d": 14, "30d": 30 };
  const PAGE = 40;

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };

  const state = {
    data: null, byId: new Map(), sourceKind: new Map(),
    window: null, kind: "all", source: null, q: "", sort: "top",
    showLow: false, starredOnly: false, limit: PAGE,
    stars: new Set(store.get("ff-stars", [])),
  };

  // formatting -------------------------------------------------------------

  function ago(iso) {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return "now";
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) return `${Math.floor(s / 3600)}h`;
    if (s < 86400 * 30) return `${Math.floor(s / 86400)}d`;
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  const updated = (iso) => (ago(iso) === "now" ? "Updated just now" : `Updated ${ago(iso)} ago`);
  const fullDate = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  function num(n) {
    if (n >= 10000) return `${Math.round(n / 1000)}k`;
    if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
    return String(n);
  }
  function eng(e, compact) {
    const out = [];
    if (e.score != null) out.push(`▲ ${num(e.score)}`);
    if (e.points != null) out.push(`▲ ${num(e.points)}`);
    if (e.likes != null) out.push(`♥ ${num(e.likes)}`);
    if (e.reposts != null && !compact) out.push(`↻ ${num(e.reposts)}`);
    if (e.comments != null) out.push(`${num(e.comments)} comments`);
    else if (e.replies != null && !compact) out.push(`${num(e.replies)} replies`);
    return out.join("  ");
  }
  const inWindow = (it, w) => Date.now() - new Date(it.published).getTime() <= WINDOW_DAYS[w] * 86400000;

  function linkify(text) {
    return esc(text).replace(/(https?:\/\/[^\s<]+[^\s<.,;:)\]])/g,
      (u) => `<a href="${u}" target="_blank" rel="noopener">${u.replace(/^https?:\/\/(www\.)?/, "").slice(0, 40)}${u.length > 48 ? "…" : ""}</a>`);
  }

  // rendering --------------------------------------------------------------

  function renderWindows() {
    const { windows, meta } = state.data;
    $("#windows").innerHTML = meta.windows.map((w) => {
      const n = windows[w]?.stories.length ?? 0;
      return `<button role="tab" data-w="${w}" aria-selected="${w === state.window}">${WINDOW_LABEL[w] || w}<span class="n">${n}</span></button>`;
    }).join("");
  }

  function renderBrief() {
    const brief = state.data.windows[state.window]?.brief || [];
    $("#brief").hidden = brief.length === 0;
    $("#brief-list").innerHTML = brief.map((b) => `<li>${esc(b)}</li>`).join("");
  }

  function storySignals(story, members) {
    const out = [];
    const sources = story.sources || [];
    if (sources.length > 1) out.push(`<span class="signal hot"><strong>${sources.length}</strong> sources</span>`);
    for (const m of members) {
      const e = m.engagement || {};
      const top = e.score ?? e.points ?? e.likes;
      if (top != null && top >= 1) {
        out.push(`<span class="signal">${esc(m.source_name)} <strong>${e.score != null || e.points != null ? "▲" : "♥"} ${num(top)}</strong></span>`);
        break;
      }
    }
    if (story.mentioned_by?.length) {
      const names = story.mentioned_by.slice(0, 2).join(", ") + (story.mentioned_by.length > 2 ? ` +${story.mentioned_by.length - 2}` : "");
      out.push(`<span class="signal">linked by <strong>${esc(names)}</strong></span>`);
    }
    out.push(`<span class="signal">${ago(members.reduce((a, m) => (m.published > a ? m.published : a), members[0].published))} ago</span>`);
    return out.join("");
  }

  function originalRow(m) {
    const isSocial = m.kind === "social";
    const label = isSocial ? `<span class="post-snippet">${esc(m.title)}</span>` : esc(m.title);
    const bits = [m.author, ago(m.published), eng(m.engagement || {}, true)].filter(Boolean).map(esc);
    return `<li>
      <span class="badge k-${m.kind}">${esc(m.source_name)}</span>
      <a href="${esc(m.url)}" target="_blank" rel="noopener">${label}</a>
      <span class="by">${bits.join(" · ")}</span>
    </li>`;
  }

  function renderStories() {
    const win = state.data.windows[state.window];
    const stories = win?.stories || [];
    const list = $("#story-list");
    if (!stories.length) {
      list.innerHTML = $("#empty").innerHTML;
      $("#stories-note").textContent = "";
      return;
    }
    $("#stories-note").textContent = `Top ${stories.length}, past ${WINDOW_LABEL[state.window]}`;
    list.innerHTML = stories.map((s, i) => {
      const members = s.items.map((id) => state.byId.get(id)).filter(Boolean);
      if (!members.length) return "";
      const rep = members[0];
      const shown = members.slice(0, 4);
      const rest = members.length - shown.length;
      return `<li class="story" id="s-${s.id}">
        <div class="rank">${String(i + 1).padStart(2, "0")}</div>
        <div>
          <div class="signals">${storySignals(s, members)}</div>
          <h3><a href="${esc(rep.url)}" target="_blank" rel="noopener">${esc(s.headline)}</a></h3>
          ${s.summary ? `<p class="summary">${esc(s.summary)}</p>` : ""}
          <ul class="originals" data-story="${s.id}">${shown.map(originalRow).join("")}</ul>
          ${rest > 0 ? `<button type="button" class="link-btn" data-expand="${s.id}">+ ${rest} more from ${new Set(members.slice(4).map((m) => m.source_name)).size} sources</button>` : ""}
        </div>
      </li>`;
    }).join("");
  }

  function feedItems() {
    const q = state.q.trim().toLowerCase();
    let items = state.data.items.filter((it) => inWindow(it, state.window));
    if (!state.showLow) items = items.filter((it) => it.relevance !== 0);
    if (state.kind !== "all") items = items.filter((it) => it.kind === state.kind);
    if (state.source) items = items.filter((it) => it.source === state.source);
    if (state.starredOnly) items = items.filter((it) => state.stars.has(it.id));
    if (q) items = items.filter((it) => `${it.title} ${it.author} ${it.text} ${it.source_name}`.toLowerCase().includes(q));
    if (state.sort === "new") items.sort((a, b) => (a.published < b.published ? 1 : -1));
    else items.sort((a, b) => b.score - a.score || (a.published < b.published ? 1 : -1));
    return items;
  }

  function itemCard(it) {
    const starred = state.stars.has(it.id);
    const e = eng(it.engagement || {}, false);
    const storyLink = it.cluster && storyIndex.has(it.cluster)
      ? `<a class="in-story" href="#s-${storyIndex.get(it.cluster)}">in top stories</a>` : "";
    const meta = `<div class="item-meta">
        <span class="badge k-${it.kind}">${esc(it.source_name)}</span>
        ${it.author ? `<span class="author">${esc(it.author)}</span>` : ""}
        <time datetime="${it.published}" title="${esc(fullDate(it.published))}">${ago(it.published)}</time>
        ${e ? `<span class="eng">${esc(e)}</span>` : ""}
        ${storyLink}
      </div>`;
    const body = it.kind === "social"
      ? `<p class="post"><a href="${esc(it.url)}" target="_blank" rel="noopener">${linkify(it.text || it.title)}</a></p>`
      : `<h4><a href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a></h4>
         ${it.text && it.text !== it.title ? `<p class="excerpt">${esc(it.text)}</p>` : ""}`;
    return `<article class="item ${it.kind === "social" ? "social" : ""} ${it.relevance === 0 ? "low" : ""}">
      <div>${meta}${body}</div>
      <button type="button" class="star ${starred ? "on" : ""}" data-star="${it.id}" aria-pressed="${starred}" aria-label="${starred ? "Unstar" : "Star"}">
        <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><path d="m12 3.5 2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8Z" fill="${starred ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
      </button>
    </article>`;
  }

  let storyIndex = new Map();

  function renderFeed() {
    storyIndex = new Map();
    for (const s of state.data.windows[state.window]?.stories || []) {
      for (const id of s.items) {
        const it = state.byId.get(id);
        if (it?.cluster) storyIndex.set(it.cluster, s.id);
      }
    }
    const items = feedItems();
    const shown = items.slice(0, state.limit);
    $("#feed-list").innerHTML = shown.length ? shown.map(itemCard).join("") : $("#empty").innerHTML;
    $("#more").hidden = items.length <= state.limit;
    $("#more").textContent = `Show more (${items.length - state.limit} left)`;
    $("#feed-count").textContent = `${items.length} item${items.length === 1 ? "" : "s"}`;
    const src = state.source && state.data.meta.sources.find((s) => s.key === state.source);
    $("#clear-source").hidden = !src;
    if (src) $("#clear-source").textContent = `Only ${src.name} · clear`;
  }

  function renderKinds() {
    const counts = {};
    const base = state.data.items.filter((it) => inWindow(it, state.window) && (state.showLow || it.relevance !== 0));
    for (const it of base) counts[it.kind] = (counts[it.kind] || 0) + 1;
    const kinds = Object.keys(KIND_LABEL).filter((k) => counts[k]);
    if (state.kind !== "all" && !counts[state.kind]) state.kind = "all";
    $("#kinds").innerHTML = [`<button type="button" class="chip ${state.kind === "all" ? "on" : ""}" data-kind="all">All <span class="n">${base.length}</span></button>`]
      .concat(kinds.map((k) => `<button type="button" class="chip ${state.kind === k ? "on" : ""}" data-kind="${k}">${KIND_LABEL[k]} <span class="n">${counts[k]}</span></button>`))
      .join("");
  }

  function renderSidebar() {
    const { meta, items } = state.data;
    const counts = {};
    for (const it of items) if (inWindow(it, state.window)) counts[it.source] = (counts[it.source] || 0) + 1;
    $("#source-list").innerHTML = meta.sources.map((s) => {
      const kind = state.sourceKind.get(s.key) || "news";
      const n = s.error ? "error" : counts[s.key] || 0;
      return `<li><button type="button" class="source-btn ${state.source === s.key ? "on" : ""} ${s.error ? "err" : ""}" data-source="${s.key}"
        style="--c: var(--k-${kind})" title="${esc(s.error || `${s.name}: ${n} items in the past ${WINDOW_LABEL[state.window]}`)}">
        <span class="dot"></span><span class="name">${esc(s.name)}</span><span class="n">${n}</span></button></li>`;
    }).join("");

    $("#about-field").textContent = meta.description || meta.tagline;
    const llm = meta.llm || {};
    const working = meta.sources.filter((s) => !s.error).length;
    const rows = [
      ["Updated", updated(meta.generated_at).replace("Updated ", "")],
      ["Items tracked", num(items.length)],
      ["Sources", `${working} of ${meta.sources.length} live`],
      ["Model", llm.enabled ? (llm.smart_model || llm.provider) : "off"],
    ];
    $("#stats").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    const old = $(".panel .notice");
    if (old) old.remove();
    if (!llm.enabled) {
      $("#stats").insertAdjacentHTML("afterend",
        `<p class="notice">Ranking runs on engagement and cross-source links only. Add a model key (for example <code>ANTHROPIC_API_KEY</code>) to get headlines, summaries and relevance filtering.</p>`);
    }
  }

  function renderDateline() {
    const d = new Date(state.data.meta.generated_at);
    const day = d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    const fresh = Date.now() - d.getTime() < 6 * 3600000;
    $("#dateline").innerHTML = `${esc(day)} <span aria-hidden="true">·</span> <span class="${fresh ? "live" : ""}">${updated(state.data.meta.generated_at)}</span>`;
  }

  function render() {
    renderWindows();
    renderBrief();
    renderStories();
    renderKinds();
    renderFeed();
    renderSidebar();
    $("#lowrel-wrap").hidden = !state.data.items.some((it) => it.relevance === 0);
  }

  // events -----------------------------------------------------------------

  function setWindow(w, push) {
    if (!state.data.windows[w]) return;
    state.window = w;
    state.limit = PAGE;
    if (push) history.replaceState(null, "", `#${w}`);
    render();
  }

  function bind() {
    $("#windows").addEventListener("click", (e) => {
      const b = e.target.closest("[data-w]");
      if (b) setWindow(b.dataset.w, true);
    });
    $("#windows").addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const ws = state.data.meta.windows;
      const i = ws.indexOf(state.window) + (e.key === "ArrowRight" ? 1 : -1);
      if (ws[i]) { setWindow(ws[i], true); $(`[data-w="${ws[i]}"]`).focus(); }
    });
    $("#story-list").addEventListener("click", (e) => {
      const b = e.target.closest("[data-expand]");
      if (!b) return;
      const s = state.data.windows[state.window].stories.find((x) => x.id === b.dataset.expand);
      const members = s.items.map((id) => state.byId.get(id)).filter(Boolean);
      document.querySelector(`[data-story="${s.id}"]`).innerHTML = members.map(originalRow).join("");
      b.remove();
    });
    $("#kinds").addEventListener("click", (e) => {
      const b = e.target.closest("[data-kind]");
      if (!b) return;
      state.kind = b.dataset.kind; state.limit = PAGE;
      renderKinds(); renderFeed();
    });
    $("#source-list").addEventListener("click", (e) => {
      const b = e.target.closest("[data-source]");
      if (!b) return;
      state.source = state.source === b.dataset.source ? null : b.dataset.source;
      state.kind = "all"; state.limit = PAGE;
      renderKinds(); renderFeed(); renderSidebar();
      $("#feed").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("#clear-source").addEventListener("click", () => {
      state.source = null; renderFeed(); renderSidebar();
    });
    let t;
    $("#q").addEventListener("input", (e) => {
      clearTimeout(t);
      t = setTimeout(() => { state.q = e.target.value; state.limit = PAGE; renderFeed(); }, 120);
    });
    document.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => {
      state.sort = b.dataset.sort;
      document.querySelectorAll("[data-sort]").forEach((x) => x.classList.toggle("on", x === b));
      renderFeed();
    }));
    $("#show-low").addEventListener("change", (e) => { state.showLow = e.target.checked; renderKinds(); renderFeed(); });
    $("#only-starred").addEventListener("change", (e) => { state.starredOnly = e.target.checked; renderFeed(); });
    $("#more").addEventListener("click", () => { state.limit += PAGE; renderFeed(); });
    $("#feed-list").addEventListener("click", (e) => {
      const b = e.target.closest("[data-star]");
      if (!b) return;
      const id = b.dataset.star;
      state.stars.has(id) ? state.stars.delete(id) : state.stars.add(id);
      store.set("ff-stars", [...state.stars]);
      renderFeed();
    });
    $("#theme-toggle").addEventListener("click", () => {
      const root = document.documentElement;
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("ff-theme", root.dataset.theme); } catch (e) {}
    });
    window.addEventListener("hashchange", () => {
      const w = location.hash.slice(1);
      if (w !== state.window && state.data.windows[w]) setWindow(w, false);
    });
  }

  // boot -------------------------------------------------------------------

  function skeleton() {
    $("#story-list").innerHTML = Array.from({ length: 3 }, () =>
      `<li class="story"><div class="rank"></div><div><div class="skeleton w40"></div><div class="skeleton w80"></div><div class="skeleton w60"></div></div></li>`).join("");
  }

  async function boot() {
    skeleton();
    let data;
    try {
      const build = document.querySelector('meta[name="fieldfeed-build"]')?.content || Date.now();
      const r = await fetch(`data.json?v=${build}`, { cache: "no-cache" });
      if (!r.ok) throw new Error(r.status);
      data = await r.json();
    } catch (e) {
      $("#story-list").innerHTML = `<div class="empty"><p>Could not load data.json. Run <code>fieldfeed run</code> and serve the <code>public/</code> folder.</p></div>`;
      return;
    }
    state.data = data;
    for (const it of data.items) {
      state.byId.set(it.id, it);
      if (!state.sourceKind.has(it.source)) state.sourceKind.set(it.source, it.kind);
    }
    const fromHash = location.hash.slice(1);
    const ws = data.meta.windows;
    state.window = data.windows[fromHash] ? fromHash
      : ws.find((w) => (data.windows[w]?.stories.length || 0) >= 3) || ws[0];
    renderDateline();
    bind();
    render();
  }

  boot();
})();
