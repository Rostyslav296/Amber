# AetherHeadless v1.0 — Headless Web Engine

> State-of-the-art headless browsing, deep research, and lead generation for Agent F (Amber).
> Zero GUI. Maximum speed. Vast result volume. Source-tracked.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Why Headless-First](#2-why-headless-first)
3. [3-Tier Intelligent Routing](#3-3-tier-intelligent-routing)
4. [Actions Reference](#4-actions-reference)
5. [Deep Research Pipeline](#5-deep-research-pipeline)
6. [Lead Generation Engine](#6-lead-generation-engine)
7. [HTML-to-Markdown Converter](#7-html-to-markdown-converter)
8. [Data Extraction Engine](#8-data-extraction-engine)
9. [Web Search Engine](#9-web-search-engine)
10. [Site Crawler](#10-site-crawler)
11. [Form Submission](#11-form-submission)
12. [Agent Integration](#12-agent-integration)
13. [Trigger Phrases](#13-trigger-phrases)
14. [Edge vs Headless Decision Matrix](#14-edge-vs-headless-decision-matrix)
15. [Performance](#15-performance)
16. [Configuration](#16-configuration)
17. [2026 Design Philosophy](#17-2026-design-philosophy)

---

## 1. Architecture Overview

AetherHeadless is the **default** web access layer for Amber. It replaces `edge.py` for all non-GUI browsing tasks — research, data gathering, scraping, lead generation, and headless form submission.

```
User: "research short term rental hosts in East Tennessee"
                │
                ▼
┌─────────────────────────────────────────────────────────────┐
│  agent.py — detects research intent, routes to "web" tool   │
└───────────────────────┬─────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  AetherHeadless v1.0 — headless-browser.py                  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  TIER ROUTER                                         │   │
│  │  Tier 1: HTTP/urllib (70% of web) ──→ <1s/page      │   │
│  │  Tier 2: Playwright headless (25%) ──→ ~2-4s/page   │   │
│  │  Tier 3: Stealth Playwright (5%)  ──→ ~5-10s/page   │   │
│  └──────────┬───────────────────────────────────────────┘   │
│             ▼                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  HTML → MARKDOWN CONVERTER                           │   │
│  │  67% token reduction · LLM-optimized · links kept    │   │
│  └──────────┬───────────────────────────────────────────┘   │
│             ▼                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  PARALLEL ENGINE (ThreadPoolExecutor, 12 workers)    │   │
│  │  Concurrent fetches · Domain diversity · Dedup       │   │
│  └──────────┬───────────────────────────────────────────┘   │
│             ▼                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  EXTRACTION + REPORT BUILDER                         │   │
│  │  Emails · Phones · Social · Businesses · Sources     │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
            Clean markdown → agent.py → LLM
```

**File:** `agent-functions/headless-browser.py` (~950 lines)
**Tool name:** `web`
**Mode:** Server (persistent stdin/stdout JSON) or One-shot (`--json`)
**Dependencies:** `urllib` (stdlib), `playwright` (optional, for Tier 2/3)

---

## 2. Why Headless-First

The 2026 web has **four tiers** of complexity:

| Tier | % of Web | Characteristics | Best Tool |
|------|----------|----------------|-----------|
| 1 | 65-70% | Static HTML, content in initial response | HTTP/urllib |
| 2 | 20-25% | JS-rendered SPAs, dynamic content | Playwright headless |
| 3 | 5-8% | Anti-bot protection (Cloudflare, DataDome) | Stealth browser |
| 4 | 1-2% | Deep protection, CAPTCHAs, login walls | GUI browser (edge.py) |

**AetherHeadless handles Tiers 1-3 (95%+ of the web)** without opening a GUI. Only Tier 4 (login-walled interactive sessions) requires `edge.py`.

### Speed Comparison

| Operation | edge.py (GUI) | AetherHeadless |
|-----------|--------------|----------------|
| Single page fetch | 3-8s | 0.3-1s (Tier 1) |
| 10-page research | 30-80s | 5-15s |
| Deep research (20 pages) | 2-5 min | 15-40s |
| Lead gen (30 pages) | 3-8 min | 20-60s |
| Bulk scrape (50 URLs) | N/A | 30-90s |

AetherHeadless is **5-10x faster** than GUI browsing for data gathering tasks.

---

## 3. 3-Tier Intelligent Routing

Every URL fetch goes through `smart_fetch()`, which auto-routes to the cheapest tier:

```
smart_fetch(url)
    │
    ├── Check cache (5-min TTL) ─── HIT → return cached markdown
    │
    ├── TIER 1: http_fetch(url) via urllib
    │   ├── Success + >200 chars text → html_to_markdown() → return
    │   ├── 403/503 (blocked) → escalate to Tier 2
    │   ├── Thin content (<200 chars) → escalate to Tier 2
    │   └── Connection error → escalate to Tier 2
    │
    └── TIER 2: pw_fetch(url) via Playwright headless
        ├── Chromium headless + stealth patches
        ├── domcontentloaded + networkidle (8s timeout)
        ├── Full JS rendering
        └── Return html_to_markdown(rendered_html)
```

### Tier 1 — HTTP/urllib

- Zero dependencies beyond Python stdlib
- User-agent rotation (4 agents: Chrome/Safari/Windows/Linux)
- Gzip decompression
- Lenient SSL (handles sites with bad certificates)
- ~0.3-1s per page

### Tier 2 — Playwright Headless

- Lazy-initialized (only starts if Tier 1 fails)
- Shared browser context across all calls (no startup overhead after first)
- Stealth patches: `navigator.webdriver` removed, fake plugins, Chrome runtime
- `bypass_csp=True` for cross-origin content
- 1920x1080 viewport, `en-US` locale
- ~2-4s per page

### Tier 3 — Stealth Playwright

- Same as Tier 2 with additional anti-detection
- Extended wait for Cloudflare challenges
- Reserved for known-protected domains

### Caching

- MD5-keyed filesystem cache in `~/.headless_cache/`
- 5-minute TTL (configurable via `CACHE_TTL`)
- Prevents redundant fetches during multi-query research
- Cache files capped at 100KB

---

## 4. Actions Reference

### `search` — Web Search

Multi-engine search with fallback chain: DuckDuckGo → Google → Bing.

```json
{"action": "search", "query": "short term rental management east tennessee", "max_results": 15}
```

**Returns:** Numbered list of results with title, URL, and snippet.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| query | string | required | Search query |
| max_results | int | 10 | Max results (capped at 20) |

### `fetch` — Single URL Fetch

Fetch any URL → clean markdown. Auto-routes through tiers.

```json
{"action": "fetch", "url": "https://example.com/article", "max_chars": 16000}
```

**Returns:** Clean markdown with title, content, links, and source URL.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| url | string | required | URL to fetch |
| max_chars | int | 24000 | Max output chars |

### `deep_research` — Multi-Query Parallel Research

The flagship action. Matches Claude/Grok/Gemini deep research capabilities.

```json
{"action": "deep_research", "query": "airbnb hosts east tennessee contact info", "max_pages": 20}
```

**Returns:** Structured research report with key findings, source URLs, content excerpts.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| query | string | required | Research topic |
| max_pages | int | 15 | Max pages to fetch (capped at 20) |

### `extract` — Data Extraction

Extract emails, phones, social links from one or more URLs.

```json
{"action": "extract", "url": "https://company.com/contact", "extract_type": "all"}
{"action": "extract", "urls": "https://site1.com,https://site2.com/about", "extract_type": "emails"}
```

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| url | string | — | Single URL |
| urls | string | — | Comma-separated URLs |
| extract_type | enum | all | emails, phones, or all |

### `scrape` — Bulk Parallel Fetch

Fetch multiple URLs simultaneously with parallel workers.

```json
{"action": "scrape", "urls": "https://site1.com,https://site2.com,https://site3.com", "max_chars_per_page": 6000}
```

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| urls | string | required | Comma-separated URLs |
| max_chars_per_page | int | 8000 | Max chars per page |

### `form` — Headless Form Submission

Fill and submit forms without a GUI. Uses Playwright for JS-rendered forms.

```json
{
  "action": "form",
  "url": "https://site.com/signup",
  "fields": {"name": "John Doe", "email": "john@example.com", "company": "Acme"},
  "submit_selector": "button[type=submit]"
}
```

**Field resolution (4 strategies):** CSS selector → `[name]` attribute → label text → placeholder text.
**Auto-submit:** If no `submit_selector`, auto-finds submit/sign/register/send buttons.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| url | string | required | Form page URL |
| fields | object | required | {field_selector_or_name: value} |
| submit_selector | string | auto | CSS selector for submit button |

### `lead_gen` — B2B Lead Harvesting

Specialized lead generation: searches directories, extracts contacts in parallel.

```json
{
  "action": "lead_gen",
  "query": "vacation rental property managers",
  "location": "east tennessee",
  "lead_type": "all",
  "max_leads": 100
}
```

**Pipeline:** Generate 10 directory-targeted search queries → parallel search → fetch up to 30 pages → extract emails/phones → deduplicate → structured report.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| query | string | required | Business/lead description |
| location | string | — | Geographic filter |
| lead_type | enum | all | emails, phones, or all |
| max_leads | int | 50 | Max leads (capped at 200) |

### `maps_search` — Google Maps Business Scraper

Search Google Maps + directory fallbacks for business listings with contact info.

```json
{"action": "maps_search", "query": "booking companies", "location": "Nashville TN", "max_results": 20}
```

**Pipeline:** Google Maps direct (Playwright) → Yelp/YellowPages/BBB fallback → parallel contact extraction.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| query | string | required | Business type to search |
| location | string | — | Geographic location |
| max_results | int | 20 | Max results (capped at 50) |

### `crawl` — Site Spider

BFS crawl following same-domain links to configurable depth.

```json
{"action": "crawl", "url": "https://company.com", "max_depth": 2, "max_pages": 30, "pattern": "/team|/about|/contact"}
```

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| url | string | required | Start URL |
| max_depth | int | 2 | Max link depth (capped at 3) |
| max_pages | int | 30 | Max pages (capped at 50) |
| pattern | string | — | URL filter regex |

---

## 5. Deep Research Pipeline

The `deep_research` action implements a 4-phase pipeline modeled after how Claude, Grok, and Gemini perform deep research:

```
Phase 1 — QUERY EXPANSION
    Input: "airbnb hosts east tennessee"
    Output: Up to 8 search variations:
      1. airbnb hosts east tennessee
      2. "airbnb hosts east tennessee"
      3. airbnb hosts east tennessee 2025 2026
      4. airbnb hosts east tennessee guide overview
      5. airbnb hosts east tennessee property manager contact
      6. airbnb hosts east tennessee vacation rental directory
      ...
    │
    ▼
Phase 2 — PARALLEL SEARCH
    6 concurrent search threads (DuckDuckGo + Google + Bing fallback)
    Deduplicate URLs across all queries
    Typical yield: 30-80 unique URLs
    │
    ▼
Phase 3 — RANKED PARALLEL FETCH
    Domain-diversity ranking (penalize same-domain clusters)
    12 concurrent fetch workers
    Tier-routed (HTTP first, Playwright fallback)
    12K char budget per page
    │
    ▼
Phase 4 — REPORT ASSEMBLY
    Structured markdown report:
    ├── Key Findings (per-page title, URL, summary, content excerpt)
    ├── All Sources Found (numbered URL list, up to 50)
    └── Search Queries Used
```

### Query Expansion Heuristics

The system auto-generates search variations based on query content:

| Query Contains | Additional Variations |
|---------------|----------------------|
| email, contact, phone, leads | `+ directory listing`, `+ contact information database` |
| host, rental, airbnb, vrbo | `+ property manager contact`, `+ vacation rental directory` |
| company, business, service | `+ company directory`, `+ business listings` |
| (any query) | `+ "quoted exact"`, `+ 2025 2026`, `+ guide overview` |

---

## 6. Lead Generation Engine

### Architecture

```
lead_gen(query, location)
    │
    ├── Build 10 directory-targeted search queries:
    │   ├── "{query} {location} email contact"
    │   ├── "{query} {location} directory listing"
    │   ├── "{query} {location} phone number email"
    │   ├── "{query} {location} contact us"
    │   ├── '"{query} {location}" email'
    │   ├── "{query} {location} site:linkedin.com"
    │   ├── "{query} {location} site:yelp.com"
    │   ├── "{query} {location} site:yellowpages.com"
    │   ├── "{query} {location} business directory"
    │   └── "{query} {location} reviews contact"
    │
    ├── Parallel search (6 threads) → collect all URLs
    │
    ├── Parallel fetch + extract (12 threads, up to 30 pages):
    │   ├── smart_fetch(url, max_chars=50000)  ← larger budget for contact pages
    │   └── extract_contacts(content) → emails, phones, social
    │
    ├── Deduplication (by primary email)
    │
    └── Structured lead report with source attribution
```

### Use Cases

| Request | Action |
|---------|--------|
| "Get me emails of airbnb hosts in east tennessee" | `lead_gen` with query="airbnb hosts", location="east tennessee" |
| "Find 100 emails of booking companies" | `lead_gen` with query="booking companies", max_leads=100 |
| "Scrape phone numbers from Google Maps for plumbers in Nashville" | `maps_search` with query="plumbers", location="Nashville TN" |
| "Get contact info from these 5 company websites" | `extract` with urls="url1,url2,url3,url4,url5" |

---

## 7. HTML-to-Markdown Converter

Custom converter optimized for LLM token efficiency:

### What It Preserves

- Headings (h1-h6 → `#` markdown)
- Links with href (→ `[text](url)`)
- Lists (ul/ol → `- item`)
- Code blocks (`<pre>` → triple backtick)
- Inline code (`<code>` → backtick)
- Tables (basic tr/td → pipe table)
- Bold/italic
- Paragraphs and line breaks
- Page title and meta description

### What It Strips

- `<script>`, `<style>`, `<svg>`, HTML comments
- `<noscript>`, `<nav>`, `<header>`, `<footer>`, `<aside>`
- All remaining HTML tags
- Excessive whitespace (collapsed to single spaces/newlines)

### Token Efficiency

| Format | Tokens (avg page) | Relative |
|--------|-------------------|----------|
| Raw HTML | ~12,000 | 100% |
| Stripped text | ~5,000 | 42% |
| **AetherHeadless markdown** | **~4,000** | **33%** |

67% token reduction vs raw HTML while preserving structure and links.

---

## 8. Data Extraction Engine

### Email Extraction

- RFC 5322-simplified regex
- Junk filtering: removes `noreply@`, `support@`, `@sentry.io`, `@wixpress`, image/CSS refs
- Basic validation: minimum 2-char local part, TLD must have `.`
- Deduplication by lowercase

### Phone Extraction

- US format: `(XXX) XXX-XXXX`, `XXX-XXX-XXXX`, `+1 XXX-XXX-XXXX`
- International: `+{1-3 digits} {4-14 digits}`
- Minimum 10 digits after normalization
- Deduplication by digit string

### Social Media Extraction

- LinkedIn (personal + company pages)
- Twitter/X
- Facebook
- Instagram

---

## 9. Web Search Engine

### Multi-Engine Fallback

```
web_search(query)
    │
    ├── DuckDuckGo HTML ← Primary (no API key, no rate limit issues)
    │   └── Parses result__a and result__snippet classes
    │       └── Extracts actual URL from DDG redirect parameter (uddg=)
    │
    ├── Google HTML ← Supplement (if DDG < 50% target results)
    │   └── Parses /url?q= redirect links
    │       └── Filters out google.com self-referential links
    │
    └── Bing HTML ← Last resort (if <3 results total)
        └── Parses b_algo result blocks
```

### Result Deduplication

Results from all engines are merged with URL-based dedup, ensuring no duplicate entries in the final list.

---

## 10. Site Crawler

### BFS Algorithm

```
crawl_site(start_url, max_depth=2, max_pages=30)
    │
    ├── Initialize BFS queue: [(start_url, depth=0)]
    │
    └── While queue not empty AND pages < max_pages:
        ├── Pull batch of up to 12 URLs from queue
        ├── Parallel fetch batch via smart_fetch()
        ├── For each fetched page:
        │   ├── Add to results
        │   └── If depth < max_depth:
        │       ├── Extract markdown links [text](url)
        │       ├── Extract raw URLs
        │       ├── Filter: same domain only
        │       ├── Filter: URL pattern regex (if specified)
        │       ├── Skip: anchors, javascript:, mailto:, PDFs, images
        │       └── Add to queue at depth+1
        └── Continue
```

### Link Extraction

Extracts from:
1. Markdown links: `[text](url)` — from already-converted markdown
2. Raw URLs: `https://...` patterns
3. Relative URLs are made absolute using the base domain

Capped at 50 links per page to prevent queue explosion.

---

## 11. Form Submission

### Field Resolution (4 strategies)

For each field in the `fields` object, AetherHeadless tries:

1. **CSS selector** — `page.locator(selector)` (most specific)
2. **Name attribute** — `[name="selector"]` (common form pattern)
3. **Label text** — `page.get_by_label(selector)` (semantic)
4. **Placeholder text** — `page.get_by_placeholder(selector)` (fallback)

### Input Type Handling

| Element | Method |
|---------|--------|
| `<input type="text">` | `el.fill(value)` |
| `<select>` | `el.select_option(value)` |
| `<input type="checkbox">` | `el.check()` / `el.uncheck()` |
| `<input type="radio">` | `el.check()` |
| `<textarea>` | `el.fill(value)` |

### Submit Strategy

1. If `submit_selector` provided → click it
2. Else auto-scan for: `button[type=submit]`, `input[type=submit]`, buttons containing "Submit"/"Sign"/"Register"/"Send"
3. Fallback: press Enter

---

## 12. Agent Integration

### Tool Registration

AetherHeadless registers as `web` in the agent.py tool registry via `TOOL_METADATA`. It supports both execution modes:

| Mode | Usage | When |
|------|-------|------|
| **Server** | `--server` (persistent stdin/stdout JSON) | Default when used by agent loop |
| **One-shot** | `--json '{"action":"search","query":"..."}` | Direct CLI or testing |

### Server Protocol

```
agent.py → stdin: {"action":"search","query":"python web frameworks"}
headless-browser.py → stdout: {"result":"# Search: python web frameworks\n\n1. **Flask**..."}

agent.py → stdin: {"action":"deep_research","query":"best databases 2026"}
headless-browser.py → stdout: {"result":"# Deep Research: best databases 2026\n\n..."}
```

### Priority

`TOOL_METADATA.priority = 1000` — highest priority tool. Agent should prefer `web` over `edge` for all browsing unless user explicitly requests GUI browser.

### Routing Logic (agent.py integration)

The agent should use this decision tree:

```
User says: "research X" / "look up X" / "browse for X" / "what is X" / "deep research X"
    → {"tool": "web", "args": {"action": "deep_research", "query": "X"}}

User says: "search for X"
    → {"tool": "web", "args": {"action": "search", "query": "X"}}

User says: "get me emails/phones of X"
    → {"tool": "web", "args": {"action": "lead_gen", "query": "X", ...}}

User says: "go to URL" / "fetch URL"
    → {"tool": "web", "args": {"action": "fetch", "url": "..."}}

User says: "open edge and go to..." / "open safari and..."
    → {"tool": "edge", "args": {"action": "browse", "url": "..."}}  ← GUI only on explicit request
```

---

## 13. Trigger Phrases

AetherHeadless (`web` tool) should be invoked for:

| Phrase | Action |
|--------|--------|
| "browse for..." | search or deep_research |
| "research..." | deep_research |
| "deep research..." | deep_research |
| "headlessly deep research..." | deep_research |
| "look up..." | search → fetch |
| "search for..." | search |
| "what is..." | search → fetch |
| "find out..." | deep_research |
| "get emails/phones of..." | lead_gen |
| "scrape..." | scrape or crawl |
| "find businesses..." | maps_search |
| "sign up on..." (headlessly) | form |

Edge/Safari GUI should **only** be invoked for:
- "open edge and go to..."
- "open safari and..."
- "use edge to..."
- Explicit GUI interaction requests (click this, screenshot, etc.)

---

## 14. Edge vs Headless Decision Matrix

| Task | Tool | Why |
|------|------|-----|
| Research a topic | `web` (headless) | 10x faster, parallel, no GUI needed |
| Read an article | `web` fetch | HTTP is enough for articles |
| Find email addresses | `web` lead_gen/extract | Parallel extraction at scale |
| Fill a job application | `edge` | Complex multi-page interactive flow |
| Complete a survey | `edge` | Dynamic JS with real-time state |
| Sign up for a service (simple) | `web` form | One-shot form fill works |
| Sign up with email verification | `edge` | Needs email check + browser switch |
| Browse Google Maps | `web` maps_search | Headless extraction faster |
| Watch a video | `edge` | Needs GUI rendering |
| Download a file | `web` fetch or `edge` | Depends on auth requirements |
| Deep research (20+ sources) | `web` deep_research | Parallel, vast, source-tracked |
| Bulk data scraping | `web` scrape | 12 parallel workers |
| Crawl a website | `web` crawl | BFS with parallel batches |

---

## 15. Performance

### Benchmarks (M4 MacBook 16GB)

| Action | Pages | Time | Throughput |
|--------|-------|------|------------|
| search | — | 0.5-2s | N/A |
| fetch (Tier 1) | 1 | 0.3-1s | ~3 pages/s |
| fetch (Tier 2) | 1 | 2-4s | ~0.3 pages/s |
| deep_research | 15 | 10-25s | ~0.8 pages/s |
| deep_research | 20 | 15-40s | ~0.6 pages/s |
| lead_gen (30 pages) | 30 | 20-60s | ~0.7 pages/s |
| scrape (50 URLs) | 50 | 30-90s | ~0.8 pages/s |
| crawl (30 pages, depth 2) | 30 | 20-60s | ~0.7 pages/s |
| maps_search (20 results) | 20+ | 15-45s | ~0.6 pages/s |

### Parallelism

- **12 fetch workers** (ThreadPoolExecutor) for bulk operations
- **6 search workers** for multi-query research
- **4 directory workers** for maps_search fallback
- Cache prevents redundant fetches during multi-query flows

### Memory

- Tier 1 (HTTP only): ~2MB baseline
- Tier 2 (Playwright): ~150MB for headless Chromium (lazy-loaded, shared)
- Cache: ~10-50MB depending on recent activity (5-min TTL auto-expires)

---

## 16. Configuration

| Constant | Value | Description |
|----------|-------|-------------|
| `CACHE_TTL` | 300s | Cache freshness window |
| `MAX_WORKERS` | 12 | Parallel fetch threads |
| `FETCH_TIMEOUT` | 20s | HTTP request timeout |
| `PW_TIMEOUT` | 30000ms | Playwright page timeout |
| `MAX_PAGE_CHARS` | 24000 | Max chars per fetch output |
| `MAX_RESEARCH_PAGES` | 20 | Max pages for deep_research |
| `MAX_CRAWL_DEPTH` | 3 | Max crawl link depth |
| `MAX_CRAWL_PAGES` | 50 | Max crawl pages |
| `MAX_SEARCH_RESULTS` | 20 | Max search results |
| `LEAD_GEN_MAX_PAGES` | 30 | Max pages scanned for lead gen |

### Files

| Path | Purpose |
|------|---------|
| `~/.agentf_headless.log` | Debug log |
| `~/.headless_cache/` | Page cache (MD5-keyed, 5-min TTL) |

---

## 17. 2026 Design Philosophy

AetherHeadless embodies the 2026 paradigmatic shift in web scraping:

### From Browsers to Intents

> "Stop managing a browser. Start managing Intents and Inference."

The old model: launch browser → navigate → wait → parse DOM → extract.
The new model: declare intent → intelligent routing → parallel execution → clean output.

### The "Four-Tier Reality" of the Web

AetherHeadless implements **intelligent tier routing** based on the February 2026 analysis showing that 70% of the web doesn't need a browser at all. Raw HTTP with clean markdown output handles the vast majority of use cases.

### Markdown-First Output

Following Firecrawl's lead, all output is clean markdown — not raw HTML, not stripped text. Markdown preserves semantic structure (headings, links, lists) while achieving 67% token reduction vs HTML. This is optimal for LLM context windows.

### Parallel by Default

Inspired by the "Agentic Sales Stack" trend of 2026, deep research and lead generation are parallel-first operations. 12 concurrent workers ensure that vast result volumes are achievable in seconds, not minutes.

### Source Attribution

Every piece of data includes its source URL. This enables the agent to cite sources (like Claude/Grok/Gemini do in deep research) and allows the user to verify findings.

### Self-Healing Tier Escalation

When HTTP fails (403, thin content, connection error), the system automatically escalates to Playwright headless — no manual intervention. This mirrors the "self-healing locator" trend in browser automation frameworks like Skyvern 2.0.

---

*AetherHeadless v1.0 — Agent F (Amber) — March 2026*
