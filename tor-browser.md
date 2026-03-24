# AetherTor v1.0 — Headless Tor Browser Engine

State-of-the-art headless dark-web/deep-web browser for the Amber AI agent. Security-airgapped Tor browsing with research-grade crawling, file discovery, and data extraction.

---

## Architecture Overview

```
User prompt ("use tor", "browse the dark web", etc.)
     ↓
agent.py load_tools() → discovers tor-browser.py via TOOL_METADATA
     ↓
AetherTor server mode (persistent stdin/stdout JSON)
     ↓
┌─────────────────────────────────────────────────┐
│              AetherTor Engine                    │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │         Tor Daemon Manager                │   │
│  │  • Auto-start/stop via stem              │   │
│  │  • Circuit rotation (NEWNYM)             │   │
│  │  • Stream isolation per domain           │   │
│  │  • Control port monitoring               │   │
│  └──────────┬───────────────────────────────┘   │
│             │                                    │
│  ┌──────────▼───────────────────────────────┐   │
│  │       Security Airgap Layer               │   │
│  │  • All traffic → SOCKS5 (127.0.0.1:9050)│   │
│  │  • DNS resolution through Tor (rdns=True)│   │
│  │  • Tor Browser UA fingerprint            │   │
│  │  • No WebRTC / No geolocation            │   │
│  │  • Circuit isolation via SOCKS5 auth     │   │
│  └──────────┬───────────────────────────────┘   │
│             │                                    │
│  ┌──────────▼───────────────────────────────┐   │
│  │       3-Tier Fetch Engine                 │   │
│  │                                           │   │
│  │  Tier 1: SOCKS HTTP/urllib               │   │
│  │    └─ Raw HTTP through Tor. <3s/page.    │   │
│  │    └─ PySocks SOCKS5h (remote DNS).      │   │
│  │                                           │   │
│  │  Tier 2: Playwright + Tor SOCKS5         │   │
│  │    └─ JS-rendered onion sites. ~5-10s.   │   │
│  │    └─ Chromium/Firefox with proxy config. │   │
│  │    └─ Dedicated worker thread (safe).    │   │
│  │                                           │   │
│  │  Tier 3: Circuit Rotation + Retry        │   │
│  │    └─ NEWNYM → fresh exit → retry Tier 1 │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │       Processing Pipeline                 │   │
│  │  • HTML → Markdown converter              │   │
│  │  • Content fingerprinting (MinHash)       │   │
│  │  • .onion link extraction                 │   │
│  │  • File discovery engine                  │   │
│  │  • Data extraction (wallets/PGP/emails)  │   │
│  │  • Response caching (10min TTL)          │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

---

## Security Airgapping

AetherTor implements multiple layers of security isolation to prevent tracking:

### Network Layer
- **All traffic routed through Tor SOCKS5** (127.0.0.1:9050) — zero clearnet leaks
- **DNS resolution through Tor** — PySocks `rdns=True` ensures DNS queries go through the Tor circuit, preventing DNS leaks
- **Stream isolation** — unique SOCKS5 username/password per domain via `hashlib.md5(host)`, isolating circuits per destination
- **No fallback to clearnet** — if Tor is unavailable, the tool reports an error rather than bypassing

### Browser Fingerprint
- **Tor Browser user-agent** — `Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0` matches real Tor Browser users
- **Standard viewport** — 1280x800 matching Tor Browser default
- **WebRTC disabled** — both at Chromium flag level and JavaScript level to prevent IP leakage
- **Geolocation disabled** — no location APIs available
- **Screen resolution spoofed** — matches Tor Browser standard values

### Circuit Management
- **Automatic rotation** — circuit rotated (NEWNYM) between crawl batches, research branches, and on failures
- **MaxCircuitDirtiness 300** — circuits replaced every 5 minutes maximum
- **NewCircuitPeriod 30** — new circuits considered every 30 seconds
- **NumEntryGuards 8** — distributed entry guards for resilience
- **SafeSocks 1** — reject unsafe SOCKS requests

### Playwright Security
- `--disable-webrtc` — WebRTC completely disabled
- `--enforce-webrtc-ip-permission-check` — additional WebRTC protection
- `--disable-geolocation` — no location access
- `--disable-extensions` — no extension fingerprinting
- `--disable-background-networking` — no background connections
- `ignore_https_errors=True` — handle onion site self-signed certs
- `bypass_csp=True` — access content on restrictive sites
- JavaScript stealth patches — `navigator.webdriver` undefined, `RTCPeerConnection` removed

---

## Actions

### search
Search across Tor search engines with multi-engine fallback.

**Engines (fallback chain):**
1. **Ahmia** (ahmia.fi) — clearnet gateway to Tor search, fastest
2. **Torch** (.onion) — major Tor-native search engine
3. **DuckDuckGo onion** (.onion) — privacy-focused, limited onion results
4. **Haystack** (.onion) — large onion index

```json
{"action": "search", "query": "security research databases", "max_results": 10}
```

### fetch
Fetch any URL through Tor and return clean markdown.

**Supports:** .onion URLs, clearnet URLs (anonymized through Tor exit nodes)

```json
{"action": "fetch", "url": "http://example.onion/page", "max_chars": 24000}
```

### deep_research
Multi-branch hierarchical research across Tor network. Decomposes query into sub-topics, searches and fetches across multiple branches with fact extraction.

**Topic templates:** security, marketplace, privacy, default

**Pipeline:**
1. SEED — initial broad search across Tor engines
2. DECOMPOSE — select topic template, create research branches
3. BRANCH — per-branch: search → fetch → extract facts (iterative)
4. DEDUPLICATE — MinHash content fingerprinting removes mirrors
5. SYNTHESIZE — structured report with executive summary + branch findings

```json
{"action": "deep_research", "query": "cybersecurity threat intelligence", "max_pages": 30}
```

### crawl
BFS spider for .onion sites with deduplication and file detection.

**Features:**
- Breadth-first crawl to configurable depth
- MinHash content deduplication (detects mirror sites)
- Automatic file discovery on every page
- .onion link extraction (internal + external)
- Circuit rotation every 10 pages
- URL pattern filtering

```json
{"action": "crawl", "url": "http://example.onion", "max_depth": 2, "max_pages": 30, "pattern": "/data/.*"}
```

### discover
Discover .onion addresses from directories and search engines.

**Sources:**
- Tor search engine results
- Hidden Wiki mirrors
- Dark.fail directory
- Ahmia onion index

```json
{"action": "discover", "query": "security research", "max_results": 20}
```

### extract_files
Scan pages for downloadable files and data assets.

**Detected file types:**

| Category | Extensions |
|----------|-----------|
| Database | .db, .sqlite, .sqlite3, .sql, .mdb, .accdb, .dbf |
| Data | .csv, .json, .jsonl, .xml, .yaml, .yml, .tsv, .parquet |
| Archive | .zip, .tar, .gz, .tar.gz, .tgz, .bz2, .7z, .rar, .xz |
| Backup | .bak, .dump, .backup, .old, .orig, .save |
| Document | .pdf, .doc, .docx, .txt, .rtf, .odt, .xls, .xlsx |
| Code | .py, .js, .php, .rb, .go, .rs, .c, .cpp, .java |
| Config | .conf, .cfg, .ini, .env, .htaccess, .htpasswd |
| Credential | .pem, .key, .crt, .cert, .pfx, .p12, .jks |

```json
{"action": "extract_files", "url": "http://example.onion/data/"}
```

### extract
Extract structured data from onion pages.

**Extract types:**
- `emails` — email addresses (with junk filtering)
- `wallets` — Bitcoin, Ethereum, Monero wallet addresses
- `pgp` — PGP public key blocks
- `all` — everything above + onion links

```json
{"action": "extract", "url": "http://example.onion/contact", "extract_type": "all"}
```

### status
Check Tor daemon status, circuit information, and connectivity.

```json
{"action": "status"}
```

**Returns:**
- Dependency status (tor, stem, PySocks, Playwright)
- SOCKS proxy connectivity
- Tor Project API verification (confirms Tor is active)
- Exit node IP address
- Active circuit paths (guard → middle → exit)
- Traffic statistics

---

## Content Deduplication

Dark web crawling encounters massive duplication (research shows ~94% of onion content is mirrored). AetherTor uses MinHash-style content fingerprinting:

1. **Shingling** — text split into k=5 character shingles
2. **Hashing** — each shingle MD5-hashed, truncated to 8 chars
3. **MinHash** — keep smallest 64 hashes as fingerprint
4. **Jaccard similarity** — compare fingerprint sets (threshold: 0.7)

This detects:
- Mirror sites (same content, different .onion address)
- Phishing clones
- Content farms reposting the same pages

---

## Trigger Phrases

The agent activates AetherTor when the user says any of:
- "browse the deep web"
- "browse the dark web"
- "use tor browser"
- "use tor" / "using tor"
- "dark web" + subject
- "deep web" + subject
- ".onion" reference
- "tor network"

Regular web browsing always uses the `web` tool (headless-browser.py) instead.

---

## Installation & Setup

### Quick Setup

```bash
# Install Tor daemon
brew install tor

