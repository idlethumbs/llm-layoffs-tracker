# Discoverability + Latest News Section

**Date:** 2026-05-06
**Status:** Spec approved, awaiting plan
**Scope:** Static GitHub Pages site (`index.html`, `data/*.json`, GitHub Actions refresh).

## Summary

Two additive changes to the layoffs tracker:

1. **Discoverability** — make the page legible to Google and AI answer engines (ChatGPT search, Perplexity, Claude, Gemini, etc.) via `robots.txt`, `llms.txt`, `sitemap.xml`, schema.org Dataset JSON-LD, and Open Graph metadata.
2. **Latest news section** — render a dark-mode card grid of recent AI/labour-market news under the chart, sourced live from HN Algolia's keyless API using a multi-term query strategy.

Both changes are purely additive: no chart behaviour changes, no build step, no API keys, no proxies.

## Section 1 — Discoverability

### `robots.txt` (repo root)

Explicit allow-everything policy, with named lines for AI crawlers so the intent is unambiguous to bots that scan for explicit signals:

```
User-agent: *
Allow: /

User-agent: GPTBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: CCBot
Allow: /

User-agent: cohere-ai
Allow: /

Sitemap: https://<pages-host>/sitemap.xml
```

The `<pages-host>` placeholder is resolved at planning time from the repo's GitHub Pages settings (likely `idlethumbs.github.io/llm-layoffs-tracker` or a custom domain — verify before writing).

### `llms.txt` (repo root)

