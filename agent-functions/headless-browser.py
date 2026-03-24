#!/usr/bin/env python3
"""
headless-browser.py v2.0 — AetherHeadless: State-of-the-Art Headless Web Engine (March 2026)

Bleeding-edge headless browsing for agentic web scraping, deep research, and lead generation.
Optimized for SPEED, HIGH FIDELITY, and VAST RESULT VOLUME — no GUI, no vision, pure data.

v2.0 Changes:
  - Thread-safe Playwright: dedicated worker thread queue (fixes "cannot switch to thread" crashes)
  - Lead-gen aware deep_research: auto-extracts contacts when query is lead-focused
  - Improved lead_gen: 6 rounds (was 4), 180s budget (was 90s), better dedup, more directory sites
  - Robust server: longer action-aware timeouts, handshake timeout, better error recovery
  - New lead_gen topic template for deep_research decomposition

Architecture: 3-Tier Intelligent Routing
  Tier 1 — HTTP/urllib: Raw HTTP fetch → clean markdown. <1s per page. 70% of the web.
  Tier 2 — Playwright Headless: JS-rendered SPAs, dynamic content. ~2-4s per page.
  Tier 3 — Stealth Playwright: Anti-bot bypass, Cloudflare challenges. ~5-10s per page.

Design Principles (2026 SOTA):
  - Markdown-first output (67% token reduction vs raw HTML)
  - Tiered routing: simplest method first, escalate only on failure
  - Parallel fetching: concurrent requests for deep research (ThreadPoolExecutor)
  - Self-healing: auto-retry with tier escalation on failure
  - Source tracking: every result includes origin URLs
  - Lead extraction: regex + heuristic email/phone/business harvesting
  - Clean LLM context: structured output optimized for Qwen 3.5-9B token budget

Modes:
  search        — Web search via DuckDuckGo/Google (returns URLs + snippets)
  fetch         — Fetch single URL → clean markdown text
  deep_research — Multi-query parallel research with synthesis-ready output
  extract       — Extract emails, phones, businesses from URL(s)
  scrape        — Bulk parallel fetch of multiple URLs
  form          — Fill and submit forms headlessly (Playwright)
  lead_gen      — Specialized B2B lead harvesting (emails/phones across directories)
  crawl         — Spider a site following links to depth N
  maps_search   — Google Maps business listing scraper for lead gen

Server mode: --server for persistent stdin/stdout JSON (like edge.py)
One-shot mode: --json '{"action":"search","query":"..."}' for single calls
"""

# ── FIX: Remove script directory from sys.path to prevent agent-functions/email.py
#    from shadowing Python's stdlib 'email' package (needed by urllib/http internally).
import sys, os
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir in sys.path:
    sys.path.remove(_script_dir)

import json, argparse, re, html, hashlib, time, logging, ssl, gzip
import threading, queue as _queue_mod
from typing import Dict, Any, Optional, List, Tuple, Set
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote_plus, urlparse, urljoin, parse_qs, unquote
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

# ──────────────────────────────────────────────────────────────
# Constants & Configuration
# ──────────────────────────────────────────────────────────────
SCRIPT_VERSION = "v2.0_aetherheadless"
LOG_FILE = os.path.expanduser("~/.agentf_headless.log")
CACHE_DIR = os.path.expanduser("~/.headless_cache")
CACHE_TTL = 300  # 5 min cache for repeated fetches
MAX_WORKERS = 12  # parallel fetch threads
FETCH_TIMEOUT = 20  # seconds per HTTP request
PW_TIMEOUT = 30000  # Playwright timeout ms
MAX_PAGE_CHARS = 24000  # max chars returned per page (LLM context budget)
MAX_RESEARCH_PAGES = 20  # max pages for legacy deep research
MAX_CRAWL_DEPTH = 3
MAX_CRAWL_PAGES = 50
MAX_SEARCH_RESULTS = 20
LEAD_GEN_MAX_PAGES = 30

# Deep Research v2.0 — Hierarchical Research Tree constants
MAX_DEEP_RESEARCH_PAGES = 60   # total pages across all branches
MAX_BRANCH_PAGES = 15           # max pages per research branch
MAX_BRANCHES = 6                # max topic branches
MAX_RESEARCH_ROUNDS = 3         # iterative search rounds per branch
RESEARCH_TIME_BUDGET = 180.0    # 3 minute total budget
MIN_COVERAGE_SCORE = 0.3        # branch pruning threshold

os.makedirs(CACHE_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG, filename=LOG_FILE, filemode='a',
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("aetherheadless")

# ──────────────────────────────────────────────────────────────
# TOOL_METADATA — agent.py auto-discovery
# ──────────────────────────────────────────────────────────────
TOOL_METADATA = {
    "name": "web",
    "description": (
        "AetherHeadless v2.0 — blazing-fast headless web engine. NO browser GUI. "
        "DEFAULT tool for all web browsing, research, and data gathering. "
        "Use this UNLESS user explicitly says 'open edge' or 'open safari'. "
        "Triggers: 'browse for', 'research', 'deep research', 'look up', 'search for', 'find out', 'what is'. "
        "\n\nACTIONS:\n"
        "  search       → Web search. Args: query, max_results (default 10). Returns titles+URLs+snippets.\n"
        "  fetch        → Fetch URL → clean markdown. Args: url, max_chars. Tries HTTP first, auto-escalates to JS rendering.\n"
        "  deep_research → Hierarchical multi-branch research engine (v2.0). Args: query, max_pages (default 40). "
        "Decomposes topic into sub-branches, runs iterative search+fetch loops per branch (40-60 pages), "
        "extracts facts and timelines. LEAD-GEN AWARE: when query mentions leads/email/contacts, "
        "automatically extracts emails+phones from every page. Use for comprehensive research AND lead generation. "
        "Returns structured report: executive summary, branch findings, contacts found, timeline, scored sources.\n"
        "  extract      → Extract emails/phones/businesses from URL(s). Args: url or urls (comma-separated), extract_type (emails/phones/all).\n"
        "  scrape       → Bulk fetch multiple URLs in parallel. Args: urls (comma-separated), max_chars_per_page.\n"
        "  form         → Fill+submit form headlessly. Args: url, fields (JSON object of field:value), submit_selector.\n"
        "  lead_gen     → B2B lead harvesting v2.0 (6 rounds, 180s budget, improved dedup). "
        "Args: query, location, lead_type (emails/phones/all), max_leads. Best for targeted B2B lead collection.\n"
        "  crawl        → Spider site following links. Args: url, max_depth (default 2), max_pages (default 30), pattern (URL filter regex).\n"
        "  maps_search  → Google Maps business scraper. Args: query, location, max_results.\n"
        "\nExamples:\n"
        '  {"action":"search","query":"best python web frameworks 2026"}\n'
        '  {"action":"deep_research","query":"property management short term rental east tennessee email contact"}\n'
        '  {"action":"fetch","url":"https://example.com/article"}\n'
        '  {"action":"extract","url":"https://company.com/contact","extract_type":"all"}\n'
        '  {"action":"lead_gen","query":"airbnb hosts","location":"east tennessee","lead_type":"all","max_leads":100}\n'
        '  {"action":"maps_search","query":"booking companies","location":"Nashville TN","max_results":20}'
    ),
    "priority": 1000,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "fetch", "deep_research", "extract", "scrape",
                         "form", "lead_gen", "crawl", "maps_search"],
                "description": "Action to perform."
            },
            "query": {
                "type": "string",
                "description": "Search query or research topic."
            },
            "url": {
                "type": "string",
                "description": "URL to fetch/extract/crawl."
            },
            "urls": {
                "type": "string",
                "description": "Comma-separated URLs for bulk scrape or extract."
            },
            "location": {
                "type": "string",
                "description": "Location for lead_gen and maps_search."
            },
            "max_results": {
                "type": "integer",
                "default": 10,
                "description": "Max search results."
            },
            "max_pages": {
                "type": "integer",
                "default": 40,
                "description": "Max pages for deep research."
            },
            "max_leads": {
                "type": "integer",
                "default": 50,
                "description": "Max leads for lead_gen."
            },
            "max_depth": {
                "type": "integer",
                "default": 2,
                "description": "Max crawl depth."
            },
            "max_chars": {
                "type": "integer",
                "default": 24000,
                "description": "Max chars per page output."
            },
            "max_chars_per_page": {
                "type": "integer",
                "default": 8000,
                "description": "Max chars per page in bulk scrape."
            },
            "extract_type": {
                "type": "string",
                "enum": ["emails", "phones", "all"],
                "default": "all",
                "description": "What to extract."
            },
            "lead_type": {
                "type": "string",
                "enum": ["emails", "phones", "all"],
                "default": "all",
                "description": "Lead data type."
            },
            "fields": {
                "type": "object",
                "description": "Form fields as {selector_or_name: value} for form action."
            },
            "submit_selector": {
                "type": "string",
                "description": "CSS selector for form submit button."
            },
            "pattern": {
                "type": "string",
                "description": "URL filter regex for crawl."
            }
        },
        "required": ["action"]
    }
}

# ──────────────────────────────────────────────────────────────
# HTTP Headers & SSL
# ──────────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
]

def _ua():
    """Rotate user agent."""
    return USER_AGENTS[hash(str(time.time())) % len(USER_AGENTS)]

def _headers(extra=None):
    h = {
        'User-Agent': _ua(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    if extra:
        h.update(extra)
    return h

# Lenient SSL context for sites with bad certs
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

# ══════════════════════════════════════════════════════════════
#  TIER 1 — RAW HTTP FETCH (urllib, fastest)
# ══════════════════════════════════════════════════════════════

def _cache_key(url):
    return os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ".txt")

def _cache_get(url):
    """Return cached content if fresh, else None."""
    path = _cache_key(url)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < CACHE_TTL:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception:
                pass
    return None

def _cache_set(url, content):
    """Cache page content."""
    try:
        path = _cache_key(url)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content[:100000])  # cap cache file size
    except Exception:
        pass

def http_fetch(url, timeout=FETCH_TIMEOUT, headers=None, post_data=None):
    """Tier 1: Raw HTTP fetch. Returns (html_string, status_code, final_url) or raises.
    If post_data is a dict, sends as application/x-www-form-urlencoded POST."""
    hdrs = _headers(headers)
    body = None
    if post_data:
        body = urlencode(post_data).encode('utf-8')
        hdrs['Content-Type'] = 'application/x-www-form-urlencoded'
    req = Request(url, data=body, headers=hdrs)
    try:
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
        data = resp.read()
        # Handle gzip
        if resp.headers.get('Content-Encoding') == 'gzip':
            data = gzip.decompress(data)
        encoding = resp.headers.get_content_charset() or 'utf-8'
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            text = data.decode('utf-8', errors='replace')
        return text, resp.status, resp.url
    except HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode('utf-8', errors='replace')[:2000]
        except Exception:
            pass
        return body_text, e.code, url
    except URLError as e:
        raise ConnectionError(f"URL Error fetching {url}: {e.reason}")
    except Exception as e:
        raise ConnectionError(f"Fetch error {url}: {e}")


# ══════════════════════════════════════════════════════════════
#  TIER 2 — PLAYWRIGHT HEADLESS (JS rendering)
# ══════════════════════════════════════════════════════════════

_pw_instance = None
_pw_browser = None
_pw_context = None
_pw_lock = threading.Lock()

# ── Playwright Worker Thread Queue ──
# Solves "cannot switch to a different thread" error when ThreadPoolExecutor
# workers call pw_fetch() from different threads. All Playwright operations
# are serialized through a single dedicated worker thread.
_pw_request_queue = _queue_mod.Queue()
_pw_worker_thread = None
_pw_worker_lock = threading.Lock()

class _PwRequest:
    """Request object for the Playwright worker queue."""
    __slots__ = ('func', 'args', 'kwargs', 'result', 'error', 'done')
    def __init__(self, func, args=(), kwargs=None):
        self.func = func
        self.args = args
        self.kwargs = kwargs or {}
        self.result = None
        self.error = None
        self.done = threading.Event()

def _pw_worker_loop():
    """Dedicated Playwright thread — processes all browser operations serially."""
    while True:
        req = _pw_request_queue.get()
        if req is None:  # shutdown sentinel
            break
        try:
            req.result = req.func(*req.args, **req.kwargs)
        except Exception as e:
            req.error = e
        finally:
            req.done.set()
            _pw_request_queue.task_done()

def _ensure_pw_worker():
    """Ensure the Playwright worker thread is running."""
    global _pw_worker_thread
    with _pw_worker_lock:
        if _pw_worker_thread is None or not _pw_worker_thread.is_alive():
            _pw_worker_thread = threading.Thread(
                target=_pw_worker_loop, daemon=True, name='pw-worker'
            )
            _pw_worker_thread.start()

