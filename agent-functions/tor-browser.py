#!/usr/bin/env python3
"""
tor-browser.py v1.0 — AetherTor: State-of-the-Art Headless Tor Browser Engine (March 2026)

Bleeding-edge headless dark-web/deep-web browsing for agentic research, crawling,
and file discovery across Tor networks. Security-airgapped by design.

Architecture: Tor-Native 3-Tier Routing
  Tier 1 — SOCKS HTTP/urllib: Raw HTTP through Tor SOCKS5. <3s per .onion page.
  Tier 2 — Playwright + Tor SOCKS5: JS-rendered onion sites. ~5-10s per page.
  Tier 3 — Circuit-rotated retry: Fresh identity + retry on failure.

Security Airgapping:
  - ALL traffic routed through Tor SOCKS5 (127.0.0.1:9050) — zero clearnet leaks
  - DNS resolution through Tor (SOCKS5h remote DNS) — no DNS leaks
  - Tor Browser user-agent fingerprint — blends with real Tor users
  - Circuit isolation per-domain via SOCKS5 auth stream isolation
  - Automatic circuit rotation (NEWNYM) between crawl batches
  - No WebRTC, no geolocation, no local IP exposure
  - Playwright: disabled WebRTC, disabled geolocation, CSP bypass

Design Principles (2026 SOTA):
  - Tor-native: embedded Tor daemon management via stem
  - Markdown-first output (67% token reduction vs raw HTML)
  - .onion-aware link extraction and normalization
  - File discovery engine: .db, .sql, .csv, .json, .zip, .tar, .gz, .sqlite, .bak, .dump
  - Deduplication via content fingerprinting (MinHash-style shingle hashing)
  - Automatic Tor installation check + guided setup
  - Onion search engines: Ahmia, Torch, DuckDuckGo .onion, Haystack

Actions:
  search         — Search via Tor search engines (Ahmia/Torch/DDG onion)
  fetch          — Fetch .onion or clearnet URL through Tor → clean markdown
  deep_research  — Multi-branch hierarchical research across Tor network
  crawl          — Spider .onion sites following links to depth N
  discover       — Discover .onion addresses from directories + search engines
  extract_files  — Scan pages for downloadable files (.db, .sql, .csv, etc.)
  extract        — Extract emails, crypto wallets, PGP keys from onion pages
  status         — Check Tor daemon status, circuit info, connectivity

Server mode: --server for persistent stdin/stdout JSON (like edge.py / headless-browser.py)
One-shot mode: --json '{"action":"search","query":"..."}' for single calls
"""

# ── FIX: Remove script directory from sys.path to prevent module shadowing
import sys, os
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir in sys.path:
    sys.path.remove(_script_dir)

import json, argparse, re, html, hashlib, time, logging, ssl, gzip, signal
import threading, queue as _queue_mod, subprocess, socket, struct
from typing import Dict, Any, Optional, List, Tuple, Set
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote_plus, urlparse, urljoin, unquote
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from collections import defaultdict

# ──────────────────────────────────────────────────────────────
# Constants & Configuration
# ──────────────────────────────────────────────────────────────
SCRIPT_VERSION = "v1.0_aethertor"
LOG_FILE = os.path.expanduser("~/.agentf_tor.log")
CACHE_DIR = os.path.expanduser("~/.tor_cache")
CACHE_TTL = 600  # 10 min cache (onion sites are slow, cache more aggressively)
MAX_WORKERS = 6   # parallel fetch threads (conservative for Tor bandwidth)
FETCH_TIMEOUT = 30  # seconds per request (Tor is slower than clearnet)
PW_TIMEOUT = 45000  # Playwright timeout ms (onion sites load slowly)
MAX_PAGE_CHARS = 24000  # max chars returned per page (LLM context budget)
MAX_SEARCH_RESULTS = 20
MAX_CRAWL_DEPTH = 3
MAX_CRAWL_PAGES = 50

# Deep Research constants (Tor-tuned: slower network = smaller budgets)
MAX_DEEP_RESEARCH_PAGES = 40   # total pages across all branches
MAX_BRANCH_PAGES = 10          # max pages per research branch
MAX_BRANCHES = 5               # max topic branches
MAX_RESEARCH_ROUNDS = 2        # iterative search rounds per branch
RESEARCH_TIME_BUDGET = 300.0   # 5 minute total budget (Tor is slow)
MIN_COVERAGE_SCORE = 0.3       # branch pruning threshold

# Tor daemon configuration
TOR_SOCKS_HOST = "127.0.0.1"
TOR_SOCKS_PORT = 9050
TOR_CONTROL_PORT = 9051
TOR_DATA_DIR = os.path.expanduser("~/.aethertor")
TOR_TORRC = os.path.join(TOR_DATA_DIR, "torrc")
TOR_PID_FILE = os.path.join(TOR_DATA_DIR, "tor.pid")

# File types to discover
FILE_EXTENSIONS = {
    'database': ['.db', '.sqlite', '.sqlite3', '.sql', '.mdb', '.accdb', '.dbf'],
    'data': ['.csv', '.json', '.jsonl', '.xml', '.yaml', '.yml', '.tsv', '.parquet'],
    'archive': ['.zip', '.tar', '.gz', '.tar.gz', '.tgz', '.bz2', '.7z', '.rar', '.xz'],
    'backup': ['.bak', '.dump', '.backup', '.old', '.orig', '.save'],
    'document': ['.pdf', '.doc', '.docx', '.txt', '.rtf', '.odt', '.xls', '.xlsx'],
    'code': ['.py', '.js', '.php', '.rb', '.go', '.rs', '.c', '.cpp', '.java'],
    'config': ['.conf', '.cfg', '.ini', '.env', '.htaccess', '.htpasswd'],
    'credential': ['.pem', '.key', '.crt', '.cert', '.pfx', '.p12', '.jks'],
}
ALL_FILE_EXTENSIONS = set()
for exts in FILE_EXTENSIONS.values():
    ALL_FILE_EXTENSIONS.update(exts)

# Onion link regex
ONION_RE = re.compile(
    r'(?:https?://)?([a-z2-7]{16,56}\.onion)(/[^\s"\'<>]*)?',
    re.IGNORECASE
)

# Crypto wallet patterns
BTC_RE = re.compile(r'\b(?:bc1[a-zA-HJ-NP-Z0-9]{25,39}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b')
ETH_RE = re.compile(r'\b0x[a-fA-F0-9]{40}\b')
XMR_RE = re.compile(r'\b4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}\b')  # Monero

# PGP key block
PGP_RE = re.compile(
    r'-----BEGIN PGP PUBLIC KEY BLOCK-----.*?-----END PGP PUBLIC KEY BLOCK-----',
    re.DOTALL
)

# Tor Browser user-agent (match real Tor Browser to blend in)
TOR_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"
)

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(TOR_DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG, filename=LOG_FILE, filemode='a',
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("aethertor")

# ──────────────────────────────────────────────────────────────
# TOOL_METADATA — agent.py auto-discovery
# ──────────────────────────────────────────────────────────────
TOOL_METADATA = {
    "name": "tor",
    "description": (
        "AetherTor v1.0 — state-of-the-art headless Tor browser engine for dark web and deep web research. "
        "Security-airgapped: all traffic routed through Tor SOCKS5, no DNS leaks, circuit isolation. "
        "Use ONLY when user says 'browse the deep web', 'browse the dark web', 'use tor browser', "
        "'use tor', 'using tor', 'dark web', 'deep web', '.onion', 'tor network'. "
        "Do NOT use for regular web browsing — use 'web' tool instead. "
        "\n\nACTIONS:\n"
        "  search        → Search Tor search engines (Ahmia/Torch/DDG onion). Args: query, max_results (default 10).\n"
        "  fetch         → Fetch .onion or clearnet URL through Tor → clean markdown. Args: url, max_chars.\n"
        "  deep_research → Multi-branch hierarchical research across Tor network. Args: query, max_pages (default 30). "
        "Decomposes topic into sub-branches, runs iterative search+fetch loops across onion sites.\n"
        "  crawl         → Spider .onion site following links. Args: url, max_depth (default 2), max_pages (default 30), pattern (URL regex).\n"
        "  discover      → Discover .onion addresses from directories + search engines. Args: query, max_results.\n"
        "  extract_files → Scan pages for downloadable files (.db, .sql, .csv, .zip, etc). Args: url or urls (comma-sep).\n"
        "  extract       → Extract crypto wallets, PGP keys, emails from onion pages. Args: url, extract_type (all/wallets/pgp/emails).\n"
        "  status        → Check Tor daemon status, circuit info, connectivity. No args required.\n"
        "\nExamples:\n"
        '  {"action":"search","query":"security research databases"}\n'
        '  {"action":"fetch","url":"http://example.onion/page"}\n'
        '  {"action":"deep_research","query":"cybersecurity threat intelligence feeds"}\n'
        '  {"action":"crawl","url":"http://example.onion","max_depth":2}\n'
        '  {"action":"discover","query":"security research"}\n'
        '  {"action":"extract_files","url":"http://example.onion/data/"}\n'
        '  {"action":"status"}'
    ),
    "priority": 100,  # lower than web (1000) — only used when explicitly requested
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "fetch", "deep_research", "crawl", "discover",
                         "extract_files", "extract", "status"],
                "description": "Action to perform."
            },
            "query": {
                "type": "string",
                "description": "Search query or research topic."
            },
            "url": {
                "type": "string",
                "description": "URL to fetch/crawl/extract (.onion or clearnet via Tor)."
            },
            "urls": {
                "type": "string",
                "description": "Comma-separated URLs for bulk operations."
            },
            "max_results": {
                "type": "integer",
                "default": 10,
                "description": "Max search/discover results."
            },
            "max_pages": {
                "type": "integer",
                "default": 30,
                "description": "Max pages for deep research."
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
            "extract_type": {
                "type": "string",
                "enum": ["all", "wallets", "pgp", "emails"],
                "default": "all",
                "description": "What to extract."
            },
            "pattern": {
                "type": "string",
                "description": "URL filter regex for crawl."
            }
        },
        "required": ["action"]
    }
}