Plain Markdown following the [llms.txt convention](https://llmstxt.org). Single H1, short summary, then sectioned links to the data and source. Example shape:

```
# Tech layoffs vs. LLM coding-model releases

> Cumulative tech-industry layoffs by industry, plotted against major
> LLM coding-model release dates. Auto-refreshed twice weekly.

## Data
- [Layoffs time series (JSON)](./data/data.json): cumulative monthly counts per industry
- [Model-release markers (JSON)](./data/markers.json): curated coding-model release timeline

## About
- [README](./README.md): provenance, refresh cadence, license
- [Source repository](https://github.com/idlethumbs/llm-layoffs-tracker)
```

### `sitemap.xml` (repo root)

Single-URL sitemap. `<lastmod>` is updated by the existing refresh action whenever data is regenerated:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://<pages-host>/</loc>
    <lastmod>2026-05-06</lastmod>
    <changefreq>weekly</changefreq>
  </url>
</urlset>
```

### `index.html` `<head>` additions

- Open Graph: `og:title`, `og:description`, `og:type=website`, `og:url`, `og:image` (path to a static `og.png` to be added separately — out of scope for this spec, fallback handled by absent tag).
- Twitter card: `twitter:card=summary_large_image`, `twitter:title`, `twitter:description`.
- Canonical: `<link rel="canonical" href="...">`.
- Robots: `<meta name="robots" content="index,follow">`.
- JSON-LD `Dataset` block describing the layoffs dataset, with `name`, `description`, `url`, `license` (`https://opensource.org/licenses/MIT` — repo is MIT-licensed, confirmed in `LICENSE`), `creator`, `dateModified`, and a `distribution` array pointing at `data/data.json` and `data/markers.json` with `encodingFormat: application/json`.

### GitHub Action change

The existing refresh workflow (`.github/workflows/refresh.yml` running `scripts/build_data.py`) already commits regenerated `data/*.json`. Extend `scripts/build_data.py` to also rewrite:

- `sitemap.xml`'s `<lastmod>` to today's UTC date.
- The JSON-LD `dateModified` value inside `index.html` (a single regex/string replacement on a uniquely-shaped line).

These rewrites happen in the same commit as the data refresh.

## Section 2 — Latest news section

### DOM structure

New section in `index.html`, inserted after `</main>` and before `<footer>`:

```html
<section class="news" id="news" aria-label="Latest AI layoff coverage">
  <h2>Latest AI layoff coverage</h2>
  <div class="news-list" id="news-list">
    <!-- cards rendered by script -->
  </div>
  <p class="news-meta">
    Sourced from Hacker News via the Algolia API. Updated on each page load.
  </p>
</section>
```

The section starts hidden (`hidden` attribute) and is unhidden only after the script renders at least one card. If all fetches fail or the filter produces zero results, the section stays hidden — no error UI, no broken layout.

### Styling

Reuse existing CSS variables (`--bg-soft`, `--grid`, `--fg`, `--fg-dim`, `--accent`). Cards use the same border/radius treatment as `.chart-wrap`. Layout:

- `.news-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }`
- `.news-card { background: var(--bg-soft); border: 1px solid var(--grid); border-radius: 12px; padding: 14px 16px; }`
- Card title: link, `var(--fg)`, weight 500, two-line clamp.
- Card meta row: `var(--fg-dim)`, 12 px, contains source domain + relative date + HN discussion link.
- Hover: border colour shifts to `var(--fg-dim)`.

Light mode is inherited automatically via the existing `prefers-color-scheme: light` block — no extra rules needed.

### Data fetch

Five parallel `fetch` calls to HN Algolia, run inside their own `try/catch` IIFE that is fully isolated from the chart code:

```js
const TERMS = ["layoffs", '"job cuts"', "redundancies", '"workforce reduction"', "downsizing"];
const ENDPOINT = "https://hn.algolia.com/api/v1/search_by_date";

async function loadNews() {
  const responses = await Promise.allSettled(
    TERMS.map(t => fetch(`${ENDPOINT}?query=${encodeURIComponent(t)}&tags=story&hitsPerPage=8`)
      .then(r => r.ok ? r.json() : null))
  );
  // Flatten hits from all fulfilled responses
  // Dedupe by objectID
  // Filter: points >= 2 && title matches topic-guard regex
  // Sort by created_at desc, slice top 6
  // Render or stay hidden
}
```

Topic-guard regex: `/\b(AI|LLM|automation|model|agent|tech|software|engineer|coding|developer|startup)\b/i`. Tuned from the test data: drops government/USDA-style downsizing while keeping all the relevant tech and AI labour stories surfaced during testing.

### Card render

For each hit, derive:

- `title`: `hit.title`.
- `articleUrl`: `hit.url || \`https://news.ycombinator.com/item?id=${hit.objectID}\``.
- `source`: hostname of `articleUrl` (fall back to "Hacker News" for `news.ycombinator.com`).
- `dateLabel`: relative ("2 days ago"); fall back to ISO date if `< 1 day` calculation is awkward — use `Intl.RelativeTimeFormat`.
- `hnUrl`: `https://news.ycombinator.com/item?id=${hit.objectID}`.
- `comments`: `hit.num_comments`.

Card markup (built with `textContent` for the title to avoid HTML injection from API content):

```
<article class="news-card">
  <a class="news-title" href="${articleUrl}" target="_blank" rel="noopener">${title}</a>
  <div class="news-meta-row">
    <span>${source}</span>
    <span>·</span>
    <span>${dateLabel}</span>
  </div>
  <a class="news-hn" href="${hnUrl}" target="_blank" rel="noopener">↗ HN discussion · ${comments}</a>
</article>
```

All user-visible string interpolation uses safe DOM APIs (`textContent`, `setAttribute`) rather than `innerHTML`, since titles and URLs come from a third-party API.

### Failure isolation

The news IIFE runs after the chart IIFE. Wrapping it in `try/catch` and gating the section unhide on successful render guarantees:

- A network failure leaves the chart untouched.
- A schema change at HN Algolia leaves the chart untouched.
- An empty result set leaves the section hidden, not broken.

## Out of scope (explicitly)

- Server-side rendering or build-time fetch of news (would require build step; client-side fetch keeps the static-site simplicity).
- A second news source (GDELT). Keep it to one source for v1; revisit if breadth feels thin after a week.
- Per-card thumbnails / previews. HN Algolia doesn't expose article body or images.
- Caching layer (localStorage). Page loads are infrequent enough that the 5-fetch cost is invisible.
- A static `og.png` social card image. The OG meta tag will be added now; the image asset can be added separately.

## Acceptance criteria

1. `curl https://<pages-host>/robots.txt` returns the policy with explicit AI-bot allow lines.
2. `curl https://<pages-host>/llms.txt` returns valid Markdown following the llms.txt convention.
3. `curl https://<pages-host>/sitemap.xml` returns valid XML with current `<lastmod>`.
4. View source of homepage shows JSON-LD `Dataset` block, OG tags, canonical link.
5. Page renders a news grid with 1–6 cards under the chart (when HN Algolia is reachable).
6. Disabling network in DevTools and reloading: chart still renders, news section stays hidden, no console errors.
7. Forcing the news fetch to throw: chart unaffected, no console errors.
8. Light mode (`prefers-color-scheme: light`) renders the news cards correctly without additional CSS.

## Files touched

- New: `robots.txt`, `llms.txt`, `sitemap.xml` (repo root)
- Modified: `index.html` (`<head>` additions, news section markup, news fetch script)
- Modified: `scripts/build_data.py` (sitemap `<lastmod>` + JSON-LD `dateModified` updates)