# Install Python dependencies
pip3 install stem PySocks

# Optional: install Playwright for JS rendering
pip3 install playwright
playwright install chromium

# Check status
python3 agent-functions/tor-browser.py --status

# Auto-install Python deps
python3 agent-functions/tor-browser.py --install
```

### Tor Configuration

AetherTor auto-generates a secure `torrc` at `~/.aethertor/torrc`:

```
SocksPort 9050
ControlPort 9051
CookieAuthentication 1
SafeSocks 1
MaxCircuitDirtiness 300
NewCircuitPeriod 30
NumEntryGuards 8
CircuitBuildTimeout 30
```

### Starting Tor

AetherTor will automatically start Tor when needed. You can also:

```bash
# Manual start
tor -f ~/.aethertor/torrc

# Or via Homebrew
brew services start tor

# Verify
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip
```

---

## Server Protocol

Follows the same stdin/stdout JSON protocol as headless-browser.py and edge.py.

### Handshake
```json
{"status": "ready", "version": "v1.0_aethertor", "tool": "tor"}
```

### Request
```json
{"action": "search", "query": "security databases"}
```

### Response
```json
{"status": "success", "page": "# Tor Search: security databases\n..."}
```

### Error
```json
{"status": "error", "message": "Tor not available"}
```

---

## Architecture Comparison

| Feature | headless-browser.py (web) | tor-browser.py (tor) |
|---------|---------------------------|----------------------|
| Network | Direct clearnet | Tor SOCKS5 proxy |
| DNS | System resolver | Through Tor (rdns) |
| Speed | <1s (Tier 1) | ~3s (Tier 1) |
| Search engines | DDG, Google, Bing | Ahmia, Torch, DDG onion, Haystack |
| Priority | 1000 (default) | 100 (explicit only) |
| JS rendering | Playwright (direct) | Playwright + SOCKS5 |
| Deduplication | None | MinHash fingerprinting |
| File discovery | No | Yes (8 categories) |
| Circuit management | N/A | NEWNYM rotation |
| User-agent | Chrome/Safari rotating | Fixed Tor Browser UA |
| Cache TTL | 5 minutes | 10 minutes |

---

## Performance Tuning

Tor is inherently slower than clearnet. AetherTor is tuned for this:

- **FETCH_TIMEOUT: 30s** (vs 20s for clearnet) — onion sites load slowly
- **PW_TIMEOUT: 45s** (vs 30s) — JS rendering through Tor is slow
- **MAX_WORKERS: 6** (vs 12) — conservative parallelism to avoid Tor congestion
- **CACHE_TTL: 600s** (vs 300s) — longer caching for slow fetches
- **RESEARCH_TIME_BUDGET: 300s** (vs 180s) — more time for Tor-speed research
- **Circuit rotation every 10 pages** — balance anonymity vs speed

---

## File Layout

```
agent-functions/
  tor-browser.py          # Main tool (~1500 lines)

~/.aethertor/
  torrc                   # Auto-generated Tor config
  data/                   # Tor data directory
  tor.log                 # Tor daemon logs

~/.agentf_tor.log         # AetherTor application logs
~/.tor_cache/             # Cached page content (MD5 keyed)
```

---

## Limitations

- **Speed** — Tor circuits add 2-10s latency per request. Deep research takes 3-5 minutes.
- **Availability** — Onion sites have high churn. Sites may be offline between search and fetch.
- **Search coverage** — No Tor search engine is comprehensive. Discovery relies on multiple sources.
- **JavaScript** — Requires Playwright + Tor SOCKS5 for JS-heavy onion sites. Some may still block headless browsers.
- **Restricted discovery** — Future onion services with client authorization (Proposal 368) will require auth keys.
- **Rate limiting** — Tor search engines may rate-limit queries. AetherTor rotates circuits to mitigate.

---

## Legal Notice

This tool is designed for authorized security research, threat intelligence, OSINT, and educational purposes. Users are responsible for compliance with applicable laws and regulations. Always obtain proper authorization before conducting security research.
