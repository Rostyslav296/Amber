#!/usr/bin/env python3
"""
safari.py v16.0 — Agentic Safari Automation Engine

Full browser control for autonomous web research and microtask completion:
- Accessibility-tree-style page extraction with numbered element refs + coordinates
- Web search with structured result extraction (Google/DuckDuckGo)
- Enhanced content extraction with tables, lists, metadata for research
- React/Vue/Angular compatible form filling with human-like keystroke typing
- iframe traversal for embedded surveys
- Shadow DOM traversal for web components
- Video playback control (play, speed up, skip, wait for completion)
- Keyboard simulation with modifier combos (cmd+a, cmd+c, cmd+shift+r, etc.)
- Smart element targeting (#N refs, css: selectors, text search)
- Coordinate-based click/double-click/right-click via CoreGraphics
- Radio/checkbox/dropdown handling including custom dropdowns
- Scroll to text, infinite scroll for lazy-loading content
- Tab management with multi-tab research orchestration
- File upload via macOS native file dialog automation
- Download monitoring in ~/Downloads
- Find in page (Cmd+F) with text highlighting
- Select all and copy page text to clipboard
- Network-idle + DOM-settle + SPA navigation wait primitives
- JS dialog interception (alert/confirm/prompt → non-blocking)
- Form validation error detection
- Cookie and localStorage management
- Human-like timing to avoid bot detection
- SPA navigation detection (pushState/popState hooks)
- Auto-iframe detection: when main page is sparse, auto-discovers and reports iframe content
- Overlay/cookie-banner auto-dismissal before snapshot
- Snapshot fallback chain: JS → simple extraction → accessibility API → OCR
- Drag and drop for sliders and custom UI elements
- Multi-window/popup management
- Vision OCR fallback using macOS native framework
- Retry wrapper for transient failures
- Element occlusion detection (covered by overlays)
"""

import subprocess as sp
import sys, json, argparse, time, os, logging, base64, tempfile, re, random
import urllib.parse
from typing import Dict, Any, Optional

LOG_FILE = os.path.expanduser("~/.agentf_safari.log")
SCREENSHOT_DIR = os.path.expanduser("~/Developer/llm/safari_agent")
DOWNLOADS_DIR = os.path.expanduser("~/Downloads")
SCRIPT_VERSION = "v16.0_agentic"

logging.basicConfig(
    level=logging.DEBUG, filename=LOG_FILE, filemode='a',
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("safari")

TOOL_METADATA = {
    "name": "safari",
    "description": """Safari browser automation — your eyes and hands on the web.

ACTIONS (pick one):
  new_tab    → Open URL in new tab. Args: url
  snapshot   → See everything on the page (text, links, buttons, forms, videos)
  read       → Extract page content for summarization (headings, text, links, tables)
  click      → Click element. Args: text="#3" or text="button text". value="double"/"right" for double/right click
  click_at   → Click at viewport coordinates. Args: text="350,200"
  fill       → Type into field. Args: text="#N or field name", value="text to type"
  select     → Pick dropdown/radio option. Args: text="#N", value="option text"
  scroll     → Scroll page. Args: value="down"/"up"/"bottom"/"top". text="search text" scrolls until text found
  keys       → Press keys or combos. Args: value="Enter"/"Tab" or "cmd+a"/"cmd+c"/"cmd+shift+r"/"ctrl+click"
  wait       → Wait for condition. Args: text="text", value="appear"/"disappear"/"stable"/"network"
  search     → Search the web. Args: text="query" — returns results with titles, URLs, snippets
  video      → Control video. Args: value="play"/"speed"/"skip"/"status"/"wait"
  tabs       → Manage tabs. Args: value="list"/"close"/"next"/"prev"/"switch 3"/"new"
  back       → Go back one page
  forward    → Go forward one page
  iframe     → Enter/exit iframe. Args: value="enter 0"/"exit"/"list"
  hover      → Hover over element. Args: text="#N or text"
  upload     → Upload file to input. Args: text="#N or selector", value="/path/to/file"
  download   → Check recent downloads or download URL. Args: url (optional)
  find       → Find text on page (Cmd+F highlight). Args: text="search text"
  select_text → Select all page text and copy to clipboard. Returns clipboard contents
  cookies    → Manage cookies/storage. Args: value="get"/"set"/"storage"/"clear", text="name", script="val"
  run_js     → Run custom JavaScript. Args: script="code"
  execute    → Run raw AppleScript. Args: script="code"
  screenshot → Save screenshot to disk
  drag       → Drag from element to element. Args: text="#N or x,y" (source), value="#M or x,y" (dest)
  window     → Manage Safari windows/popups. Args: value="list"/"switch N"/"popup"/"main"/"close"
  dismiss    → Dismiss cookie banners, popups, overlays blocking the page

SNAPSHOT OUTPUT:
  Elements show as [N] ... @x,y — use #N to click/fill/select them.
  The @x,y are viewport coordinates for coordinate clicking.
  === FORM STATUS === shows validation errors on forms.
  === DIALOG === shows intercepted JS alerts/confirms.

WORKFLOW: new_tab → snapshot → (click/fill/select) → snapshot → repeat
Always snapshot after navigation to see the page.

RESEARCH WORKFLOW: search "topic" → open interesting result URLs in new tabs → read each tab → synthesize
For research tasks, use search to find relevant pages, open several in tabs, read each one with the read action, then compile findings with links.

TIPS:
- If #N gives NOT_FOUND, the page changed. Snapshot to get fresh refs.
- If JS click doesn't work, try click_at with the @x,y coords.
- Check FORM STATUS for why a form won't submit.
- Scroll down if you don't see expected elements — the page may be longer.
- For custom dropdowns, click the trigger first, then snapshot to see options.
- Use 'read' instead of 'snapshot' when you need to summarize or extract content from articles/pages.
- Use 'snapshot' when you need to interact with elements (click, fill, select).
- Use 'search' to find information — it returns structured results you can open.
- Use keys combos like "cmd+a" (select all), "cmd+c" (copy), "cmd+f" (find), "cmd+t" (new tab).
- For double-click, use click with value="double". For right-click, use value="right".
- For file uploads, use upload with the file input ref and file path.
- Use scroll with text="keyword" to auto-scroll until that text is found on the page.
- If snapshot is empty/sparse, the content may be in an iframe — use 'iframe list' then 'iframe enter 0'.
- Auto-iframe detection will try to find survey iframes automatically if main page is sparse.
- Cookie banners/overlays are auto-dismissed on each snapshot. Use 'dismiss' explicitly if needed.
- Use 'drag' for sliders, rating widgets, and sortable elements.
- Use 'window popup' to switch to a newly opened popup window.
- For surveys: read the Q: text, look at radio/checkbox options, select answers, then click Next/Continue.

EXAMPLES:
  {"tool":"safari","args":{"action":"search","text":"best duck breeds for pets"}}
  {"tool":"safari","args":{"action":"new_tab","url":"https://example.com"}}
  {"tool":"safari","args":{"action":"snapshot"}}
  {"tool":"safari","args":{"action":"read"}}
  {"tool":"safari","args":{"action":"click","text":"#5"}}
  {"tool":"safari","args":{"action":"click","text":"#5","value":"double"}}
  {"tool":"safari","args":{"action":"click_at","text":"350,200"}}
  {"tool":"safari","args":{"action":"fill","text":"#8","value":"John Smith"}}
  {"tool":"safari","args":{"action":"select","text":"#12","value":"25-34"}}
  {"tool":"safari","args":{"action":"scroll","value":"down"}}
  {"tool":"safari","args":{"action":"scroll","value":"down","text":"pricing"}}
  {"tool":"safari","args":{"action":"keys","value":"cmd+a"}}
  {"tool":"safari","args":{"action":"keys","value":"Enter"}}
  {"tool":"safari","args":{"action":"upload","text":"#3","value":"~/Documents/resume.pdf"}}
  {"tool":"safari","args":{"action":"download"}}
  {"tool":"safari","args":{"action":"find","text":"pricing"}}
  {"tool":"safari","args":{"action":"select_text"}}
  {"tool":"safari","args":{"action":"video","value":"speed"}}
  {"tool":"safari","args":{"action":"cookies","value":"get"}}
  {"tool":"safari","args":{"action":"wait","text":"loading","value":"disappear"}}""",
    "priority": 999,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "enum": [
                    "new_tab", "browse", "snapshot", "read", "click", "click_at",
                    "fill", "select", "scroll", "keys", "wait", "search", "video",
                    "tabs", "back", "forward", "iframe", "hover", "upload",
                    "download", "find", "select_text", "cookies", "run_js",
                    "execute", "screenshot", "drag", "window", "dismiss"
                ]
            },
            "url": {"type": "string", "description": "URL for new_tab/browse/download"},
            "text": {"type": "string", "description": "Element ref (#N), search query, text to match, or field name"},
            "value": {"type": "string", "description": "Value for fill/select, direction for scroll, action for video/tabs/wait/cookies, click type (double/right), file path for upload"},
            "script": {"type": "string", "description": "JS code for run_js, AppleScript for execute, value for cookie set"}
        },
        "required": ["action"]
    }
}