# ══════════════════════════════════════════════════════════════
#  DEPENDENCY MANAGEMENT
# ══════════════════════════════════════════════════════════════

def _check_dependencies():
    """Check and report on required dependencies."""
    status = {"tor": False, "stem": False, "socks": False, "playwright": False}
    issues = []

    # Check tor daemon
    try:
        result = subprocess.run(["tor", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            status["tor"] = True
            log.info(f"Tor found: {result.stdout.strip().split(chr(10))[0]}")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        issues.append("Tor not installed. Install with: brew install tor")

    # Check stem (Tor controller)
    try:
        import stem
        status["stem"] = True
    except ImportError:
        issues.append("stem not installed. Install with: pip3 install stem")

    # Check PySocks (SOCKS proxy support)
    try:
        import socks
        status["socks"] = True
    except ImportError:
        issues.append("PySocks not installed. Install with: pip3 install PySocks")

    # Check Playwright
    try:
        from playwright.sync_api import sync_playwright
        status["playwright"] = True
    except ImportError:
        issues.append("Playwright not installed (optional, for JS rendering)")

    return status, issues


def _auto_install_deps():
    """Attempt to auto-install missing critical dependencies."""
    installed = []
    try:
        import socks
    except ImportError:
        log.info("Auto-installing PySocks...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "PySocks"],
                         capture_output=True, timeout=30)
            installed.append("PySocks")
        except Exception as e:
            log.error(f"Failed to install PySocks: {e}")

    try:
        import stem
    except ImportError:
        log.info("Auto-installing stem...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "stem"],
                         capture_output=True, timeout=30)
            installed.append("stem")
        except Exception as e:
            log.error(f"Failed to install stem: {e}")

    return installed


# ══════════════════════════════════════════════════════════════
#  TOR DAEMON MANAGEMENT
# ══════════════════════════════════════════════════════════════

_tor_process = None
_tor_lock = threading.Lock()

def _write_torrc():
    """Write secure torrc configuration."""
    config = f"""# AetherTor auto-generated config
SocksPort {TOR_SOCKS_PORT}
ControlPort {TOR_CONTROL_PORT}
CookieAuthentication 1
DataDirectory {TOR_DATA_DIR}/data
Log notice file {TOR_DATA_DIR}/tor.log
# Security hardening
SafeSocks 1
TestSocks 0
# Circuit management for crawling
MaxCircuitDirtiness 300
NewCircuitPeriod 30
NumEntryGuards 8
# Performance
CircuitBuildTimeout 30
LearnCircuitBuildTimeout 1
# DNS safety — force all DNS through Tor
DNSPort 0
AutomapHostsOnResolve 1
"""
    os.makedirs(os.path.join(TOR_DATA_DIR, "data"), exist_ok=True)
    with open(TOR_TORRC, 'w') as f:
        f.write(config)
    log.info(f"Wrote torrc to {TOR_TORRC}")


def _is_tor_running():
    """Check if Tor SOCKS proxy is accepting connections."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((TOR_SOCKS_HOST, TOR_SOCKS_PORT))
        s.close()
        return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def _start_tor():
    """Start Tor daemon if not already running."""
    global _tor_process
    with _tor_lock:
        if _is_tor_running():
            log.info("Tor already running on SOCKS port")
            return True

        # Check if tor binary exists
        try:
            subprocess.run(["tor", "--version"], capture_output=True, timeout=5)
        except FileNotFoundError:
            log.error("Tor binary not found")
            return False

        _write_torrc()

        log.info("Starting Tor daemon...")
        try:
            _tor_process = subprocess.Popen(
                ["tor", "-f", TOR_TORRC],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as e:
            log.error(f"Failed to start Tor: {e}")
            return False

        # Wait for Tor to bootstrap (up to 60 seconds)
        deadline = time.time() + 60
        while time.time() < deadline:
            if _is_tor_running():
                log.info("Tor daemon started and SOCKS proxy ready")
                # Wait a bit more for full bootstrap
                time.sleep(2)
                return True
            # Check if process died
            if _tor_process.poll() is not None:
                stderr = _tor_process.stderr.read()
                log.error(f"Tor process exited: {stderr}")
                return False
            time.sleep(1)

        log.error("Tor daemon failed to start within 60s")
        return False


def _stop_tor():
    """Stop our managed Tor daemon (not system Tor)."""
    global _tor_process
    with _tor_lock:
        if _tor_process:
            try:
                _tor_process.terminate()
                _tor_process.wait(timeout=10)
            except Exception:
                try:
                    _tor_process.kill()
                except Exception:
                    pass
            _tor_process = None
            log.info("Tor daemon stopped")


def _rotate_circuit():
    """Request new Tor circuit (NEWNYM) via control port using stem."""
    try:
        from stem import Signal
        from stem.control import Controller
        with Controller.from_port(port=TOR_CONTROL_PORT) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            log.info("Tor circuit rotated (NEWNYM)")
            time.sleep(1)  # Brief pause for new circuit
            return True
    except Exception as e:
        log.warning(f"Circuit rotation failed: {e}")
        return False


def _get_circuit_info():
    """Get current Tor circuit information."""
    try:
        from stem.control import Controller
        with Controller.from_port(port=TOR_CONTROL_PORT) as controller:
            controller.authenticate()
            info = {
                "version": controller.get_version().raw(),
                "circuits": [],
                "bytes_read": controller.get_info("traffic/read", "unknown"),
                "bytes_written": controller.get_info("traffic/written", "unknown"),
                "uptime": controller.get_info("uptime", "unknown"),
            }
            for circ in sorted(controller.get_circuits()):
                if circ.status == "BUILT":
                    path = [f"{entry.nickname}({entry.fingerprint[:8]})" for entry in circ.path]
                    info["circuits"].append({
                        "id": circ.id,
                        "purpose": circ.purpose,
                        "path": " → ".join(path),
                    })
            return info
    except Exception as e:
        return {"error": str(e)}


def _ensure_tor():
    """Ensure Tor is running. Returns True if ready, False otherwise."""
    if _is_tor_running():
        return True
    return _start_tor()


# ══════════════════════════════════════════════════════════════
#  SOCKS5 HTTP FETCH (Tier 1 — all traffic through Tor)
# ══════════════════════════════════════════════════════════════

def _tor_headers():
    """HTTP headers matching real Tor Browser fingerprint."""
    return {
        'User-Agent': TOR_USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }


def _create_socks_connection(host, port, proxy_host=TOR_SOCKS_HOST,
                              proxy_port=TOR_SOCKS_PORT, stream_id=None):
    """Create a SOCKS5 connection through Tor with optional stream isolation."""
    import socks
    s = socks.socksocket()
    s.settimeout(FETCH_TIMEOUT)
    # Stream isolation: use unique username per domain for circuit isolation
    username = stream_id or hashlib.md5(host.encode()).hexdigest()[:16]
    s.set_proxy(socks.SOCKS5, proxy_host, proxy_port,
                rdns=True,  # DNS through Tor (critical — no DNS leaks)
                username=username, password=username)
    s.connect((host, port))
    return s


def tor_http_fetch(url, timeout=FETCH_TIMEOUT, headers=None):
    """Tier 1: Raw HTTP fetch through Tor SOCKS5 proxy.
    Returns (html_string, status_code, final_url) or raises.
    Uses PySocks for SOCKS5h (remote DNS resolution through Tor).
    """
    try:
        import socks
        import urllib.request

        # Install SOCKS opener globally for this fetch
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        is_https = parsed.scheme == 'https'

        # Build HTTP handler with SOCKS proxy
        # We use the socks module to create a custom opener
        import http.client

        class SocksHTTPConnection(http.client.HTTPConnection):
            def connect(self):
                self.sock = _create_socks_connection(
                    self.host, self.port, stream_id=host
                )

        class SocksHTTPSConnection(http.client.HTTPSConnection):
            def connect(self):
                sock = _create_socks_connection(
                    self.host, self.port, stream_id=host
                )
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                self.sock = ctx.wrap_socket(sock, server_hostname=self.host)

        class SocksHTTPHandler(urllib.request.HTTPHandler):
            def http_open(self, req):
                return self.do_open(SocksHTTPConnection, req)

        class SocksHTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return self.do_open(SocksHTTPSConnection, req)

        opener = urllib.request.build_opener(SocksHTTPHandler, SocksHTTPSHandler)

        hdrs = _tor_headers()
        if headers:
            hdrs.update(headers)

        req = Request(url, headers=hdrs)
        resp = opener.open(req, timeout=timeout)

        data = resp.read()
        # Handle gzip
        if resp.headers.get('Content-Encoding') == 'gzip':
            try:
                data = gzip.decompress(data)
            except Exception:
                pass
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
    except Exception as e:
        raise ConnectionError(f"Tor fetch error {url}: {e}")


# ══════════════════════════════════════════════════════════════
#  TIER 2 — PLAYWRIGHT + TOR SOCKS5 (JS rendering through Tor)
# ══════════════════════════════════════════════════════════════

_pw_instance = None
_pw_browser = None
_pw_context = None
_pw_lock = threading.Lock()

# Playwright Worker Thread Queue (same pattern as headless-browser.py)
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
                target=_pw_worker_loop, daemon=True, name='tor-pw-worker'
            )
            _pw_worker_thread.start()


def _run_in_pw_thread(func, *args, timeout_sec=60, **kwargs):
    """Execute a function in the Playwright worker thread and wait for result."""
    _ensure_pw_worker()
    req = _PwRequest(func, args, kwargs)
    _pw_request_queue.put(req)
    if not req.done.wait(timeout=timeout_sec):
        raise RuntimeError(f"Playwright operation timed out after {timeout_sec}s")
    if req.error:
        raise req.error
    return req.result


def _get_tor_playwright():
    """Lazy-init Playwright headless browser with Tor SOCKS5 proxy."""
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
                '--disable-extensions',
                '--disable-background-networking',
                '--disable-sync',
                '--disable-translate',
                # Security: disable WebRTC to prevent IP leaks
                '--disable-webrtc',
                '--enforce-webrtc-ip-permission-check',
                '--disable-webrtc-hw-encoding',
                '--disable-webrtc-hw-decoding',
                # Disable geolocation
                '--disable-geolocation',
            ]
            # Route ALL browser traffic through Tor SOCKS5
            tor_proxy = f"socks5://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}"
            try:
                _pw_browser = _pw_instance.chromium.launch(
                    headless=True,
                    args=launch_args,
                    proxy={"server": tor_proxy},
                )
            except Exception:
                try:
                    _pw_browser = _pw_instance.firefox.launch(
                        headless=True,
                        proxy={"server": tor_proxy},
                    )
                except Exception:
                    _pw_browser = _pw_instance.chromium.launch(
                        headless=True,
                        proxy={"server": tor_proxy},
                    )

            _pw_context = _pw_browser.new_context(
                user_agent=TOR_USER_AGENT,
                viewport={'width': 1280, 'height': 800},
                locale='en-US',
                timezone_id='America/New_York',
                java_script_enabled=True,
                bypass_csp=True,
                ignore_https_errors=True,
                # Disable geolocation
                geolocation=None,
                permissions=[],
            )
            # Stealth patches matching Tor Browser
            _pw_context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
                // Disable WebRTC at JS level
                if (window.RTCPeerConnection) {
                    window.RTCPeerConnection = undefined;
                }
                if (window.webkitRTCPeerConnection) {
                    window.webkitRTCPeerConnection = undefined;
                }
                // Spoof screen resolution (Tor Browser standard)
                Object.defineProperty(screen, 'width', {get: () => 1280});
                Object.defineProperty(screen, 'height', {get: () => 800});
                Object.defineProperty(screen, 'availWidth', {get: () => 1280});
                Object.defineProperty(screen, 'availHeight', {get: () => 800});
            """)
            log.info("Playwright + Tor SOCKS5 browser initialized")
            return _pw_context
        except ImportError:
            log.warning("Playwright not available, Tier 2 disabled")
            return None
        except Exception as e:
            log.error(f"Playwright + Tor init failed: {e}")
            return None


def _pw_tor_fetch_impl(url, timeout=PW_TIMEOUT):
    """Internal Playwright fetch through Tor — runs ONLY in pw-worker thread."""
    ctx = _get_tor_playwright()
    if not ctx:
        raise RuntimeError("Playwright + Tor not available")
    page = ctx.new_page()
    try:
        page.set_default_timeout(timeout)
        resp = page.goto(url, wait_until='domcontentloaded', timeout=timeout)
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
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


def pw_tor_fetch(url, timeout=PW_TIMEOUT):
    """Thread-safe Playwright fetch through Tor."""
    wait_sec = max(60, timeout // 1000 + 20)
    return _run_in_pw_thread(_pw_tor_fetch_impl, url, timeout, timeout_sec=wait_sec)


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
    """Convert HTML to clean markdown optimized for LLM consumption."""
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
        h = re.sub(rf'<h{i}[^>]*>(.*?)</h{i}>', lambda m, lvl=i: '\n' + '#' * lvl + ' ' + _strip_tags(m.group(1)).strip() + '\n', h, flags=re.DOTALL | re.IGNORECASE)

    # Convert links — preserve href (especially .onion links)
    def _link_replace(m):
        href = m.group(1)
        text = _strip_tags(m.group(2)).strip()
        if not text or text == href:
            return f' {href} '
        # Make absolute for onion sites
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

    # Convert tables
    h = re.sub(r'<tr[^>]*>', '\n| ', h, flags=re.IGNORECASE)
    h = re.sub(r'<t[dh][^>]*>', ' ', h, flags=re.IGNORECASE)
    h = re.sub(r'</t[dh]>', ' | ', h, flags=re.IGNORECASE)
    h = re.sub(r'</tr>', '', h, flags=re.IGNORECASE)

    # Convert paragraphs and breaks
    h = re.sub(r'<br\s*/?>', '\n', h, flags=re.IGNORECASE)
    h = re.sub(r'<p[^>]*>', '\n\n', h, flags=re.DOTALL | re.IGNORECASE)
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
        result = result[:max_chars] + f"\n\n... [truncated at {max_chars} chars]"

    return result


# ══════════════════════════════════════════════════════════════
#  CACHE
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
        with open(_cache_key(url), 'w', encoding='utf-8') as f:
            f.write(content[:100000])
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
#  CONTENT FINGERPRINTING (MinHash-style dedup)
# ══════════════════════════════════════════════════════════════

def _shingle_hash(text, k=5, num_hashes=64):
    """Generate MinHash-style fingerprint for deduplication.
    Uses k-character shingles hashed with multiple hash functions.
    """
    if not text or len(text) < k:
        return set()
    # Normalize text
    text = re.sub(r'\s+', ' ', text.lower().strip())
    # Generate shingles
    shingles = set()
    for i in range(len(text) - k + 1):
        shingle = text[i:i+k]
        shingles.add(hashlib.md5(shingle.encode()).hexdigest()[:8])
    # MinHash: keep smallest N hashes
    sorted_hashes = sorted(shingles)[:num_hashes]
    return frozenset(sorted_hashes)


def _jaccard_similarity(fp1, fp2):
    """Estimate Jaccard similarity from two fingerprint sets."""
    if not fp1 or not fp2:
        return 0.0
    intersection = len(fp1 & fp2)
    union = len(fp1 | fp2)
    return intersection / union if union > 0 else 0.0


class DeduplicationEngine:
    """Track content fingerprints to detect duplicate/mirror sites."""
    def __init__(self, threshold=0.7):
        self.fingerprints = {}  # url → fingerprint
        self.threshold = threshold

    def is_duplicate(self, url, text):
        """Check if content is a duplicate of already-seen content.
        Returns (is_dup, similar_url) tuple.
        """
        fp = _shingle_hash(text)
        if not fp:
            return False, None

        for seen_url, seen_fp in self.fingerprints.items():
            sim = _jaccard_similarity(fp, seen_fp)
            if sim >= self.threshold:
                return True, seen_url

        self.fingerprints[url] = fp
        return False, None

    def add(self, url, text):
        """Add content fingerprint."""
        fp = _shingle_hash(text)
        if fp:
            self.fingerprints[url] = fp


# ══════════════════════════════════════════════════════════════
#  INTELLIGENT TOR FETCH (auto-tier with circuit rotation)
# ══════════════════════════════════════════════════════════════

def smart_tor_fetch(url, max_chars=MAX_PAGE_CHARS, need_js=False):
    """Intelligent Tor fetch with tier escalation and circuit rotation.
    Tier 1: HTTP through SOCKS5 (fastest)
    Tier 2: Playwright through Tor SOCKS5 (JS rendering)
    Tier 3: Rotate circuit + retry
    """
    # Check cache
    cached = _cache_get(url)
    if cached:
        log.info(f"Cache hit: {url}")
        return {"markdown": cached[:max_chars], "url": url, "tier": "cache", "status": 200}

    # Tier 1: HTTP through Tor SOCKS5
    if not need_js:
        try:
            raw_html, status, final_url = tor_http_fetch(url)
            if status == 200 and len(raw_html) > 200:
                text_content = _strip_tags(raw_html)
                text_content = re.sub(r'\s+', ' ', text_content).strip()
                if len(text_content) > 100:
                    md = html_to_markdown(raw_html, final_url, max_chars)
                    _cache_set(url, md)
                    return {"markdown": md, "url": final_url, "tier": 1, "status": status}
                else:
                    log.info(f"Tier 1 thin content, escalating: {url}")
            elif status in (403, 503):
                log.info(f"Tier 1 blocked ({status}), escalating: {url}")
            elif status >= 400:
                return {"markdown": f"HTTP {status} error fetching {url}", "url": url, "tier": 1, "status": status}
        except ConnectionError as e:
            log.info(f"Tier 1 failed, escalating: {e}")

    # Tier 2: Playwright through Tor
    try:
        raw_html, status, final_url = pw_tor_fetch(url)
        if raw_html:
            md = html_to_markdown(raw_html, final_url, max_chars)
            _cache_set(url, md)
            return {"markdown": md, "url": final_url, "tier": 2, "status": status}
    except RuntimeError:
        log.warning("Playwright + Tor not available")
    except Exception as e:
        log.error(f"Tier 2 failed: {e}")

    # Tier 3: Rotate circuit and retry Tier 1
    _rotate_circuit()
    try:
        raw_html, status, final_url = tor_http_fetch(url, timeout=FETCH_TIMEOUT * 2)
        if raw_html:
            md = html_to_markdown(raw_html, final_url, max_chars)
            _cache_set(url, md)
            return {"markdown": md, "url": final_url, "tier": 3, "status": status}
    except Exception as e:
        log.error(f"Tier 3 failed: {e}")

    return {"markdown": f"Failed to fetch {url} through Tor (all tiers exhausted)", "url": url, "tier": "error", "status": 0}


# ══════════════════════════════════════════════════════════════
#  ONION LINK EXTRACTION
# ══════════════════════════════════════════════════════════════

def extract_onion_links(text, source_url=""):
    """Extract all .onion links from text/HTML."""
    links = set()
    # Regex-based extraction
    for match in ONION_RE.finditer(text):
        domain = match.group(1).lower()
        path = match.group(2) or "/"
        links.add(f"http://{domain}{path}")

    # Also extract from href attributes
    href_matches = re.findall(r'href=["\']((?:https?://)?[a-z2-7]{16,56}\.onion[^"\']*)["\']', text, re.IGNORECASE)
    for href in href_matches:
        if not href.startswith('http'):
            href = 'http://' + href
        links.add(href)

    # Resolve relative links against source
    if source_url:
        rel_links = re.findall(r'href=["\'](/[^"\']*)["\']', text, re.IGNORECASE)
        parsed = urlparse(source_url)
        if parsed.hostname and parsed.hostname.endswith('.onion'):
            for rel in rel_links:
                links.add(f"{parsed.scheme}://{parsed.netloc}{rel}")

    return list(links)


# ══════════════════════════════════════════════════════════════
#  DATA EXTRACTION ENGINE (wallets, PGP, emails, files)
# ══════════════════════════════════════════════════════════════

# Email regex
EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', re.IGNORECASE)

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
            parts = e_lower.split('@')
            if len(parts) == 2 and '.' in parts[1] and len(parts[0]) >= 2:
                seen.add(e_lower)
                results.append(e)
    return results


def extract_crypto_wallets(text):
    """Extract cryptocurrency wallet addresses."""
    wallets = {}
    btc = list(set(BTC_RE.findall(text)))
    if btc:
        wallets['bitcoin'] = btc[:20]
    eth = list(set(ETH_RE.findall(text)))
    if eth:
        wallets['ethereum'] = eth[:20]
    xmr = list(set(XMR_RE.findall(text)))
    if xmr:
        wallets['monero'] = xmr[:20]
    return wallets


def extract_pgp_keys(text):
    """Extract PGP public key blocks."""
    keys = PGP_RE.findall(text)
    return keys[:10]  # Cap at 10 keys


def extract_file_links(html_text, source_url=""):
    """Extract links to downloadable files from HTML."""
    files = []
    # Find all href and src attributes
    links = re.findall(r'(?:href|src)=["\'](.*?)["\']', html_text, re.IGNORECASE)
    for link in links:
        # Check against known file extensions
        link_lower = link.lower()
        for category, extensions in FILE_EXTENSIONS.items():
            for ext in extensions:
                if link_lower.endswith(ext) or ext + '?' in link_lower:
                    # Make absolute URL
                    full_url = link
                    if link.startswith('/') and source_url:
                        parsed = urlparse(source_url)
                        full_url = f"{parsed.scheme}://{parsed.netloc}{link}"
                    elif not link.startswith('http'):
                        if source_url:
                            full_url = urljoin(source_url, link)
                        else:
                            continue
                    files.append({
                        'url': full_url,
                        'filename': os.path.basename(urlparse(link).path),
                        'extension': ext,
                        'category': category,
                    })
                    break

    # Also check for direct text mentions of files
    file_mentions = re.findall(
        r'[\w\-./]+(?:' + '|'.join(re.escape(ext) for ext in ALL_FILE_EXTENSIONS) + r')\b',
        html_text, re.IGNORECASE
    )
    for mention in file_mentions:
        ext = os.path.splitext(mention)[1].lower()
        if ext in ALL_FILE_EXTENSIONS:
            category = next((cat for cat, exts in FILE_EXTENSIONS.items() if ext in exts), 'unknown')
            files.append({
                'url': mention,
                'filename': os.path.basename(mention),
                'extension': ext,
                'category': category,
            })

    # Deduplicate by URL
    seen = set()
    unique = []
    for f in files:
        if f['url'] not in seen:
            seen.add(f['url'])
            unique.append(f)
    return unique


# ══════════════════════════════════════════════════════════════
#  ONION SEARCH ENGINES
# ══════════════════════════════════════════════════════════════

def _search_ahmia(query, max_results=10):
    """Search Ahmia — clearnet Tor search engine (searches .onion sites)."""
    url = f"https://ahmia.fi/search/?q={quote_plus(query)}"
    try:
        raw_html, status, _ = tor_http_fetch(url, timeout=20)
        if status != 200:
            return []

        results = []
        # Ahmia result pattern: <li class="result"> blocks with <a href="..."> and <p>
        blocks = re.findall(
            r'<li[^>]*class="[^"]*result[^"]*"[^>]*>.*?'
            r'<a[^>]*href="((?:https?://)?[a-z2-7]{16,56}\.onion[^"]*)"[^>]*>(.*?)</a>'
            r'.*?<p[^>]*>(.*?)</p>',
            raw_html, re.DOTALL | re.IGNORECASE
        )
        # Fallback: broader pattern
        if not blocks:
            blocks = re.findall(
                r'<a[^>]*href="([^"]*redirect[^"]*url=(?:https?://)?[a-z2-7]{16,56}\.onion[^"]*)"[^>]*>(.*?)</a>',
                raw_html, re.DOTALL | re.IGNORECASE
            )
            blocks = [(href, title, '') for href, title in blocks]

        if not blocks:
            # Last fallback: find all onion links on the page
            all_onion = re.findall(
                r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                raw_html, re.DOTALL | re.IGNORECASE
            )
            for href, title_html in all_onion:
                # Check if href contains/points to .onion
                onion_match = re.search(r'(https?://[a-z2-7]{16,56}\.onion[^\s"]*)', href, re.IGNORECASE)
                if onion_match:
                    actual_url = onion_match.group(1)
                    title = _decode_entities(_strip_tags(title_html)).strip()
                    if title and len(title) > 3:
                        results.append({'title': title[:200], 'url': actual_url, 'snippet': ''})
                        if len(results) >= max_results:
                            break
            return results

        for href, title_html, snippet_html in blocks[:max_results]:
            # Extract actual onion URL from redirect
            onion_match = re.search(r'(https?://[a-z2-7]{16,56}\.onion[^\s"&]*)', href, re.IGNORECASE)
            actual_url = onion_match.group(1) if onion_match else href
            if not actual_url.startswith('http'):
                actual_url = 'http://' + actual_url

            title = _decode_entities(_strip_tags(title_html)).strip()
            snippet = _decode_entities(_strip_tags(snippet_html)).strip() if snippet_html else ''
            if title:
                results.append({'title': title[:200], 'url': actual_url, 'snippet': snippet[:300]})

        return results
    except Exception as e:
        log.error(f"Ahmia search failed: {e}")
        return []


def _search_torch(query, max_results=10):
    """Search Torch — major Tor search engine."""
    # Torch .onion address (well-known)
    torch_url = "http://xmh57jrknzkhv6y3ls3ubitzfqnkrwxhopf5aygthi7d6rplyvk3noyd.onion/cgi-bin/omega/omega"
    params = f"?P={quote_plus(query)}&DB=1"
    try:
        raw_html, status, _ = tor_http_fetch(torch_url + params, timeout=30)
        if status != 200:
            return []

        results = []
        # Torch result pattern
        blocks = re.findall(
            r'<dt>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<dd>(.*?)</dd>',
            raw_html, re.DOTALL | re.IGNORECASE
        )
        if not blocks:
            # Fallback: any links to .onion sites
            links = re.findall(
                r'<a[^>]*href="(http[^"]*\.onion[^"]*)"[^>]*>(.*?)</a>',
                raw_html, re.DOTALL | re.IGNORECASE
            )
            blocks = [(href, title, '') for href, title in links]

        for href, title_html, snippet_html in blocks[:max_results]:
            title = _decode_entities(_strip_tags(title_html)).strip()
            snippet = _decode_entities(_strip_tags(snippet_html)).strip() if snippet_html else ''
            if title and '.onion' in href:
                results.append({'title': title[:200], 'url': href, 'snippet': snippet[:300]})

        return results
    except Exception as e:
        log.error(f"Torch search failed: {e}")
        return []


def _search_duckduckgo_onion(query, max_results=10):
    """Search DuckDuckGo .onion instance — for onion-specific results."""
    # DDG onion address
    ddg_onion = "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"
    url = f"{ddg_onion}/html/?q={quote_plus(query + ' site:.onion')}"
    try:
        raw_html, status, _ = tor_http_fetch(url, timeout=25)
        if status != 200:
            # Fallback to clearnet DDG with .onion filter
            url = f"https://html.duckduckgo.com/html/"
            raw_html, status, _ = tor_http_fetch(url, headers={'Accept': 'text/html'},
                                                  timeout=20)
            if status != 200:
                return []

        results = []
        blocks = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'class="result__snippet"[^>]*>(.*?)</(?:a|td)',
            raw_html, re.DOTALL | re.IGNORECASE
        )
        for href, title, snippet in blocks[:max_results]:
            actual_url = href
            uddg = re.search(r'uddg=([^&"]+)', href)
            if uddg:
                actual_url = unquote(uddg.group(1))
            if 'duckduckgo.com/y.js' in actual_url:
                continue
            actual_url = actual_url.replace('&amp;', '&')
            title_clean = _decode_entities(_strip_tags(title)).strip()
            snippet_clean = _decode_entities(_strip_tags(snippet)).strip()
            if title_clean and actual_url.startswith('http'):
                results.append({'title': title_clean, 'url': actual_url, 'snippet': snippet_clean[:300]})

        return results
    except Exception as e:
        log.error(f"DDG onion search failed: {e}")
        return []


def _search_haystack(query, max_results=10):
    """Search Haystack — Tor search engine."""
    haystack_url = "http://haystak5njsmn2hqkewecpaxetahtwhsbsa64jom2k22z5afxhnpxfid.onion"
    url = f"{haystack_url}/?q={quote_plus(query)}"
    try:
        raw_html, status, _ = tor_http_fetch(url, timeout=30)
        if status != 200:
            return []

        results = []
        # Extract links to .onion sites
        links = re.findall(
            r'<a[^>]*href="(http[^"]*\.onion[^"]*)"[^>]*>(.*?)</a>',
            raw_html, re.DOTALL | re.IGNORECASE
        )
        seen = set()
        for href, title_html in links:
            if href in seen or haystack_url in href:
                continue
            seen.add(href)
            title = _decode_entities(_strip_tags(title_html)).strip()
            if title and len(title) > 3:
                results.append({'title': title[:200], 'url': href, 'snippet': ''})
            if len(results) >= max_results:
                break

        return results
    except Exception as e:
        log.error(f"Haystack search failed: {e}")
        return []


def tor_search(query, max_results=10):
    """Multi-engine Tor search with fallback chain.
    Ahmia (clearnet gateway) → Torch (.onion) → DDG onion → Haystack
    Returns deduplicated results with titles, URLs, snippets.
    """
    results = _search_ahmia(query, max_results)
    log.info(f"Ahmia returned {len(results)} results for '{query}'")

    if len(results) < max_results // 2:
        torch_results = _search_torch(query, max_results)
        log.info(f"Torch returned {len(torch_results)} results")
        seen_urls = {r['url'] for r in results}
        for tr in torch_results:
            if tr['url'] not in seen_urls:
                results.append(tr)
                seen_urls.add(tr['url'])

    if len(results) < max_results // 2:
        ddg_results = _search_duckduckgo_onion(query, max_results)
        log.info(f"DDG onion returned {len(ddg_results)} results")
        seen_urls = {r['url'] for r in results}
        for dr in ddg_results:
            if dr['url'] not in seen_urls:
                results.append(dr)
                seen_urls.add(dr['url'])

    if len(results) < 3:
        haystack_results = _search_haystack(query, max_results)
        log.info(f"Haystack returned {len(haystack_results)} results")
        seen_urls = {r['url'] for r in results}
        for hr in haystack_results:
            if hr['url'] not in seen_urls:
                results.append(hr)
                seen_urls.add(hr['url'])

    return results[:max_results]


# ══════════════════════════════════════════════════════════════
#  ONION DISCOVERY (directory scraping)
# ══════════════════════════════════════════════════════════════

# Well-known onion directories and link lists
ONION_DIRECTORIES = [
    "http://s4k4ceiapber4xby.onion/",  # The Hidden Wiki (v2 — may be down)
    "http://zqktlwiuavvvqqt4ybvgvi7tyo4hjl5xgfuvpdf6otjiycgwqbym2qad.onion/wiki/",  # Hidden Wiki v3
    "http://donionsixbjtiohce24abfgsffo2l4tk26qx464za23vl3hbg4ncwqbqd.onion/",  # Dark.fail
    "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/",  # Ahmia onion
]


def discover_onions(query="", max_results=20):
    """Discover .onion addresses from directories + search engines.
    Combines search results with directory scraping for maximum coverage.
    """
    all_onions = {}  # domain → {url, title, source}

    # Step 1: Search via Tor search engines
    if query:
        search_results = tor_search(query, max_results=max_results)
        for r in search_results:
            parsed = urlparse(r['url'])
            if parsed.hostname and parsed.hostname.endswith('.onion'):
                domain = parsed.hostname
                if domain not in all_onions:
                    all_onions[domain] = {
                        'url': r['url'],
                        'title': r['title'],
                        'snippet': r.get('snippet', ''),
                        'source': 'search',
                    }

    # Step 2: Scrape onion directories for links
    for dir_url in ONION_DIRECTORIES[:2]:  # Limit to avoid slowness
        try:
            raw_html, status, _ = tor_http_fetch(dir_url, timeout=30)
            if status == 200 and raw_html:
                links = extract_onion_links(raw_html, dir_url)
                for link in links:
                    parsed = urlparse(link)
                    domain = parsed.hostname
                    if domain and domain not in all_onions:
                        # Try to find title near the link
                        title = ""
                        title_match = re.search(
                            rf'[>]([^<]{{3,80}})[^<]*<a[^>]*href=["\'][^"\']*{re.escape(domain)}',
                            raw_html, re.IGNORECASE
                        )
                        if title_match:
                            title = _decode_entities(title_match.group(1)).strip()
                        all_onions[domain] = {
                            'url': link,
                            'title': title or domain,
                            'snippet': '',
                            'source': 'directory',
                        }
        except Exception as e:
            log.warning(f"Directory scrape failed for {dir_url}: {e}")

    # Format output
    results = list(all_onions.values())[:max_results]
    lines = [f"# Onion Discovery Results\n"]
    lines.append(f"Found {len(results)} unique .onion services" +
                 (f" for '{query}'" if query else "") + "\n")

    for i, r in enumerate(results, 1):
        lines.append(f"\n## {i}. {r['title']}")
        lines.append(f"URL: {r['url']}")
        if r['snippet']:
            lines.append(f"Description: {r['snippet']}")
        lines.append(f"Source: {r['source']}")

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════
#  CRAWL ENGINE (.onion spider)
# ══════════════════════════════════════════════════════════════

def crawl_onion(start_url, max_depth=2, max_pages=30, pattern=None):
    """BFS spider for .onion sites with deduplication and file detection.
    Extracts links, detects files, and builds site map.
    """
    if not _ensure_tor():
        return "ERROR: Tor not available. Install with: brew install tor"

    dedup = DeduplicationEngine(threshold=0.7)
    visited = set()
    queue = [(start_url, 0)]  # (url, depth)
    results = []
    all_files = []
    all_onion_links = set()
    pages_fetched = 0
    pattern_re = re.compile(pattern, re.IGNORECASE) if pattern else None

    start_time = time.time()
    # Rotate circuit before crawl
    _rotate_circuit()

    while queue and pages_fetched < max_pages:
        # Check time budget
        if time.time() - start_time > RESEARCH_TIME_BUDGET:
            log.info(f"Crawl time budget exceeded after {pages_fetched} pages")
            break

        url, depth = queue.pop(0)

        # Normalize URL
        parsed = urlparse(url)
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if normalized in visited:
            continue
        if depth > max_depth:
            continue
        if pattern_re and not pattern_re.search(url):
            continue

        visited.add(normalized)

        try:
            fetch_result = smart_tor_fetch(url, max_chars=MAX_PAGE_CHARS)
            if fetch_result['tier'] == 'error':
                continue

            pages_fetched += 1
            md = fetch_result['markdown']

            # Check for duplicate content
            is_dup, dup_url = dedup.is_duplicate(url, md)
            if is_dup:
                log.info(f"Duplicate content: {url} matches {dup_url}")
                results.append({
                    'url': url, 'depth': depth, 'status': 'duplicate',
                    'duplicate_of': dup_url, 'chars': len(md),
                })
                continue

            dedup.add(url, md)

            # Extract files
            try:
                raw_html, _, _ = tor_http_fetch(url, timeout=15)
                files = extract_file_links(raw_html, url)
                all_files.extend(files)
            except Exception:
                pass

            # Extract onion links for queue
            onion_links = extract_onion_links(md + (raw_html if 'raw_html' in dir() else ''), url)
            for link in onion_links:
                all_onion_links.add(link)
                link_parsed = urlparse(link)
                link_norm = f"{link_parsed.scheme}://{link_parsed.netloc}{link_parsed.path}"
                if link_norm not in visited and depth + 1 <= max_depth:
                    queue.append((link, depth + 1))

            results.append({
                'url': url, 'depth': depth, 'status': 'success',
                'chars': len(md), 'tier': fetch_result['tier'],
                'title': md.split('\n')[0][:100] if md else '',
            })

            # Rotate circuit every 10 pages
            if pages_fetched % 10 == 0:
                _rotate_circuit()

        except Exception as e:
            log.error(f"Crawl fetch error: {url}: {e}")
            results.append({'url': url, 'depth': depth, 'status': 'error', 'error': str(e)})

    elapsed = time.time() - start_time

    # Build report
    lines = [f"# Tor Crawl Report\n"]
    lines.append(f"Start: {start_url}")
    lines.append(f"Pages fetched: {pages_fetched} | Depth: {max_depth} | Time: {elapsed:.1f}s\n")

    # Pages summary
    lines.append("## Pages Crawled\n")
    for r in results:
        status_icon = "+" if r['status'] == 'success' else "~" if r['status'] == 'duplicate' else "x"
        title = r.get('title', '')[:60]
        lines.append(f"  [{status_icon}] d={r['depth']} {r['url'][:80]}" +
                     (f" — {title}" if title else ""))

    # Files discovered
    if all_files:
        lines.append(f"\n## Files Discovered ({len(all_files)})\n")
        by_category = defaultdict(list)
        for f in all_files:
            by_category[f['category']].append(f)
        for cat, files in sorted(by_category.items()):
            lines.append(f"\n### {cat.upper()} ({len(files)} files)")
            for f in files[:20]:
                lines.append(f"  - {f['filename']} ({f['extension']}) — {f['url'][:80]}")

    # Discovered onion links
    if all_onion_links:
        external = [l for l in all_onion_links
                    if urlparse(l).hostname != urlparse(start_url).hostname]
        if external:
            lines.append(f"\n## External Onion Links ({len(external)})\n")
            for link in list(external)[:30]:
                lines.append(f"  - {link}")

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════
#  DEEP RESEARCH ENGINE (Tor-adapted hierarchical research)
# ══════════════════════════════════════════════════════════════

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
    'up', 'us', 'well', 'while', 'as', 'much', 'many', 'like',
})