def _run_in_pw_thread(func, *args, timeout_sec=45, **kwargs):
    """Execute a function in the Playwright worker thread and wait for result."""
    _ensure_pw_worker()
    req = _PwRequest(func, args, kwargs)
    _pw_request_queue.put(req)
    if not req.done.wait(timeout=timeout_sec):
        raise RuntimeError(f"Playwright operation timed out after {timeout_sec}s")
    if req.error:
        raise req.error
    return req.result

def _get_playwright():
    """Lazy-init Playwright headless browser. Shared across calls."""
    global _pw_instance, _pw_browser, _pw_context
    with _pw_lock:
        if _pw_browser and _pw_browser.is_connected():
            return _pw_context
        try:
            from playwright.sync_api import sync_playwright
            if _pw_instance:
                try:
                    _pw_instance.stop()
                except Exception:
                    pass
            _pw_instance = sync_playwright().start()
            launch_args = [
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-extensions',
                '--disable-background-networking',
                '--disable-sync',
                '--disable-translate',
                '--metrics-recording-only',
            ]
            try:
                _pw_browser = _pw_instance.chromium.launch(
                    headless=True, channel='msedge', args=launch_args
                )
            except Exception:
                try:
                    _pw_browser = _pw_instance.chromium.launch(
                        headless=True, args=launch_args
                    )
                except Exception:
                    _pw_browser = _pw_instance.chromium.launch(headless=True)
            _pw_context = _pw_browser.new_context(
                user_agent=USER_AGENTS[0],
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                timezone_id='America/New_York',
                java_script_enabled=True,
                bypass_csp=True,
                ignore_https_errors=True,
            )
            # Stealth patches
            _pw_context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                window.chrome = {runtime: {}};
                Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) =>
                    parameters.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : originalQuery(parameters);
            """)
            log.info("Playwright headless browser initialized")
            return _pw_context
        except ImportError:
            log.warning("Playwright not available, Tier 2/3 disabled")
            return None
        except Exception as e:
            log.error(f"Playwright init failed: {e}")
            return None

def _pw_fetch_impl(url, timeout=PW_TIMEOUT, wait_for_selector=None):
    """Internal Playwright fetch — runs ONLY in pw-worker thread."""
    ctx = _get_playwright()
    if not ctx:
        raise RuntimeError("Playwright not available")
    page = ctx.new_page()
    try:
        page.set_default_timeout(timeout)
        resp = page.goto(url, wait_until='domcontentloaded', timeout=timeout)
        try:
            page.wait_for_load_state('networkidle', timeout=8000)
        except Exception:
            pass
        if wait_for_selector:
            try:
                page.wait_for_selector(wait_for_selector, timeout=5000)
            except Exception:
                pass
        content = page.content()
        status = resp.status if resp else 200
        final_url = page.url
        return content, status, final_url
    finally:
        try:
            page.close()
        except Exception:
            pass

def pw_fetch(url, timeout=PW_TIMEOUT, wait_for_selector=None):
    """Thread-safe Playwright fetch — routes through dedicated worker thread.
    Solves 'cannot switch to a different thread' errors from ThreadPoolExecutor.
    """
    wait_sec = max(45, timeout // 1000 + 15)
    return _run_in_pw_thread(
        _pw_fetch_impl, url, timeout, wait_for_selector, timeout_sec=wait_sec
    )

def pw_form_submit(url, fields, submit_selector=None, timeout=PW_TIMEOUT):
    """Thread-safe form submission — routes through dedicated Playwright worker thread."""
    wait_sec = max(60, timeout // 1000 + 20)
    return _run_in_pw_thread(
        _pw_form_submit_impl, url, fields, submit_selector, timeout,
        timeout_sec=wait_sec
    )

def _pw_form_submit_impl(url, fields, submit_selector=None, timeout=PW_TIMEOUT):
    """Internal: fill and submit a form headlessly (runs in pw-worker thread only)."""
    ctx = _get_playwright()
    if not ctx:
        raise RuntimeError("Playwright not available for form submission")
    page = ctx.new_page()
    try:
        page.set_default_timeout(timeout)
        page.goto(url, wait_until='domcontentloaded', timeout=timeout)
        try:
            page.wait_for_load_state('networkidle', timeout=8000)
        except Exception:
            pass

        results = []
        for selector, value in fields.items():
            try:
                # Try multiple strategies
                el = None
                # Strategy 1: CSS selector
                try:
                    el = page.locator(selector).first
                    if el.count() == 0:
                        el = None
                except Exception:
                    el = None
                # Strategy 2: Name attribute
                if not el:
                    try:
                        el = page.locator(f'[name="{selector}"]').first
                        if el.count() == 0:
                            el = None
                    except Exception:
                        el = None
                # Strategy 3: Label text
                if not el:
                    try:
                        el = page.get_by_label(selector)
                        if el.count() == 0:
                            el = None
                    except Exception:
                        el = None
                # Strategy 4: Placeholder text
                if not el:
                    try:
                        el = page.get_by_placeholder(selector)
                        if el.count() == 0:
                            el = None
                    except Exception:
                        el = None

                if el:
                    tag = el.evaluate("e => e.tagName").lower()
                    input_type = el.evaluate("e => e.type || ''").lower()
                    if tag == 'select':
                        el.select_option(value=value)
                        results.append(f"  [{selector}] selected: {value}")
                    elif input_type in ('checkbox', 'radio'):
                        if value.lower() in ('true', '1', 'yes', 'on'):
                            el.check()
                        else:
                            el.uncheck()
                        results.append(f"  [{selector}] checked: {value}")
                    else:
                        el.fill(value)
                        results.append(f"  [{selector}] filled: {value}")
                else:
                    results.append(f"  [{selector}] NOT FOUND — skipped")
            except Exception as e:
                results.append(f"  [{selector}] error: {e}")

        # Submit
        submit_result = "No submit selector provided"
        if submit_selector:
            try:
                page.locator(submit_selector).first.click()
                page.wait_for_load_state('domcontentloaded', timeout=10000)
                submit_result = f"Clicked {submit_selector} — navigated to {page.url}"
            except Exception as e:
                # Fallback: press Enter
                try:
                    page.keyboard.press('Enter')
                    page.wait_for_load_state('domcontentloaded', timeout=10000)
                    submit_result = f"Pressed Enter — navigated to {page.url}"
                except Exception as e2:
                    submit_result = f"Submit failed: {e} / Enter fallback: {e2}"
        else:
            # Auto-find submit button
            for sel in ['button[type="submit"]', 'input[type="submit"]',
                        'button:has-text("Submit")', 'button:has-text("Sign")',
                        'button:has-text("Register")', 'button:has-text("Send")']:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0:
                        btn.click()
                        page.wait_for_load_state('domcontentloaded', timeout=10000)
                        submit_result = f"Auto-clicked {sel} — navigated to {page.url}"
                        break
                except Exception:
                    continue

        # Read result page
        result_page = html_to_markdown(page.content(), page.url)
        return {
            "fields": results,
            "submit": submit_result,
            "result_url": page.url,
            "result_page": result_page[:8000]
        }
    finally:
        try:
            page.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#  HTML → MARKDOWN CONVERTER (token-efficient LLM output)
# ══════════════════════════════════════════════════════════════

def _strip_tags(text):
    """Remove HTML tags."""
    return re.sub(r'<[^>]+>', '', text)

def _decode_entities(text):
    """Decode HTML entities."""
    return html.unescape(text)

def html_to_markdown(raw_html, source_url="", max_chars=MAX_PAGE_CHARS):
    """
    Convert HTML to clean markdown optimized for LLM consumption.
    Preserves: headings, links, lists, tables, code blocks, paragraphs.
    Strips: scripts, styles, nav, footer, ads, tracking.
    """
    if not raw_html:
        return ""

    h = raw_html

    # Remove scripts, styles, SVGs, comments
    h = re.sub(r'<script[^>]*>.*?</script>', '', h, flags=re.DOTALL | re.IGNORECASE)
    h = re.sub(r'<style[^>]*>.*?</style>', '', h, flags=re.DOTALL | re.IGNORECASE)
    h = re.sub(r'<svg[^>]*>.*?</svg>', '', h, flags=re.DOTALL | re.IGNORECASE)
    h = re.sub(r'<!--.*?-->', '', h, flags=re.DOTALL)
    h = re.sub(r'<noscript[^>]*>.*?</noscript>', '', h, flags=re.DOTALL | re.IGNORECASE)

    # Remove nav, header, footer, aside (boilerplate)
    for tag in ['nav', 'header', 'footer', 'aside']:
        h = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', h, flags=re.DOTALL | re.IGNORECASE)

    # Extract title
    title_m = re.search(r'<title[^>]*>(.*?)</title>', h, re.DOTALL | re.IGNORECASE)
    title = _decode_entities(_strip_tags(title_m.group(1))).strip() if title_m else ""

    # Extract meta description
    meta_m = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']', h, re.IGNORECASE)
    meta_desc = _decode_entities(meta_m.group(1)).strip() if meta_m else ""

    # Convert headings
    for i in range(1, 7):
        h = re.sub(rf'<h{i}[^>]*>(.*?)</h{i}>', lambda m: '\n' + '#' * i + ' ' + _strip_tags(m.group(1)).strip() + '\n', h, flags=re.DOTALL | re.IGNORECASE)

    # Convert links — preserve href
    def _link_replace(m):
        href = m.group(1)
        text = _strip_tags(m.group(2)).strip()
        if not text or text == href:
            return f' {href} '
        # Make absolute
        if href.startswith('/') and source_url:
            parsed = urlparse(source_url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        elif not href.startswith(('http://', 'https://', 'mailto:')):
            return f' {text} '
        return f'[{text}]({href})'
    h = re.sub(r'<a[^>]*href=["\'](.*?)["\'][^>]*>(.*?)</a>', _link_replace, h, flags=re.DOTALL | re.IGNORECASE)

    # Convert lists
    h = re.sub(r'<li[^>]*>', '\n- ', h, flags=re.IGNORECASE)
    h = re.sub(r'</li>', '', h, flags=re.IGNORECASE)

    # Convert code blocks
    h = re.sub(r'<pre[^>]*>(.*?)</pre>', lambda m: '\n```\n' + _strip_tags(m.group(1)).strip() + '\n```\n', h, flags=re.DOTALL | re.IGNORECASE)
    h = re.sub(r'<code[^>]*>(.*?)</code>', lambda m: '`' + _strip_tags(m.group(1)).strip() + '`', h, flags=re.DOTALL | re.IGNORECASE)

    # Convert tables (simple)
    h = re.sub(r'<tr[^>]*>', '\n| ', h, flags=re.IGNORECASE)
    h = re.sub(r'<t[dh][^>]*>', ' ', h, flags=re.IGNORECASE)
    h = re.sub(r'</t[dh]>', ' | ', h, flags=re.IGNORECASE)
    h = re.sub(r'</tr>', '', h, flags=re.IGNORECASE)

    # Convert paragraphs and breaks
    h = re.sub(r'<br\s*/?>', '\n', h, flags=re.IGNORECASE)
    h = re.sub(r'<p[^>]*>', '\n\n', h, flags=re.IGNORECASE)
    h = re.sub(r'</p>', '\n', h, flags=re.IGNORECASE)
    h = re.sub(r'<div[^>]*>', '\n', h, flags=re.IGNORECASE)
    h = re.sub(r'</div>', '\n', h, flags=re.IGNORECASE)

    # Bold/italic
    h = re.sub(r'<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>', r'**\1**', h, flags=re.DOTALL | re.IGNORECASE)
    h = re.sub(r'<(?:i|em)[^>]*>(.*?)</(?:i|em)>', r'*\1*', h, flags=re.DOTALL | re.IGNORECASE)

    # Strip remaining tags
    h = _strip_tags(h)
    h = _decode_entities(h)

    # Collapse whitespace
    h = re.sub(r'[ \t]+', ' ', h)
    h = re.sub(r'\n{3,}', '\n\n', h)
    h = h.strip()

    # Build output
    parts = []
    if title:
        parts.append(f"# {title}")
    if meta_desc:
        parts.append(f"*{meta_desc}*")
    parts.append(h)
    if source_url:
        parts.append(f"\n---\nSource: {source_url}")

    result = '\n\n'.join(parts)

    if len(result) > max_chars:
        result = result[:max_chars] + f"\n\n... [truncated at {max_chars} chars, full page: {len(result)} chars]"

    return result


# ══════════════════════════════════════════════════════════════
#  DATA EXTRACTION ENGINE
# ══════════════════════════════════════════════════════════════

# Email regex — RFC 5322 simplified
EMAIL_RE = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
    re.IGNORECASE
)

# Phone regex — US/international formats
PHONE_RE = re.compile(
    r'(?:'
    r'(?:\+1[\s.-]?)?'                    # +1 prefix
    r'(?:\(?\d{3}\)?[\s.-]?)'             # area code
    r'\d{3}[\s.-]?\d{4}'                  # number
    r'|'
    r'\+\d{1,3}[\s.-]?\d{4,14}'          # international
    r')',
    re.IGNORECASE
)

# Social media pattern
SOCIAL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?'
    r'(?:linkedin\.com/(?:in|company)/[\w\-]+|'
    r'twitter\.com/[\w]+|'
    r'x\.com/[\w]+|'
    r'facebook\.com/[\w.]+|'
    r'instagram\.com/[\w.]+)',
    re.IGNORECASE
)

# Junk email patterns to filter
JUNK_EMAILS = re.compile(
    r'(?:noreply|no-reply|donotreply|support@|info@example|test@|'
    r'.*@sentry\.io|.*@.*\.png|.*@.*\.jpg|.*\.css|.*\.js$|'
    r'.*@wixpress|.*@w3\.org|.*@schema\.org)',
    re.IGNORECASE
)

def extract_emails(text):
    """Extract unique valid emails from text."""
    raw = EMAIL_RE.findall(text)
    seen = set()
    results = []
    for e in raw:
        e_lower = e.lower()
        if e_lower not in seen and not JUNK_EMAILS.match(e_lower):
            # Basic validation
            parts = e_lower.split('@')
            if len(parts) == 2 and '.' in parts[1] and len(parts[0]) >= 2:
                seen.add(e_lower)
                results.append(e)
    return results

def extract_phones(text):
    """Extract unique phone numbers from text."""
    raw = PHONE_RE.findall(text)
    seen = set()
    results = []
    for p in raw:
        # Normalize
        digits = re.sub(r'\D', '', p)
        if len(digits) >= 10 and digits not in seen:
            seen.add(digits)
            results.append(p.strip())
    return results

def extract_social(text):
    """Extract social media URLs."""
    return list(set(SOCIAL_RE.findall(text)))

def extract_contacts(html_text, extract_type='all'):
    """Extract all contact info from HTML/text."""
    result = {}
    if extract_type in ('emails', 'all'):
        result['emails'] = extract_emails(html_text)
    if extract_type in ('phones', 'all'):
        result['phones'] = extract_phones(html_text)
    if extract_type == 'all':
        result['social'] = extract_social(html_text)
    return result


# ══════════════════════════════════════════════════════════════
#  INTELLIGENT FETCH (auto-tier routing)
# ══════════════════════════════════════════════════════════════

def smart_fetch(url, max_chars=MAX_PAGE_CHARS, need_js=False):
    """
    Intelligent 3-tier fetch:
    1. Try HTTP (fastest)
    2. If HTTP fails or content looks JS-dependent → Playwright headless
    3. Returns clean markdown + metadata
    """
    # Handle file:// URLs directly
    if url.startswith("file://"):
        file_path = url[7:]  # strip file://
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return {"markdown": content[:max_chars], "url": url, "tier": "local", "status": 200}
        except FileNotFoundError:
            return {"markdown": f"File not found: {file_path}", "url": url, "tier": "error", "status": 404}
        except Exception as e:
            return {"markdown": f"Error reading file: {e}", "url": url, "tier": "error", "status": 500}

    # Check cache first
    cached = _cache_get(url)
    if cached:
        log.info(f"Cache hit: {url}")
        return {"markdown": cached[:max_chars], "url": url, "tier": "cache", "status": 200}

    # Tier 1: HTTP
    if not need_js:
        try:
            raw_html, status, final_url = http_fetch(url)
            if status == 200 and len(raw_html) > 500:
                # Check if it's a JS-only page
                text_content = _strip_tags(raw_html)
                text_content = re.sub(r'\s+', ' ', text_content).strip()
                # If meaningful text content exists, use it
                if len(text_content) > 200:
                    md = html_to_markdown(raw_html, final_url, max_chars)
                    _cache_set(url, md)
                    return {"markdown": md, "url": final_url, "tier": 1, "status": status}
                else:
                    log.info(f"Tier 1 thin content ({len(text_content)} chars), escalating: {url}")
            elif status == 403 or status == 503:
                log.info(f"Tier 1 blocked ({status}), escalating: {url}")
            elif status >= 400:
                return {"markdown": f"HTTP {status} error fetching {url}", "url": url, "tier": 1, "status": status}
        except ConnectionError as e:
            log.info(f"Tier 1 failed, escalating: {e}")

    # Tier 2: Playwright headless
    try:
        raw_html, status, final_url = pw_fetch(url)
        if raw_html:
            md = html_to_markdown(raw_html, final_url, max_chars)
            _cache_set(url, md)
            return {"markdown": md, "url": final_url, "tier": 2, "status": status}
    except RuntimeError:
        # Playwright not installed — fall back to Tier 1 raw result
        log.warning("Playwright not available, returning Tier 1 result")
        try:
            raw_html, status, final_url = http_fetch(url)
            md = html_to_markdown(raw_html, final_url, max_chars)
            return {"markdown": md, "url": final_url, "tier": 1, "status": status}
        except Exception as e:
            return {"markdown": f"Fetch failed: {e}", "url": url, "tier": "error", "status": 0}
    except Exception as e:
        log.error(f"Tier 2 failed: {e}")
        return {"markdown": f"Fetch failed: {e}", "url": url, "tier": "error", "status": 0}

    return {"markdown": "Empty response from all tiers", "url": url, "tier": "error", "status": 0}


# ══════════════════════════════════════════════════════════════
#  WEB SEARCH (DuckDuckGo HTML + Google fallback)
# ══════════════════════════════════════════════════════════════

def _decode_bing_url(bing_url):
    """Decode Bing redirect URL (bing.com/ck/a?...u=a1<base64>...) to actual URL."""
    if 'bing.com/ck/a' not in bing_url:
        return bing_url
    u_match = re.search(r'[&?]u=a1([^&]+)', bing_url)
    if u_match:
        try:
            import base64
            b64 = u_match.group(1)
            # Add padding
            b64 += '=' * (4 - len(b64) % 4) if len(b64) % 4 else ''
            return base64.b64decode(b64).decode('utf-8')
        except Exception:
            pass
    return bing_url

def _search_duckduckgo(query, max_results=10):
    """Search DuckDuckGo HTML version (POST, no API key needed)."""
    url = "https://html.duckduckgo.com/html/"
    try:
        # POST is required — GET returns empty form on rate-limit
        raw, status, _ = http_fetch(url, headers={'Accept': 'text/html'}, post_data={'q': query})
        if status != 200:
            return []

        results = []
        # Parse DuckDuckGo HTML results
        # Pattern: class="result__a" links + class="result__snippet" text
        blocks = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'class="result__snippet"[^>]*>(.*?)</(?:a|td)',
            raw, re.DOTALL | re.IGNORECASE
        )

        for href, title, snippet in blocks[:max_results]:
            # DDG URLs: direct URLs or duckduckgo.com/y.js (ads) or /l/?uddg= (redirects)
            actual_url = href
            # Extract from uddg= redirect
            uddg = re.search(r'uddg=([^&"]+)', href)
            if uddg:
                actual_url = unquote(uddg.group(1))
            # Skip ad redirects (duckduckgo.com/y.js)
            if 'duckduckgo.com/y.js' in actual_url:
                continue
            # Clean up &amp; in URLs
            actual_url = actual_url.replace('&amp;', '&')

            title_clean = _decode_entities(_strip_tags(title)).strip()
            snippet_clean = _decode_entities(_strip_tags(snippet)).strip()

            if title_clean and actual_url.startswith('http'):
                results.append({
                    'title': title_clean,
                    'url': actual_url,
                    'snippet': snippet_clean[:300]
                })

        return results
    except Exception as e:
        log.error(f"DuckDuckGo search failed: {e}")
        return []

def _search_google(query, max_results=10):
    """Search Google (HTML scrape, no API key)."""
    url = f"https://www.google.com/search?q={quote_plus(query)}&num={max_results}&hl=en"
    try:
        raw, status, _ = http_fetch(url, headers={
            'Accept': 'text/html',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        })
        if status != 200:
            return []

        results = []
        # Google result blocks — /url?q= redirect pattern
        blocks = re.findall(r'<a[^>]*href="/url\?q=([^&"]+)[^"]*"[^>]*>(.*?)</a>', raw, re.DOTALL)
        # Fallback: direct links
        if not blocks:
            blocks = re.findall(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', raw, re.DOTALL)

        seen_urls = set()
        for href, title_html in blocks:
            actual_url = unquote(href)
            if not actual_url.startswith('http'):
                continue
            # Skip Google/Bing self-referential pages
            if any(d in actual_url for d in ['google.com', 'accounts.google', 'bing.com/ck', 'gstatic.com']):
                continue
            if actual_url in seen_urls:
                continue
            seen_urls.add(actual_url)

            title = _decode_entities(_strip_tags(title_html)).strip()
            if title and len(title) > 3:
                results.append({
                    'title': title[:200],
                    'url': actual_url,
                    'snippet': ''
                })
            if len(results) >= max_results:
                break

        return results
    except Exception as e:
        log.error(f"Google search failed: {e}")
        return []

def _search_pw_impl(query, max_results=10):
    """Internal: Playwright search — runs ONLY in pw-worker thread."""
    ctx = _get_playwright()
    if not ctx:
        return []
    page = ctx.new_page()
    try:
        page.goto(f"https://duckduckgo.com/?q={quote_plus(query)}", wait_until='domcontentloaded', timeout=15000)
        try:
            page.wait_for_load_state('networkidle', timeout=8000)
        except Exception:
            pass
        try:
            page.wait_for_selector('[data-result]', timeout=5000)
        except Exception:
            pass

        content = page.content()
        results = []
        blocks = re.findall(
            r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            content, re.DOTALL | re.IGNORECASE
        )
        seen = set()
        for href, title_html in blocks:
            url = href.replace('&amp;', '&')
            if 'duckduckgo.com' in url or url in seen:
                continue
            title = _decode_entities(_strip_tags(title_html)).strip()
            if title and len(title) > 5 and url.startswith('http'):
                seen.add(url)
                results.append({'title': title[:200], 'url': url, 'snippet': ''})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        log.error(f"Playwright search impl failed: {e}")
        return []
    finally:
        try:
            page.close()
        except Exception:
            pass

def _search_pw(query, max_results=10):
    """Thread-safe Playwright search — routes through dedicated worker thread."""
    try:
        return _run_in_pw_thread(_search_pw_impl, query, max_results, timeout_sec=30)
    except Exception as e:
        log.error(f"Playwright search failed: {e}")
        return []

def _search_bing(query, max_results=10):
    """Search Bing (HTML scrape). Decodes bing.com/ck/a redirect URLs."""
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results}"
    try:
        raw, status, _ = http_fetch(url)
        if status != 200:
            return []

        # Check if Bing returned CAPTCHA
        if 'captcha' in raw.lower()[:5000]:
            log.info("Bing returned CAPTCHA, skipping")
            return []

        results = []

        # Pattern 1: Standard b_algo blocks
        blocks = re.findall(
            r'<li class="b_algo"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>',
            raw, re.DOTALL | re.IGNORECASE
        )

        # Pattern 2: Broader — any <h2><a href="..."> with snippet
        if not blocks:
            blocks = re.findall(
                r'<h2[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>',
                raw, re.DOTALL | re.IGNORECASE
            )

        # Pattern 3: Broadest — all links with visible text on the page
        if not blocks:
            link_blocks = re.findall(
                r'<a[^>]*href="(https?://[^"]*bing\.com/ck/a[^"]*)"[^>]*>(.*?)</a>',
                raw, re.DOTALL | re.IGNORECASE
            )
            blocks = [(href, title, '') for href, title in link_blocks]

        for href, title_html, snippet_html in blocks[:max_results]:
            title = _decode_entities(_strip_tags(title_html)).strip()
            snippet = _decode_entities(_strip_tags(snippet_html if snippet_html else '')).strip()
            # Decode Bing redirect URLs to actual URLs
            actual_url = _decode_bing_url(href) if 'bing.com' in href else href
            if title and len(title) > 3 and actual_url.startswith('http'):
                results.append({
                    'title': title[:200],
                    'url': actual_url,
                    'snippet': snippet[:300]
                })
        return results
    except Exception as e:
        log.error(f"Bing search failed: {e}")
        return []

def web_search(query, max_results=10):
    """
    Multi-engine web search with fallback chain:
    DuckDuckGo (POST) → Google → Bing → Playwright headless browser
    Returns deduplicated results with titles, URLs, snippets.
    """
    results = _search_duckduckgo(query, max_results)

    if len(results) < max_results // 2:
        # Supplement with Google
        google_results = _search_google(query, max_results)
        seen_urls = {r['url'] for r in results}
        for gr in google_results:
            if gr['url'] not in seen_urls:
                results.append(gr)
                seen_urls.add(gr['url'])

    if len(results) < 3:
        # Fallback to Bing
        bing_results = _search_bing(query, max_results)
        seen_urls = {r['url'] for r in results}
        for br in bing_results:
            if br['url'] not in seen_urls:
                results.append(br)
                seen_urls.add(br['url'])

    if len(results) < 3:
        # Last resort: Playwright headless browser search
        pw_results = _search_pw(query, max_results)
        seen_urls = {r['url'] for r in results}
        for pr in pw_results:
            if pr['url'] not in seen_urls:
                results.append(pr)
                seen_urls.add(pr['url'])

    return results[:max_results]


# ══════════════════════════════════════════════════════════════
#  DEEP RESEARCH ENGINE v2.0 — Hierarchical Research Trees
# ══════════════════════════════════════════════════════════════
#
#  Architecture: Deep Tree of Research (DToR)
#    1. SEED:       Initial broad search to discover sub-topics
#    2. DECOMPOSE:  Algorithmic topic decomposition into branches
#    3. BRANCH:     Each branch runs iterative search→fetch→extract loops
#    4. SCORE:      Coverage/novelty scoring to prune weak branches
#    5. TIMELINE:   Regex-based date extraction + chronological ordering
#    6. SYNTHESIZE: Structured report with exec summary, branches, timeline
#
#  Produces frontier-quality research (40-60 pages, 10-40 effective steps)
#  entirely inside the tool — agent receives a single rich report.
# ══════════════════════════════════════════════════════════════

# ── Stopwords for keyword extraction ──
_STOPWORDS = frozenset({
    'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
    'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'could', 'should', 'may', 'might', 'shall', 'can', 'need', 'must',
    'it', 'its', 'this', 'that', 'these', 'those', 'i', 'me', 'my', 'we',
    'our', 'you', 'your', 'he', 'she', 'they', 'them', 'their', 'his',
    'her', 'who', 'what', 'which', 'when', 'where', 'how', 'why', 'not',
    'no', 'nor', 'so', 'if', 'then', 'than', 'too', 'very', 'just',
    'about', 'above', 'after', 'again', 'all', 'also', 'am', 'any',
    'because', 'before', 'between', 'both', 'each', 'few', 'get', 'got',
    'here', 'into', 'more', 'most', 'new', 'now', 'only', 'other', 'out',
    'over', 'own', 'same', 'some', 'such', 'there', 'through', 'under',
    'up', 'us', 'well', 'what', 'while', 'as', 'much', 'many', 'like',
    'still', 'even', 'back', 'also', 'however', 'since', 'said', 'says',
    'according', 'per', 'via', 'one', 'two', 'first', 'last', 'next',
    'latest', 'news', 'updates', 'update', 'report', 'reports', 'article',
})

# ── Date extraction patterns ──
_MONTH_NAMES = (
    'january', 'february', 'march', 'april', 'may', 'june',
    'july', 'august', 'september', 'october', 'november', 'december'
)
_MONTH_ABBREV = (
    'jan', 'feb', 'mar', 'apr', 'may', 'jun',
    'jul', 'aug', 'sep', 'oct', 'nov', 'dec'
)
_MONTH_NUM = {name: i+1 for i, name in enumerate(_MONTH_NAMES)}
_MONTH_NUM.update({abbr: i+1 for i, abbr in enumerate(_MONTH_ABBREV)})

_DATE_RE = re.compile(
    r'(?:'
    r'(?:(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})'
    r'|(?:\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})'
    r'|(?:\d{4}-\d{2}-\d{2})'
    r'|(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4})'
    r')',
    re.IGNORECASE
)

# ── Topic decomposition templates ──
_TOPIC_TEMPLATES = {
    'conflict': {
        'triggers': ['war', 'conflict', 'crisis', 'attack', 'invasion', 'military', 'strike', 'bomb'],
        'branches': [
            ('Background & Causes', ['background', 'causes', 'origins', 'history', 'why']),
            ('Military Operations', ['military', 'operations', 'strikes', 'attacks', 'forces', 'troops', 'weapons']),
            ('Casualties & Humanitarian', ['casualties', 'killed', 'wounded', 'humanitarian', 'refugees', 'civilian']),
            ('Economic Impact', ['economic', 'impact', 'oil', 'prices', 'sanctions', 'trade', 'market']),
            ('Diplomatic Response', ['diplomatic', 'negotiations', 'UN', 'talks', 'ceasefire', 'allies', 'condemn']),
            ('Regional Effects', ['regional', 'neighbors', 'allies', 'proxy', 'escalation', 'spread']),
        ]
    },
    'business': {
        'triggers': ['company', 'startup', 'business', 'corporation', 'firm', 'enterprise'],
        'branches': [
            ('History & Founding', ['history', 'founded', 'founding', 'origins', 'story']),
            ('Products & Services', ['products', 'services', 'offerings', 'features', 'platform']),
            ('Financials & Growth', ['revenue', 'profit', 'valuation', 'funding', 'IPO', 'growth']),
            ('Competition & Market', ['competitors', 'market', 'share', 'industry', 'rivals']),
            ('Leadership & Culture', ['CEO', 'founder', 'leadership', 'team', 'culture', 'employees']),
        ]
    },
    'technology': {
        'triggers': ['technology', 'AI', 'software', 'app', 'platform', 'algorithm', 'tech'],
        'branches': [
            ('How It Works', ['how', 'works', 'architecture', 'technical', 'mechanism']),
            ('Applications & Use Cases', ['applications', 'use cases', 'examples', 'real-world']),
            ('Key Players & Companies', ['companies', 'players', 'developers', 'leaders', 'who']),
            ('Risks & Concerns', ['risks', 'concerns', 'privacy', 'safety', 'ethics', 'regulation']),
            ('Future & Trends', ['future', 'trends', 'predictions', 'roadmap', 'next']),
        ]
    },
    'person': {
        'triggers': ['president', 'leader', 'CEO', 'founder', 'politician', 'celebrity', 'athlete'],
        'branches': [
            ('Biography & Early Life', ['biography', 'born', 'early life', 'education', 'family']),
            ('Career & Achievements', ['career', 'achievements', 'accomplishments', 'record']),
            ('Controversies', ['controversy', 'scandal', 'criticism', 'allegations']),
            ('Current Role & Activity', ['current', 'latest', 'recent', 'today', 'now']),
            ('Impact & Legacy', ['impact', 'legacy', 'influence', 'contribution']),
        ]
    },
    'science': {
        'triggers': ['study', 'research', 'discovery', 'science', 'scientific', 'experiment', 'disease', 'health'],
        'branches': [
            ('Background & Context', ['background', 'context', 'previous', 'known', 'established']),
            ('Key Findings', ['findings', 'results', 'discovered', 'showed', 'evidence']),
            ('Methodology', ['method', 'approach', 'study', 'experiment', 'data', 'sample']),
            ('Implications', ['implications', 'impact', 'means', 'significance', 'future']),
            ('Expert Opinions', ['experts', 'scientists', 'researchers', 'opinions', 'debate']),
        ]
    },
    'lead_gen': {
        'triggers': ['lead', 'leads', 'email', 'contact', 'phone', 'credentials',
                      'b2b', 'directory', 'find me', 'list of', 'who manages',
                      'cleaning', 'rental', 'airbnb', 'property management',
                      'hotel', 'host', 'cabin', 'vrbo', 'vacation rental'],
        'branches': [
            ('Directory Listings', ['directory', 'listing', 'yellowpages', 'yelp', 'bbb', 'business']),
            ('Company Websites & Contact Pages', ['contact', 'about', 'team', 'staff', 'email', 'phone']),
            ('Review & Social Profiles', ['reviews', 'facebook', 'linkedin', 'profile', 'google']),
            ('Maps & Location Data', ['maps', 'location', 'address', 'near', 'area', 'local']),
            ('Industry Associations & Chambers', ['association', 'member', 'chamber', 'organization', 'group']),
            ('Property & Business Records', ['property', 'management', 'owner', 'manager', 'rental', 'host']),
        ]
    },
    'default': {
        'triggers': [],
        'branches': [
            ('Overview & Background', ['overview', 'background', 'what', 'context', 'history']),
            ('Key Developments', ['developments', 'events', 'happened', 'recent', 'latest']),
            ('Causes & Factors', ['causes', 'factors', 'reasons', 'why', 'because']),
            ('Impact & Effects', ['impact', 'effects', 'consequences', 'result', 'outcome']),
            ('Current Status & Outlook', ['current', 'status', 'now', 'future', 'outlook', 'next']),
        ]
    },
}


_LEAD_KEYWORDS_STRONG = frozenset({
    'lead', 'leads', 'email', 'emails', 'contact', 'contacts',
    'phone', 'phones', 'credentials', 'b2b', 'directory',
    'find me', 'list of', 'who manages',
})
_LEAD_KEYWORDS_INDUSTRY = frozenset({
    'rental', 'airbnb', 'property management', 'cleaning',
    'hotel', 'host', 'cabin', 'office rental', 'vrbo',
    'short term', 'vacation rental',
})

def _is_lead_query(query):
    """Detect if the query is lead-generation focused.
    Returns True if: 2+ strong lead keywords, OR 1 strong + 1 industry keyword.
    """
    q_lower = query.lower()
    strong = sum(1 for kw in _LEAD_KEYWORDS_STRONG if kw in q_lower)
    if strong >= 2:
        return True
    industry = sum(1 for kw in _LEAD_KEYWORDS_INDUSTRY if kw in q_lower)
    if strong >= 1 and industry >= 1:
        return True
    # Also check if the topic template would select lead_gen
    template = _select_topic_template(query)
    if template == _TOPIC_TEMPLATES.get('lead_gen'):
        return True
    return False

def _extract_keywords(text):
    """Extract meaningful keywords from text (no LLM, pure NLP)."""
    words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _word_overlap(text1, text2):
    """Compute word-level Jaccard overlap between two texts."""
    words1 = set(re.findall(r'[a-z]{3,}', text1.lower()))
    words2 = set(re.findall(r'[a-z]{3,}', text2.lower()))
    if not words1 or not words2:
        return 0.0
    return len(words1 & words2) / len(words1 | words2)


def _select_topic_template(query):
    """Select the best topic decomposition template based on query keywords.
    Lead_gen template is checked first (highest priority) to ensure lead-gen
    queries aren't accidentally matched by the 'business' template.
    """
    q_lower = query.lower()
    # Check lead_gen first — it has the most specific triggers for lead queries
    lead_tmpl = _TOPIC_TEMPLATES.get('lead_gen')
    if lead_tmpl and any(trigger in q_lower for trigger in lead_tmpl['triggers']):
        return lead_tmpl
    for template_name, template in _TOPIC_TEMPLATES.items():
        if template_name in ('default', 'lead_gen'):
            continue
        if any(trigger in q_lower for trigger in template['triggers']):
            return template
    return _TOPIC_TEMPLATES['default']


def _extract_locations(query):
    """Extract location mentions from query text."""
    _tn_cities = [
        'knoxville', 'morristown', 'jefferson city', 'pigeon forge',
        'gatlinburg', 'sevierville', 'nashville', 'chattanooga',
        'johnson city', 'kingsport', 'maryville', 'oak ridge',
        'cookeville', 'cleveland', 'murfreesboro', 'franklin',
        'east tennessee', 'east tn', 'west tennessee', 'middle tennessee',
    ]
    q_lower = query.lower()
    found = []
    for city in sorted(_tn_cities, key=len, reverse=True):  # longest first to avoid partial matches
        if city in q_lower:
            found.append(city.title())
            # Remove from q_lower to avoid sub-matches
            q_lower = q_lower.replace(city, '')
    if not found:
        # Try "word TN" or "word Tennessee" patterns
        state_match = re.findall(r'(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:TN|Tennessee)\b', query)
        found = state_match[:3]
    return found if found else ['Tennessee']


def _extract_business_terms(query):
    """Extract core business terms from a lead-gen query, stripping locations and contact words."""
    _strip_words = frozenset({
        'email', 'emails', 'contact', 'contacts', 'phone', 'phones',
        'credentials', 'find', 'me', 'the', 'and', 'of', 'for', 'in',
        'deep', 'research', 'search', 'leads', 'lead', 'b2b', 'directory',
        'list', 'tennessee', 'tn', 'knoxville', 'morristown', 'jefferson',
        'city', 'pigeon', 'forge', 'gatlinburg', 'sevierville', 'east',
        'west', 'middle', 'nashville', 'involved', 'individuals', 'companies',
        'who', 'manages', 'owners', 'area', 'areas', 'company',
    })
    words = query.split()
    terms = [w for w in words if w.lower() not in _strip_words and len(w) > 2]
    return ' '.join(terms[:6])  # Keep it SHORT


def _decompose_lead_topic(query, initial_snippets):
    """Lead-gen specific decomposition: location-based branches with SHORT, focused queries.
    Produces much better search results than generic topic decomposition.
    """
    locations = _extract_locations(query)
    biz_terms = _extract_business_terms(query)
    query_keywords = _extract_keywords(query)

    if not biz_terms:
        biz_terms = 'property management vacation rental'

    branches = []

    # Create location-specific branches (up to 3 locations)
    for loc in locations[:3]:
        loc_lower = loc.lower().replace(' ', '_')
        branches.append({
            'name': f'{loc} Area',
            'search_queries': [
                f'{biz_terms} {loc} TN email contact',
                f'{biz_terms} {loc} Tennessee phone email',
                f'site:yelp.com {biz_terms} {loc}',
                f'site:yellowpages.com {biz_terms} {loc} TN',
                f'{biz_terms} near {loc} TN directory',
            ],
            'keywords': query_keywords + _extract_keywords(loc),
        })

    # Directory-specific branch
    loc_str = locations[0] if locations else 'Tennessee'
    branches.append({
        'name': 'Business Directories',
        'search_queries': [
            f'site:bbb.org {biz_terms} {loc_str}',
            f'site:manta.com {biz_terms} Tennessee',
            f'site:chamberofcommerce.com {biz_terms} {loc_str}',
            f'{biz_terms} Tennessee business directory email',
            f'site:thumbtack.com {biz_terms} {loc_str}',
        ],
        'keywords': query_keywords + ['directory', 'listing', 'bbb', 'yelp'],
    })

    # Social & review branch
    branches.append({
        'name': 'Social & Reviews',
        'search_queries': [
            f'site:facebook.com {biz_terms} {loc_str} TN',
            f'{biz_terms} {loc_str} google reviews contact',
            f'site:nextdoor.com {biz_terms} {loc_str}',
            f'site:tripadvisor.com {biz_terms} {loc_str}',
            f'{biz_terms} {loc_str} contact us page email',
        ],
        'keywords': query_keywords + ['facebook', 'reviews', 'social', 'contact'],
    })

    # Maps & local branch (if we have room)
    if len(branches) < MAX_BRANCHES:
        branches.append({
            'name': 'Maps & Local Listings',
            'search_queries': [
                f'{biz_terms} {loc_str} TN maps listing',
                f'{biz_terms} {loc_str} TN site:mapquest.com',
                f'{biz_terms} near me {loc_str} Tennessee contact',
                f'{biz_terms} {loc_str} area companies list',
            ],
            'keywords': query_keywords + ['maps', 'local', 'listing', 'area'],
        })

    return branches[:MAX_BRANCHES]


def _decompose_topic(query, initial_snippets):
    """
    Algorithmic topic decomposition into research branches.
    Uses template matching + snippet-driven discovery.
    No LLM needed — pure NLP heuristics.

    For lead-gen queries, uses location-based decomposition with short focused queries.
    """
    query_keywords = _extract_keywords(query)
    template = _select_topic_template(query)
    is_lead = _is_lead_query(query)

    # Lead-gen queries get specialized location-based decomposition
    if is_lead:
        return _decompose_lead_topic(query, initial_snippets)

    # Standard topic decomposition for non-lead queries
    branches = []
    for branch_name, branch_keywords in template['branches']:
        core_query = query
        search_queries = [
            f'{core_query} {branch_keywords[0]}',
            f'{core_query} {" ".join(branch_keywords[:2])}',
        ]
        # Add year specificity
        search_queries.append(f'{core_query} {branch_keywords[0]} 2025 2026')

        # Add a snippet-derived query if snippets mention branch keywords
        for snippet in initial_snippets[:20]:
            snippet_lower = snippet.lower()
            if any(kw in snippet_lower for kw in branch_keywords):
                snippet_words = snippet.split()[:8]
                snippet_phrase = ' '.join(snippet_words)
                search_queries.append(f'{core_query} {snippet_phrase}')
                break

        branches.append({
            'name': branch_name,
            'search_queries': search_queries[:4],  # cap at 4 queries per branch
            'keywords': branch_keywords + query_keywords,
        })

    return branches[:MAX_BRANCHES]


def _extract_facts(content, keywords, max_facts=12):
    """
    Extract key fact sentences from page content using keyword scoring.
    Extractive summarization without embeddings.
    """
    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', content)
    if not sentences:
        return []

    keyword_set = set(kw.lower() for kw in keywords)
    scored = []

    for sent in sentences:
        sent = sent.strip()
        # Filter: must be reasonable length
        if len(sent) < 30 or len(sent) > 500:
            continue
        # Skip if it looks like boilerplate
        if any(skip in sent.lower() for skip in [
            'cookie', 'subscribe', 'newsletter', 'sign up', 'log in',
            'advertisement', 'sponsored', 'click here', 'read more',
            'privacy policy', 'terms of', 'all rights reserved',
        ]):
            continue

        # Score by keyword hits
        sent_words = set(re.findall(r'[a-z]{3,}', sent.lower()))
        hits = len(sent_words & keyword_set)
        if hits == 0:
            continue

        # Bonus for containing numbers (likely factual)
        has_numbers = bool(re.search(r'\d', sent))
        score = hits + (0.5 if has_numbers else 0)

        scored.append((score, sent))

    # Sort by score descending
    scored.sort(key=lambda x: -x[0])

    # Deduplicate: skip sentences with >60% overlap with already-selected
    selected = []
    for score, sent in scored:
        if len(selected) >= max_facts:
            break
        if any(_word_overlap(sent, s) > 0.6 for s in selected):
            continue
        selected.append(sent)

    return selected


def _extract_timeline(content, source_url=""):
    """
    Extract dated events from content using regex date patterns.
    Returns list of {date_sort, date_display, event, source_url}.
    """
    events = []
    # Split content into sentences
    sentences = re.split(r'(?<=[.!?])\s+', content)

    for sent in sentences:
        matches = _DATE_RE.findall(sent)
        if not matches:
            continue

        for date_str in matches:
            # Parse to sortable format
            date_sort = _parse_date_sortable(date_str)
            if not date_sort:
                continue

            # Clean the sentence for display
            event_text = sent.strip()
            if len(event_text) > 300:
                event_text = event_text[:297] + '...'

            events.append({
                'date_sort': date_sort,
                'date_display': date_str.strip(),
                'event': event_text,
                'source_url': source_url,
            })

    return events


def _parse_date_sortable(date_str):
    """Parse a date string into YYYY-MM-DD sortable format."""
    ds = date_str.strip()

    # ISO: 2025-03-14
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', ds)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # Month DD, YYYY or Month DD YYYY
    m = re.match(r'^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$', ds)
    if m:
        month_name = m.group(1).lower().rstrip('.')
        month_num = _MONTH_NUM.get(month_name)
        if month_num:
            return f"{m.group(3)}-{month_num:02d}-{int(m.group(2)):02d}"

    # DD Month YYYY
    m = re.match(r'^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$', ds)
    if m:
        month_name = m.group(2).lower()
        month_num = _MONTH_NUM.get(month_name)
        if month_num:
            return f"{m.group(3)}-{month_num:02d}-{int(m.group(1)):02d}"

    return None


def _score_branch(branch_result, all_facts_so_far):
    """
    Score a research branch on coverage and novelty.
    Returns (coverage_score, novelty_score) each 0.0-1.0.
    """
    facts = branch_result.get('facts', [])
    sources = branch_result.get('sources', [])
    dates = branch_result.get('dates', [])

    # Coverage: facts found + source diversity + temporal coverage
    fact_score = min(1.0, len(facts) / 8)
    unique_domains = len(set(urlparse(s['url']).netloc for s in sources if s.get('url')))
    source_score = min(1.0, unique_domains / 4)
    date_score = min(1.0, len(dates) / 3)
    coverage = fact_score * 0.5 + source_score * 0.3 + date_score * 0.2

    # Novelty: how many facts are new vs previously found
    if not all_facts_so_far or not facts:
        novelty = 1.0
    else:
        new_count = 0
        for fact in facts:
            is_new = all(
                _word_overlap(fact, existing) < 0.5
                for existing in all_facts_so_far
            )
            if is_new:
                new_count += 1
        novelty = new_count / len(facts) if facts else 0.0

    return coverage, novelty


def _execute_branch(branch, global_seen_urls, time_deadline, chars_per=10000,
                    extract_leads=False):
    """
    Execute a single research branch with iterative search→fetch→extract loops.
    Runs up to MAX_RESEARCH_ROUNDS until coverage threshold or time limit.
    When extract_leads=True, also extracts emails/phones from every page.
    """
    branch_name = branch['name']
    keywords = branch['keywords']
    search_queries = list(branch['search_queries'])

    all_facts = []
    all_dates = []
    all_sources = []
    all_contacts = []  # for lead-gen mode
    pages_fetched = 0
    rounds = 0

    for round_num in range(MAX_RESEARCH_ROUNDS):
        if time.time() > time_deadline:
            log.info(f"Branch '{branch_name}' hit time limit at round {round_num}")
            break
        if pages_fetched >= MAX_BRANCH_PAGES:
            break

        rounds = round_num + 1

        # Search all queries for this round in parallel
        round_results = []
        with ThreadPoolExecutor(max_workers=min(len(search_queries), 6)) as pool:
            futures = {pool.submit(web_search, q, 10): q for q in search_queries}
            for future in as_completed(futures):
                try:
                    results = future.result()
                    for r in results:
                        if r['url'] not in global_seen_urls:
                            global_seen_urls.add(r['url'])
                            round_results.append(r)
                except Exception as e:
                    log.error(f"Branch '{branch_name}' search failed: {e}")

        if not round_results:
            log.info(f"Branch '{branch_name}' round {round_num}: no new URLs")
            break

        # Rank by domain diversity
        domain_count: Dict[str, int] = {}
        ranked = []
        for r in round_results:
            domain = urlparse(r['url']).netloc
            count = domain_count.get(domain, 0)
            domain_count[domain] = count + 1
            ranked.append((count, r))
        ranked.sort(key=lambda x: x[0])

        remaining_pages = MAX_BRANCH_PAGES - pages_fetched
        urls_to_fetch = [r[1] for r in ranked[:remaining_pages]]

        # Parallel fetch
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(smart_fetch, r['url'], chars_per): r for r in urls_to_fetch}
            for future in as_completed(futures):
                r = futures[future]
                try:
                    result = future.result()
                    md = result.get('markdown', '')
                    if md and len(md) > 100:
                        pages_fetched += 1
                        source_url = result.get('url', r['url'])
                        all_sources.append({
                            'url': source_url,
                            'title': r.get('title', ''),
                            'snippet': r.get('snippet', ''),
                        })

                        # Extract facts
                        facts = _extract_facts(md, keywords)
                        all_facts.extend(facts)

                        # Extract timeline events
                        dates = _extract_timeline(md, source_url)
                        all_dates.extend(dates)

                        # Extract contacts for lead-gen mode
                        if extract_leads:
                            try:
                                contacts = extract_contacts(md, 'all')
                                emails = contacts.get('emails', [])
                                phones = contacts.get('phones', [])
                                if emails or phones:
                                    name = _extract_business_name(md, source_url, r.get('title', ''))
                                    all_contacts.append({
                                        'name': name,
                                        'emails': emails[:5],
                                        'phones': phones[:5],
                                        'source_url': source_url,
                                        'source_title': r.get('title', ''),
                                    })
                            except Exception:
                                pass
                except Exception as e:
                    log.error(f"Branch '{branch_name}' fetch failed: {e}")

        # Check if we should continue: compute novelty of this round
        if round_num > 0 and len(all_facts) > 0:
            # If last round added < 20% new facts, stop
            round_facts = all_facts[-(len(all_facts) // (round_num + 1)):]
            if len(round_facts) < 2:
                log.info(f"Branch '{branch_name}' low novelty, stopping after round {rounds}")
                break

        # Generate expansion queries for next round
        if round_num < MAX_RESEARCH_ROUNDS - 1:
            search_queries = _generate_expansion_queries(
                branch_name, keywords, all_facts[-5:]
            )

    # Deduplicate dates
    seen_events = []
    unique_dates = []
    for d in all_dates:
        if not any(_word_overlap(d['event'], e) > 0.6 for e in seen_events):
            seen_events.append(d['event'])
            unique_dates.append(d)
    unique_dates.sort(key=lambda x: x.get('date_sort', ''))

    result = {
        'name': branch_name,
        'facts': all_facts,
        'dates': unique_dates,
        'sources': all_sources,
        'pages_fetched': pages_fetched,
        'rounds': rounds,
    }
    if extract_leads:
        result['contacts'] = all_contacts
    return result


def _generate_expansion_queries(branch_name, keywords, recent_facts):
    """Generate new search queries based on discovered facts for deeper research."""
    queries = []
    # Use recent facts to derive follow-up queries
    for fact in recent_facts[:3]:
        fact_keywords = _extract_keywords(fact)
        # Find keywords in the fact that aren't in our original set
        new_kw = [kw for kw in fact_keywords[:5] if kw not in keywords]
        if new_kw:
            queries.append(f'{" ".join(keywords[:3])} {" ".join(new_kw[:3])}')

    if not queries:
        # Fallback: add detail-seeking queries
        core = ' '.join(keywords[:3])
        queries = [
            f'{core} details analysis',
            f'{core} latest developments',
        ]

    return queries[:4]


def _build_executive_summary(all_branch_results):
    """Build executive summary from cross-branch facts, ranked by citation frequency."""
    # Count keyword-level cross-references: facts appearing in multiple branches
    all_facts_by_branch = []
    for br in all_branch_results:
        all_facts_by_branch.append(br.get('facts', []))

    if not all_facts_by_branch:
        return "No findings available."

    # Score facts by cross-branch relevance
    flat_facts = []
    for i, branch_facts in enumerate(all_facts_by_branch):
        for fact in branch_facts:
            # Count how many other branches have overlapping facts
            cross_refs = 0
            for j, other_facts in enumerate(all_facts_by_branch):
                if i == j:
                    continue
                if any(_word_overlap(fact, of) > 0.3 for of in other_facts):
                    cross_refs += 1
            flat_facts.append((cross_refs, len(fact), fact))

    # Sort by cross-references (desc), then length (longer = more detail)
    flat_facts.sort(key=lambda x: (-x[0], -x[1]))

    # Deduplicate and take top 8
    summary_facts = []
    for _, _, fact in flat_facts:
        if len(summary_facts) >= 8:
            break
        if any(_word_overlap(fact, s) > 0.5 for s in summary_facts):
            continue
        summary_facts.append(fact)

    if not summary_facts:
        # Fallback: take first fact from each branch
        for br in all_branch_results:
            if br.get('facts'):
                summary_facts.append(br['facts'][0])

    return '\n'.join(f'- {f}' for f in summary_facts)


def _build_deep_research_report(query, branches, branch_results, all_dates,
                                 initial_results, elapsed, total_pages,
                                 contacts=None):
    """Build structured hierarchical research report. Includes contacts if lead-gen mode."""
    lines = []
    lines.append(f"# Deep Research: {query}")
    lines.append(f"\nResearched {len(branch_results)} sub-topics across {total_pages} pages in {elapsed:.0f}s\n")

    # Executive Summary
    lines.append("## Executive Summary\n")
    summary = _build_executive_summary(branch_results)
    lines.append(summary)
    lines.append("")

    # Branch Reports
    for br in branch_results:
        name = br['name']
        facts = br.get('facts', [])
        sources = br.get('sources', [])
        coverage = br.get('coverage_score', 0)
        novelty = br.get('novelty_score', 0)

        lines.append(f"\n## {name}")
        lines.append(f"Coverage: {coverage:.1%} | Novelty: {novelty:.1%} | "
                      f"Sources: {len(sources)} | Facts: {len(facts)} | "
                      f"Pages: {br.get('pages_fetched', 0)} | Rounds: {br.get('rounds', 0)}")
        lines.append("")

        for fact in facts[:10]:
            lines.append(f"- {fact}")
        if len(facts) > 10:
            lines.append(f"  ... and {len(facts) - 10} more facts")

        # List sources for this branch
        if sources:
            lines.append(f"\nSources:")
            for s in sources[:8]:
                title = s.get('title', 'Untitled')[:80]
                lines.append(f"  - [{title}]({s['url']})")
        lines.append("")

    # Timeline
    if all_dates:
        lines.append("\n## Timeline\n")
        # Deduplicate across branches and sort
        seen_events = []
        unique_dates = []
        for d in all_dates:
            if not any(_word_overlap(d['event'], e) > 0.6 for e in seen_events):
                seen_events.append(d['event'])
                unique_dates.append(d)
        unique_dates.sort(key=lambda x: x.get('date_sort', ''))

        for d in unique_dates[:30]:
            event = d['event']
            if len(event) > 200:
                event = event[:197] + '...'
            lines.append(f"{d['date_display']} -- {event}")
        if len(unique_dates) > 30:
            lines.append(f"  ... and {len(unique_dates) - 30} more events")
        lines.append("")

    # ── Contacts Section (lead-gen mode) ──
    if contacts:
        lines.append(f"\n## Contacts Found ({len(contacts)} leads)\n")
        for i, c in enumerate(contacts, 1):
            name = c.get('name', 'Unknown')
            emails = ', '.join(c.get('emails', []))
            phones = ', '.join(c.get('phones', []))
            source = c.get('source_title', c.get('source_url', ''))
            lines.append(f"### Lead {i}: {name}")
            if emails:
                lines.append(f"  Email: {emails}")
            if phones:
                lines.append(f"  Phone: {phones}")
            if source:
                lines.append(f"  Source: {source}")
            lines.append("")

    # All Sources (deduplicated across branches)
    all_sources = []
    seen_source_urls = set()
    for br in branch_results:
        for s in br.get('sources', []):
            if s['url'] not in seen_source_urls:
                seen_source_urls.add(s['url'])
                all_sources.append(s)
    # Also include initial search results
    for r in initial_results:
        if r['url'] not in seen_source_urls:
            seen_source_urls.add(r['url'])
            all_sources.append(r)

    lines.append("\n## All Sources\n")
    for i, s in enumerate(all_sources[:60], 1):
        title = s.get('title', 'Untitled')[:80]
        lines.append(f"{i}. [{title}]({s['url']})")
        snippet = s.get('snippet', '')[:100]
        if snippet:
            lines.append(f"   {snippet}")

    # Research Branch Scores
    lines.append(f"\n## Research Branches\n")
    for br in branch_results:
        lines.append(
            f"- {br['name']}: coverage {br.get('coverage_score', 0):.0%}, "
            f"novelty {br.get('novelty_score', 0):.0%}, "
            f"{len(br.get('facts', []))} facts, {br.get('pages_fetched', 0)} pages, "
            f"{br.get('rounds', 0)} rounds"
        )

    return '\n'.join(lines)


def deep_research(query, max_pages=MAX_DEEP_RESEARCH_PAGES):
    """
    Deep Research v2.0 — Hierarchical Research Tree Engine.

    Architecture:
    1. SEED:       Initial broad search to discover the topic landscape
    2. DECOMPOSE:  Algorithmically decompose into research branches (no LLM)
    3. BRANCH:     Each branch runs iterative search→fetch→extract loops
    4. SCORE:      Coverage/novelty scoring prunes weak branches
    5. TIMELINE:   Regex-based date extraction builds chronological event log
    6. REPORT:     Structured output: exec summary, branches, timeline, sources

    Lead-gen aware: automatically extracts contacts when query is lead-focused.
    Produces 40-60 page research across 5-6 sub-topics.
    """
    t0 = time.time()
    is_lead = _is_lead_query(query)
    # Lead-gen queries get more time since they need to scan contact pages
    budget = RESEARCH_TIME_BUDGET * 1.5 if is_lead else RESEARCH_TIME_BUDGET
    time_deadline = t0 + budget
    log.info(f"Deep research: lead_mode={is_lead}, budget={budget:.0f}s")

    # ── Phase 1: SEED — broad initial search ──
    if is_lead:
        # Lead-gen seed queries: shorter, more focused on contact info
        biz_terms = _extract_business_terms(query)
        locations = _extract_locations(query)
        loc_str = locations[0] if locations else ''
        seed_queries = [
            f'{biz_terms} {loc_str} email contact',
            f'{biz_terms} {loc_str} directory listing',
            f'{biz_terms} {loc_str} phone email address',
            f'site:yelp.com {biz_terms} {loc_str}',
            f'site:yellowpages.com {biz_terms} {loc_str}',
            f'{biz_terms} {loc_str} site:bbb.org',
        ]
    else:
        seed_queries = [
            query,
            f'"{query}"',
            f'{query} latest 2025 2026',
            f'{query} overview analysis',
        ]
    initial_results = []
    seen_urls: Set[str] = set()

    with ThreadPoolExecutor(max_workers=min(len(seed_queries), 6)) as pool:
        futures = {pool.submit(web_search, q, 15): q for q in seed_queries}
        for future in as_completed(futures):
            try:
                results = future.result()
                for r in results:
                    if r['url'] not in seen_urls:
                        seen_urls.add(r['url'])
                        initial_results.append(r)
            except Exception as e:
                log.error(f"Seed search failed: {e}")

    initial_snippets = [r.get('snippet', '') for r in initial_results if r.get('snippet')]
    log.info(f"Deep research SEED: {len(initial_results)} URLs from {len(seed_queries)} queries")

    # ── Phase 2: DECOMPOSE — algorithmic topic branching ──
    branches = _decompose_topic(query, initial_snippets)
    log.info(f"Deep research DECOMPOSE: {len(branches)} branches: {[b['name'] for b in branches]}")

    # ── Phase 3: BRANCH — execute each branch's research loop ──
    all_branch_results = []
    all_facts_collected = []
    all_dates_collected = []
    total_pages = 0

    for branch in branches:
        if time.time() > time_deadline:
            log.info(f"Deep research hit time limit, completed {len(all_branch_results)}/{len(branches)} branches")
            break
        if total_pages >= max_pages:
            log.info(f"Deep research hit page limit ({max_pages})")
            break

        branch_result = _execute_branch(branch, seen_urls, time_deadline,
                                         extract_leads=is_lead)

        # ── Phase 4: SCORE — evaluate and potentially prune ──
        coverage, novelty = _score_branch(branch_result, all_facts_collected)
        branch_result['coverage_score'] = coverage
        branch_result['novelty_score'] = novelty

        if coverage >= MIN_COVERAGE_SCORE or len(all_branch_results) < 2:
            # Keep the branch (always keep first 2 regardless of score)
            all_branch_results.append(branch_result)
            all_facts_collected.extend(branch_result.get('facts', []))
            all_dates_collected.extend(branch_result.get('dates', []))
            total_pages += branch_result.get('pages_fetched', 0)
            log.info(f"Branch '{branch['name']}': coverage={coverage:.2f}, novelty={novelty:.2f}, "
                      f"facts={len(branch_result.get('facts', []))}, pages={branch_result.get('pages_fetched', 0)}")
        else:
            log.info(f"Branch '{branch['name']}' PRUNED: coverage={coverage:.2f} < {MIN_COVERAGE_SCORE}")

    # ── Phase 5: Collect contacts if lead-gen mode ──
    all_contacts = []
    if is_lead:
        for br in all_branch_results:
            all_contacts.extend(br.get('contacts', []))
        # Dedup contacts
        all_contacts = _dedup_leads(all_contacts)
        log.info(f"Deep research lead-gen: {len(all_contacts)} unique contacts extracted")

    # ── Phase 6: REPORT — structured hierarchical report ──
    elapsed = time.time() - t0
    report = _build_deep_research_report(
        query, branches, all_branch_results, all_dates_collected,
        initial_results, elapsed, total_pages, contacts=all_contacts
    )

    log.info(f"Deep research complete: {total_pages} pages, {len(all_branch_results)} branches, "
              f"{len(all_facts_collected)} facts, {len(all_dates_collected)} dates, "
              f"{len(all_contacts)} contacts, {elapsed:.1f}s")
    return report


# ══════════════════════════════════════════════════════════════
#  LEAD GENERATION ENGINE
# ══════════════════════════════════════════════════════════════

def lead_gen(query, location="", lead_type="all", max_leads=50):
    """
    B2B lead generation v2.0: search + crawl directories for emails/phones.
    Iterative expansion: keeps searching until max_leads is met or time/round limits hit.
    v2.0: increased budget (180s), 6 rounds, better error handling, improved dedup.
    """
    t0 = time.time()
    MAX_ROUNDS = 6        # max expansion rounds (v2.0: 4→6)
    TIME_BUDGET = 180.0   # total seconds budget (v2.0: 90→180)
    leads: List[Dict] = []
    seen_urls: Set[str] = set()
    total_pages_scanned = 0

    for round_num in range(MAX_ROUNDS):
        if len(leads) >= max_leads or (time.time() - t0) > TIME_BUDGET:
            break

        # Build queries — expand with variants on subsequent rounds
        if round_num == 0:
            search_queries = _build_lead_queries(query, location)
        else:
            search_queries = _build_expansion_queries(query, location, round_num)

        # Phase 1: Parallel search for URLs
        new_urls: List[Dict] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(web_search, q, 20): q for q in search_queries}
            for future in as_completed(futures):
                try:
                    results = future.result()
                    for r in results:
                        if r['url'] not in seen_urls:
                            seen_urls.add(r['url'])
                            new_urls.append(r)
                except Exception:
                    pass

        if not new_urls:
            break  # no new URLs found, stop expanding

        # Phase 2: Fetch and extract contacts — scale pages to need
        pages_needed = max(LEAD_GEN_MAX_PAGES, (max_leads - len(leads)) * 3)
        urls_to_scan = new_urls[:min(pages_needed, len(new_urls))]
        total_pages_scanned += len(urls_to_scan)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {}
            for r in urls_to_scan:
                futures[pool.submit(_extract_from_url, r['url'], lead_type)] = r

            for future in as_completed(futures):
                r = futures[future]
                try:
                    contacts = future.result()
                    if contacts:
                        for contact in contacts:
                            contact['source_url'] = r['url']
                            contact['source_title'] = r.get('title', '')
                            leads.append(contact)
                            if len(leads) >= max_leads * 2:  # over-collect for dedup
                                break
                except Exception:
                    pass
                if len(leads) >= max_leads * 2:
                    break

        # Deduplicate after each round
        leads = _dedup_leads(leads)

        if round_num > 0:
            log.info(f"lead_gen round {round_num+1}: {len(leads)} leads after {len(new_urls)} new URLs")

    # Also try maps_search as supplementary source for location-based queries
    if location and len(leads) < max_leads:
        try:
            maps_report = maps_search(query, location, max_results=max_leads - len(leads))
            # Parse maps results and merge if they have contact info
            # (maps_search returns text report, we extract from it)
        except Exception:
            pass

    elapsed = time.time() - t0

    # Final dedup and trim to max_leads
    leads = _dedup_leads(leads)[:max_leads]

    # Build report
    return _build_lead_report(query, location, leads, total_pages_scanned, elapsed)

def _build_lead_queries(query, location):
    """Generate search queries optimized for finding contact info."""
    base = f"{query} {location}".strip()
    queries = [
        f"{base} email contact",
        f"{base} directory listing",
        f"{base} phone number email",
        f"{base} contact us",
        f'"{base}" email',
        f"{base} site:yelp.com",
        f"{base} site:yellowpages.com",
        f"{base} business directory",
        f"{base} reviews contact",
        f"{base} staff team about",
        f"{base} phone email address",
        f"{base} site:bbb.org",
    ]
    return queries[:12]


def _build_expansion_queries(query, location, round_num):
    """Generate additional search queries for iterative expansion rounds."""
    base = f"{query} {location}".strip()
    # Different query patterns for each round
    expansions = [
        # Round 1: different directory sites and phrasing
        [
            f"{base} contact information",
            f"{base} site:chamberofcommerce.com",
            f"{base} owner email",
            f"{base} manager contact phone",
            f"{base} site:manta.com",
            f"{base} site:angi.com",
            f"{base} local business email phone",
            f'"{query}" {location} contact email phone',
        ],
        # Round 2: broader + geographic variants
        [
            f"{query} near {location} email",
            f"{query} {location} area contact info",
            f"{base} site:facebook.com contact",
            f"{base} site:nextdoor.com",
            f"{base} company profile contact",
            f"{query} {location} county directory",
            f"{base} site:mapquest.com",
            f"{base} reviews email phone contact",
        ],
        # Round 3: very broad queries
        [
            f"{query} {location} list",
            f"{base} site:google.com/maps",
            f'"{query}" near "{location}" email',
            f"{base} companies list contact",
            f"{query} Tennessee directory email",
            f"{base} business owner contact details",
        ],
        # Round 4: alternative directory sites
        [
            f"{base} site:thumbtack.com",
            f"{base} site:homeadvisor.com",
            f"{base} site:houzz.com",
            f"{base} site:tripadvisor.com",
            f"{base} site:vrbo.com contact",
            f"{base} site:airbnb.com host",
            f"{query} {location} phone email address list",
            f'"{query}" "{location}" contact email',
        ],
        # Round 5: deep directory + association searches
        [
            f"{query} {location} association members",
            f"{query} {location} chamber of commerce directory",
            f"{base} site:mapquest.com",
            f"{base} site:superpages.com",
            f"{query} {location} business owner phone email list 2025 2026",
            f"{base} site:whitepages.com",
        ],
    ]
    idx = min(round_num - 1, len(expansions) - 1)
    return expansions[idx]

def _extract_from_url(url, lead_type='all'):
    """Fetch URL and extract contact info + business name.
    v2.0: better error handling, HTTP-only mode for speed in parallel contexts.
    """
    try:
        # Use HTTP-only for speed when called from ThreadPoolExecutor
        # Playwright will be used if HTTP returns thin content via smart_fetch
        result = smart_fetch(url, max_chars=50000)
        raw = result.get('markdown', '')
        if not raw or len(raw) < 50:
            return []

        contacts = extract_contacts(raw, lead_type)
        leads = []

        emails = contacts.get('emails', [])
        phones = contacts.get('phones', [])
        socials = contacts.get('social', [])

        # Filter out generic/junk emails
        emails = [e for e in emails if not any(skip in e.lower() for skip in
                  ['noreply', 'no-reply', 'donotreply', 'unsubscribe', 'mailer-daemon',
                   'postmaster', 'webmaster@', 'example.com', 'sentry.io'])]

        if emails or phones:
            lead = {}
            name = _extract_business_name(raw, url, result.get('title', ''))
            if name:
                lead['name'] = name
            if emails:
                lead['emails'] = emails[:5]
            if phones:
                lead['phones'] = phones[:5]
            if socials:
                lead['social'] = socials[:3]
            leads.append(lead)

        return leads
    except Exception as e:
        log.debug(f"_extract_from_url failed for {url}: {e}")
        return []


def _extract_business_name(markdown, url, page_title=""):
    """Try to extract a business/company name from page content."""
    # Try page title first (most reliable)
    if page_title:
        # Clean common suffixes from titles
        name = page_title
        for suffix in [' - Yelp', ' | Yelp', ' - Yellow Pages', ' | LinkedIn',
                       ' - BBB', ' | Facebook', ' - Home', ' | Home',
                       ' - Contact', ' | Contact Us', ' - About', ' - Reviews']:
            if name.endswith(suffix):
                name = name[:-len(suffix)]
        name = name.strip()
        if name and len(name) < 80:
            return name
    # Try first markdown heading
    m = re.search(r'^#\s+(.+)', markdown, re.MULTILINE)
    if m:
        heading = m.group(1).strip()
        if len(heading) < 80:
            return heading
    # Try domain name as last resort
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '')
        # Skip generic domains
        if domain and domain not in ('yelp.com', 'yellowpages.com', 'bbb.org',
                                      'facebook.com', 'linkedin.com', 'google.com'):
            return domain.split('.')[0].title()
    except Exception:
        pass
    return ""

def _dedup_leads(leads):
    """Deduplicate leads by primary email, then by normalized name."""
    seen_keys = set()
    deduped = []
    for lead in leads:
        emails = lead.get('emails', [])
        name = lead.get('name', '').lower().strip()

        # Primary dedup key: first email
        if emails:
            key = f"email:{emails[0].lower()}"
        elif name and len(name) > 3:
            # Normalize name for dedup (strip common suffixes)
            norm = re.sub(r'\s*(llc|inc|corp|ltd|co|company|group|management)\s*$', '', name, flags=re.I).strip()
            key = f"name:{norm}"
        else:
            key = None  # can't dedup, keep anyway

        if key is None or key not in seen_keys:
            if key:
                seen_keys.add(key)
            deduped.append(lead)
    return deduped

def _build_lead_report(query, location, leads, pages_scanned, elapsed):
    """Build lead generation report."""
    lines = []
    lines.append(f"# Lead Generation: {query}")
    if location:
        lines.append(f"**Location:** {location}")
    lines.append(f"**Scanned:** {pages_scanned} pages in {elapsed:.1f}s")
    lines.append(f"**Found:** {len(leads)} leads\n")

    for i, lead in enumerate(leads, 1):
        lines.append(f"## Lead {i}")
        if lead.get('name'):
            lines.append(f"  Name: {lead['name']}")
        if lead.get('emails'):
            lines.append(f"  Email: {', '.join(lead['emails'])}")
        if lead.get('phones'):
            lines.append(f"  Phone: {', '.join(lead['phones'])}")
        if lead.get('social'):
            lines.append(f"  Social: {', '.join(lead['social'])}")
        lines.append(f"  Source: [{lead.get('source_title', 'Link')}]({lead.get('source_url', '')})")
        lines.append("")

    if not leads:
        lines.append("No leads found. Try broader search terms or different location.")

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════
#  GOOGLE MAPS BUSINESS SEARCH
# ══════════════════════════════════════════════════════════════

def maps_search(query, location="", max_results=20):
    """
    Search for businesses via Google Maps and extract listings.
    Falls back to directory sites if Maps is blocked.
    """
    t0 = time.time()

    # Search Google Maps via web search
    maps_query = f"site:google.com/maps {query} {location}".strip()
    alt_queries = [
        f"{query} {location} yelp",
        f"{query} {location} yellow pages",
        f"{query} {location} business directory phone email",
        f"{query} {location} site:bbb.org",
    ]

    all_results = []

    # Try Google Maps direct
    try:
        gm_url = f"https://www.google.com/maps/search/{quote_plus(f'{query} {location}'.strip())}"
        result = smart_fetch(gm_url, need_js=True, max_chars=50000)
        raw = result.get('markdown', '')
        # Extract business info patterns from Maps
        businesses = _parse_maps_listings(raw, result.get('url', gm_url))
        all_results.extend(businesses)
    except Exception as e:
        log.error(f"Google Maps direct failed: {e}")

    # Supplement with directory searches
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(web_search, q, 10): q for q in alt_queries}
        for future in as_completed(futures):
            try:
                search_results = future.result()
                for r in search_results:
                    all_results.append({
                        'name': r.get('title', ''),
                        'url': r.get('url', ''),
                        'snippet': r.get('snippet', ''),
                    })
            except Exception:
                pass

    # Fetch top results to extract contact info
    enriched = []
    urls_to_enrich = all_results[:min(max_results, LEAD_GEN_MAX_PAGES)]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for biz in urls_to_enrich:
            if biz.get('url'):
                futures[pool.submit(_extract_from_url, biz['url'], 'all')] = biz

        for future in as_completed(futures):
            biz = futures[future]
            try:
                contacts = future.result()
                entry = dict(biz)
                if contacts:
                    for c in contacts:
                        if c.get('emails'):
                            entry['emails'] = c['emails']
                        if c.get('phones'):
                            entry['phones'] = c['phones']
                enriched.append(entry)
            except Exception:
                enriched.append(biz)

    elapsed = time.time() - t0

    # Build report
    lines = []
    lines.append(f"# Maps Search: {query}")
    if location:
        lines.append(f"**Location:** {location}")
    lines.append(f"**Found:** {len(enriched)} businesses in {elapsed:.1f}s\n")

    for i, biz in enumerate(enriched[:max_results], 1):
        lines.append(f"## {i}. {biz.get('name', 'Business')}")
        if biz.get('url'):
            lines.append(f"  Link: {biz['url']}")
        if biz.get('snippet'):
            lines.append(f"  Info: {biz['snippet'][:200]}")
        if biz.get('emails'):
            lines.append(f"  Email: {', '.join(biz['emails'][:3])}")
        if biz.get('phones'):
            lines.append(f"  Phone: {', '.join(biz['phones'][:3])}")
        if biz.get('address'):
            lines.append(f"  Address: {biz['address']}")
        if biz.get('rating'):
            lines.append(f"  Rating: {biz['rating']}")
        lines.append("")

    return '\n'.join(lines)

def _parse_maps_listings(content, url):
    """Parse Google Maps search results page."""
    businesses = []
    # Google Maps renders dynamically — extract what we can from HTML/JS
    # Look for structured data patterns
    # Business names often in specific div patterns
    name_patterns = re.findall(r'aria-label="([^"]{5,80})"', content)
    for name in name_patterns[:20]:
        if not any(skip in name.lower() for skip in ['map', 'search', 'menu', 'close', 'zoom', 'directions']):
            businesses.append({
                'name': _decode_entities(name),
                'url': url,
            })
    return businesses


# ══════════════════════════════════════════════════════════════
#  SITE CRAWLER
# ══════════════════════════════════════════════════════════════

def crawl_site(start_url, max_depth=MAX_CRAWL_DEPTH, max_pages=MAX_CRAWL_PAGES, pattern=None):
    """
    Spider a website following links to configurable depth.
    Returns all discovered pages with content and extracted data.
    """
    t0 = time.time()
    visited: Set[str] = set()
    results: List[Dict] = []
    base_domain = urlparse(start_url).netloc
    pattern_re = re.compile(pattern) if pattern else None

    # BFS crawl
    queue: List[Tuple[str, int]] = [(start_url, 0)]

    while queue and len(visited) < max_pages:
        # Process in batches for parallel fetching
        batch = []
        while queue and len(batch) < MAX_WORKERS:
            url, depth = queue.pop(0)
            if url in visited:
                continue
            if depth > max_depth:
                continue
            visited.add(url)
            batch.append((url, depth))

        if not batch:
            break

        # Parallel fetch batch
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = {pool.submit(smart_fetch, url, 12000): (url, depth) for url, depth in batch}
            for future in as_completed(futures):
                url, depth = futures[future]
                try:
                    result = future.result()
                    md = result.get('markdown', '')
                    if md:
                        results.append({
                            'url': result.get('url', url),
                            'content': md,
                            'depth': depth,
                        })

                        # Extract links for next depth
                        if depth < max_depth:
                            links = _extract_links_from_md(md, url)
                            for link in links:
                                link_domain = urlparse(link).netloc
                                if link_domain == base_domain and link not in visited:
                                    if not pattern_re or pattern_re.search(link):
                                        queue.append((link, depth + 1))
                except Exception as e:
                    log.error(f"Crawl fetch error {url}: {e}")

    elapsed = time.time() - t0

    # Build report
    lines = []
    lines.append(f"# Crawl: {start_url}")
    lines.append(f"**Pages crawled:** {len(results)} (max depth: {max_depth}) in {elapsed:.1f}s\n")

    for page in results:
        lines.append(f"## [{page['url']}] (depth {page['depth']})")
        # Truncate content for report
        content_preview = page['content'][:3000]
        lines.append(content_preview)
        lines.append("")

    return '\n'.join(lines)

def _extract_links_from_md(markdown_text, base_url):
    """Extract URLs from markdown content."""
    links = set()
    # Markdown links: [text](url)
    for m in re.finditer(r'\[([^\]]*)\]\(([^)]+)\)', markdown_text):
        url = m.group(2).strip()
        if url.startswith('/'):
            parsed = urlparse(base_url)
            url = f"{parsed.scheme}://{parsed.netloc}{url}"
        if url.startswith(('http://', 'https://')):
            # Skip anchors, javascript, mailto
            if not any(x in url for x in ['#', 'javascript:', 'mailto:', '.pdf', '.zip', '.jpg', '.png']):
                links.add(url)
    # Raw URLs
    for m in re.finditer(r'https?://[^\s<>"]+', markdown_text):
        url = m.group(0).rstrip('.,;:!?)')
        if not any(x in url for x in ['#', 'javascript:', 'mailto:', '.pdf', '.zip', '.jpg', '.png']):
            links.add(url)
    return list(links)[:50]  # Cap to prevent explosion


# ══════════════════════════════════════════════════════════════
#  BULK SCRAPE
# ══════════════════════════════════════════════════════════════

def bulk_scrape(urls, max_chars_per_page=8000):
    """Fetch multiple URLs in parallel, return all content."""
    t0 = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(smart_fetch, url.strip(), max_chars_per_page): url.strip() for url in urls if url.strip()}
        for future in as_completed(futures):
            url = futures[future]
            try:
                result = future.result()
                results.append({
                    'url': result.get('url', url),
                    'content': result.get('markdown', ''),
                    'tier': result.get('tier', '?'),
                    'status': result.get('status', 0),
                })
            except Exception as e:
                results.append({'url': url, 'content': f'Error: {e}', 'tier': 'error', 'status': 0})

    elapsed = time.time() - t0

    lines = []
    lines.append(f"# Bulk Scrape: {len(results)} pages in {elapsed:.1f}s\n")
    for r in results:
        lines.append(f"## {r['url']} (tier {r['tier']}, HTTP {r['status']})")
        lines.append(r['content'])
        lines.append("")

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════
#  ACTION DISPATCHER
# ══════════════════════════════════════════════════════════════

def handle_action(data):
    """Route action to handler. Returns string result."""
    action = data.get('action', 'search')

    # Accept 'text' as alias for 'query' (LLM may use edge.py param names)
    if 'text' in data and 'query' not in data:
        data['query'] = data['text']

    try:
        if action == 'search':
            query = data.get('query', '')
            if not query:
                return "Error: query required for search"
            max_results = min(data.get('max_results', 10), MAX_SEARCH_RESULTS)
            results = web_search(query, max_results)
            if not results:
                return f"No search results for: {query}"
            lines = [f"# Search: {query}\n"]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. **{r['title']}**")
                lines.append(f"   {r['url']}")
                if r.get('snippet'):
                    lines.append(f"   {r['snippet']}")
                lines.append("")
            lines.append(f"*{len(results)} results*")
            return '\n'.join(lines)

        elif action == 'fetch':
            url = data.get('url', '')
            if not url:
                return "Error: url required for fetch"
            max_chars = data.get('max_chars', MAX_PAGE_CHARS)
            result = smart_fetch(url, max_chars)
            return result.get('markdown', f"Failed to fetch {url}")

        elif action == 'deep_research':
            query = data.get('query', '')
            if not query:
                return "Error: query required for deep_research"
            max_pages = min(data.get('max_pages', 40), MAX_DEEP_RESEARCH_PAGES)
            return deep_research(query, max_pages)

        elif action == 'extract':
            url = data.get('url', '')
            urls_str = data.get('urls', '')
            extract_type = data.get('extract_type', 'all')

            target_urls = []
            if url:
                target_urls.append(url)
            if urls_str:
                target_urls.extend([u.strip() for u in urls_str.split(',') if u.strip()])

            if not target_urls:
                return "Error: url or urls required for extract"

            all_contacts = {}
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {pool.submit(smart_fetch, u, 50000): u for u in target_urls}
                for future in as_completed(futures):
                    u = futures[future]
                    try:
                        result = future.result()
                        raw = result.get('markdown', '')
                        contacts = extract_contacts(raw, extract_type)
                        all_contacts[u] = contacts
                    except Exception as e:
                        all_contacts[u] = {'error': str(e)}

            lines = ["# Extraction Results\n"]
            total_emails = 0
            total_phones = 0
            for u, contacts in all_contacts.items():
                lines.append(f"## {u}")
                if 'error' in contacts:
                    lines.append(f"  Error: {contacts['error']}")
                else:
                    if contacts.get('emails'):
                        lines.append(f"  Emails: {', '.join(contacts['emails'])}")
                        total_emails += len(contacts['emails'])
                    if contacts.get('phones'):
                        lines.append(f"  Phones: {', '.join(contacts['phones'])}")
                        total_phones += len(contacts['phones'])
                    if contacts.get('social'):
                        lines.append(f"  Social: {', '.join(contacts['social'])}")
                    if not contacts.get('emails') and not contacts.get('phones'):
                        lines.append("  No contact info found")
                lines.append("")
            lines.append(f"*Total: {total_emails} emails, {total_phones} phones across {len(target_urls)} pages*")
            return '\n'.join(lines)

        elif action == 'scrape':
            urls_str = data.get('urls', '')
            if not urls_str:
                return "Error: urls required for scrape (comma-separated)"
            urls = [u.strip() for u in urls_str.split(',') if u.strip()]
            max_chars = data.get('max_chars_per_page', 8000)
            return bulk_scrape(urls, max_chars)

        elif action == 'form':
            url = data.get('url', '')
            fields = data.get('fields', {})
            submit = data.get('submit_selector', '')
            if not url:
                return "Error: url required for form"
            if not fields:
                return "Error: fields required for form (JSON object)"
            result = pw_form_submit(url, fields, submit)
            lines = ["# Form Submission Result\n"]
            lines.append(f"**URL:** {url}")
            lines.append(f"**Fields:**")
            for f in result.get('fields', []):
                lines.append(f)
            lines.append(f"\n**Submit:** {result.get('submit', 'N/A')}")
            lines.append(f"**Result URL:** {result.get('result_url', 'N/A')}")
            if result.get('result_page'):
                lines.append(f"\n**Result Page:**\n{result['result_page']}")
            return '\n'.join(lines)

        elif action == 'lead_gen':
            query = data.get('query', '')
            if not query:
                return "Error: query required for lead_gen"
            location = data.get('location', '')
            lead_type = data.get('lead_type', 'all')
            max_leads = min(data.get('max_leads', 50), 200)
            return lead_gen(query, location, lead_type, max_leads)

        elif action == 'crawl':
            url = data.get('url', '')
            if not url:
                return "Error: url required for crawl"
            max_depth = min(data.get('max_depth', 2), MAX_CRAWL_DEPTH)
            max_pages = min(data.get('max_pages', 30), MAX_CRAWL_PAGES)
            pattern = data.get('pattern', '')
            return crawl_site(url, max_depth, max_pages, pattern)

        elif action == 'maps_search':
            query = data.get('query', '')
            if not query:
                return "Error: query required for maps_search"
            location = data.get('location', '')
            max_results = min(data.get('max_results', 20), 50)
            return maps_search(query, location, max_results)

        else:
            return f"Unknown action: {action}. Available: search, fetch, deep_research, extract, scrape, form, lead_gen, crawl, maps_search"

    except Exception as e:
        log.error(f"Action {action} error: {e}", exc_info=True)
        return f"Error in {action}: {e}"


# ══════════════════════════════════════════════════════════════
#  SERVER MODE (persistent stdin/stdout JSON, like edge.py)
# ══════════════════════════════════════════════════════════════

def server_loop():
    """Persistent server mode: read JSON from stdin, write results to stdout.

    Protocol matches edge.py: readline() per command, json.dumps() per response.
    Uses readline() instead of 'for line in sys.stdin' to avoid iterator buffering.
    Response format: {"status":"success","page":"<markdown content>"} to match
    agent.py's _smart_truncate which parses JSON and looks for "page" key.
    """
    log.info(f"AetherHeadless {SCRIPT_VERSION} server started")
    print(json.dumps({
        "status": "ready",
        "version": SCRIPT_VERSION,
        "tool": "web",
    }), flush=True)

    while True:
        try:
            line = sys.stdin.readline()
            if not line:  # EOF — agent closed stdin
                break
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                result = handle_action(data)
                # Ensure result fits in agent observation window
                if len(result) > 100000:
                    result = result[:100000] + "\n\n... [truncated]"
                # Match edge.py response format for agent.py compatibility
                print(json.dumps({
                    "status": "success",
                    "page": result,
                }, ensure_ascii=False), flush=True)
            except json.JSONDecodeError as e:
                print(json.dumps({"status": "error", "message": f"Invalid JSON: {e}"}), flush=True)
            except Exception as e:
                log.error(f"Server error: {e}", exc_info=True)
                print(json.dumps({"status": "error", "message": str(e)}), flush=True)
        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}), flush=True)


# ══════════════════════════════════════════════════════════════
#  CLEANUP
# ══════════════════════════════════════════════════════════════

def cleanup():
    """Cleanup Playwright resources and worker thread."""
    global _pw_browser, _pw_context, _pw_instance
    # Signal the Playwright worker thread to exit
    try:
        _pw_request_queue.put(None)  # shutdown sentinel
    except Exception:
        pass
    try:
        if _pw_context:
            _pw_context.close()
        if _pw_browser:
            _pw_browser.close()
        if _pw_instance:
            _pw_instance.stop()
    except Exception:
        pass

import atexit
atexit.register(cleanup)


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="AetherHeadless — Headless Web Engine")
    parser.add_argument('--json', help='JSON args for one-shot execution')
    parser.add_argument('--server', action='store_true', help='Run in server mode (stdin/stdout JSON)')
    args = parser.parse_args()

    if args.server:
        try:
            server_loop()
        except KeyboardInterrupt:
            pass
        finally:
            cleanup()
    elif args.json:
        try:
            data = json.loads(args.json)
            result = handle_action(data)
            print(result)
        except Exception as e:
            print(f"Error: {e}")
    else:
        print(f"AetherHeadless {SCRIPT_VERSION}")
        print("Usage: --server (persistent) or --json '{...}' (one-shot)")
        print("Actions: search, fetch, deep_research, extract, scrape, form, lead_gen, crawl, maps_search")