class SafariAgent:
    def __init__(self):
        self._iframe_idx: Optional[int] = None
        self._noscript_refs: Dict[int, Dict[str, str]] = {}  # #N -> {text, tag, href, ...}
        self._js_available: Optional[bool] = None  # cached JS availability test
        self._js_enable_attempted: bool = False  # prevent repeated enable attempts
        self._noscript_fill_index: int = 0  # tracks Tab position for sequential fills
        self._last_fill_snapshot_id: Optional[str] = None  # tracks when to reset fill index
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    # ─── Low-level execution ─────────────────────────────────────

    def _osa(self, script: str, timeout: int = 45) -> str:
        try:
            r = sp.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0 and r.stderr:
                log.warning(f"osascript stderr: {r.stderr.strip()}")
            return r.stdout.strip()
        except sp.TimeoutExpired:
            log.error(f"osascript timeout ({timeout}s)")
            return "ERROR:TIMEOUT"
        except Exception as e:
            log.error(f"osascript error: {e}")
            return f"ERROR:{e}"

    def _js(self, code: str, timeout: int = 30) -> str:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.js', delete=False, encoding='utf-8'
        ) as f:
            f.write(code)
            path = f.name
        try:
            return self._osa(
                f'set js to read POSIX file "{path}" as «class utf8»\n'
                f'tell application "Safari" to do JavaScript js in current tab of front window',
                timeout
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _js_ctx(self, code: str, timeout: int = 30) -> str:
        if self._iframe_idx is not None:
            wrapped = (
                "(function() {\n"
                f"  var __f = document.querySelectorAll('iframe')[{self._iframe_idx}];\n"
                f"  if (!__f) return 'ERROR: iframe[{self._iframe_idx}] not found';\n"
                "  if (!__f.contentDocument) return 'ERROR: iframe cross-origin';\n"
                "  return (function(document, window) {\n"
                f"    {code}\n"
                "  })(__f.contentDocument, __f.contentWindow);\n"
                "})()"
            )
            return self._js(wrapped, timeout)
        return self._js(code, timeout)

    # ─── JS availability check & non-JS fallbacks ──────────────

    def _test_js(self) -> bool:
        """Quick test if Safari allows JavaScript execution from Apple Events."""
        result = self._js("'JS_OK'")
        return result == "JS_OK"

    def _auto_enable_js(self) -> bool:
        """Attempt to enable 'Allow JavaScript from Apple Events' in Safari.

        Strategy:
        1. Set the defaults key directly (fastest, most reliable)
        2. Ensure the Develop menu is enabled
        3. Test if JS now works
        4. If enabled, reload the current page to exit any NO-JS fallback state

        Returns True if JS was successfully enabled.
        """
        if self._js_enable_attempted:
            return False  # don't retry repeatedly
        self._js_enable_attempted = True
        log.info("Attempting to auto-enable JavaScript from Apple Events...")

        try:
            # Step 1: Enable via defaults write (works without GUI interaction)
            sp.run(
                ["defaults", "write", "com.apple.Safari",
                 "AllowJavaScriptFromAppleEvents", "-bool", "true"],
                capture_output=True, text=True, timeout=5
            )
            # Also ensure Develop menu is visible
            sp.run(
                ["defaults", "write", "com.apple.Safari",
                 "IncludeDevelopMenu", "-bool", "true"],
                capture_output=True, text=True, timeout=5
            )
            log.info("Set Safari defaults for JS from Apple Events + Develop menu")
        except Exception as e:
            log.warning(f"defaults write failed: {e}")

        # Step 2: Try toggling via Develop menu (picks up the change in running Safari)
        self._osa(
            'tell application "Safari" to activate\n'
            'delay 0.3\n'
            'tell application "System Events"\n'
            '  tell process "Safari"\n'
            '    try\n'
            '      click menu item "Allow JavaScript from Apple Events" '
            'of menu 1 of menu bar item "Develop" of menu bar 1\n'
            '      delay 0.3\n'
            '    end try\n'
            '  end tell\n'
            'end tell'
        )
        time.sleep(0.5)

        # Step 3: Test if JS now works
        if self._test_js():
            log.info("JS from Apple Events is now ENABLED")
            self._js_available = True

            # Step 4: Reload the page to exit NO-JS mode
            self._osa(
                'tell application "Safari"\n'
                '  tell front window\n'
                '    set URL of current tab to URL of current tab\n'
                '  end tell\n'
                'end tell'
            )
            self._wait_idle(1.0, 10.0)
            log.info("Page reloaded after enabling JS")
            return True
        else:
            # The toggle might have turned it OFF if it was already ON
            # Try toggling again
            self._osa(
                'tell application "System Events"\n'
                '  tell process "Safari"\n'
                '    try\n'
                '      click menu item "Allow JavaScript from Apple Events" '
                'of menu 1 of menu bar item "Develop" of menu bar 1\n'
                '    end try\n'
                '  end tell\n'
                'end tell'
            )
            time.sleep(0.5)
            if self._test_js():
                log.info("JS from Apple Events enabled on second toggle")
                self._js_available = True
                self._osa(
                    'tell application "Safari"\n'
                    '  tell front window\n'
                    '    set URL of current tab to URL of current tab\n'
                    '  end tell\n'
                    'end tell'
                )
                self._wait_idle(1.0, 10.0)
                return True

        log.warning("Could not auto-enable JS from Apple Events")
        return False

    def _get_page_text_noscript(self) -> str:
        """Get visible page text via AppleScript (NO JavaScript required).

        Uses Safari's native 'text of current tab' property which works
        even when 'Allow JavaScript from Apple Events' is disabled.
        """
        return self._osa(
            'tell application "Safari" to return text of current tab of front window'
        ) or ""

    def _get_page_source_noscript(self) -> str:
        """Get page HTML source via AppleScript (NO JavaScript required).

        Uses Safari's native 'source of current tab' property.
        """
        return self._osa(
            'tell application "Safari" to return source of current tab of front window',
            timeout=30
        ) or ""

    def _snapshot_noscript(self) -> str:
        """Build a snapshot using ONLY AppleScript (no do JavaScript required).

        Fallback for when Safari's 'Allow JavaScript from Apple Events' is disabled.
        Uses 'text of current tab' for content and 'source of current tab' + regex
        parsing for interactive element extraction.
        """
        info = self._osa(
            'tell application "Safari" to return '
            '(URL of current tab of front window) & " | " & '
            '(name of current tab of front window)'
        )
        url, title = info.split(" | ", 1) if " | " in info else (info, "")

        out = "=== PAGE ===\n"
        out += f"URL: {url}\n"
        out += f"TITLE: {title}\n"
        out += "⚠️ MODE: NO-JS FALLBACK (JavaScript execution unavailable)\n"
        out += "⚠️ To enable full features: Safari menu → Develop → Allow JavaScript from Apple Events\n"
        out += "⚠️ If Develop menu is hidden: Safari → Settings → Advanced → Show features for web developers\n\n"

        # Get page text (works without JS)
        page_text = self._get_page_text_noscript()

        # Get page source and parse for interactive elements
        source = self._get_page_source_noscript()
        if source:
            out += "=== ELEMENTS (parsed from HTML, click by text) ===\n"
            elements = self._parse_html_elements(source)
            for elem in elements[:150]:
                out += elem + "\n"
            if len(elements) > 150:
                out += f"[... {len(elements) - 150} more elements]\n"
            out += "\n"

        out += "=== TEXT ===\n"
        if page_text:
            # Clean up the text - remove excessive whitespace
            lines = [l.strip() for l in page_text.split('\n') if l.strip()]
            # Deduplicate adjacent identical lines
            cleaned = []
            for line in lines:
                if not cleaned or cleaned[-1] != line:
                    cleaned.append(line)
            text = '\n'.join(cleaned[:200])
            out += text[:8000]
            if len(page_text) > 8000:
                out += f"\n[truncated — {len(page_text)} chars total]"
        else:
            out += "(page text unavailable)\n"

        return out

    def _parse_html_elements(self, html_source: str) -> list:
        """Parse HTML source to extract interactive elements using regex.

        Returns list of element description strings. Also populates
        self._noscript_refs so #N refs can be resolved to text for clicking.
        """
        elements = []
        self._noscript_refs = {}
        ref_n = 1

        # Extract links: <a href="...">text</a>
        for m in re.finditer(
            r'<a\s[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>',
            html_source, re.DOTALL | re.IGNORECASE
        ):
            href = m.group(1).strip()
            text = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            text = re.sub(r'\s+', ' ', text)[:80]
            if not text or href.startswith('javascript:'):
                continue
            if len(text) < 2:
                continue
            line = f'[{ref_n}] link "{text}"'
            if href and not href.startswith('#'):
                line += f' -> {href[:120]}'
            elements.append(line)
            self._noscript_refs[ref_n] = {"tag": "a", "text": text, "href": href}
            ref_n += 1
            if ref_n > 200:
                break

        # Extract buttons: <button>text</button> and input[type=submit]
        for m in re.finditer(
            r'<button[^>]*>(.*?)</button>',
            html_source, re.DOTALL | re.IGNORECASE
        ):
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            text = re.sub(r'\s+', ' ', text)[:80]
            if text and len(text) >= 2:
                elements.append(f'[{ref_n}] button "{text}"')
                self._noscript_refs[ref_n] = {"tag": "button", "text": text}
                ref_n += 1

        for m in re.finditer(
            r'<input\s[^>]*type=["\']submit["\'][^>]*>',
            html_source, re.IGNORECASE
        ):
            val_m = re.search(r'value=["\']([^"\']*)["\']', m.group(0))
            text = val_m.group(1) if val_m else 'Submit'
            elements.append(f'[{ref_n}] button "{text}"')
            self._noscript_refs[ref_n] = {"tag": "input", "type": "submit", "text": text}
            ref_n += 1

        # Extract input fields
        for m in re.finditer(
            r'<input\s([^>]*)>',
            html_source, re.IGNORECASE
        ):
            attrs = m.group(1)
            type_m = re.search(r'type=["\']([^"\']*)["\']', attrs)
            input_type = type_m.group(1).lower() if type_m else 'text'
            if input_type in ('hidden', 'submit', 'button'):
                continue
            name_m = re.search(r'name=["\']([^"\']*)["\']', attrs)
            ph_m = re.search(r'placeholder=["\']([^"\']*)["\']', attrs)
            label = ph_m.group(1) if ph_m else (name_m.group(1) if name_m else '')
            line = f'[{ref_n}] input[{input_type}]'
            if label:
                line += f' "{label}"'
            if name_m:
                line += f' name={name_m.group(1)}'
            elements.append(line)
            self._noscript_refs[ref_n] = {"tag": "input", "type": input_type, "text": label, "name": name_m.group(1) if name_m else ""}
            ref_n += 1

        # Extract select dropdowns
        for m in re.finditer(
            r'<select\s([^>]*)>.*?</select>',
            html_source, re.DOTALL | re.IGNORECASE
        ):
            attrs = m.group(1)
            name_m = re.search(r'name=["\']([^"\']*)["\']', attrs)
            name = name_m.group(1) if name_m else ''
            # Extract options
            opts = re.findall(r'<option[^>]*>(.*?)</option>', m.group(0), re.IGNORECASE)
            opts_text = [re.sub(r'<[^>]+>', '', o).strip()[:30] for o in opts[:10] if o.strip()]
            line = f'[{ref_n}] select'
            if name:
                line += f' name={name}'
            if opts_text:
                line += f' [{" | ".join(opts_text)}]'
            elements.append(line)
            self._noscript_refs[ref_n] = {"tag": "select", "text": name, "name": name, "options": opts_text}
            ref_n += 1

        # Extract textareas
        for m in re.finditer(
            r'<textarea\s([^>]*)>',
            html_source, re.IGNORECASE
        ):
            attrs = m.group(1)
            name_m = re.search(r'name=["\']([^"\']*)["\']', attrs)
            ph_m = re.search(r'placeholder=["\']([^"\']*)["\']', attrs)
            line = f'[{ref_n}] textarea'
            if ph_m:
                line += f' "{ph_m.group(1)}"'
            if name_m:
                line += f' name={name_m.group(1)}'
            elements.append(line)
            self._noscript_refs[ref_n] = {"tag": "textarea", "text": ph_m.group(1) if ph_m else "", "name": name_m.group(1) if name_m else ""}
            ref_n += 1

        # Extract elements with role=button/link
        for m in re.finditer(
            r'<\w+\s[^>]*role=["\'](?:button|link)["\'][^>]*>(.*?)</\w+>',
            html_source, re.DOTALL | re.IGNORECASE
        ):
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            text = re.sub(r'\s+', ' ', text)[:80]
            role_m = re.search(r'role=["\'](\w+)["\']', m.group(0))
            role = role_m.group(1) if role_m else 'button'
            if text and len(text) >= 2:
                elements.append(f'[{ref_n}] {role} "{text}"')
                self._noscript_refs[ref_n] = {"tag": role, "text": text}
                ref_n += 1

        return elements

    def _ensure_safari(self):
        self._osa(
            'tell application "Safari"\n'
            '  activate\n'
            '  if (count of windows) = 0 then make new document\n'
            'end tell'
        )

    # ─── Hook injection ──────────────────────────────────────────

    def _inject_idle_hooks(self):
        """Inject XHR/fetch counter and MutationObserver for network-idle detection."""
        self._js("""
(function() {
    if (window.__safari_idle_hooked) return;
    window.__safari_idle_hooked = true;
    window.__safari_pending = 0;
    window.__safari_last_mutation = Date.now();

    // Hook XHR
    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function() {
        this.__tracked = true;
        return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function() {
        if (this.__tracked) {
            window.__safari_pending++;
            this.addEventListener('loadend', function() {
                window.__safari_pending = Math.max(0, window.__safari_pending - 1);
            });
        }
        return origSend.apply(this, arguments);
    };

    // Hook fetch
    var origFetch = window.fetch;
    window.fetch = function() {
        window.__safari_pending++;
        return origFetch.apply(this, arguments).finally(function() {
            window.__safari_pending = Math.max(0, window.__safari_pending - 1);
        });
    };

    // Mutation observer
    if (document.body) {
        var obs = new MutationObserver(function() {
            window.__safari_last_mutation = Date.now();
        });
        obs.observe(document.body, {childList: true, subtree: true, attributes: true});
    }

    // SPA navigation detection (pushState/popState)
    window.__safari_nav_count = 0;
    var origPush = history.pushState;
    var origReplace = history.replaceState;
    history.pushState = function() {
        window.__safari_nav_count++;
        window.__safari_last_mutation = Date.now();
        return origPush.apply(this, arguments);
    };
    history.replaceState = function() {
        window.__safari_nav_count++;
        window.__safari_last_mutation = Date.now();
        return origReplace.apply(this, arguments);
    };
    window.addEventListener('popstate', function() {
        window.__safari_nav_count++;
        window.__safari_last_mutation = Date.now();
    });
})()
""")

    def _inject_dialog_hooks(self):
        """Override alert/confirm/prompt to prevent blocking."""
        self._js("""
(function() {
    if (window.__safari_dialogs_hooked) return;
    window.__safari_dialogs_hooked = true;
    window.__safari_last_dialog = null;

    window.__orig_alert = window.alert;
    window.__orig_confirm = window.confirm;
    window.__orig_prompt = window.prompt;

    window.alert = function(msg) {
        window.__safari_last_dialog = {type: 'alert', message: String(msg).substring(0, 500)};
    };
    window.confirm = function(msg) {
        window.__safari_last_dialog = {type: 'confirm', message: String(msg).substring(0, 500)};
        return true;
    };
    window.prompt = function(msg, def) {
        window.__safari_last_dialog = {type: 'prompt', message: String(msg).substring(0, 500)};
        return def || '';
    };
})()
""")

    # ─── Wait primitives ─────────────────────────────────────────

    def _wait_load(self, max_wait: int = 15) -> bool:
        for _ in range(max_wait * 4):
            state = self._js("document.readyState")
            if state == "complete":
                return True
            time.sleep(0.25)
        return False

    def _wait_idle(self, settle: float = 0.5, max_wait: float = 10.0):
        """Wait for page to be truly idle: DOM loaded + no pending network + DOM settled."""
        self._wait_load()
        self._inject_idle_hooks()
        self._inject_dialog_hooks()

        deadline = time.time() + max_wait
        while time.time() < deadline:
            status = self._js(
                "(function() {"
                "  var p = window.__safari_pending || 0;"
                "  var ms = Date.now() - (window.__safari_last_mutation || 0);"
                "  return p + ',' + ms;"
                "})()"
            )
            try:
                parts = status.split(",")
                pending = int(parts[0])
                since_mutation = int(parts[1])
                if pending == 0 and since_mutation > settle * 1000:
                    return
            except (ValueError, IndexError):
                pass
            time.sleep(0.25)

    # ─── Human-like timing ────────────────────────────────────────

    def _human_delay(self, action: str = "click"):
        delays = {
            "click": (0.3, 0.8),
            "fill_pre": (0.2, 0.5),
            "select": (0.3, 0.6),
            "scroll": (0.15, 0.4),
            "navigate": (0.5, 1.2),
        }
        lo, hi = delays.get(action, (0.2, 0.5))
        time.sleep(random.uniform(lo, hi))

    # ─── Retry wrapper ────────────────────────────────────────────

    def _retry(self, fn, max_attempts: int = 3, delay: float = 1.0):
        """Retry a function with backoff on empty/error results."""
        for attempt in range(max_attempts):
            result = fn()
            if result and result != "ERROR:TIMEOUT" and not result.startswith("ERROR:"):
                return result
            if attempt < max_attempts - 1:
                time.sleep(delay * (attempt + 1))
                log.warning(f"Retry {attempt+1}/{max_attempts}: got '{str(result)[:60]}'")
        return result

    # ─── Overlay / cookie banner dismissal ────────────────────────

    def _dismiss_overlays(self):
        """Auto-dismiss cookie consent banners, popups, and overlays that block interaction."""
        self._js("""(function() {
// Common cookie consent / GDPR / overlay selectors
var dismissSelectors = [
    // Cookie consent buttons
    '[class*=cookie] button[class*=accept]',
    '[class*=cookie] button[class*=agree]',
    '[class*=cookie] button[class*=allow]',
    '[class*=cookie] button[class*=close]',
    '[id*=cookie] button[class*=accept]',
    '[id*=cookie] button[class*=agree]',
    '[class*=consent] button[class*=accept]',
    '[class*=consent] button[class*=agree]',
    '[class*=gdpr] button[class*=accept]',
    '#onetrust-accept-btn-handler',
    '.cc-dismiss', '.cc-accept', '.cc-allow',
    '[data-testid*=cookie] button',
    '[aria-label*=cookie] button',
    '[aria-label*=Accept]',
    '[aria-label*=accept]',
    // Generic overlay close buttons
    '.modal .close', '.modal [class*=close]',
    '.popup .close', '.popup [class*=close]',
    '[class*=overlay] [class*=close]',
    '[class*=banner] [class*=close]',
    '[class*=banner] [class*=dismiss]',
    // Notification dismiss
    '[class*=notification] [class*=close]',
    '[class*=notification] [class*=dismiss]',
];

var dismissed = [];
for (var i = 0; i < dismissSelectors.length; i++) {
    try {
        var els = document.querySelectorAll(dismissSelectors[i]);
        for (var j = 0; j < els.length; j++) {
            var el = els[j];
            try {
                var cs = window.getComputedStyle(el);
                if (cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0') {
                    var rect = el.getBoundingClientRect();
                    if (rect.width > 5 && rect.height > 5) {
                        el.click();
                        dismissed.push(dismissSelectors[i]);
                    }
                }
            } catch(e) {}
        }
    } catch(e) {}
}

// Also try removing common overlay containers that block the page
var overlaySelectors = [
    '.cookie-banner', '.cookie-notice', '.cookie-consent',
    '#cookie-banner', '#cookie-notice', '#cookie-consent',
    '[class*=cookie-overlay]', '[class*=consent-overlay]',
    '.gdpr-banner', '#gdpr-banner',
    '[class*=CookieConsent]', '[id*=CookieConsent]',
];
for (var k = 0; k < overlaySelectors.length; k++) {
    try {
        var overlay = document.querySelector(overlaySelectors[k]);
        if (overlay) {
            var ocs = window.getComputedStyle(overlay);
            if (ocs.display !== 'none') {
                overlay.style.display = 'none';
                dismissed.push('HIDDEN:' + overlaySelectors[k]);
            }
        }
    } catch(e) {}
}

// Remove any fixed/sticky overlays that cover the entire viewport
var allFixed = document.querySelectorAll('[style*="position: fixed"],[style*="position:fixed"]');
for (var f = 0; f < allFixed.length; f++) {
    try {
        var fr = allFixed[f].getBoundingClientRect();
        if (fr.width > window.innerWidth * 0.8 && fr.height > window.innerHeight * 0.5) {
            var zIdx = parseInt(window.getComputedStyle(allFixed[f]).zIndex) || 0;
            if (zIdx > 100) {
                allFixed[f].style.display = 'none';
                dismissed.push('HIDDEN:fixed-overlay-z' + zIdx);
            }
        }
    } catch(e) {}
}

return dismissed.length ? 'DISMISSED:' + dismissed.join(',') : 'NO_OVERLAYS';
})()""")

    # ─── Auto-iframe detection ────────────────────────────────────

    def _detect_survey_iframe(self) -> Optional[int]:
        """Detect if the page content is inside an iframe (common for surveys).

        Returns the iframe index if found, None otherwise.
        """
        result = self._js("""(function() {
var frames = document.querySelectorAll('iframe');
if (!frames.length) return 'NO_IFRAMES';

// Check if main page has very little interactive content
var mainEls = document.querySelectorAll('input:not([type=hidden]),textarea,select,button:not([class*=close]),[role=button]');
var visibleMain = 0;
for (var i = 0; i < mainEls.length; i++) {
    try {
        var r = mainEls[i].getBoundingClientRect();
        if (r.width > 5 && r.height > 5) visibleMain++;
    } catch(e) {}
}

// If main page has few interactive elements, look for content-rich iframes
if (visibleMain <= 3) {
    for (var fi = 0; fi < frames.length; fi++) {
        var f = frames[fi];
        try {
            if (!f.contentDocument) continue;
            var fRect = f.getBoundingClientRect();
            // Skip tiny iframes (ads, tracking pixels)
            if (fRect.width < 200 || fRect.height < 100) continue;

            // Check if iframe has form elements
            var iframeEls = f.contentDocument.querySelectorAll('input,textarea,select,button,[role=button]');
            if (iframeEls.length >= 2) {
                return 'SURVEY_IFRAME:' + fi + ':' + iframeEls.length + ' elements';
            }

            // Check if iframe has substantial text content
            var iframeText = (f.contentDocument.body || {}).innerText || '';
            if (iframeText.length > 200) {
                return 'CONTENT_IFRAME:' + fi + ':' + iframeText.length + ' chars';
            }
        } catch(e) {
            // Cross-origin iframe — can't access
            continue;
        }
    }
}
return 'NO_SURVEY_IFRAME:main_has_' + visibleMain + '_elements';
})()""")

        if result and ('SURVEY_IFRAME:' in result or 'CONTENT_IFRAME:' in result):
            try:
                idx = int(result.split(':')[1])
                log.info(f"Auto-detected survey iframe: {result}")
                return idx
            except (ValueError, IndexError):
                pass
        return None

    # ─── Snapshot fallback (simple extraction) ────────────────────

    def _snapshot_simple(self) -> str:
        """Simple page text extraction as fallback when full snapshot fails."""
        return self._js("""(function() {
try {
    var out = '=== PAGE ===\\n';
    out += 'URL: ' + window.location.href + '\\n';
    out += 'TITLE: ' + (document.title || '') + '\\n\\n';

    out += '=== ELEMENTS (use #N to interact) ===\\n';
    window.__safari_refs = {};
    var refN = 1;

    // Simple element extraction with error isolation
    var sels = 'input:not([type=hidden]),textarea,select,button,a[href],[role=button],[role=link]';
    var els;
    try { els = document.querySelectorAll(sels); } catch(e) { return out + 'ERROR:' + e.message; }

    for (var i = 0; i < els.length && refN <= 200; i++) {
        var el = els[i];
        try {
            var r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) continue;
            var cs = window.getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        } catch(e) { continue; }

        var tag = el.tagName.toLowerCase();
        var line = '[' + refN + '] ' + tag;
        if (el.type) line += '[' + el.type + ']';
        var label = (el.textContent || el.value || el.placeholder || el.title || el.getAttribute('aria-label') || '').trim();
        if (label) line += ' "' + label.substring(0, 80) + '"';
        if (el.name) line += ' name=' + el.name;

        try {
            var cx = Math.round(r.left + r.width / 2);
            var cy = Math.round(r.top + r.height / 2);
            line += ' @' + cx + ',' + cy;
            window.__safari_refs[refN] = el;
        } catch(e) {}

        out += line + '\\n';
        refN++;
    }

    // Basic text content
    out += '\\n=== TEXT ===\\n';
    var bodyText = (document.body && document.body.innerText) || '';
    out += bodyText.substring(0, 3000);
    if (bodyText.length > 3000) out += '\\n[truncated]';

    return out;
} catch(e) {
    return 'SNAPSHOT_ERROR: ' + e.message + '\\nURL: ' + window.location.href;
}
})()""")

    # ─── OCR fallback via macOS Vision ────────────────────────────

    def _ocr_screenshot(self) -> str:
        """Take screenshot and OCR it using macOS Vision framework.

        Ultimate fallback when all JS-based extraction fails.
        """
        path = self._screenshot()
        if not path:
            return "OCR_FAILED: Screenshot failed"

        # Use Swift/Python bridge via osascript JXA to call Vision framework
        ocr_script = f"""
ObjC.import('Vision');
ObjC.import('AppKit');

var imgUrl = $.NSURL.fileURLWithPath('{path}');
var img = $.NSImage.alloc.initWithContentsOfURL(imgUrl);
if (!img) {{ 'OCR_FAILED: Could not load image'; }}

var cgRef = img.CGImageForProposedRect(null, null, null);
if (!cgRef) {{ 'OCR_FAILED: Could not get CGImage'; }}

var request = $.VNRecognizeTextRequest.alloc.init;
request.recognitionLevel = $.VNRequestTextRecognitionLevelAccurate;

var handler = $.VNImageRequestHandler.alloc.initWithCGImageOptions(cgRef, $());
var error = $();
handler.performRequestsError([request], error);

var results = request.results;
var text = '';
for (var i = 0; i < results.count; i++) {{
    var obs = results.objectAtIndex(i);
    var candidates = obs.topCandidates(1);
    if (candidates.count > 0) {{
        text += candidates.objectAtIndex(0).string.js + '\\n';
    }}
}}
text || 'OCR_EMPTY';
"""
        try:
            r = sp.run(
                ["osascript", "-l", "JavaScript", "-e", ocr_script],
                capture_output=True, text=True, timeout=30
            )
            result = r.stdout.strip()
            if result and result != "OCR_EMPTY" and not result.startswith("OCR_FAILED"):
                log.info(f"OCR extracted {len(result)} chars")
                current_url = self._osa('tell application "Safari" to return URL of current tab of front window')
                return f"=== OCR TEXT (screenshot-based) ===\nURL: {current_url}\n\n{result}"
            log.warning(f"OCR returned: {result[:100] if result else 'empty'}")
            return result or "OCR_EMPTY"
        except Exception as e:
            log.error(f"OCR failed: {e}")
            return f"OCR_FAILED: {e}"

    # ─── Screenshot ──────────────────────────────────────────────

    def _screenshot(self) -> str:
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(SCREENSHOT_DIR, f"snap_{ts}.png")
        self._osa('tell application "Safari" to activate')
        time.sleep(0.3)
        try:
            sp.run(["screencapture", "-x", "-o", "-t", "png", path],
                   timeout=5, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        except Exception as e:
            log.error(f"Screenshot failed: {e}")
            return ""
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        return ""

    # ─── Target resolution ───────────────────────────────────────

    def _resolve_js(self, text: str) -> str:
        """Convert target string to JS expression that finds the DOM element.

        Formats:
          #5       → element ref from last snapshot (with isConnected check)
          css:.foo → CSS selector
          (other)  → text search across interactive elements (incl. shadow DOM)
        """
        text = text.strip()

        if text.startswith("#") and text[1:].isdigit():
            n = int(text[1:])
            return (
                "(function() {"
                f"  var el = window.__safari_refs && window.__safari_refs[{n}];"
                "  if (el && el.isConnected) return el;"
                "  return null;"
                "})()"
            )

        if text.startswith("css:"):
            sel = text[4:].replace("\\", "\\\\").replace("'", "\\'")
            return f"document.querySelector('{sel}')"

        safe = text.replace("\\", "\\\\").replace("'", "\\'")
        return (
            "(function() {\n"
            f"  var q = '{safe}'.toLowerCase();\n"
            "  function searchIn(root) {\n"
            "    var sels = 'a,button,[role=button],[role=link],[role=tab],"
            "[role=menuitem],input,select,textarea,label,[onclick],"
            "summary,.btn,[tabindex]';\n"
            "    var all = root.querySelectorAll(sels);\n"
            "    for (var i = 0; i < all.length; i++) {\n"
            "      var el = all[i];\n"
            "      var t = (el.textContent||el.value||el.placeholder||"
            "el.title||el.getAttribute('aria-label')||'').trim().toLowerCase();\n"
            "      if (t.includes(q)) return el;\n"
            "    }\n"
            "    var everything = root.querySelectorAll('*');\n"
            "    for (var j = 0; j < everything.length; j++) {\n"
            "      if (everything[j].shadowRoot) {\n"
            "        var found = searchIn(everything[j].shadowRoot);\n"
            "        if (found) return found;\n"
            "      }\n"
            "    }\n"
            "    return null;\n"
            "  }\n"
            "  return searchIn(document);\n"
            "})()"
        )

    # ─── Coordinate clicking ─────────────────────────────────────

    def _get_safari_content_origin(self) -> tuple:
        """Get the screen coordinates of Safari's content area top-left corner.

        Dynamically measures the actual toolbar height by comparing the window
        position to where the content area starts (via accessibility).
        """
        # Get window position and size via AppleScript
        info = self._osa(
            'tell application "Safari"\n'
            '  set w to front window\n'
            '  set {wx, wy} to {x position of w, y position of w}\n'
            'end tell\n'
            'tell application "System Events" to tell process "Safari"\n'
            '  tell window 1\n'
            '    try\n'
            '      set webArea to first group whose role is "AXGroup"\n'
            '      set {_, cy} to position of webArea\n'
            '      return (wx as text) & "," & (cy as text)\n'
            '    on error\n'
            '      return (wx as text) & "," & ((wy + 74) as text)\n'
            '    end try\n'
            '  end tell\n'
            'end tell'
        )
        try:
            parts = info.split(",")
            cx, cy = int(parts[0].strip()), int(parts[1].strip())
            return cx, cy
        except (ValueError, IndexError):
            pass
        # Fallback: window position + estimated toolbar
        bounds = self._osa(
            'tell application "Safari" to return '
            '(x position of front window) & "," & (y position of front window)'
        )
        try:
            parts = bounds.split(",")
            wx, wy = int(parts[0].strip()), int(parts[1].strip())
        except (ValueError, IndexError):
            wx, wy = 0, 0
        return wx, wy + 74

    def _click_at_viewport(self, vx: int, vy: int) -> str:
        """Click at viewport-relative coordinates using OS-level mouse events.

        Uses JXA (JavaScript for Automation) to call CoreGraphics directly.
        Generates real mouse events indistinguishable from human clicks.
        """
        ox, oy = self._get_safari_content_origin()
        sx, sy = ox + vx, oy + vy

        self._osa('tell application "Safari" to activate')
        time.sleep(0.1)

        # Use JXA to call CoreGraphics for real mouse events
        jxa_script = f"""
ObjC.import('CoreGraphics');
var point = $.CGPointMake({sx}, {sy});
var mouseDown = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseDown, point, $.kCGMouseButtonLeft);
$.CGEventPost($.kCGSessionEventTap, mouseDown);
delay(0.05);
var mouseUp = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseUp, point, $.kCGMouseButtonLeft);
$.CGEventPost($.kCGSessionEventTap, mouseUp);
'CLICKED_AT:{sx},{sy}';
"""
        try:
            r = sp.run(
                ["osascript", "-l", "JavaScript", "-e", jxa_script],
                capture_output=True, text=True, timeout=10
            )
            result = r.stdout.strip() or r.stderr.strip()
            if "CLICKED_AT" in result or r.returncode == 0:
                log.info(f"click_at({vx},{vy}) -> screen({sx},{sy}) OK")
                return f"CLICKED_AT:viewport({vx},{vy})->screen({sx},{sy})"
            log.warning(f"click_at JXA stderr: {r.stderr}")
            return f"CLICK_AT_WARN:{result}"
        except Exception as e:
            log.error(f"click_at error: {e}")
            # Fallback: AppleScript System Events click
            try:
                self._osa(
                    f'tell application "System Events" to click at {{{sx}, {sy}}}'
                )
                return f"CLICKED_AT_FALLBACK:({sx},{sy})"
            except Exception as e2:
                return f"CLICK_AT_FAILED:{e2}"

    # ─── Navigation ──────────────────────────────────────────────

    @staticmethod
    def _escape_url_for_osa(url: str) -> str:
        """Escape a URL for safe embedding in AppleScript strings."""
        return url.replace("\\", "\\\\").replace('"', '\\"')

    def new_tab(self, url: str = "https://www.swagbucks.com") -> Dict:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        self._iframe_idx = None
        safe_url = self._escape_url_for_osa(url)
        self._osa(
            'tell application "Safari"\n'
            '  activate\n'
            '  if (count of windows) = 0 then make new document\n'
            '  tell front window\n'
            f'    set newTab to make new tab with properties {{URL:"{safe_url}"}}\n'
            '    set current tab to newTab\n'
            '  end tell\n'
            'end tell'
        )
        self._wait_idle(1.0)
        return self.snapshot()

    def browse(self, url: str) -> Dict:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        self._iframe_idx = None
        safe_url = self._escape_url_for_osa(url)
        self._ensure_safari()
        self._osa(
            'tell application "Safari"\n'
            f'  tell front window to set URL of current tab to "{safe_url}"\n'
            'end tell'
        )
        self._wait_idle(1.0)
        return self.snapshot()

    def back(self) -> Dict:
        self._js("history.back()")
        self._wait_idle(1.0)
        return self.snapshot()

    def forward(self) -> Dict:
        self._js("history.forward()")
        self._wait_idle(1.0)
        return self.snapshot()

    # ─── Snapshot (the agent's eyes) ─────────────────────────────

    def snapshot(self) -> Dict:
        iframe_js_null = 'null' if self._iframe_idx is None else str(self._iframe_idx)

        if self._iframe_idx is not None:
            doc_ref = f"document.querySelectorAll('iframe')[{self._iframe_idx}].contentDocument"
            win_ref = f"document.querySelectorAll('iframe')[{self._iframe_idx}].contentWindow"
            ctx_note = f"[INSIDE IFRAME {self._iframe_idx}] "
        else:
            doc_ref = "document"
            win_ref = "window"
            ctx_note = ""

        # ── Try to dismiss overlays first ──
        self._dismiss_overlays()

        # ── Primary snapshot: full JS extraction ──
        js = self._build_snapshot_js(doc_ref, win_ref, ctx_note, iframe_js_null)
        elements = self._js(js, timeout=30)

        # ── Fallback chain if primary snapshot fails or returns empty ──
        is_empty = not elements or len(elements.strip()) < 30 or elements.startswith("ERROR")

        if is_empty and self._iframe_idx is None:
            log.warning("Primary snapshot empty — trying overlay dismissal + retry")
            time.sleep(0.5)
            elements = self._js(js, timeout=30)
            is_empty = not elements or len(elements.strip()) < 30 or elements.startswith("ERROR")

        # ── Auto-enable JS if snapshots keep failing ──
        if is_empty and self._iframe_idx is None and not self._js_enable_attempted:
            log.warning("Snapshot empty — attempting auto-enable JavaScript from Apple Events")
            if self._auto_enable_js():
                log.info("JS enabled! Retrying snapshot with full JS...")
                js = self._build_snapshot_js(doc_ref, win_ref, ctx_note, iframe_js_null)
                elements = self._js(js, timeout=30)
                is_empty = not elements or len(elements.strip()) < 30 or elements.startswith("ERROR")
                if not is_empty:
                    log.info("Snapshot succeeded after enabling JS!")

        if is_empty and self._iframe_idx is None:
            log.warning("Snapshot still empty — trying simple fallback extraction")
            elements = self._snapshot_simple()
            is_empty = not elements or len(elements.strip()) < 30 or elements.startswith("ERROR")

        # ── Auto-iframe detection: if main page is sparse, check for survey iframes ──
        auto_iframe_note = ""
        if self._iframe_idx is None:
            # Count interactive elements in the snapshot
            element_count = elements.count('[') if elements else 0
            if element_count < 5:
                iframe_idx = self._detect_survey_iframe()
                if iframe_idx is not None:
                    log.info(f"Auto-entering iframe {iframe_idx} (main page sparse)")
                    self._iframe_idx = iframe_idx
                    iframe_doc = f"document.querySelectorAll('iframe')[{iframe_idx}].contentDocument"
                    iframe_win = f"document.querySelectorAll('iframe')[{iframe_idx}].contentWindow"
                    iframe_note = f"[AUTO-ENTERED IFRAME {iframe_idx}] "
                    iframe_js = self._build_snapshot_js(iframe_doc, iframe_win, iframe_note, str(iframe_idx))
                    iframe_elements = self._js(iframe_js, timeout=30)

                    if iframe_elements and len(iframe_elements.strip()) > 30:
                        auto_iframe_note = f"\n\n⚠️ AUTO-IFRAME: Main page was sparse. Auto-entered iframe {iframe_idx} which has form content.\nUse 'iframe exit' to return to main page.\n"
                        elements = iframe_elements
                        is_empty = False
                    else:
                        # iframe snapshot also failed — exit back
                        self._iframe_idx = None

        # ── Non-JS fallback: use AppleScript text/source properties ──
        if is_empty:
            js_works = self._test_js()
            if not js_works:
                log.warning("JavaScript execution unavailable — using no-JS fallback (AppleScript text/source)")
                noscript_result = self._snapshot_noscript()
                if noscript_result and len(noscript_result.strip()) > 50:
                    elements = noscript_result
                    is_empty = False
                    auto_iframe_note += "\n⚠️ NO-JS MODE: Click elements by their text content, not #N refs.\n"
            else:
                log.warning("JS works but snapshot returns empty — page may block content extraction")
                # Try getting at least the page text
                page_text = self._get_page_text_noscript()
                if page_text and len(page_text.strip()) > 30:
                    elements = (
                        "=== PAGE ===\n"
                        f"URL: (see below)\n"
                        f"TITLE: (see below)\n"
                        "⚠️ JS works but snapshot extraction failed. Showing page text.\n"
                        "⚠️ Use click with text content or click_at with coordinates.\n\n"
                        f"=== TEXT ===\n{page_text[:8000]}"
                    )
                    is_empty = False

        # ── Ultimate fallback: OCR ──
        if is_empty:
            log.warning("All extraction failed — attempting OCR fallback")
            ocr_result = self._ocr_screenshot()
            if ocr_result and not ocr_result.startswith("OCR_FAILED") and ocr_result != "OCR_EMPTY":
                elements = ocr_result
                auto_iframe_note += "\n⚠️ OCR MODE: Using screenshot OCR. Click by text content or coordinates.\n"

        info = self._osa(
            'tell application "Safari" to return '
            '(URL of current tab of front window) & " | " & '
            '(name of current tab of front window)'
        )
        url, title = info.split(" | ", 1) if " | " in info else (info, "")

        page_content = (elements or "") + auto_iframe_note

        return {
            "status": "success",
            "url": url,
            "title": title,
            "page": page_content,
            "message": page_content,
        }

    def _build_snapshot_js(self, doc_ref, win_ref, ctx_note, iframe_null):
        """Build the snapshot JavaScript.

        Output order (priority for truncation — TEXT gets cut first):
        1. PAGE — url, title, scroll, size
        2. ELEMENTS — interactive elements with #N refs and @x,y coords
        3. RADIO GROUPS
        4. FORM STATUS — validation errors
        5. DIALOG — intercepted JS dialogs
        6. IFRAMES
        7. TEXT — page content (lowest priority, cut first by smart truncation)
        """
        return f"""(function() {{
var doc = {doc_ref};
var win = {win_ref};
if (!doc) return 'ERROR: Cannot access document';

var out = '';

// ─── PAGE INFO ───
out += '=== PAGE ===\\n';
out += '{ctx_note}URL: ' + (win.location ? win.location.href : 'N/A') + '\\n';
out += 'TITLE: ' + (doc.title || '') + '\\n';
var sh = doc.documentElement ? doc.documentElement.scrollHeight : 0;
var vh = win.innerHeight || 0;
var sy = win.scrollY || win.pageYOffset || 0;
var maxScroll = Math.max(1, sh - vh);
out += 'SCROLL: ' + Math.round(sy) + '/' + maxScroll + 'px (' + Math.round(sy / maxScroll * 100) + '%)\\n';
out += 'SIZE: ' + (win.innerWidth || 0) + 'x' + vh + '\\n';

// Progress detection
var progressEl = doc.querySelector('progress,[role=progressbar],.progress-bar,.progress');
if (progressEl) {{
    var pv = progressEl.value || progressEl.getAttribute('aria-valuenow') || '';
    var pm = progressEl.max || progressEl.getAttribute('aria-valuemax') || '100';
    out += 'PROGRESS: ' + pv + '/' + pm + '\\n';
}}
var bodyText = doc.body ? doc.body.innerText : '';
var qm = bodyText.match(/(?:question|step|page)\\s+(\\d+)\\s+(?:of|\\/)\\s+(\\d+)/i);
if (qm) out += 'STEP: ' + qm[0] + '\\n';
out += '\\n';

// ─── INTERACTIVE ELEMENTS (highest priority) ───
out += '=== ELEMENTS (use #N to interact) ===\\n';
win.__safari_refs = {{}};
win.__safari_refs_coords = {{}};
var refN = 1;

// Shadow DOM recursive collector
function walkShadow(root, collector, depth) {{
    if (depth > 3) return;
    var sels = 'a[href],button,[role=button],[role=link],[role=tab],[role=menuitem],'
        + 'input:not([type=hidden]),textarea,select,[onclick],[contenteditable=true],'
        + 'summary,video,audio,iframe,.btn,[tabindex]:not([tabindex=\\"-1\\"])';
    var els = root.querySelectorAll(sels);
    for (var i = 0; i < els.length; i++) collector.push(els[i]);
    var everything = root.querySelectorAll('*');
    for (var i = 0; i < everything.length; i++) {{
        if (everything[i].shadowRoot) {{
            walkShadow(everything[i].shadowRoot, collector, depth + 1);
        }}
    }}
}}

var allEls = [];
walkShadow(doc, allEls, 0);

var radioGroups = {{}};

for (var i = 0; i < allEls.length && refN <= 500; i++) {{
    var el = allEls[i];

    // Skip invisible
    try {{
        var rect = el.getBoundingClientRect();
        var cs = win.getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
        if (rect.width < 2 && rect.height < 2) continue;
    }} catch (e) {{ continue; }}

    // Compute center coordinates for coordinate clicking
    var cx = Math.round(rect.left + rect.width / 2);
    var cy = Math.round(rect.top + rect.height / 2);
    var coordStr = ' @' + cx + ',' + cy;

    var tag = el.tagName.toLowerCase();
    var line = '';

    if (tag === 'input') {{
        var tp = (el.type || 'text').toLowerCase();

        if (tp === 'radio') {{
            var gn = el.name || '_unnamed';
            if (!radioGroups[gn]) {{
                radioGroups[gn] = {{ firstRef: refN, options: [], label: '' }};
                var fs = el.closest('fieldset');
                if (fs) {{
                    var lg = fs.querySelector('legend');
                    if (lg) radioGroups[gn].label = lg.textContent.trim();
                }}
            }}
            var rlbl = '';
            if (el.labels && el.labels[0]) rlbl = el.labels[0].textContent.trim();
            else if (el.parentElement) {{
                var rpt = el.parentElement.textContent.trim();
                if (rpt.length < 100) rlbl = rpt;
            }}
            radioGroups[gn].options.push({{
                text: rlbl || el.value || '', selected: el.checked, ref: refN, coord: coordStr
            }});
            win.__safari_refs[refN] = el;
            win.__safari_refs_coords[refN] = [cx, cy];
            refN++;
            continue;
        }}

        if (tp === 'checkbox') {{
            var clbl = '';
            if (el.labels && el.labels[0]) clbl = el.labels[0].textContent.trim();
            else if (el.parentElement) {{
                var cpt = el.parentElement.textContent.trim();
                if (cpt.length < 100) clbl = cpt;
            }}
            line = '[' + refN + '] checkbox ' + (el.checked ? '[x]' : '[ ]') + ' "' + (clbl || el.name || '') + '"';
        }} else if (tp === 'submit' || tp === 'button') {{
            line = '[' + refN + '] button "' + (el.value || 'Submit') + '"';
            if (el.disabled) line += ' DISABLED';
        }} else {{
            line = '[' + refN + '] input[' + tp + ']';
            var ilbl = (el.labels && el.labels[0]) ? el.labels[0].textContent.trim() : '';
            if (ilbl) line += ' label="' + ilbl.substring(0, 60) + '"';
            if (el.name) line += ' name=' + el.name;
            if (el.placeholder) line += ' hint="' + el.placeholder.substring(0, 40) + '"';
            if (el.value) line += ' value="' + el.value.substring(0, 60) + '"';
            if (el.required) line += ' REQUIRED';
        }}

    }} else if (tag === 'select') {{
        line = '[' + refN + '] select';
        var slbl = (el.labels && el.labels[0]) ? el.labels[0].textContent.trim() : '';
        if (slbl) line += ' "' + slbl.substring(0, 60) + '"';
        if (el.name) line += ' name=' + el.name;
        var sopts = [];
        for (var so = 0; so < el.options.length && so < 20; so++) {{
            var sopt = el.options[so];
            if (sopt.value === '' && so === 0) continue;
            sopts.push(sopt.selected ? '>' + sopt.text.trim() + '<' : sopt.text.trim());
        }}
        line += ' [' + sopts.join(' | ') + ']';

    }} else if (tag === 'textarea') {{
        line = '[' + refN + '] textarea';
        var tlbl = (el.labels && el.labels[0]) ? el.labels[0].textContent.trim() : '';
        if (tlbl) line += ' "' + tlbl + '"';
        if (el.name) line += ' name=' + el.name;
        if (el.placeholder) line += ' hint="' + el.placeholder.substring(0, 40) + '"';
        if (el.value) line += ' ="' + el.value.substring(0, 80) + '"';

    }} else if (tag === 'a') {{
        var lt = (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 80);
        if (!lt) continue;
        line = '[' + refN + '] link "' + lt + '"';
        if (el.href && !el.href.startsWith('javascript:')) line += ' -> ' + el.href.substring(0, 120);

    }} else if (tag === 'button' || el.getAttribute('role') === 'button') {{
        var bt = (el.textContent || el.value || el.title || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').substring(0, 80);
        if (!bt) continue;
        line = '[' + refN + '] button "' + bt + '"';
        if (el.disabled) line += ' DISABLED';

    }} else if (tag === 'video' || tag === 'audio') {{
        var dur = el.duration ? Math.round(el.duration) : '?';
        var cur = Math.round(el.currentTime || 0);
        line = '[' + refN + '] ' + tag + ' ' + cur + 's/' + dur + 's';
        line += el.paused ? ' PAUSED' : ' PLAYING';
        if (el.muted) line += ' MUTED';
        line += ' speed=' + (el.playbackRate || 1) + 'x';
        if (el.ended) line += ' ENDED';

    }} else if (tag === 'iframe') {{
        line = '[' + refN + '] iframe';
        if (el.title) line += ' "' + el.title + '"';
        if (el.src) line += ' src=' + el.src.substring(0, 100);
        try {{
            line += el.contentDocument ? ' (accessible)' : ' (cross-origin)';
        }} catch (e) {{ line += ' (cross-origin)'; }}

    }} else {{
        var gt = (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 80);
        if (!gt || gt.length < 2) continue;
        line = '[' + refN + '] ' + (el.getAttribute('role') || tag) + ' "' + gt + '"';
    }}

    if (line) {{
        win.__safari_refs[refN] = el;
        win.__safari_refs_coords[refN] = [cx, cy];
        out += line + coordStr + '\\n';
        refN++;
    }}
}}

// Radio groups
for (var gn in radioGroups) {{
    var g = radioGroups[gn];
    var gl = g.label || gn;
    var gopts = g.options.map(function(o) {{
        return (o.selected ? '>' : '') + '#' + o.ref + ' ' + o.text + (o.selected ? '<' : '') + o.coord;
    }}).join(' | ');
    out += 'RADIO "' + gl + '": [' + gopts + ']\\n';
}}

// ─── FORM STATUS (validation errors) ───
var formErrors = '';
var forms = doc.querySelectorAll('form');
for (var fi = 0; fi < forms.length; fi++) {{
    var form = forms[fi];
    var invalids = form.querySelectorAll(':invalid');
    var empties = [];
    try {{ empties = form.querySelectorAll('[required]:not(:valid)'); }} catch(e) {{}}

    if (invalids.length > 0 || empties.length > 0) {{
        formErrors += 'FORM[' + fi + ']';
        if (form.id) formErrors += ' id=' + form.id;
        if (form.action) formErrors += ' action=' + form.action.substring(0, 80);
        formErrors += ': ' + invalids.length + ' invalid, ' + empties.length + ' required-empty\\n';

        for (var vi = 0; vi < invalids.length && vi < 10; vi++) {{
            var inv = invalids[vi];
            var msg = inv.validationMessage || 'invalid';
            var lbl = (inv.labels && inv.labels[0]) ? inv.labels[0].textContent.trim() : inv.name || inv.id || '';
            formErrors += '  ! ' + lbl.substring(0, 40) + ': ' + msg.substring(0, 80) + '\\n';
        }}
    }}
}}
// Visible error messages on page
var errSels = '.error,.err,.field-error,.form-error,.validation-error,.invalid-feedback,.alert-danger,[class*=error],[class*=Error],[role=alert]';
var errEls = doc.querySelectorAll(errSels);
for (var ei = 0; ei < errEls.length && ei < 5; ei++) {{
    var et = (errEls[ei].textContent || '').trim().replace(/\\s+/g, ' ');
    if (et.length > 3 && et.length < 200) {{
        try {{
            var ecs = win.getComputedStyle(errEls[ei]);
            if (ecs.display !== 'none' && ecs.visibility !== 'hidden') {{
                formErrors += 'ERROR: ' + et + '\\n';
            }}
        }} catch(e) {{}}
    }}
}}
if (formErrors) {{
    out += '\\n=== FORM STATUS ===\\n' + formErrors;
}}

// ─── DIALOGS (HTML modals + intercepted JS dialogs) ───
var dialogOut = '';
var dlgs = doc.querySelectorAll('dialog[open],[role=dialog],[role=alertdialog],.modal.show,.modal.active');
for (var di = 0; di < dlgs.length; di++) {{
    try {{
        var ds = win.getComputedStyle(dlgs[di]);
        if (ds.display !== 'none' && ds.visibility !== 'hidden') {{
            dialogOut += 'MODAL: ' + (dlgs[di].textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 400) + '\\n';
        }}
    }} catch(e) {{}}
}}
// Intercepted JS dialogs
if (win.__safari_last_dialog) {{
    var d = win.__safari_last_dialog;
    dialogOut += 'JS_' + d.type.toUpperCase() + ': ' + d.message + '\\n';
}}
if (dialogOut) {{
    out += '\\n=== DIALOG ===\\n' + dialogOut;
}}

// ─── IFRAMES ───
var frames = doc.querySelectorAll('iframe');
if (frames.length && {iframe_null} === null) {{
    out += '\\n=== IFRAMES (' + frames.length + ') ===\\n';
    for (var fi = 0; fi < frames.length; fi++) {{
        out += '[' + fi + '] ';
        if (frames[fi].title) out += '"' + frames[fi].title + '" ';
        if (frames[fi].src) out += frames[fi].src.substring(0, 120);
        out += '\\n';
    }}
    out += 'Use iframe action to enter one.\\n';
}}

// ─── TEXT CONTENT (lowest priority — gets truncated first) ───
out += '\\n=== TEXT ===\\n';

// Questions / headings first (most important for surveys)
var headings = doc.querySelectorAll('h1,h2,h3,h4,.question,.question-text,[class*=question]');
for (var h = 0; h < headings.length && h < 10; h++) {{
    var ht = (headings[h].textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 300);
    if (ht.length > 3) {{
        try {{
            var hcs = win.getComputedStyle(headings[h]);
            if (hcs.display !== 'none') out += 'Q: ' + ht + '\\n';
        }} catch(e) {{ out += 'Q: ' + ht + '\\n'; }}
    }}
}}

// Informational text
var txSels = 'p,li,td,th,blockquote,figcaption,legend,dt,dd';
var txEls = doc.querySelectorAll(txSels);
var seen = {{}};
var tc = 0;
for (var t = 0; t < txEls.length && tc < 30; t++) {{
    var txt = (txEls[t].textContent || '').trim().replace(/\\s+/g, ' ');
    if (txt.length < 3 || txt.length > 500) continue;
    // Skip if inside an interactive element (already captured above)
    if (txEls[t].closest('button,a,[role=button],label')) continue;
    txt = txt.substring(0, 200);
    if (seen[txt]) continue;
    seen[txt] = 1;
    out += txt + '\\n';
    tc++;
}}

// Notices/alerts
var notices = doc.querySelectorAll('.alert:not(.alert-danger),.notice,.message,.success,.warning,.info,[role=status]');
for (var n = 0; n < notices.length && n < 5; n++) {{
    var nt = (notices[n].textContent || '').trim().replace(/\\s+/g, ' ');
    if (nt.length > 3 && nt.length < 300) {{
        try {{
            var ncs = win.getComputedStyle(notices[n]);
            if (ncs.display !== 'none' && ncs.visibility !== 'hidden') {{
                out += 'NOTICE: ' + nt.substring(0, 200) + '\\n';
            }}
        }} catch(e) {{}}
    }}
}}

return out;
}})()"""

    # ─── Click ───────────────────────────────────────────────────

    def click(self, text: str, click_type: str = "") -> Dict:
        ct = click_type.lower().strip()
        target_js = self._resolve_js(text)

        # ── Double-click or right-click at element coordinates via CoreGraphics ──
        if ct in ("double", "dblclick", "dbl"):
            coords_js = (
                "(function() {\n"
                f"  var el = {target_js};\n"
                "  if (!el) return 'NOT_FOUND';\n"
                "  el.scrollIntoView({behavior:'instant',block:'center'});\n"
                "  var r = el.getBoundingClientRect();\n"
                "  return Math.round(r.left+r.width/2)+','+Math.round(r.top+r.height/2);\n"
                "})()"
            )
            coords = self._js_ctx(coords_js)
            if coords == "NOT_FOUND":
                snap = self.snapshot()
                snap["click_result"] = "NOT_FOUND"
                return snap
            try:
                vx, vy = [int(c) for c in coords.split(",")]
                ox, oy = self._get_safari_content_origin()
                sx, sy = ox + vx, oy + vy
                self._osa('tell application "Safari" to activate')
                time.sleep(0.1)
                jxa = f"""
ObjC.import('CoreGraphics');
var p = $.CGPointMake({sx}, {sy});
var d1 = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseDown, p, $.kCGMouseButtonLeft);
$.CGEventSetIntegerValueField(d1, $.kCGMouseEventClickState, 1);
$.CGEventPost($.kCGSessionEventTap, d1);
delay(0.05);
var u1 = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseUp, p, $.kCGMouseButtonLeft);
$.CGEventSetIntegerValueField(u1, $.kCGMouseEventClickState, 1);
$.CGEventPost($.kCGSessionEventTap, u1);
delay(0.05);
var d2 = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseDown, p, $.kCGMouseButtonLeft);
$.CGEventSetIntegerValueField(d2, $.kCGMouseEventClickState, 2);
$.CGEventPost($.kCGSessionEventTap, d2);
delay(0.05);
var u2 = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseUp, p, $.kCGMouseButtonLeft);
$.CGEventSetIntegerValueField(u2, $.kCGMouseEventClickState, 2);
$.CGEventPost($.kCGSessionEventTap, u2);
'DOUBLE_CLICKED';
"""
                sp.run(["osascript", "-l", "JavaScript", "-e", jxa],
                       capture_output=True, text=True, timeout=10)
                time.sleep(0.5)
                snap = self.snapshot()
                snap["click_result"] = f"DOUBLE_CLICKED at ({vx},{vy})"
                return snap
            except Exception as e:
                return {"status": "error", "message": f"Double-click failed: {e}"}

        if ct in ("right", "rightclick", "context"):
            coords_js = (
                "(function() {\n"
                f"  var el = {target_js};\n"
                "  if (!el) return 'NOT_FOUND';\n"
                "  el.scrollIntoView({behavior:'instant',block:'center'});\n"
                "  var r = el.getBoundingClientRect();\n"
                "  return Math.round(r.left+r.width/2)+','+Math.round(r.top+r.height/2);\n"
                "})()"
            )
            coords = self._js_ctx(coords_js)
            if coords == "NOT_FOUND":
                snap = self.snapshot()
                snap["click_result"] = "NOT_FOUND"
                return snap
            try:
                vx, vy = [int(c) for c in coords.split(",")]
                ox, oy = self._get_safari_content_origin()
                sx, sy = ox + vx, oy + vy
                self._osa('tell application "Safari" to activate')
                time.sleep(0.1)
                jxa = f"""
ObjC.import('CoreGraphics');
var p = $.CGPointMake({sx}, {sy});
var d = $.CGEventCreateMouseEvent($(), $.kCGEventRightMouseDown, p, $.kCGMouseButtonRight);
$.CGEventPost($.kCGSessionEventTap, d);
delay(0.05);
var u = $.CGEventCreateMouseEvent($(), $.kCGEventRightMouseUp, p, $.kCGMouseButtonRight);
$.CGEventPost($.kCGSessionEventTap, u);
'RIGHT_CLICKED';
"""
                sp.run(["osascript", "-l", "JavaScript", "-e", jxa],
                       capture_output=True, text=True, timeout=10)
                time.sleep(0.5)
                snap = self.snapshot()
                snap["click_result"] = f"RIGHT_CLICKED at ({vx},{vy})"
                return snap
            except Exception as e:
                return {"status": "error", "message": f"Right-click failed: {e}"}

        # ── Standard click ──
        js = (
            "(function() {\n"
            f"  var el = {target_js};\n"
            "  if (!el) return 'NOT_FOUND';\n"
            "  el.scrollIntoView({behavior:'instant',block:'center'});\n"
            "  if (el.focus) el.focus();\n"
            "  var tag = el.tagName.toLowerCase();\n"
            "  var type = (el.type||'').toLowerCase();\n"
            "  if (type==='checkbox'||type==='radio') {\n"
            "    el.checked = type==='radio' ? true : !el.checked;\n"
            "    el.dispatchEvent(new Event('change',{bubbles:true}));\n"
            "    el.dispatchEvent(new Event('input',{bubbles:true}));\n"
            "    el.dispatchEvent(new MouseEvent('click',{bubbles:true}));\n"
            "    return 'TOGGLED:' + (el.checked?'checked':'unchecked');\n"
            "  }\n"
            "  el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));\n"
            "  el.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,cancelable:true}));\n"
            "  el.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));\n"
            "  if (el.click) el.click();\n"
            "  return 'CLICKED:' + (el.textContent||el.value||tag).trim().substring(0,80);\n"
            "})()"
        )
        result = self._js_ctx(js)
        log.info(f"click({text}) -> {result}")

        # JS completely failed (empty result) — try auto-enable JS, then fall back
        if not result or result.startswith("ERROR"):
            # Try auto-enable JS first
            if not self._js_enable_attempted:
                log.info("Click JS failed — attempting auto-enable JS")
                if self._auto_enable_js():
                    # Retry the click with JS
                    target_js = self._resolve_js(text)
                    result = self._js_ctx(js)
                    if result and not result.startswith("ERROR") and result != "NOT_FOUND":
                        log.info(f"Click succeeded after enabling JS: {result}")
                        self._human_delay("click")
                        time.sleep(1.0)
                        self._wait_idle(0.5, 5.0)
                        snap = self.snapshot()
                        snap["click_result"] = result
                        return snap

            # Resolve #N refs from noscript_refs to get the element's text
            click_text = text
            if text.strip().startswith("#") and text.strip()[1:].isdigit():
                ref_n = int(text.strip()[1:])
                ref_info = self._noscript_refs.get(ref_n)
                if ref_info and ref_info.get("text"):
                    click_text = ref_info["text"]
                    log.info(f"Resolved noscript ref #{ref_n} -> '{click_text}'")

            log.warning(f"JS click returned empty/error — trying Accessibility API click for '{click_text}'")
            ax_result = self._click_via_accessibility(click_text)
            if ax_result and "CLICKED" in ax_result:
                time.sleep(1.0)
                snap = self.snapshot()
                snap["click_result"] = ax_result
                return snap
            # If accessibility also fails, try search + coordinate click for text targets
            if not click_text.startswith("#"):
                log.warning(f"Accessibility click failed — trying coordinate search for '{click_text}'")
                ax_coords = self._find_text_coordinates_ax(click_text)
                if ax_coords:
                    coord_result = self._click_at_viewport(ax_coords[0], ax_coords[1])
                    time.sleep(1.0)
                    snap = self.snapshot()
                    snap["click_result"] = f"AX_COORD_CLICK:{coord_result} (found '{click_text}' via accessibility)"
                    return snap

        # Stale ref recovery: if #N not found, try coordinate click
        if result == "NOT_FOUND":
            if text.strip().startswith("#") and text.strip()[1:].isdigit():
                n = int(text.strip()[1:])
                coords = self._js_ctx(
                    f"JSON.stringify(window.__safari_refs_coords && window.__safari_refs_coords[{n}])"
                )
                if coords and coords != "null" and coords != "undefined":
                    try:
                        cx, cy = json.loads(coords)
                        self._human_delay("click")
                        coord_result = self._click_at_viewport(cx, cy)
                        time.sleep(1.0)
                        self._wait_idle(0.5, 5.0)
                        snap = self.snapshot()
                        snap["click_result"] = f"COORD_CLICK:{coord_result} (ref #{n} was stale, used coords @{cx},{cy})"
                        return snap
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                # Coords not available either — return snapshot with hint
                snap = self.snapshot()
                snap["click_result"] = "STALE_REF: Element #{} not found. Page may have changed. Use updated #N refs above.".format(n)
                snap["hint"] = "STALE_REF"
                return snap

            # Text-based click not found
            snap = self.snapshot()
            snap["click_result"] = f"NOT_FOUND: No element matching '{text}'. Check snapshot for available elements."
            return snap

        self._human_delay("click")
        time.sleep(1.0)
        self._wait_idle(0.5, 5.0)
        snap = self.snapshot()
        snap["click_result"] = result
        return snap

    # ─── Click at coordinates ────────────────────────────────────

    def click_at(self, coords: str) -> Dict:
        """Click at viewport coordinates. coords format: 'x,y'"""
        try:
            parts = coords.strip().split(",")
            vx, vy = int(parts[0].strip()), int(parts[1].strip())
        except (ValueError, IndexError):
            return {"status": "error", "message": f"Invalid coordinates: {coords}. Use format: 'x,y'"}

        self._human_delay("click")
        result = self._click_at_viewport(vx, vy)
        time.sleep(1.0)
        self._wait_idle(0.5, 5.0)
        snap = self.snapshot()
        snap["click_result"] = result
        return snap

    # ─── Fill ────────────────────────────────────────────────────

    def fill(self, text: str, value: str, fast: bool = False) -> Dict:
        target_js = self._resolve_js(text)

        # Check field type (password fields always use JS for security)
        field_type = self._js_ctx(
            "(function() {\n"
            f"  var el = {target_js};\n"
            "  if (!el) return 'NOT_FOUND';\n"
            "  return (el.type||el.tagName||'text').toLowerCase();\n"
            "})()"
        )

        # No-JS fallback: if JS returns empty, try to enable JS first
        if not field_type or field_type.startswith("ERROR"):
            log.warning(f"JS fill check returned '{field_type}' for '{text}' — attempting recovery")

            # Try to auto-enable JS from Apple Events
            if not self._js_enable_attempted:
                log.info("Attempting auto-enable JS before falling back to noscript fill")
                if self._auto_enable_js():
                    # JS is now enabled and page reloaded — retry with JS
                    log.info("JS enabled! Retrying fill with JS...")
                    time.sleep(0.5)
                    # Re-snapshot to get fresh refs
                    self.snapshot()
                    time.sleep(0.3)
                    # Retry the fill with JS
                    target_js = self._resolve_js(text)
                    field_type = self._js_ctx(
                        "(function() {\n"
                        f"  var el = {target_js};\n"
                        "  if (!el) return 'NOT_FOUND';\n"
                        "  return (el.type||el.tagName||'text').toLowerCase();\n"
                        "})()"
                    )
                    if field_type and not field_type.startswith("ERROR") and field_type != "NOT_FOUND":
                        log.info(f"JS fill now working! field_type={field_type}")
                        # Fall through to normal JS fill below
                    else:
                        log.warning("JS enabled but field still not found — using noscript")
                        return self._fill_noscript(text, value)
                else:
                    log.warning("Auto-enable JS failed — using noscript fill")
                    return self._fill_noscript(text, value)
            else:
                return self._fill_noscript(text, value)

        if field_type == "NOT_FOUND":
            if text.strip().startswith("#"):
                # Try noscript ref resolution
                ref_info = None
                if text.strip()[1:].isdigit():
                    ref_info = self._noscript_refs.get(int(text.strip()[1:]))
                if ref_info:
                    return self._fill_noscript(ref_info.get("text", "") or ref_info.get("name", ""), value)
                snap = self.snapshot()
                snap["fill_result"] = "STALE_REF: Element not found. Use updated #N refs."
                snap["hint"] = "STALE_REF"
                return snap
            return {"status": "error", "fill_result": "NOT_FOUND", "message": f"Field '{text}' not found."}

        is_password = field_type == "password"
        is_contenteditable = field_type in ("div", "span", "p", "true")

        # Focus and clear the field
        if is_contenteditable:
            clear_js = (
                "(function() {\n"
                f"  var el = {target_js};\n"
                "  if (!el) return 'NOT_FOUND';\n"
                "  el.scrollIntoView({behavior:'instant',block:'center'});\n"
                "  el.focus();\n"
                "  el.innerHTML = '';\n"
                "  return 'READY';\n"
                "})()"
            )
        else:
            clear_js = (
                "(function() {\n"
                f"  var el = {target_js};\n"
                "  if (!el) return 'NOT_FOUND';\n"
                "  el.scrollIntoView({behavior:'instant',block:'center'});\n"
                "  el.focus();\n"
                "  el.value = '';\n"
                "  el.dispatchEvent(new Event('input',{bubbles:true}));\n"
                "  return 'READY';\n"
                "})()"
            )
        self._js_ctx(clear_js)

        self._human_delay("fill_pre")

        # Choose fill strategy:
        # - Passwords: always JS (never log keystrokes)
        # - ASCII <=200 chars: AppleScript keystrokes (human-like, but only safe ASCII)
        # - Everything else: JS value setter (fast, handles unicode)
        use_keystrokes = (
            not fast
            and not is_password
            and not is_contenteditable
            and len(value) <= 200
            and all(32 <= ord(c) <= 126 for c in value)  # ASCII printable only
        )

        if use_keystrokes:
            # Human-like typing via System Events (real key events)
            safe = value.replace("\\", "\\\\").replace('"', '\\"')
            self._osa(
                'tell application "Safari" to activate\n'
                'tell application "System Events"\n'
                f'  tell process "Safari" to keystroke "{safe}"\n'
                'end tell'
            )
        elif is_contenteditable:
            # Contenteditable: use insertText command
            safe_val = json.dumps(value)  # proper JSON string escaping
            self._js_ctx(
                "(function() {\n"
                f"  var el = {target_js};\n"
                "  if (!el) return 'NOT_FOUND';\n"
                "  el.focus();\n"
                f"  document.execCommand('insertText', false, {safe_val});\n"
                "  return 'SET';\n"
                "})()"
            )
        else:
            # JS value setter (handles unicode, passwords, long text)
            safe_val = json.dumps(value)  # proper JSON string escaping
            self._js_ctx(
                "(function() {\n"
                f"  var el = {target_js};\n"
                "  if (!el) return 'NOT_FOUND';\n"
                f"  var val = {safe_val};\n"
                "  var proto = el.tagName==='TEXTAREA'\n"
                "    ? window.HTMLTextAreaElement.prototype\n"
                "    : window.HTMLInputElement.prototype;\n"
                "  var setter = Object.getOwnPropertyDescriptor(proto,'value');\n"
                "  if (setter && setter.set) setter.set.call(el, val);\n"
                "  else el.value = val;\n"
                "  return 'SET';\n"
                "})()"
            )

        # Dispatch React/Vue/Angular compatible events
        self._js_ctx(
            "(function() {\n"
            f"  var el = {target_js};\n"
            "  if (!el) return;\n"
            "  el.dispatchEvent(new Event('input',{bubbles:true}));\n"
            "  el.dispatchEvent(new Event('change',{bubbles:true}));\n"
            "  el.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:'a'}));\n"
            "  el.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true,key:'a'}));\n"
            "  try { el.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,inputType:'insertText'})); } catch(e) {}\n"
            "})()"
        )

        log.info(f"fill({text},{'***' if is_password else value[:30]}...) -> OK")
        time.sleep(0.3)
        snap = self.snapshot()
        snap["fill_result"] = f"FILLED:{'***' if is_password else value[:50]}"
        return snap

    # ─── Fill (no-JS fallback) ───────────────────────────────────

    def _fill_noscript(self, field_identifier: str, value: str) -> Dict:
        """Fill a form field using Accessibility API + System Events keystrokes.

        Works without JavaScript. Uses multiple strategies to find and focus
        the correct field, then types the value via keystrokes.

        Strategy order:
        1. Match field in _noscript_refs by placeholder/name → determine field index
        2. Use 'entire contents' accessibility walk to find ALL text fields
        3. Click the matching field (by description/title) or the Nth field
        4. Clear with Cmd+A, then type value via keystroke
        5. If all else fails, use Tab navigation from current position
        """
        self._osa('tell application "Safari" to activate')
        time.sleep(0.3)

        safe_id = field_identifier.replace('"', '\\"').replace("'", "\\'")

        # ── Strategy 1: Resolve target from noscript_refs ──
        target_field_index = None  # 0-based index among fillable fields
        fillable_refs = []  # ordered list of (ref_n, ref_info) for inputs/textareas

        for n, ref in sorted(self._noscript_refs.items()):
            if ref.get("tag") in ("input", "textarea"):
                ref_type = (ref.get("type", "") or "text").lower()
                if ref_type not in ("submit", "button", "hidden", "checkbox", "radio", "image"):
                    fillable_refs.append((n, ref))

        # Match by placeholder text, name, or type
        id_lower = field_identifier.lower().strip()
        for i, (n, ref) in enumerate(fillable_refs):
            ref_text = (ref.get("text", "") or "").lower()
            ref_name = (ref.get("name", "") or "").lower()
            if id_lower and (
                id_lower == ref_text
                or id_lower == ref_name
                or id_lower in ref_text
                or id_lower in ref_name
                or ref_text in id_lower
            ):
                target_field_index = i
                log.info(f"Matched '{field_identifier}' to noscript_ref #{n} "
                         f"(index {i}): {ref}")
                break

        # If we have a #N ref, look it up directly
        if target_field_index is None and field_identifier.strip().startswith("#"):
            try:
                ref_n = int(field_identifier.strip()[1:])
                for i, (n, _) in enumerate(fillable_refs):
                    if n == ref_n:
                        target_field_index = i
                        break
            except ValueError:
                pass

        log.info(f"_fill_noscript: id='{field_identifier}', target_index={target_field_index}, "
                 f"total_fillable={len(fillable_refs)}")

        # ── Strategy 2: Full accessibility tree walk with 'entire contents' ──
        focused = self._osa(f"""
tell application "System Events"
    tell process "Safari"
        tell window 1
            try
                -- Get the web content area (Safari's main content group)
                set webArea to first group whose role is "AXGroup"

                -- Walk ENTIRE tree to find all text fields at any depth
                set allFields to every text field of entire contents of webArea

                if (count of allFields) = 0 then
                    return "NO_FIELDS:0"
                end if

                -- First pass: try exact match by description or title
                set fieldCount to count of allFields
                repeat with idx from 1 to fieldCount
                    set f to item idx of allFields
                    try
                        set fdesc to description of f
                        set ftitle to title of f
                        if fdesc contains "{safe_id}" or ftitle contains "{safe_id}" then
                            click f
                            return "FOCUSED_MATCH:" & idx & ":" & fdesc
                        end if
                    end try
                end repeat

                -- Second pass: click by index if we know which field to target
                if {target_field_index if target_field_index is not None else -1} >= 0 then
                    set targetIdx to {(target_field_index or 0) + 1}
                    if targetIdx ≤ fieldCount then
                        click item targetIdx of allFields
                        return "FOCUSED_INDEX:" & targetIdx & "/" & fieldCount
                    end if
                end if

                -- Fallback: click the first unfilled field
                repeat with idx from 1 to fieldCount
                    set f to item idx of allFields
                    try
                        set fval to value of f
                        if fval is "" or fval is missing value then
                            click f
                            return "FOCUSED_EMPTY:" & idx & "/" & fieldCount
                        end if
                    on error
                        click f
                        return "FOCUSED_FALLBACK:" & idx & "/" & fieldCount
                    end try
                end repeat

                -- Last resort: click the first field
                click item 1 of allFields
                return "FOCUSED_FIRST:" & fieldCount
            on error errMsg
                -- Fallback: try without the AXGroup requirement
                try
                    set allFields to every text field of entire contents of group 1
                    if (count of allFields) > 0 then
                        if {target_field_index if target_field_index is not None else -1} >= 0 then
                            set targetIdx to {(target_field_index or 0) + 1}
                            if targetIdx ≤ (count of allFields) then
                                click item targetIdx of allFields
                                return "FOCUSED_G1_INDEX:" & targetIdx
                            end if
                        end if
                        click item 1 of allFields
                        return "FOCUSED_G1_FIRST"
                    end if
                end try
                return "NO_FIELD_FOUND:" & errMsg
            end try
        end tell
    end tell
end tell
""")
        log.info(f"_fill_noscript focus result: {focused}")

        # ── Strategy 3: Tab navigation fallback ──
        if "NO_FIELD" in (focused or "") or "NO_FIELDS" in (focused or ""):
            # Click the page content area first to establish focus
            self._osa(
                'tell application "System Events"\n'
                '  tell process "Safari"\n'
                '    tell window 1\n'
                '      try\n'
                '        set webArea to first group whose role is "AXGroup"\n'
                '        click webArea\n'
                '      end try\n'
                '    end tell\n'
                '  end tell\n'
                'end tell'
            )
            time.sleep(0.3)

            # Tab to the target field (or just Tab once if no target known)
            tab_count = (target_field_index or 0) + 1
            for _ in range(tab_count):
                self._osa(
                    'tell application "System Events"\n'
                    '  tell process "Safari" to key code 48\n'  # Tab
                    'end tell'
                )
                time.sleep(0.15)
            log.info(f"Tab navigation: pressed Tab {tab_count} times")

        # ── Clear existing value and type new value ──
        time.sleep(0.15)

        # Select all in the field (Cmd+A) then type
        self._osa(
            'tell application "System Events"\n'
            '  tell process "Safari"\n'
            '    keystroke "a" using command down\n'
            '  end tell\n'
            'end tell'
        )
        time.sleep(0.1)

        # Type the value via keystrokes
        safe_val = value.replace("\\", "\\\\").replace('"', '\\"')
        if all(32 <= ord(c) <= 126 for c in value):
            self._osa(
                'tell application "System Events"\n'
                f'  tell process "Safari" to keystroke "{safe_val}"\n'
                'end tell'
            )
        else:
            # For non-ASCII, use clipboard paste
            self._osa(f'set the clipboard to "{safe_val}"')
            time.sleep(0.1)
            self._osa(
                'tell application "System Events"\n'
                '  tell process "Safari" to keystroke "v" using command down\n'
                'end tell'
            )

        log.info(f"_fill_noscript typed: '{value[:30]}' into field "
                 f"(index={target_field_index})")
        time.sleep(0.5)
        snap = self.snapshot()
        snap["fill_result"] = (
            f"NOSCRIPT_FILLED:{value[:50]} "
            f"(field_index={target_field_index}, focus={focused[:60] if focused else 'none'})"
        )
        return snap

    # ─── Select (dropdown / radio) ───────────────────────────────

    def select(self, text: str, value: str) -> Dict:
        target_js = self._resolve_js(text)
        safe_val = value.replace("\\", "\\\\").replace("'", "\\'")
        js = (
            "(function() {\n"
            f"  var el = {target_js};\n"
            "  if (!el) return 'NOT_FOUND';\n"
            "  el.scrollIntoView({behavior:'instant',block:'center'});\n"
            "  var tag = el.tagName.toLowerCase();\n"
            "  if (tag === 'select') {\n"
            "    var found = false;\n"
            "    for (var i = 0; i < el.options.length; i++) {\n"
            f"      if (el.options[i].text.trim().toLowerCase().includes('{safe_val}'.toLowerCase())\n"
            f"        || el.options[i].value.toLowerCase() === '{safe_val}'.toLowerCase()) {{\n"
            "        el.selectedIndex = i; found = true; break;\n"
            "      }\n"
            "    }\n"
            "    if (!found) return 'OPTION_NOT_FOUND';\n"
            "    var setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype,'selectedIndex');\n"
            "    if (setter && setter.set) setter.set.call(el, el.selectedIndex);\n"
            "    el.dispatchEvent(new Event('change',{bubbles:true}));\n"
            "    el.dispatchEvent(new Event('input',{bubbles:true}));\n"
            "    return 'SELECTED:' + el.options[el.selectedIndex].text;\n"
            "  }\n"
            "  if (el.type === 'radio') {\n"
            "    el.checked = true;\n"
            "    el.dispatchEvent(new Event('change',{bubbles:true}));\n"
            "    el.dispatchEvent(new MouseEvent('click',{bubbles:true}));\n"
            "    return 'RADIO_SELECTED';\n"
            "  }\n"
            # Custom dropdown fallback: click trigger, wait, then find option
            "  el.click();\n"
            "  return 'CUSTOM_OPENED';\n"
            "})()"
        )
        result = self._js_ctx(js)
        log.info(f"select({text},{value}) -> {result}")

        if result == "NOT_FOUND":
            if text.strip().startswith("#"):
                snap = self.snapshot()
                snap["select_result"] = "STALE_REF: Element not found. Use updated #N refs."
                snap["hint"] = "STALE_REF"
                return snap
            return {"status": "error", "select_result": "NOT_FOUND"}

        if result == "CUSTOM_OPENED":
            # Custom dropdown: wait for options to appear, then click matching one
            self._human_delay("select")
            find_option_js = (
                "(function() {\n"
                f"  var q = '{safe_val}'.toLowerCase();\n"
                "  var sels = '[role=option],[role=listbox] > *,.dropdown-item,.dropdown-menu li,"
                "ul[role=listbox] li,.select-option,.option,li[data-value]';\n"
                "  var opts = document.querySelectorAll(sels);\n"
                "  for (var i = 0; i < opts.length; i++) {\n"
                "    var t = (opts[i].textContent||'').trim().toLowerCase();\n"
                "    if (t.includes(q)) {\n"
                "      opts[i].click();\n"
                "      return 'CUSTOM_SELECTED:' + opts[i].textContent.trim().substring(0,60);\n"
                "    }\n"
                "  }\n"
                "  return 'CUSTOM_OPTION_NOT_FOUND';\n"
                "})()"
            )
            result = self._js_ctx(find_option_js)

        self._human_delay("select")
        time.sleep(0.5)
        snap = self.snapshot()
        snap["select_result"] = result
        return snap

    # ─── Scroll ──────────────────────────────────────────────────

    def scroll(self, direction: str = "down", amount: int = 600,
                text_target: str = "") -> Dict:
        d = direction.lower().strip()

        # ── Scroll to text: scroll down until the text is visible ──
        if text_target:
            safe = text_target.replace("\\", "\\\\").replace("'", "\\'").lower()
            for attempt in range(20):
                found = self._js_ctx(
                    f"(document.body.innerText||'').toLowerCase().includes('{safe}')"
                    "? (function() {{"
                    f"  var tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);"
                    "  while (tw.nextNode()) {{"
                    f"    if (tw.currentNode.textContent.toLowerCase().includes('{safe}')) {{"
                    "      tw.currentNode.parentElement.scrollIntoView({{behavior:'instant',block:'center'}});"
                    "      return 'FOUND';"
                    "    }}"
                    "  }}"
                    "  return 'NOT_VISIBLE';"
                    "}})()"
                    ": 'NOT_FOUND'"
                )
                if found == "FOUND":
                    time.sleep(0.3)
                    snap = self.snapshot()
                    snap["scroll_result"] = f"Scrolled to: {text_target}"
                    return snap
                # Not found yet — check if we can scroll more
                at_bottom = self._js_ctx(
                    "(window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 10)"
                    "? 'BOTTOM' : 'MORE'"
                )
                if at_bottom == "BOTTOM":
                    snap = self.snapshot()
                    snap["scroll_result"] = f"NOT_FOUND: '{text_target}' not found after scrolling to bottom"
                    return snap
                self._js_ctx(f"window.scrollBy(0,{amount})")
                time.sleep(0.4)
            snap = self.snapshot()
            snap["scroll_result"] = f"TIMEOUT: '{text_target}' not found after 20 scrolls"
            return snap

        # ── Infinite scroll: scroll to bottom, loading all lazy content ──
        if d in ("infinite", "load_all", "all"):
            prev_height = 0
            for _ in range(30):
                self._js_ctx("window.scrollTo(0,document.documentElement.scrollHeight)")
                time.sleep(1.5)
                new_height = self._js_ctx("document.documentElement.scrollHeight")
                try:
                    new_height = int(new_height)
                except (ValueError, TypeError):
                    break
                if new_height == prev_height:
                    break
                prev_height = new_height
            snap = self.snapshot()
            snap["scroll_result"] = f"Loaded all content (height: {prev_height}px)"
            return snap

        # ── Standard scroll ──
        js_map = {
            "down": f"window.scrollBy(0,{amount})",
            "d": f"window.scrollBy(0,{amount})",
            "up": f"window.scrollBy(0,-{amount})",
            "u": f"window.scrollBy(0,-{amount})",
            "left": f"window.scrollBy(-{amount},0)",
            "right": f"window.scrollBy({amount},0)",
            "bottom": "window.scrollTo(0,document.documentElement.scrollHeight)",
            "end": "window.scrollTo(0,document.documentElement.scrollHeight)",
            "top": "window.scrollTo(0,0)",
            "start": "window.scrollTo(0,0)",
            "home": "window.scrollTo(0,0)",
        }
        self._js_ctx(js_map.get(d, f"window.scrollBy(0,{amount})"))
        self._human_delay("scroll")
        time.sleep(0.3)
        return self.snapshot()

    # ─── Keyboard ────────────────────────────────────────────────

    def keys(self, keystrokes: str) -> Dict:
        key_codes = {
            "enter": 36, "return": 36, "tab": 48,
            "escape": 53, "esc": 53,
            "backspace": 51, "delete": 117, "space": 49,
            "up": 126, "down": 125, "left": 123, "right": 124,
            "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
            "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96,
            "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
            "f11": 103, "f12": 111,
        }
        k = keystrokes.strip()
        k_lower = k.lower()

        # ── Modifier combos: "cmd+a", "cmd+shift+r", "ctrl+c", etc. ──
        if '+' in k_lower:
            parts = k_lower.split('+')
            modifiers = []
            key_part = parts[-1].strip()
            for mod in parts[:-1]:
                mod = mod.strip()
                if mod in ('cmd', 'command', '⌘'):
                    modifiers.append('command down')
                elif mod in ('shift', '⇧'):
                    modifiers.append('shift down')
                elif mod in ('alt', 'option', 'opt', '⌥'):
                    modifiers.append('option down')
                elif mod in ('ctrl', 'control', '⌃'):
                    modifiers.append('control down')
            mod_str = ', '.join(modifiers)

            self._osa('tell application "Safari" to activate')
            time.sleep(0.1)

            if key_part in key_codes:
                self._osa(
                    'tell application "System Events"\n'
                    f'  tell process "Safari" to key code {key_codes[key_part]}'
                    f' using {{{mod_str}}}\n'
                    'end tell'
                )
            else:
                safe_key = key_part.replace("\\", "\\\\").replace('"', '\\"')
                self._osa(
                    'tell application "System Events"\n'
                    f'  tell process "Safari" to keystroke "{safe_key}"'
                    f' using {{{mod_str}}}\n'
                    'end tell'
                )
            time.sleep(0.3)
            return self.snapshot()

        # ── Single special key ──
        if k_lower in key_codes:
            self._osa(
                'tell application "Safari" to activate\n'
                'tell application "System Events"\n'
                f'  tell process "Safari" to key code {key_codes[k_lower]}\n'
                'end tell'
            )
        else:
            safe = keystrokes.replace("\\", "\\\\").replace('"', '\\"')
            self._osa(
                'tell application "Safari" to activate\n'
                'tell application "System Events"\n'
                f'  tell process "Safari" to keystroke "{safe}"\n'
                'end tell'
            )
        time.sleep(0.3)
        return self.snapshot()

    # ─── Wait ────────────────────────────────────────────────────

    def wait(self, text: str = "", css: str = "",
             condition: str = "appear", max_wait: int = 15) -> Dict:
        """Wait for a condition on the page.

        Conditions:
          appear    — wait for text/css to exist (default)
          disappear — wait for text/css to vanish (loading spinners)
          url_change — wait for URL to change
          stable    — wait for DOM to stop changing
        """
        cond = condition.lower().strip()

        if cond == "stable":
            self._wait_idle(1.0, max_wait)
            snap = self.snapshot()
            snap["wait_result"] = "Page stabilized"
            return snap

        if cond in ("network", "idle", "network_idle"):
            self._inject_idle_hooks()
            deadline = time.time() + max_wait
            while time.time() < deadline:
                pending = self._js(
                    "(window.__safari_pending || 0)"
                )
                try:
                    if int(pending) == 0:
                        time.sleep(0.5)
                        # Double check
                        pending2 = self._js("(window.__safari_pending || 0)")
                        if int(pending2) == 0:
                            snap = self.snapshot()
                            snap["wait_result"] = "Network idle"
                            return snap
                except (ValueError, TypeError):
                    pass
                time.sleep(0.3)
            snap = self.snapshot()
            snap["wait_result"] = f"TIMEOUT: Network not idle after {max_wait}s"
            return snap

        if cond == "url_change":
            current_url = self._osa(
                'tell application "Safari" to return URL of current tab of front window'
            )
            for _ in range(max_wait * 2):
                new_url = self._osa(
                    'tell application "Safari" to return URL of current tab of front window'
                )
                if new_url != current_url:
                    self._wait_idle(0.5, 5.0)
                    snap = self.snapshot()
                    snap["wait_result"] = f"URL changed: {new_url}"
                    return snap
                time.sleep(0.5)
            snap = self.snapshot()
            snap["wait_result"] = f"TIMEOUT: URL did not change after {max_wait}s"
            return snap

        target = text or css
        for _ in range(max_wait * 2):
            if text:
                safe = text.replace("\\", "\\\\").replace("'", "\\'").lower()
                found = self._js_ctx(
                    f"(document.body.innerText||'').toLowerCase().includes('{safe}')?'FOUND':'WAITING'"
                )
            elif css:
                safe = css.replace("\\", "\\\\").replace("'", "\\'")
                found = self._js_ctx(
                    f"document.querySelector('{safe}')?'FOUND':'WAITING'"
                )
            else:
                time.sleep(max_wait)
                return self.snapshot()

            if cond == "disappear":
                if found == "WAITING":  # element NOT found = disappeared
                    snap = self.snapshot()
                    snap["wait_result"] = f"Disappeared: {target}"
                    return snap
            else:  # appear
                if found == "FOUND":
                    snap = self.snapshot()
                    snap["wait_result"] = f"Found: {target}"
                    return snap
            time.sleep(0.5)

        snap = self.snapshot()
        snap["wait_result"] = f"TIMEOUT: condition '{cond}' for '{target}' not met after {max_wait}s"
        return snap

    # ─── Hover ───────────────────────────────────────────────────

    def hover(self, text: str) -> Dict:
        target_js = self._resolve_js(text)
        js = (
            "(function() {\n"
            f"  var el = {target_js};\n"
            "  if (!el) return 'NOT_FOUND';\n"
            "  el.scrollIntoView({behavior:'instant',block:'center'});\n"
            "  el.dispatchEvent(new MouseEvent('mouseenter',{bubbles:true}));\n"
            "  el.dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));\n"
            "  return 'HOVERED:' + (el.textContent||'').trim().substring(0,50);\n"
            "})()"
        )
        result = self._js_ctx(js)
        time.sleep(0.5)
        snap = self.snapshot()
        snap["hover_result"] = result
        return snap

    # ─── Drag and Drop ────────────────────────────────────────────

    def drag(self, from_text: str, to_text: str) -> Dict:
        """Drag from one element/coordinate to another.

        Supports: #N refs, 'x,y' coordinates, or text search.
        Useful for sliders, sortable lists, rating widgets.
        """
        # Resolve source coordinates
        src_coords = self._resolve_coords(from_text)
        if not src_coords:
            snap = self.snapshot()
            snap["drag_result"] = f"NOT_FOUND: Source '{from_text}'"
            return snap

        # Resolve destination coordinates
        dst_coords = self._resolve_coords(to_text)
        if not dst_coords:
            snap = self.snapshot()
            snap["drag_result"] = f"NOT_FOUND: Destination '{to_text}'"
            return snap

        sx, sy = src_coords
        dx, dy = dst_coords

        # Convert viewport coords to screen coords
        ox, oy = self._get_safari_content_origin()
        abs_sx, abs_sy = ox + sx, oy + sy
        abs_dx, abs_dy = ox + dx, oy + dy

        self._osa('tell application "Safari" to activate')
        time.sleep(0.1)

        # Smooth drag via CoreGraphics (JXA)
        steps = max(10, int(((abs_dx - abs_sx)**2 + (abs_dy - abs_sy)**2)**0.5 / 5))
        jxa = f"""
ObjC.import('CoreGraphics');

var sx = {abs_sx}, sy = {abs_sy};
var dx = {abs_dx}, dy = {abs_dy};
var steps = {steps};

// Mouse down at source
var p = $.CGPointMake(sx, sy);
var down = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseDown, p, $.kCGMouseButtonLeft);
$.CGEventPost($.kCGSessionEventTap, down);
delay(0.05);

// Smooth drag
for (var i = 1; i <= steps; i++) {{
    var t = i / steps;
    var cx = sx + (dx - sx) * t;
    var cy = sy + (dy - sy) * t;
    var mp = $.CGPointMake(cx, cy);
    var drag = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseDragged, mp, $.kCGMouseButtonLeft);
    $.CGEventPost($.kCGSessionEventTap, drag);
    delay(0.005);
}}

// Mouse up at destination
var dp = $.CGPointMake(dx, dy);
var up = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseUp, dp, $.kCGMouseButtonLeft);
$.CGEventPost($.kCGSessionEventTap, up);

'DRAGGED:(' + sx + ',' + sy + ')->(' + dx + ',' + dy + ')';
"""
        try:
            r = sp.run(
                ["osascript", "-l", "JavaScript", "-e", jxa],
                capture_output=True, text=True, timeout=15
            )
            result = r.stdout.strip() or r.stderr.strip()
            log.info(f"drag({from_text}->{to_text}) -> {result}")
        except Exception as e:
            result = f"DRAG_FAILED: {e}"

        time.sleep(0.5)
        snap = self.snapshot()
        snap["drag_result"] = result
        return snap

    def _resolve_coords(self, text: str) -> Optional[tuple]:
        """Resolve a target specification to viewport (x, y) coordinates."""
        text = text.strip()

        # Direct coordinates: "350,200"
        if ',' in text and all(p.strip().lstrip('-').isdigit() for p in text.split(',')):
            parts = text.split(',')
            return int(parts[0].strip()), int(parts[1].strip())

        # Element ref or text search
        target_js = self._resolve_js(text)
        coords = self._js_ctx(
            "(function() {\n"
            f"  var el = {target_js};\n"
            "  if (!el) return 'NOT_FOUND';\n"
            "  el.scrollIntoView({behavior:'instant',block:'center'});\n"
            "  var r = el.getBoundingClientRect();\n"
            "  return Math.round(r.left+r.width/2)+','+Math.round(r.top+r.height/2);\n"
            "})()"
        )
        if coords and coords != "NOT_FOUND":
            try:
                parts = coords.split(",")
                return int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                pass
        return None

    # ─── Window / Popup Management ────────────────────────────────

    def window_manage(self, action: str = "list") -> Dict:
        """Manage Safari windows — handle popups, new windows, login dialogs.

        Actions:
          list   — list all Safari windows
          switch N — switch to window N
          close  — close current window
          popup  — switch to most recent popup window
          main   — switch back to the first (main) window
          count  — return window count
        """
        a = action.lower().strip()

        if a in ("list", ""):
            info = self._osa(
                'tell application "Safari"\n'
                '  set out to ""\n'
                '  set idx to 1\n'
                '  repeat with w in windows\n'
                '    if w = front window then\n'
                '      set out to out & "→ [" & idx & "] " & (name of w) & " — " & (URL of current tab of w) & linefeed\n'
                '    else\n'
                '      set out to out & "  [" & idx & "] " & (name of w) & " — " & (URL of current tab of w) & linefeed\n'
                '    end if\n'
                '    set idx to idx + 1\n'
                '  end repeat\n'
                '  return out\n'
                'end tell'
            )
            return {"status": "success", "windows": info, "message": info}

        if a == "count":
            count = self._osa('tell application "Safari" to return count of windows')
            return {"status": "success", "count": count, "message": f"Safari has {count} window(s)"}

        if a == "close":
            self._osa('tell application "Safari" to close front window')
            time.sleep(0.3)
            return self.snapshot()

        if a in ("popup", "last", "newest"):
            # Switch to the last (newest) window — typically a popup
            count = self._osa('tell application "Safari" to return count of windows')
            try:
                n = int(count)
                if n > 1:
                    self._osa(f'tell application "Safari" to set index of window {n} to 1')
                    self._iframe_idx = None
                    time.sleep(0.5)
                    return self.snapshot()
            except ValueError:
                pass
            return {"status": "error", "message": "No popup window found"}

        if a in ("main", "first"):
            self._osa('tell application "Safari" to set index of window 1 to 1')
            self._iframe_idx = None
            time.sleep(0.3)
            return self.snapshot()

        # switch N
        m = re.search(r'\d+', a)
        if m:
            idx = int(m.group())
            self._osa(f'tell application "Safari" to set index of window {idx} to 1')
            self._iframe_idx = None
            time.sleep(0.3)
            return self.snapshot()

        return {"status": "error", "message": f"Unknown window action: {a}. Use: list, switch N, close, popup, main"}

    # ─── Accessibility API query (fallback) ───────────────────────

    def _accessibility_elements(self) -> str:
        """Extract UI elements via macOS Accessibility API as fallback.

        Uses System Events to enumerate Safari's AX tree. Works even when
        JavaScript is blocked or the page uses complex Shadow DOM.
        """
        result = self._osa("""
tell application "System Events"
    tell process "Safari"
        tell window 1
            set out to ""
            try
                tell group 1 -- web content area
                    set allElements to every UI element
                    set idx to 1
                    repeat with el in allElements
                        try
                            set elRole to role of el
                            set elDesc to description of el
                            set elVal to ""
                            try
                                set elVal to value of el
                            end try
                            set elTitle to ""
                            try
                                set elTitle to title of el
                            end try
                            set {ex, ey} to position of el
                            set {ew, eh} to size of el
                            set out to out & "[" & idx & "] " & elRole & " "
                            if elTitle is not "" then set out to out & "\"" & elTitle & "\" "
                            if elDesc is not "" then set out to out & "desc=\"" & elDesc & "\" "
                            if elVal is not "" then set out to out & "val=\"" & (text 1 thru (min of {60, length of elVal}) of elVal) & "\" "
                            set out to out & "@" & ex & "," & ey & " " & ew & "x" & eh
                            set out to out & linefeed
                            set idx to idx + 1
                        end try
                        if idx > 100 then exit repeat
                    end repeat
                end tell
            end try
            return out
        end tell
    end tell
end tell
""")
        if result:
            return f"=== ACCESSIBILITY ELEMENTS ===\n{result}"
        return ""

    # ─── Element occlusion check ──────────────────────────────────

    def _check_occlusion(self, target_js: str) -> str:
        """Check if an element is occluded (covered by another element)."""
        return self._js_ctx(
            "(function() {\n"
            f"  var el = {target_js};\n"
            "  if (!el) return 'NOT_FOUND';\n"
            "  var r = el.getBoundingClientRect();\n"
            "  var cx = r.left + r.width / 2;\n"
            "  var cy = r.top + r.height / 2;\n"
            "  var top = document.elementFromPoint(cx, cy);\n"
            "  if (!top) return 'NO_ELEMENT_AT_POINT';\n"
            "  if (top === el || el.contains(top) || top.closest && top.closest('[data-ref]') === el) return 'VISIBLE';\n"
            "  return 'OCCLUDED_BY:' + top.tagName + (top.className ? '.' + top.className.split(' ')[0] : '');\n"
            "})()"
        )

    # ─── Accessibility-based clicking (no-JS fallback) ──────────

    def _click_via_accessibility(self, text: str) -> str:
        """Click an element using macOS Accessibility API (no JavaScript needed).

        Searches Safari's FULL accessibility tree (using 'entire contents')
        for an element matching the text and performs an AXPress/click action.
        Checks buttons, links, static texts, and all UI elements recursively.
        """
        safe_text = text.replace('"', '\\"').replace("'", "\\'")
        result = self._osa(f"""
tell application "Safari" to activate
delay 0.2
tell application "System Events"
    tell process "Safari"
        tell window 1
            set foundIt to false

            -- Get the web content area
            try
                set webArea to first group whose role is "AXGroup"
            on error
                set webArea to group 1
            end try

            -- Strategy 1: Direct button/link by name (fastest)
            if not foundIt then
                try
                    click button "{safe_text}" of webArea
                    set foundIt to true
                end try
            end if
            if not foundIt then
                try
                    click link "{safe_text}" of webArea
                    set foundIt to true
                end try
            end if

            -- Strategy 2: Walk ENTIRE tree for buttons/links at any depth
            if not foundIt then
                try
                    set allButtons to every button of entire contents of webArea
                    repeat with b in allButtons
                        try
                            set bTitle to title of b
                            set bDesc to description of b
                            if bTitle contains "{safe_text}" or bDesc contains "{safe_text}" then
                                click b
                                set foundIt to true
                                exit repeat
                            end if
                        end try
                    end repeat
                end try
            end if
            if not foundIt then
                try
                    set allLinks to every link of entire contents of webArea
                    repeat with lnk in allLinks
                        try
                            set lTitle to title of lnk
                            set lDesc to description of lnk
                            if lTitle contains "{safe_text}" or lDesc contains "{safe_text}" then
                                click lnk
                                set foundIt to true
                                exit repeat
                            end if
                        end try
                    end repeat
                end try
            end if

            -- Strategy 3: Search ALL UI elements by description, title, value
            if not foundIt then
                try
                    set allEls to every UI element of entire contents of webArea
                    repeat with el in allEls
                        try
                            set elDesc to description of el
                            if elDesc contains "{safe_text}" then
                                click el
                                set foundIt to true
                                exit repeat
                            end if
                        end try
                        try
                            set elTitle to title of el
                            if elTitle contains "{safe_text}" then
                                click el
                                set foundIt to true
                                exit repeat
                            end if
                        end try
                        try
                            set elValue to value of el
                            if elValue is not missing value and (elValue as text) contains "{safe_text}" then
                                click el
                                set foundIt to true
                                exit repeat
                            end if
                        end try
                    end repeat
                end try
            end if

            -- Strategy 4: Search static texts and click their parent
            if not foundIt then
                try
                    set allTexts to every static text of entire contents of webArea
                    repeat with st in allTexts
                        try
                            set stVal to value of st
                            if stVal contains "{safe_text}" then
                                -- Click the static text's position (it might be a label for a button)
                                try
                                    click st
                                    set foundIt to true
                                    exit repeat
                                end try
                            end if
                        end try
                    end repeat
                end try
            end if

            if foundIt then
                return "AX_CLICKED:" & "{safe_text}"
            else
                return "AX_NOT_FOUND:" & "{safe_text}"
            end if
        end tell
    end tell
end tell
""", timeout=30)
        return result or ""

    def _find_text_coordinates_ax(self, text: str) -> Optional[tuple]:
        """Find the screen coordinates of text on the page using Accessibility API.

        Returns (viewport_x, viewport_y) or None if not found.
        Uses Safari's Find (Cmd+F) to locate text, then gets its position.
        """
        safe_text = text.replace('"', '\\"')
        # Use Cmd+F to find and highlight the text
        self._osa('tell application "Safari" to activate')
        time.sleep(0.1)
        self._osa(
            'tell application "System Events"\n'
            '  tell process "Safari" to keystroke "f" using command down\n'
            'end tell'
        )
        time.sleep(0.4)
        self._osa(
            'tell application "System Events"\n'
            f'  tell process "Safari" to keystroke "{safe_text}"\n'
            'end tell'
        )
        time.sleep(0.3)
        self._osa(
            'tell application "System Events"\n'
            '  tell process "Safari" to key code 36\n'  # Enter to find
            'end tell'
        )
        time.sleep(0.3)
        # Close find bar
        self._osa(
            'tell application "System Events"\n'
            '  tell process "Safari" to key code 53\n'  # Escape
            'end tell'
        )
        time.sleep(0.2)

        # The found text should now be highlighted/scrolled into view
        # Use JS to get its position (if JS works) or estimate from page
        pos = self._js(f"""
(function() {{
    var sel = window.getSelection();
    if (sel && sel.rangeCount > 0) {{
        var range = sel.getRangeAt(0);
        var rect = range.getBoundingClientRect();
        if (rect.width > 0) {{
            return Math.round(rect.left + rect.width/2) + ',' + Math.round(rect.top + rect.height/2);
        }}
    }}
    return 'NO_SELECTION';
}})()
""")
        if pos and pos != "NO_SELECTION" and ',' in pos:
            try:
                parts = pos.split(',')
                return int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                pass
        return None

    # ─── Video ───────────────────────────────────────────────────

    def video(self, action: str = "status") -> Dict:
        a = action.lower().strip()
        cmds = {
            "play": "var v=document.querySelector('video');if(v){v.play();'PLAYING'}else{'NO_VIDEO'}",
            "resume": "var v=document.querySelector('video');if(v){v.play();'PLAYING'}else{'NO_VIDEO'}",
            "pause": "var v=document.querySelector('video');if(v){v.pause();'PAUSED'}else{'NO_VIDEO'}",
            "mute": "var v=document.querySelector('video');if(v){v.muted=!v.muted;'muted='+v.muted}else{'NO_VIDEO'}",
            "unmute": "var v=document.querySelector('video');if(v){v.muted=false;'UNMUTED'}else{'NO_VIDEO'}",
            "skip": "var v=document.querySelector('video');if(v){v.currentTime=Math.max(0,v.duration-1);'SKIPPED'}else{'NO_VIDEO'}",
            "finish": "var v=document.querySelector('video');if(v){v.currentTime=Math.max(0,v.duration-1);'SKIPPED'}else{'NO_VIDEO'}",
            "speed": "var v=document.querySelector('video');if(v){v.playbackRate=4;v.muted=true;v.play();'SPEED=4x+MUTED'}else{'NO_VIDEO'}",
            "fast": "var v=document.querySelector('video');if(v){v.playbackRate=4;v.muted=true;v.play();'SPEED=4x+MUTED'}else{'NO_VIDEO'}",
            "2x": "var v=document.querySelector('video');if(v){v.playbackRate=2;v.play();'SPEED=2x'}else{'NO_VIDEO'}",
        }

        if a in cmds:
            r = self._js_ctx(cmds[a])
            return {"status": "success", "video_result": r, "message": r}

        if a == "status":
            r = self._js_ctx(
                "var vs=document.querySelectorAll('video');\n"
                "if(!vs.length) return 'NO_VIDEO';\n"
                "var out='';\n"
                "for(var i=0;i<vs.length;i++){\n"
                "  var v=vs[i];\n"
                "  out+='VIDEO['+(i+1)+'] '+Math.round(v.currentTime)+'s/'+Math.round(v.duration||0)+'s ';\n"
                "  out+=v.paused?'PAUSED':'PLAYING';\n"
                "  if(v.muted) out+=' MUTED';\n"
                "  out+=' speed='+v.playbackRate+'x';\n"
                "  if(v.ended) out+=' ENDED';\n"
                "  out+='\\n';\n"
                "}\n"
                "return out;"
            )
            return {"status": "success", "video_result": r, "message": r}

        if a.startswith("wait"):
            for _ in range(300):
                s = self._js_ctx(
                    "var v=document.querySelector('video');"
                    "if(!v)'NO_VIDEO';else if(v.ended)'ENDED';"
                    "else Math.round(v.currentTime)+'/'+Math.round(v.duration)"
                )
                if s in ("ENDED", "NO_VIDEO"):
                    break
                time.sleep(1)
            return {"status": "success", "video_result": s, "message": s}

        return {"status": "error", "message": f"Unknown video action: {a}"}

    # ─── Tabs ────────────────────────────────────────────────────

    def tabs(self, action: str = "list") -> Dict:
        a = action.lower().strip()
        # Reset iframe context on any tab switch (refs become invalid)
        if a not in ("list", ""):
            self._iframe_idx = None

        if a == "list":
            info = self._osa(
                'tell application "Safari"\n'
                '  set out to ""\n'
                '  set idx to 1\n'
                '  tell front window\n'
                '    repeat with t in tabs\n'
                '      if t = current tab then\n'
                '        set out to out & "→ [" & idx & "] " & (name of t) & " — " & (URL of t) & linefeed\n'
                '      else\n'
                '        set out to out & "  [" & idx & "] " & (name of t) & " — " & (URL of t) & linefeed\n'
                '      end if\n'
                '      set idx to idx + 1\n'
                '    end repeat\n'
                '  end tell\n'
                '  return out\n'
                'end tell'
            )
            return {"status": "success", "tabs": info, "message": info}

        if a == "close":
            self._osa('tell application "Safari" to close current tab of front window')
            time.sleep(0.3)
            return self.snapshot()

        if a == "next":
            self._osa(
                'tell application "System Events" to tell process "Safari"\n'
                '  key code 48 using {control down}\n'
                'end tell'
            )
            time.sleep(0.5)
            return self.snapshot()

        if a == "prev":
            self._osa(
                'tell application "System Events" to tell process "Safari"\n'
                '  key code 48 using {shift down, control down}\n'
                'end tell'
            )
            time.sleep(0.5)
            return self.snapshot()

        if a in ("new", "newtab"):
            self._osa(
                'tell application "Safari"\n'
                '  tell front window to make new tab\n'
                'end tell'
            )
            time.sleep(0.5)
            return self.snapshot()

        m = re.search(r'\d+', a)
        if m:
            idx = int(m.group())
            self._osa(
                f'tell application "Safari" to tell front window\n'
                f'  set current tab to tab {idx}\n'
                f'end tell'
            )
            time.sleep(0.5)
            return self.snapshot()

        return {"status": "error", "message": f"Unknown tab action: {a}"}

    # ─── Iframes ─────────────────────────────────────────────────

    def iframe(self, action: str = "list") -> Dict:
        a = action.lower().strip()

        if a in ("list", ""):
            info = self._js(
                "var frames=document.querySelectorAll('iframe');\n"
                "if(!frames.length) return 'No iframes on this page.';\n"
                "var out=frames.length+' iframe(s):\\n';\n"
                "for(var i=0;i<frames.length;i++){\n"
                "  var f=frames[i];\n"
                "  out+='['+i+'] ';\n"
                "  if(f.title) out+='\"'+f.title+'\" ';\n"
                "  if(f.src) out+='src='+f.src.substring(0,120);\n"
                "  try{out+=f.contentDocument?' accessible':' cross-origin';}catch(e){out+=' cross-origin';}\n"
                "  out+='\\n';\n"
                "}\n"
                "return out;"
            )
            return {"status": "success", "iframes": info, "message": info}

        if a.startswith("enter") or a.startswith("#"):
            m = re.search(r'\d+', a)
            if not m:
                return {"status": "error", "message": "Specify iframe index: enter 0"}
            idx = int(m.group())
            check = self._js(
                f"var f=document.querySelectorAll('iframe')[{idx}];\n"
                "if(!f)'NOT_FOUND';\n"
                "else try{f.contentDocument?'OK':'CROSS_ORIGIN'}catch(e){'CROSS_ORIGIN'}"
            )
            if check == "OK":
                self._iframe_idx = idx
                return self.snapshot()
            return {"status": "error", "message": f"iframe[{idx}]: {check}"}

        if a in ("exit", "top", "parent"):
            self._iframe_idx = None
            return self.snapshot()

        return {"status": "error", "message": f"Unknown iframe action: {a}"}

    # ─── Cookies & Storage ───────────────────────────────────────

    def cookies(self, action: str = "get", name: str = "", value: str = "") -> Dict:
        a = action.lower().strip()

        if a == "get":
            result = self._js_ctx("document.cookie || '(no cookies)'")
            return {"status": "success", "cookies": result, "message": result}

        if a == "set":
            safe_name = name.replace("'", "\\'")
            safe_val = value.replace("'", "\\'")
            self._js_ctx(f"document.cookie = '{safe_name}={safe_val}; path=/; max-age=86400'")
            return {"status": "success", "message": f"Cookie set: {name}={value}"}

        if a in ("storage", "localstorage"):
            result = self._js_ctx(
                "(function() {"
                "  var out = {};"
                "  try {"
                "    for (var i = 0; i < localStorage.length && i < 50; i++) {"
                "      var k = localStorage.key(i);"
                "      out[k] = localStorage.getItem(k).substring(0, 200);"
                "    }"
                "  } catch(e) { return 'ERROR: ' + e.message; }"
                "  return JSON.stringify(out);"
                "})()"
            )
            return {"status": "success", "storage": result, "message": result}

        if a in ("clear", "delete"):
            self._js_ctx(
                "document.cookie.split(';').forEach(function(c){"
                "  document.cookie = c.trim().split('=')[0] + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/';"
                "})"
            )
            return {"status": "success", "message": "Cookies cleared"}

        return {"status": "error", "message": f"Unknown cookies action: {a}. Use: get, set, storage, clear"}

    # ─── Read / Extract (dedicated content extraction) ─────────

    def read_page(self) -> Dict:
        """Extract readable page content — optimized for LLM summarization and research.

        Unlike snapshot (which lists interactive elements), read_page extracts
        the actual text content, article body, headings, tables, lists, and links
        in a compact format ideal for research and summarization tasks.
        """
        content = self._js_ctx("""(function() {
var doc = document;
var out = '';

// Title and URL
out += 'TITLE: ' + (doc.title || '') + '\\n';
out += 'URL: ' + window.location.href + '\\n\\n';

// Try to find article/main content area first
var article = doc.querySelector('article, [role=main], main, .post-content, .article-body, .entry-content, #content, .content');
var contentRoot = article || doc.body;

// Extract headings with hierarchy
var headings = contentRoot.querySelectorAll('h1,h2,h3,h4,h5,h6');
if (headings.length > 0) {
    out += '--- OUTLINE ---\\n';
    for (var h = 0; h < headings.length && h < 40; h++) {
        var level = parseInt(headings[h].tagName[1]);
        var indent = '  '.repeat(level - 1);
        var ht = (headings[h].textContent || '').trim().replace(/\\s+/g, ' ');
        if (ht.length > 2) out += indent + ht.substring(0, 200) + '\\n';
    }
    out += '\\n';
}

// Extract main text content
out += '--- CONTENT ---\\n';
var walker = doc.createTreeWalker(
    contentRoot,
    NodeFilter.SHOW_TEXT,
    {acceptNode: function(node) {
        var parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        var tag = parent.tagName.toLowerCase();
        if (['script','style','noscript','svg','path'].indexOf(tag) >= 0) return NodeFilter.FILTER_REJECT;
        var closest = parent.closest('script,style,noscript,nav,.nav,.sidebar,.ad,.advertisement');
        if (closest && closest !== contentRoot) return NodeFilter.FILTER_REJECT;
        try {
            var cs = window.getComputedStyle(parent);
            if (cs.display === 'none' || cs.visibility === 'hidden') return NodeFilter.FILTER_REJECT;
        } catch(e) {}
        return NodeFilter.FILTER_ACCEPT;
    }}
);

var textParts = [];
var totalChars = 0;
var maxChars = 15000;
while (walker.nextNode() && totalChars < maxChars) {
    var t = walker.currentNode.textContent.trim();
    if (t.length < 3) continue;
    t = t.replace(/\\s+/g, ' ');
    if (textParts.length > 0 && textParts[textParts.length-1] === t) continue;
    textParts.push(t);
    totalChars += t.length;
}
out += textParts.join(' ');
if (totalChars >= maxChars) out += '\\n[content truncated at ' + maxChars + ' chars]';
out += '\\n\\n';

// Extract tables as structured data
var tables = contentRoot.querySelectorAll('table');
if (tables.length > 0) {
    out += '--- TABLES ---\\n';
    for (var ti = 0; ti < tables.length && ti < 5; ti++) {
        var tbl = tables[ti];
        try {
            var cs = window.getComputedStyle(tbl);
            if (cs.display === 'none') continue;
        } catch(e) { continue; }
        var caption = tbl.querySelector('caption');
        if (caption) out += 'TABLE: ' + caption.textContent.trim() + '\\n';
        else out += 'TABLE ' + (ti+1) + ':\\n';
        var rows = tbl.querySelectorAll('tr');
        for (var ri = 0; ri < rows.length && ri < 50; ri++) {
            var cells = rows[ri].querySelectorAll('th,td');
            var cellTexts = [];
            for (var ci = 0; ci < cells.length; ci++) {
                cellTexts.push((cells[ci].textContent||'').trim().replace(/\\s+/g,' ').substring(0,80));
            }
            out += (ri === 0 && rows[ri].querySelector('th') ? '  [H] ' : '  ') + cellTexts.join(' | ') + '\\n';
        }
        if (rows.length > 50) out += '  [... ' + (rows.length - 50) + ' more rows]\\n';
        out += '\\n';
    }
}

// Extract lists as structured data
var lists = contentRoot.querySelectorAll('ul,ol');
var listCount = 0;
for (var li = 0; li < lists.length && listCount < 5; li++) {
    var list = lists[li];
    // Skip nav/menu lists and tiny lists
    if (list.closest('nav,.nav,.menu,.sidebar')) continue;
    var items = list.querySelectorAll(':scope > li');
    if (items.length < 2) continue;
    try {
        var lcs = window.getComputedStyle(list);
        if (lcs.display === 'none') continue;
    } catch(e) { continue; }
    if (listCount === 0) out += '--- LISTS ---\\n';
    var isOrdered = list.tagName === 'OL';
    for (var ii = 0; ii < items.length && ii < 30; ii++) {
        var itemText = (items[ii].textContent||'').trim().replace(/\\s+/g,' ').substring(0,150);
        if (itemText.length < 3) continue;
        out += (isOrdered ? '  ' + (ii+1) + '. ' : '  - ') + itemText + '\\n';
    }
    out += '\\n';
    listCount++;
}

// Extract links with context
out += '--- LINKS ---\\n';
var links = contentRoot.querySelectorAll('a[href]');
var seenUrls = {};
var linkCount = 0;
for (var i = 0; i < links.length && linkCount < 50; i++) {
    var a = links[i];
    var href = a.href;
    if (!href || href.startsWith('javascript:') || href === '#') continue;
    if (seenUrls[href]) continue;
    var lt = (a.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 80);
    if (!lt || lt.length < 2) continue;
    try {
        var acs = window.getComputedStyle(a);
        if (acs.display === 'none') continue;
    } catch(e) {}
    seenUrls[href] = 1;
    out += '"' + lt + '" -> ' + href.substring(0, 200) + '\\n';
    linkCount++;
}

// Extract images with alt text
var imgs = contentRoot.querySelectorAll('img[alt]');
var imgCount = 0;
for (var j = 0; j < imgs.length && imgCount < 10; j++) {
    var alt = (imgs[j].alt || '').trim();
    if (alt.length > 3) {
        if (imgCount === 0) out += '\\n--- IMAGES ---\\n';
        out += '[img] ' + alt.substring(0, 100) + '\\n';
        imgCount++;
    }
}

// Extract meta description and other metadata
var metaDesc = doc.querySelector('meta[name=description],meta[property="og:description"]');
if (metaDesc && metaDesc.content) {
    out += '\\n--- META ---\\n';
    out += 'Description: ' + metaDesc.content.substring(0, 300) + '\\n';
}
var metaAuthor = doc.querySelector('meta[name=author]');
if (metaAuthor && metaAuthor.content) out += 'Author: ' + metaAuthor.content + '\\n';
var metaDate = doc.querySelector('meta[name=date],meta[property="article:published_time"],time[datetime]');
if (metaDate) {
    var dateVal = metaDate.content || metaDate.getAttribute('datetime') || metaDate.textContent;
    if (dateVal) out += 'Date: ' + dateVal.trim().substring(0, 30) + '\\n';
}

return out;
})()""")

        info = self._osa(
            'tell application "Safari" to return '
            '(URL of current tab of front window) & " | " & '
            '(name of current tab of front window)'
        )
        url, title = info.split(" | ", 1) if " | " in info else (info, "")

        return {
            "status": "success",
            "url": url,
            "title": title,
            "content": content,
            "message": content,
        }

    # ─── Search (autonomous web research) ────────────────────────

    def search(self, query: str) -> Dict:
        """Search the web and return structured results with titles, URLs, and snippets.

        Uses Google Search. Extracts results via DOM parsing.
        The agent can then open interesting results in new tabs and read them.
        """
        safe_q = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={safe_q}"
        self._iframe_idx = None
        safe_url = self._escape_url_for_osa(url)
        self._ensure_safari()
        self._osa(
            'tell application "Safari"\n'
            f'  tell front window to set URL of current tab to "{safe_url}"\n'
            'end tell'
        )
        self._wait_idle(1.5)

        # Extract search results from Google
        results = self._js("""(function() {
var out = '';
var results = [];

// Strategy 1: Google result containers
var items = document.querySelectorAll('#search .g, #rso .g, div[data-sokoban-container]');
for (var i = 0; i < items.length && results.length < 10; i++) {
    var a = items[i].querySelector('a[href]');
    var h3 = items[i].querySelector('h3');
    if (!a || !h3) continue;
    var href = a.href;
    if (!href || href.indexOf('google.com/search') !== -1) continue;
    var snippet = '';
    var snipEl = items[i].querySelector('[data-sncf], .VwiC3b, [style*="-webkit-line-clamp"], .st, .IsZvec');
    if (snipEl) snippet = snipEl.textContent.trim();
    results.push({
        title: h3.textContent.trim(),
        url: href,
        snippet: snippet.substring(0, 250)
    });
}

// Strategy 2: Fallback — any link containing h3
if (results.length === 0) {
    var allLinks = document.querySelectorAll('a[href]');
    for (var j = 0; j < allLinks.length && results.length < 10; j++) {
        var link = allLinks[j];
        var h = link.querySelector('h3');
        if (!h) continue;
        var u = link.href;
        if (!u || u.indexOf('google') !== -1) continue;
        results.push({
            title: h.textContent.trim(),
            url: u,
            snippet: ''
        });
    }
}

// Strategy 3: DuckDuckGo fallback parse (if redirected)
if (results.length === 0 && document.querySelector('.result__title')) {
    var ddgResults = document.querySelectorAll('.result');
    for (var k = 0; k < ddgResults.length && results.length < 10; k++) {
        var ddgTitle = ddgResults[k].querySelector('.result__title a');
        var ddgSnippet = ddgResults[k].querySelector('.result__snippet');
        if (ddgTitle) {
            results.push({
                title: ddgTitle.textContent.trim(),
                url: ddgTitle.href,
                snippet: ddgSnippet ? ddgSnippet.textContent.trim().substring(0, 250) : ''
            });
        }
    }
}

out += 'SEARCH: "' + (document.querySelector('textarea[name=q], input[name=q]') || {}).value + '"\\n';
out += 'Found ' + results.length + ' results:\\n\\n';
for (var r = 0; r < results.length; r++) {
    out += '[' + (r+1) + '] ' + results[r].title + '\\n';
    out += '    ' + results[r].url + '\\n';
    if (results[r].snippet) out += '    ' + results[r].snippet + '\\n';
    out += '\\n';
}
if (results.length === 0) {
    out += 'No results extracted. The page may have a consent screen or CAPTCHA.\\n';
    out += 'Try: snapshot to see the page, or search on DuckDuckGo instead.\\n';
}
return out;
})()""")

        info = self._osa(
            'tell application "Safari" to return '
            '(URL of current tab of front window) & " | " & '
            '(name of current tab of front window)'
        )
        actual_url, title = info.split(" | ", 1) if " | " in info else (info, "")

        return {
            "status": "success",
            "url": actual_url,
            "title": title,
            "results": results,
            "message": results,
        }

    # ─── File Upload ──────────────────────────────────────────────

    def upload(self, text: str, filepath: str) -> Dict:
        """Upload a file to a file input element.

        Uses JS to set files on <input type=file> where possible,
        falls back to System Events to interact with the macOS file dialog.
        """
        filepath = os.path.expanduser(filepath)
        if not os.path.exists(filepath):
            return {"status": "error", "message": f"File not found: {filepath}"}

        abs_path = os.path.abspath(filepath)
        target_js = self._resolve_js(text)

        # Try to detect if it's a file input
        field_type = self._js_ctx(
            "(function() {\n"
            f"  var el = {target_js};\n"
            "  if (!el) return 'NOT_FOUND';\n"
            "  return (el.type||el.tagName||'').toLowerCase();\n"
            "})()"
        )

        if field_type == "NOT_FOUND":
            snap = self.snapshot()
            snap["upload_result"] = "NOT_FOUND: Element not found"
            return snap

        if field_type == "file":
            # Click the file input to trigger the native dialog
            self._js_ctx(
                f"(function() {{ var el = {target_js}; if (el) el.click(); }})()"
            )
        else:
            # Click whatever element triggers the upload
            self._js_ctx(
                f"(function() {{ var el = {target_js}; if (el) {{ el.scrollIntoView({{behavior:'instant',block:'center'}}); el.click(); }} }})()"
            )

        time.sleep(1.5)

        # Interact with the macOS file open dialog via System Events
        dir_path = os.path.dirname(abs_path)
        file_name = os.path.basename(abs_path)

        # Use Cmd+Shift+G to open "Go to Folder" in the file dialog
        self._osa(
            'tell application "System Events"\n'
            '  keystroke "g" using {command down, shift down}\n'
            'end tell'
        )
        time.sleep(0.8)

        # Type the directory path and press Enter
        safe_dir = dir_path.replace('"', '\\"')
        self._osa(
            'tell application "System Events"\n'
            f'  keystroke "{safe_dir}"\n'
            'end tell'
        )
        time.sleep(0.3)
        self._osa(
            'tell application "System Events"\n'
            '  key code 36\n'
            'end tell'
        )
        time.sleep(1.0)

        # Type the filename and press Enter to select
        safe_file = file_name.replace('"', '\\"')
        self._osa(
            'tell application "System Events"\n'
            f'  keystroke "{safe_file}"\n'
            'end tell'
        )
        time.sleep(0.3)
        self._osa(
            'tell application "System Events"\n'
            '  key code 36\n'
            'end tell'
        )
        time.sleep(1.0)

        log.info(f"upload({text}, {filepath}) -> dialog interaction complete")
        snap = self.snapshot()
        snap["upload_result"] = f"UPLOADED: {abs_path}"
        return snap

    # ─── Download ─────────────────────────────────────────────────

    def download(self, url: str = "") -> Dict:
        """Check recent downloads or trigger a download.

        If url is provided, navigates to it (triggering Safari's download).
        Always returns a list of recently downloaded files.
        """
        if url:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            safe_url = self._escape_url_for_osa(url)
            self._osa(
                'tell application "Safari"\n'
                f'  tell front window to set URL of current tab to "{safe_url}"\n'
                'end tell'
            )
            time.sleep(3.0)

        # Check ~/Downloads for recent files (last 120 seconds)
        recent = []
        now = time.time()
        try:
            for f in os.listdir(DOWNLOADS_DIR):
                if f.startswith('.'):
                    continue
                path = os.path.join(DOWNLOADS_DIR, f)
                if os.path.isfile(path):
                    mtime = os.path.getmtime(path)
                    age = now - mtime
                    if age < 120:
                        size = os.path.getsize(path)
                        recent.append({
                            "name": f,
                            "path": path,
                            "size": size,
                            "age_seconds": round(age, 1)
                        })
            recent.sort(key=lambda x: x["age_seconds"])
        except Exception as e:
            log.error(f"download check error: {e}")

        if recent:
            msg = "Recent downloads (last 2 min):\n"
            for d in recent[:10]:
                size_str = f"{d['size']}" if d['size'] < 1024 else f"{d['size']//1024}KB"
                msg += f"  {d['name']} ({size_str}) — {d['path']}\n"
        else:
            msg = "No recent downloads found in ~/Downloads"

        return {"status": "success", "downloads": recent, "message": msg}

    # ─── Find in Page ─────────────────────────────────────────────

    def find_in_page(self, text: str) -> Dict:
        """Use Safari's Cmd+F to find and highlight text on the page."""
        self._osa('tell application "Safari" to activate')
        time.sleep(0.1)

        # Open Find bar (Cmd+F)
        self._osa(
            'tell application "System Events"\n'
            '  tell process "Safari" to keystroke "f" using command down\n'
            'end tell'
        )
        time.sleep(0.5)

        # Type the search text
        safe = text.replace("\\", "\\\\").replace('"', '\\"')
        self._osa(
            'tell application "System Events"\n'
            f'  tell process "Safari" to keystroke "{safe}"\n'
            'end tell'
        )
        time.sleep(0.3)

        # Press Enter to find first match
        self._osa(
            'tell application "System Events"\n'
            '  tell process "Safari" to key code 36\n'
            'end tell'
        )
        time.sleep(0.5)

        snap = self.snapshot()
        snap["find_result"] = f"Searching for: {text}"
        return snap

    # ─── Select Text (copy page content to clipboard) ─────────────

    def select_text(self) -> Dict:
        """Select all text on the page and copy it to clipboard.

        Returns the clipboard contents. Useful for extracting all text
        from a page including content that might be missed by read_page.
        """
        self._osa('tell application "Safari" to activate')
        time.sleep(0.1)

        # First close find bar if open (Escape)
        self._osa(
            'tell application "System Events"\n'
            '  tell process "Safari" to key code 53\n'
            'end tell'
        )
        time.sleep(0.2)

        # Cmd+A (Select All)
        self._osa(
            'tell application "System Events"\n'
            '  tell process "Safari" to keystroke "a" using command down\n'
            'end tell'
        )
        time.sleep(0.3)

        # Cmd+C (Copy)
        self._osa(
            'tell application "System Events"\n'
            '  tell process "Safari" to keystroke "c" using command down\n'
            'end tell'
        )
        time.sleep(0.5)

        # Read clipboard
        clipboard = self._osa('the clipboard as text')
        if clipboard and len(clipboard) > 15000:
            clipboard = clipboard[:15000] + f"\n[clipboard truncated at 15000 chars, total: {len(clipboard)}]"

        return {
            "status": "success",
            "clipboard": clipboard,
            "message": clipboard or "(clipboard empty)",
        }

    # ─── Raw execution ───────────────────────────────────────────

    def run_js(self, code: str) -> Dict:
        result = self._js_ctx(code)
        return {"status": "success", "result": result, "message": result}

    def execute(self, script: str) -> Dict:
        result = self._osa(script)
        return {"status": "success", "result": result, "message": result}

    def screenshot(self) -> Dict:
        path = self._screenshot()
        if path:
            return {"status": "success", "path": path, "message": f"Screenshot: {path}"}
        return {"status": "error", "message": "Screenshot failed"}

    # ─── Router ──────────────────────────────────────────────────

    def process(self, payload: Dict) -> Dict:
        action = payload.get("action", "snapshot").lower().strip()
        url = payload.get("url", "")
        text = payload.get("text", "")
        value = payload.get("value", "")
        script = payload.get("script", "")

        try:
            if action in ("new_tab", "newtab", "open"):
                return self.new_tab(url or "https://www.google.com")
            if action in ("browse", "goto", "navigate", "nav"):
                if script and not url:
                    return self.run_js(script)
                return self.browse(url or "https://www.google.com")
            if action in ("back", "goback"):
                return self.back()
            if action in ("forward", "goforward"):
                return self.forward()
            if action in ("snapshot", "observe", "look", "page"):
                return self.snapshot()
            if action in ("read", "extract", "summarize", "read_page"):
                return self.read_page()
            if action in ("click", "tap", "press"):
                return self.click(text, click_type=value if value in ("double", "dblclick", "dbl", "right", "rightclick", "context") else "")
            if action in ("click_at", "clickat", "coord_click"):
                return self.click_at(text)
            if action in ("fill", "type", "input"):
                fast = payload.get("fast", "").lower() in ("true", "1", "yes")
                return self.fill(text, value, fast=fast)
            if action in ("select", "choose", "pick"):
                return self.select(text, value)
            if action in ("scroll",):
                amt = 600
                txt_target = ""
                if text and text.isdigit():
                    amt = int(text)
                elif text:
                    txt_target = text
                return self.scroll(value or "down", amt, text_target=txt_target)
            if action in ("keys", "key", "keyboard", "press_key"):
                return self.keys(value or text)
            if action in ("wait", "wait_for"):
                mw = 15
                if value and value.isdigit():
                    mw = int(value)
                    cond = text or "stable"
                    return self.wait(condition=cond, max_wait=mw)
                cond = "appear"
                if value in ("disappear", "vanish", "gone"):
                    cond = "disappear"
                elif value in ("stable", "idle", "settle"):
                    cond = "stable"
                elif value in ("url", "url_change", "navigate"):
                    cond = "url_change"
                elif value in ("network", "network_idle", "net"):
                    cond = "network"
                elif value:
                    cond = value
                return self.wait(text=text, condition=cond, max_wait=mw)
            if action in ("search", "web_search", "google"):
                return self.search(text or value)
            if action in ("hover", "mouseover"):
                return self.hover(text)
            if action in ("upload", "file_upload"):
                return self.upload(text, value)
            if action in ("download", "downloads", "get_downloads"):
                return self.download(url=url)
            if action in ("find", "find_in_page", "cmd_f"):
                return self.find_in_page(text or value)
            if action in ("select_text", "select_all", "copy_all", "copy_text"):
                return self.select_text()
            if action in ("video", "media"):
                return self.video(value or text or "status")
            if action in ("tabs", "tab"):
                return self.tabs(value or text or "list")
            if action in ("iframe", "frame"):
                return self.iframe(value or text or "list")
            if action in ("cookies", "cookie", "storage"):
                return self.cookies(action=value or "get", name=text, value=script)
            if action in ("run_js", "js", "javascript", "eval"):
                return self.run_js(script or text)
            if action in ("execute", "applescript", "osa"):
                return self.execute(script)
            if action in ("screenshot", "capture", "screen"):
                return self.screenshot()
            if action in ("drag", "drag_drop", "slide"):
                return self.drag(text, value)
            if action in ("window", "windows", "popup"):
                return self.window_manage(value or text or "list")
            if action in ("dismiss", "dismiss_overlays", "close_popups"):
                result = self._dismiss_overlays()
                time.sleep(0.5)
                snap = self.snapshot()
                snap["dismiss_result"] = result
                return snap

            snap = self.snapshot()
            snap["warning"] = f"Unknown action '{action}', showing snapshot"
            return snap

        except Exception as e:
            log.error(f"process({action}) error: {e}", exc_info=True)
            return {"status": "error", "action": action, "message": str(e)}


if __name__ == "__main__":
    agent = SafariAgent()
    parser = argparse.ArgumentParser(description=f"Safari Automation {SCRIPT_VERSION}")
    parser.add_argument("--server", action="store_true", help="Stdin/stdout JSON loop")
    parser.add_argument("--json", help="Single JSON payload")
    args = parser.parse_args()

    if args.server:
        print(json.dumps({
            "status": "mcp_ready",
            "version": SCRIPT_VERSION,
            "metadata": TOOL_METADATA
        }), flush=True)
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    # EOF — parent process closed stdin, exit cleanly
                    break
                line = line.strip()
                if not line:
                    continue
                result = agent.process(json.loads(line))
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except (EOFError, KeyboardInterrupt):
                break
            except Exception as e:
                print(json.dumps({"status": "error", "message": str(e)}), flush=True)
    elif args.json:
        result = agent.process(json.loads(args.json))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Safari Automation {SCRIPT_VERSION}")
        print('Usage: --server (stdin JSON loop) or --json \'{"action":"snapshot"}\'')