def _extract_keywords(text, top_n=8):
    """Extract top keywords from text."""
    words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    freq = defaultdict(int)
    for w in words:
        if w not in _STOPWORDS:
            freq[w] += 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:top_n]]


def _word_overlap(text1, text2):
    """Compute word overlap ratio between two texts."""
    words1 = set(re.findall(r'[a-z]{3,}', text1.lower())) - _STOPWORDS
    words2 = set(re.findall(r'[a-z]{3,}', text2.lower())) - _STOPWORDS
    if not words1 or not words2:
        return 0.0
    return len(words1 & words2) / max(len(words1), len(words2))


def _extract_facts(text, keywords, max_facts=15):
    """Extract keyword-relevant facts (sentences) from text."""
    sentences = re.split(r'[.!?\n]', text)
    scored = []
    for s in sentences:
        s = s.strip()
        if len(s) < 20 or len(s) > 500:
            continue
        score = sum(1 for kw in keywords if kw in s.lower())
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    # Deduplicate
    facts = []
    for _, fact in scored:
        if not any(_word_overlap(fact, f) > 0.6 for f in facts):
            facts.append(fact)
        if len(facts) >= max_facts:
            break
    return facts


# Tor-specific topic templates for deep research
_TOR_TOPIC_TEMPLATES = {
    'security': {
        'triggers': ['security', 'vulnerability', 'exploit', 'CVE', 'breach', 'hack', 'malware',
                     'ransomware', 'threat', 'intelligence', 'cybersecurity'],
        'branches': [
            ('Threat Landscape', ['threat', 'attack', 'vector', 'APT', 'campaign', 'actor']),
            ('Vulnerabilities & Exploits', ['vulnerability', 'exploit', 'CVE', 'zero-day', 'patch']),
            ('Data Breaches & Leaks', ['breach', 'leak', 'data', 'dump', 'database', 'credentials']),
            ('Malware & Ransomware', ['malware', 'ransomware', 'trojan', 'botnet', 'C2']),
            ('Defense & Mitigation', ['defense', 'mitigation', 'protection', 'detection', 'response']),
        ]
    },
    'marketplace': {
        'triggers': ['market', 'marketplace', 'vendor', 'shop', 'store', 'listing'],
        'branches': [
            ('Active Marketplaces', ['marketplace', 'active', 'running', 'online', 'new']),
            ('Market History', ['history', 'shutdown', 'seized', 'exit scam', 'timeline']),
            ('Vendor Ecosystem', ['vendor', 'seller', 'reputation', 'feedback', 'review']),
            ('Market Technology', ['escrow', 'multisig', 'PGP', 'wallet', 'monero']),
        ]
    },
    'privacy': {
        'triggers': ['privacy', 'anonymous', 'anonymity', 'encryption', 'VPN', 'Tor',
                     'OPSEC', 'operational security'],
        'branches': [
            ('Privacy Tools', ['tool', 'software', 'app', 'service', 'platform']),
            ('Encryption & Protocols', ['encryption', 'protocol', 'E2E', 'PGP', 'signal']),
            ('Anonymity Networks', ['Tor', 'I2P', 'Freenet', 'network', 'onion']),
            ('OPSEC Practices', ['OPSEC', 'practice', 'guide', 'best', 'secure']),
            ('Legal & Regulatory', ['legal', 'law', 'regulation', 'warrant', 'court']),
        ]
    },
    'default': {
        'triggers': [],
        'branches': [
            ('Overview & Background', ['overview', 'background', 'introduction', 'about']),
            ('Key Findings', ['finding', 'result', 'discovery', 'analysis', 'data']),
            ('Technical Details', ['technical', 'how', 'architecture', 'protocol', 'method']),
            ('Current State', ['current', 'latest', 'recent', 'active', 'status']),
            ('Resources & Links', ['resource', 'link', 'reference', 'source', 'tool']),
        ]
    },
}


def _select_tor_template(query):
    """Select the best topic template for the query."""
    query_lower = query.lower()
    best_template = 'default'
    best_score = 0

    for name, template in _TOR_TOPIC_TEMPLATES.items():
        if name == 'default':
            continue
        score = sum(1 for t in template['triggers'] if t in query_lower)
        if score > best_score:
            best_score = score
            best_template = name

    return _TOR_TOPIC_TEMPLATES[best_template]


def deep_research_tor(query, max_pages=30):
    """Multi-branch hierarchical research across Tor network.
    Similar to headless-browser deep_research but Tor-adapted.
    """
    if not _ensure_tor():
        return "ERROR: Tor not available. Install with: brew install tor"

    start_time = time.time()
    template = _select_tor_template(query)
    branches = template['branches'][:MAX_BRANCHES]
    keywords = _extract_keywords(query)

    log.info(f"Deep research: '{query}' — {len(branches)} branches, keywords: {keywords}")

    # Phase 1: Seed search
    initial_results = tor_search(query, max_results=15)
    log.info(f"Seed search returned {len(initial_results)} results")

    # Phase 2: Branch research
    branch_results = []
    total_pages = 0
    pages_per_branch = max(3, max_pages // len(branches))
    dedup = DeduplicationEngine()

    for branch_name, branch_keywords in branches:
        if time.time() - start_time > RESEARCH_TIME_BUDGET:
            log.info(f"Time budget exceeded, stopping at branch '{branch_name}'")
            break

        all_facts = []
        all_sources = []
        pages_fetched = 0

        # Build branch-specific search queries
        branch_query = f"{query} {branch_name}"
        search_results = tor_search(branch_query, max_results=8)
        # Add seed results
        search_results = initial_results[:3] + search_results

        for r in search_results:
            if pages_fetched >= pages_per_branch:
                break
            if time.time() - start_time > RESEARCH_TIME_BUDGET:
                break

            url = r['url']
            try:
                fetch_result = smart_tor_fetch(url, max_chars=MAX_PAGE_CHARS)
                if fetch_result['tier'] == 'error':
                    continue

                md = fetch_result['markdown']
                pages_fetched += 1
                total_pages += 1

                # Check for duplicates
                is_dup, _ = dedup.is_duplicate(url, md)
                if is_dup:
                    continue
                dedup.add(url, md)

                # Store source
                all_sources.append({
                    'url': url,
                    'title': r.get('title', '')[:80],
                    'snippet': r.get('snippet', ''),
                })

                # Extract facts
                combined_kw = keywords + branch_keywords
                facts = _extract_facts(md, combined_kw)
                all_facts.extend(facts)

            except Exception as e:
                log.error(f"Branch '{branch_name}' fetch failed: {e}")

        # Compute coverage score
        coverage = min(1.0, len(all_facts) / 10) * 0.5 + min(1.0, len(all_sources) / 5) * 0.5

        branch_results.append({
            'name': branch_name,
            'facts': all_facts,
            'sources': all_sources,
            'pages_fetched': pages_fetched,
            'coverage_score': coverage,
        })

        # Rotate circuit between branches
        _rotate_circuit()

    elapsed = time.time() - start_time

    # Phase 3: Build report
    return _build_tor_research_report(query, branch_results, elapsed, total_pages)


def _build_tor_research_report(query, branch_results, elapsed, total_pages):
    """Build structured hierarchical research report for Tor research."""
    lines = []
    lines.append(f"# Tor Deep Research: {query}")
    lines.append(f"\nResearched {len(branch_results)} sub-topics across {total_pages} pages in {elapsed:.0f}s")
    lines.append(f"Network: Tor | All traffic routed through SOCKS5 proxy\n")

    # Executive Summary
    lines.append("## Executive Summary\n")
    all_facts_flat = []
    for br in branch_results:
        all_facts_flat.extend(br.get('facts', [])[:3])
    # Deduplicate
    summary_facts = []
    for fact in all_facts_flat:
        if not any(_word_overlap(fact, s) > 0.5 for s in summary_facts):
            summary_facts.append(fact)
        if len(summary_facts) >= 8:
            break
    if summary_facts:
        for f in summary_facts:
            lines.append(f"- {f}")
    else:
        lines.append("No significant findings extracted.")
    lines.append("")

    # Branch Reports
    for br in branch_results:
        name = br['name']
        facts = br.get('facts', [])
        sources = br.get('sources', [])
        coverage = br.get('coverage_score', 0)

        lines.append(f"\n## {name}")
        lines.append(f"Coverage: {coverage:.1%} | Sources: {len(sources)} | "
                     f"Facts: {len(facts)} | Pages: {br.get('pages_fetched', 0)}")
        lines.append("")

        for fact in facts[:10]:
            lines.append(f"- {fact}")
        if len(facts) > 10:
            lines.append(f"  ... and {len(facts) - 10} more facts")

        if sources:
            lines.append(f"\nSources:")
            for s in sources[:6]:
                title = s.get('title', 'Untitled')[:80]
                lines.append(f"  - [{title}]({s['url']})")
        lines.append("")

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════
#  ACTION HANDLERS
# ══════════════════════════════════════════════════════════════

def handle_action(data):
    """Main action dispatcher — mirrors headless-browser.py pattern."""
    action = data.get('action', 'search')

    # Param aliasing
    if 'text' in data and 'query' not in data:
        data['query'] = data['text']

    log.info(f"Action: {action} | Data: {json.dumps({k: str(v)[:100] for k, v in data.items()})}")

    try:
        # ── STATUS ──
        if action == 'status':
            return _handle_status()

        # Ensure Tor is running for all other actions
        if not _ensure_tor():
            deps, issues = _check_dependencies()
            msg = "# Tor Not Available\n\n"
            if issues:
                msg += "Missing dependencies:\n"
                for issue in issues:
                    msg += f"- {issue}\n"
                msg += "\nQuick setup:\n"
                msg += "```\nbrew install tor\npip3 install stem PySocks\n```\n"
                msg += "Then start Tor: `tor` or `brew services start tor`"
            return msg

        # ── SEARCH ──
        if action == 'search':
            query = data.get('query', '')
            if not query:
                return "Error: 'query' parameter required for search action"
            max_results = min(data.get('max_results', 10), MAX_SEARCH_RESULTS)
            results = tor_search(query, max_results)
            if not results:
                return f"No results found for '{query}' across Tor search engines"
            lines = [f"# Tor Search: {query}\n", f"Found {len(results)} results\n"]
            for i, r in enumerate(results, 1):
                lines.append(f"## {i}. {r['title']}")
                lines.append(f"URL: {r['url']}")
                if r['snippet']:
                    lines.append(f"{r['snippet']}")
                lines.append("")
            return '\n'.join(lines)

        # ── FETCH ──
        elif action == 'fetch':
            url = data.get('url', '')
            if not url:
                return "Error: 'url' parameter required for fetch action"
            max_chars = data.get('max_chars', MAX_PAGE_CHARS)
            result = smart_tor_fetch(url, max_chars)
            return result['markdown']

        # ── DEEP RESEARCH ──
        elif action == 'deep_research':
            query = data.get('query', '')
            if not query:
                return "Error: 'query' parameter required for deep_research action"
            max_pages = min(data.get('max_pages', 30), MAX_DEEP_RESEARCH_PAGES)
            return deep_research_tor(query, max_pages)

        # ── CRAWL ──
        elif action == 'crawl':
            url = data.get('url', '')
            if not url:
                return "Error: 'url' parameter required for crawl action"
            max_depth = min(data.get('max_depth', 2), MAX_CRAWL_DEPTH)
            max_pages = min(data.get('max_pages', 30), MAX_CRAWL_PAGES)
            pattern = data.get('pattern', None)
            return crawl_onion(url, max_depth, max_pages, pattern)

        # ── DISCOVER ──
        elif action == 'discover':
            query = data.get('query', '')
            max_results = min(data.get('max_results', 20), 50)
            return discover_onions(query, max_results)

        # ── EXTRACT FILES ──
        elif action == 'extract_files':
            url = data.get('url', '')
            urls_str = data.get('urls', '')
            urls = []
            if url:
                urls.append(url)
            if urls_str:
                urls.extend([u.strip() for u in urls_str.split(',') if u.strip()])
            if not urls:
                return "Error: 'url' or 'urls' parameter required for extract_files action"

            all_files = []
            for u in urls:
                try:
                    raw_html, status, final_url = tor_http_fetch(u)
                    if status == 200:
                        files = extract_file_links(raw_html, final_url)
                        all_files.extend(files)
                except Exception as e:
                    log.error(f"extract_files failed for {u}: {e}")

            if not all_files:
                return f"No downloadable files found on {len(urls)} page(s)"

            # Group by category
            by_category = defaultdict(list)
            for f in all_files:
                by_category[f['category']].append(f)

            lines = [f"# Files Discovered\n", f"Found {len(all_files)} files across {len(urls)} page(s)\n"]
            for cat, files in sorted(by_category.items()):
                lines.append(f"\n## {cat.upper()} ({len(files)} files)\n")
                for f in files:
                    lines.append(f"- **{f['filename']}** ({f['extension']})")
                    lines.append(f"  URL: {f['url']}")
            return '\n'.join(lines)

        # ── EXTRACT (wallets, PGP, emails) ──
        elif action == 'extract':
            url = data.get('url', '')
            if not url:
                return "Error: 'url' parameter required for extract action"
            extract_type = data.get('extract_type', 'all')

            try:
                raw_html, status, final_url = tor_http_fetch(url)
                if status != 200:
                    return f"HTTP {status} error fetching {url}"

                text = _strip_tags(raw_html)
                lines = [f"# Extraction Results: {url}\n"]

                if extract_type in ('all', 'emails'):
                    emails = extract_emails(text)
                    if emails:
                        lines.append(f"\n## Emails ({len(emails)})\n")
                        for e in emails:
                            lines.append(f"- {e}")

                if extract_type in ('all', 'wallets'):
                    wallets = extract_crypto_wallets(text)
                    if wallets:
                        lines.append(f"\n## Crypto Wallets\n")
                        for crypto, addrs in wallets.items():
                            lines.append(f"\n### {crypto.upper()} ({len(addrs)})")
                            for addr in addrs:
                                lines.append(f"- `{addr}`")

                if extract_type in ('all', 'pgp'):
                    keys = extract_pgp_keys(raw_html)
                    if keys:
                        lines.append(f"\n## PGP Keys ({len(keys)})\n")
                        for key in keys:
                            lines.append(f"```\n{key[:500]}...\n```")

                # Also extract onion links
                if extract_type == 'all':
                    onion_links = extract_onion_links(raw_html, url)
                    if onion_links:
                        lines.append(f"\n## Onion Links ({len(onion_links)})\n")
                        for link in onion_links[:20]:
                            lines.append(f"- {link}")

                if len(lines) <= 1:
                    lines.append("\nNo extractable data found on this page.")

                return '\n'.join(lines)
            except Exception as e:
                return f"Error extracting from {url}: {e}"

        else:
            return (f"Unknown action: {action}. "
                    f"Available: search, fetch, deep_research, crawl, discover, "
                    f"extract_files, extract, status")

    except Exception as e:
        log.error(f"Action {action} error: {e}", exc_info=True)
        return f"Error in {action}: {e}"


def _handle_status():
    """Handle the status action — check Tor connectivity and circuit info."""
    lines = ["# AetherTor Status\n"]

    # Check dependencies
    deps, issues = _check_dependencies()
    lines.append("## Dependencies\n")
    for name, ok in deps.items():
        icon = "OK" if ok else "MISSING"
        lines.append(f"- {name}: {icon}")
    if issues:
        lines.append("\n### Setup Required:")
        for issue in issues:
            lines.append(f"- {issue}")

    # Check Tor connectivity
    lines.append(f"\n## Tor Daemon\n")
    if _is_tor_running():
        lines.append(f"- SOCKS proxy: {TOR_SOCKS_HOST}:{TOR_SOCKS_PORT} — CONNECTED")

        # Test actual Tor connectivity
        try:
            raw, status, _ = tor_http_fetch("https://check.torproject.org/api/ip", timeout=15)
            try:
                ip_data = json.loads(raw)
                lines.append(f"- Tor check: {'USING TOR' if ip_data.get('IsTor') else 'NOT TOR'}")
                lines.append(f"- Exit IP: {ip_data.get('IP', 'unknown')}")
            except json.JSONDecodeError:
                lines.append(f"- Tor check: connected (API response non-JSON)")
        except Exception as e:
            lines.append(f"- Tor check: connectivity test failed ({e})")

        # Circuit info
        circuit_info = _get_circuit_info()
        if 'error' not in circuit_info:
            lines.append(f"\n## Circuit Info\n")
            lines.append(f"- Version: {circuit_info.get('version', 'unknown')}")
            lines.append(f"- Uptime: {circuit_info.get('uptime', 'unknown')}s")
            lines.append(f"- Traffic read: {circuit_info.get('bytes_read', 'unknown')} bytes")
            lines.append(f"- Traffic written: {circuit_info.get('bytes_written', 'unknown')} bytes")
            circuits = circuit_info.get('circuits', [])
            if circuits:
                lines.append(f"- Active circuits: {len(circuits)}")
                for c in circuits[:5]:
                    lines.append(f"  [{c['id']}] {c['purpose']}: {c['path']}")
        else:
            lines.append(f"\n## Circuit Info\n")
            lines.append(f"- Control port not available: {circuit_info['error']}")
    else:
        lines.append(f"- SOCKS proxy: NOT RUNNING")
        lines.append(f"\nTo start Tor:")
        lines.append(f"```")
        lines.append(f"brew install tor")
        lines.append(f"tor")
        lines.append(f"```")

    lines.append(f"\n## Configuration\n")
    lines.append(f"- SOCKS: {TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}")
    lines.append(f"- Control: {TOR_SOCKS_HOST}:{TOR_CONTROL_PORT}")
    lines.append(f"- Data dir: {TOR_DATA_DIR}")
    lines.append(f"- Cache dir: {CACHE_DIR}")
    lines.append(f"- Cache TTL: {CACHE_TTL}s")

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════
#  SERVER MODE (persistent stdin/stdout JSON, like edge.py)
# ══════════════════════════════════════════════════════════════

def server_loop():
    """Persistent server mode: read JSON from stdin, write results to stdout."""
    log.info(f"AetherTor {SCRIPT_VERSION} server started")

    # Auto-install deps on server start
    _auto_install_deps()

    print(json.dumps({
        "status": "ready",
        "version": SCRIPT_VERSION,
        "tool": "tor",
    }), flush=True)

    while True:
        try:
            line = sys.stdin.readline()
            if not line:  # EOF
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
        _pw_request_queue.put(None)
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
    # Note: we do NOT stop the system Tor daemon on cleanup
    # Only stop if we started it ourselves
    if _tor_process:
        _stop_tor()

import atexit
atexit.register(cleanup)


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="AetherTor — Headless Tor Browser Engine")
    parser.add_argument('--json', help='JSON args for one-shot execution')
    parser.add_argument('--server', action='store_true', help='Run in server mode (stdin/stdout JSON)')
    parser.add_argument('--status', action='store_true', help='Check Tor status')
    parser.add_argument('--install', action='store_true', help='Install missing dependencies')
    args = parser.parse_args()

    if args.install:
        print("Installing dependencies...")
        installed = _auto_install_deps()
        if installed:
            print(f"Installed: {', '.join(installed)}")
        else:
            print("All Python dependencies already installed.")
        deps, issues = _check_dependencies()
        for name, ok in deps.items():
            print(f"  {'OK' if ok else 'MISSING'}: {name}")
        if issues:
            print("\nRemaining issues:")
            for i in issues:
                print(f"  - {i}")
    elif args.status:
        print(handle_action({"action": "status"}))
    elif args.server:
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
        print(f"AetherTor {SCRIPT_VERSION}")
        print("Usage: --server (persistent) or --json '{...}' (one-shot)")
        print("       --status (check Tor) or --install (install deps)")
        print("Actions: search, fetch, deep_research, crawl, discover, extract_files, extract, status")
