#!/usr/bin/env python3
"""
agent.py — Agentic Orchestration Engine for (Amber)

Architecture: ReAct-style multi-step reasoning and execution loop +
              Swarm Coordinator for parallel browser task execution.

The agent chains tool calls autonomously, observing results and deciding
next actions until the task is fully complete or the user says "stop".

Swarm Mode (v3.0):
  - 1 LLM coordinator decomposes tasks into parallel sub-plans
  - Multiple browser workers execute pre-planned action sequences in separate tabs
  - Workers report results back to coordinator for synthesis
  - Dramatically faster for multi-site or multi-task browser operations

Key capabilities:
  - Multi-step tool chaining  (observe → think → act → observe)
  - Swarm coordination  (plan → dispatch → execute parallel → synthesize)
  - Persistent server-mode tool processes  (safari/edge --server stays alive)
  - User interrupt  (Ctrl+S, Ctrl+C, or type "stop" to pause between steps)
  - Loop/stall detection — auto-recovery when agent repeats same action
  - Smart observation truncation optimized for Qwen 3.5-9B context window
  - Per-step timing and action history tracking
  - Backward-compatible public API  (route_intent still works)

Optimized for: Apple M4 + 16GB RAM + Qwen 3.5-9B MLX (4-bit)
"""

import sys, os, json, glob, ast, subprocess, re, time, threading, select, signal
from typing import Dict, List, Optional, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# ANSI colors (red theme, matching ai.py)
_R = "\033[31m"; _BR = "\033[91m"; _B = "\033[1m"
_DIM = "\033[2m"; _GR = "\033[90m"; _X = "\033[0m"

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

BASE_DIR           = os.path.dirname(os.path.abspath(__file__))
FUNCTIONS_DIR      = os.path.join(BASE_DIR, "agent-functions")
LAUNCH_TIMEOUT     = 30.0     # seconds before one-shot tool times out
MAX_AGENT_STEPS    = 100000   # virtually unlimited — agent runs until task done or user stops
OBSERVATION_CHARS  = 14000    # truncate tool outputs (v4.1: tighter for speed)
OBSERVATION_CHARS_CONTENT = 24000  # content-heavy actions (v4.1: tighter for speed)
SERVER_READ_TIMEOUT = 60.0    # default seconds to wait for server-mode response
STALL_THRESHOLD    = 3        # same action repeated N times → inject recovery guidance
STEP_TIME_WARN     = 25.0     # warn if a single step takes longer than this
CHECKPOINT_FILE    = os.path.join(BASE_DIR, ".swarm_checkpoint.json")  # swarm resume

# ═══════════════════════════════════════════════════════════════════
#  1.  HYBRID REGISTRY LOADER
# ═══════════════════════════════════════════════════════════════════

def load_tools():
    """Scan agent-functions/ for tools (JSON-metadata or legacy regex)."""
    registry = {}
    prompt_lines = []

    if not os.path.exists(FUNCTIONS_DIR):
        return {}, "Error: agent-functions folder not found."

    for script_path in glob.glob(os.path.join(FUNCTIONS_DIR, "*.py")):
        fname = os.path.basename(script_path)
        if fname.startswith("_") or fname == "ai.py":
            continue

        metadata = None
        try:
            with open(script_path, "r", encoding="utf-8") as fh:
                content = fh.read()

            # --- Strategy A: TOOL_METADATA variable ---
            try:
                tree = ast.parse(content)
                for node in tree.body:
                    if (isinstance(node, ast.Assign)
                            and len(node.targets) == 1
                            and isinstance(node.targets[0], ast.Name)
                            and node.targets[0].id == "TOOL_METADATA"):
                        metadata = ast.literal_eval(node.value)
                        break
            except Exception:
                pass

            # --- Strategy B: Legacy regex header ---
            if not metadata:
                m = re.search(r"#\s*AGENTCMD:(.*)", content)
                if m:
                    props = {}
                    for pair in m.group(1).split(";"):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            props[k.strip().lower()] = v.strip()
                    if "name" in props:
                        metadata = {
                            "name": props["name"],
                            "description": props.get("description", "Legacy Tool"),
                            "parameters": {"type": "object", "properties": {}},
                        }

            if metadata:
                name = metadata["name"]
                supports_server = "--server" in content
                registry[name] = {
                    "path": script_path,
                    "meta": metadata,
                    "server_mode": supports_server,
                }
                desc = metadata.get("description", "")
                arg_names = ", ".join(
                    metadata.get("parameters", {}).get("properties", {}).keys()
                )
                prompt_lines.append(f'- "{name}": {desc} [Args: {arg_names}]')
        except Exception:
            pass

    # Detect which browser tools are available
    has_safari = "safari" in registry
    has_edge   = "edge"   in registry
    has_web    = "web"    in registry
    browser    = "edge" if has_edge else "safari"  # prefer edge for GUI examples

    header = (
        "\n\n[TOOLS]\n"
        "Output a JSON object on its own line to call a tool.\n"
        "Format: {\"tool\": \"name\", \"args\": {\"key\": \"val\"}}\n\n"
        "PROTOCOL:\n"
        "1. Break request into steps. Execute ONE tool at a time.\n"
        "2. You receive [TOOL RESULT]. Analyze, then call next tool or reply.\n"
        "3. When DONE, reply conversationally (no JSON). Do NOT ask permission between steps.\n"
        "4. Always snapshot after navigation. If action fails, try alternative.\n"
        "5. Do NOT say you \"can't\" — attempt it with your tools.\n\n"

        # ── FILE I/O (HIGHEST PRIORITY — READ BEFORE ANYTHING ELSE) ──
        "FILE I/O — READ AND WRITE FILES:\n"
        "⚠️ To READ any local file, ALWAYS use nano_editor with operation='read':\n"
        '  {"tool":"nano_editor","args":{"file_path":"/path/to/file","operation":"read"}}\n'
        "⚠️ To WRITE a NEW file, use nano_editor or macos_terminal write_file:\n"
        '  {"tool":"nano_editor","args":{"file_path":"/path/to/file","content":"..."}}\n'
        "⚠️ To APPEND to an existing file (like leads.md), use nano_editor append:\n"
        '  {"tool":"nano_editor","args":{"file_path":"/path/to/file","operation":"append","content":"\\n## New Data\\n..."}}\n'
        "RULES:\n"
        "- nano_editor read is INSTANT (<1 second). Use it for ALL file reading.\n"
        "- NEVER use web fetch with file paths — web is for URLs only (https://).\n"
        "- NEVER use macos_terminal read to read files — it reads terminal screen, NOT files.\n"
        "- NEVER use macos_terminal run 'cat file' — it opens a slow Terminal window.\n"
        "- For leads.md or any data file: READ first, then APPEND new data. Never overwrite.\n\n"

        # ── HEADLESS BROWSING (DEFAULT FOR ALL WEB ACCESS) ──
        "HEADLESS BROWSING (DEFAULT — use \"web\" tool for ALL web access unless user says \"open edge\"):\n"
        "The \"web\" tool is your DEFAULT for research, search, browsing, data gathering, and lead gen.\n"
        "NEVER use edge/safari for research, lookups, scraping, or lead gen unless user EXPLICITLY says \"open edge\" or \"open safari\".\n\n"
        "  SEARCH:         {\"tool\":\"web\",\"args\":{\"action\":\"search\",\"query\":\"your search query\"}}\n"
        "  FETCH PAGE:     {\"tool\":\"web\",\"args\":{\"action\":\"fetch\",\"url\":\"https://example.com\"}}\n"
        "  DEEP RESEARCH:  {\"tool\":\"web\",\"args\":{\"action\":\"deep_research\",\"query\":\"topic to research\",\"max_pages\":40}}\n"
        "  LEAD GEN:       {\"tool\":\"web\",\"args\":{\"action\":\"lead_gen\",\"query\":\"business type\",\"location\":\"city state\",\"lead_type\":\"all\",\"max_leads\":50}}\n"
        "  EXTRACT DATA:   {\"tool\":\"web\",\"args\":{\"action\":\"extract\",\"url\":\"https://site.com/contact\",\"extract_type\":\"all\"}}\n"
        "  BULK SCRAPE:    {\"tool\":\"web\",\"args\":{\"action\":\"scrape\",\"urls\":\"url1,url2,url3\"}}\n"
        "  CRAWL SITE:     {\"tool\":\"web\",\"args\":{\"action\":\"crawl\",\"url\":\"https://site.com\",\"max_depth\":2}}\n"
        "  MAPS SEARCH:    {\"tool\":\"web\",\"args\":{\"action\":\"maps_search\",\"query\":\"business type\",\"location\":\"city state\"}}\n"
        "  FILL FORM:      {\"tool\":\"web\",\"args\":{\"action\":\"form\",\"url\":\"https://site.com/signup\",\"fields\":{\"name\":\"val\",\"email\":\"val\"}}}\n\n"
        "TRIGGER PHRASES → use \"web\" tool:\n"
        "  'research', 'deep research', 'browse for', 'look up', 'search for', 'find out',\n"
        "  'what is', 'lead gen', 'find emails', 'find contacts', 'scrape', 'crawl',\n"
        "  'find businesses', 'headless', 'headlessly'\n\n"
        "If headless web fails, TELL THE USER the error — do NOT fall back to edge/safari.\n\n"

        # ── GUI BROWSER (edge/safari — ONLY when explicitly requested) ──
        f"GUI BROWSER (edge/safari — ONLY when user says 'open edge', 'open safari', 'use edge'):\n"
        f"1. Search: {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"search\",\"text\":\"query\"}}}}\n"
        f"2. Open tabs: {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"new_tab\",\"url\":\"...\"}}}}\n"
        f"3. Read: {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"read\"}}}}\n"
        f"4. Switch tabs: {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"tabs\",\"value\":\"switch N\"}}}}\n"
        "5. Synthesize with source URLs.\n\n"
        "NAVIGATION (CRITICAL):\n"
        "- When a page loads, LOOK AT THE NAVIGATION MENU (nav bar, header links) to understand page structure.\n"
        "- If redirected (URL differs from requested), do NOT retry the same URL. Use the nav menu links instead.\n"
        "- CLICK EXISTING LINKS on the page (#N refs) — do NOT construct/guess URLs (exception: job sites, see below).\n"
        "- If click on a nav link doesn't navigate, try: click_at with coordinates, or browse to the link's href URL.\n"
        "- SPA sites may update content without URL change — always snapshot after click to see what changed.\n"
        "- CLICK ESCALATION: If clicking text (e.g. '$1.25') doesn't navigate (URL stays the same after FORCE_CLICKED):\n"
        "  1. Look at ELEMENTS for the actual link (#N ref with -> URL). Click the #N ref.\n"
        "  2. If #N ref fails, use browse action with the href URL from the ELEMENTS list.\n"
        "  3. Last resort: click_at with @x,y coordinates from the element.\n"
        "  DO NOT repeat the same failed click — escalate immediately.\n"
        "- WRONG PAGE RECOVERY: If you end up on the wrong page (e.g. /discover-new instead of /surveys),\n"
        f"  use browse: {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"browse\",\"url\":\"https://correct-url\"}}}}\n"
        "  Do NOT keep clicking nav links that don't work — browse directly to the URL.\n\n"
        "READING ARTICLES:\n"
        "- Use 'read' action to extract article content. It has 5-tier fallback — very robust.\n"
        "- If read returns empty, the page may still be loading. Try: wait value='stable', then read again.\n"
        "- For article summaries: read the page, note key facts, then move to next article.\n"
        "- CLICK article links from the page (#N refs) — do NOT construct URLs from headlines.\n\n"
        "CAPTCHA HANDLING:\n"
        "- If CAPTCHA_DETECTED appears in results, do NOT try to solve it.\n"
        "- Instead: go back, try a different survey/link, or inform the user.\n"
        "- CAPTCHAs cannot be automated. Don't waste steps on them.\n\n"
        "CLOUDFLARE CHALLENGE PAGES:\n"
        "- If page title is 'Just a moment...' or page shows Cloudflare challenge, WAIT for it to auto-solve.\n"
        f"  {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"wait\",\"text\":\"Just a moment\",\"condition\":\"disappear\",\"max_wait\":15}}}}\n"
        "- Then snapshot to see the actual page. Do NOT navigate away — the challenge usually auto-resolves.\n"
        "- If still blocked after waiting, try browsing to the same URL again (Cloudflare may have cleared).\n\n"
        "JOB SITES — APPLY LOOP (Indeed, LinkedIn, ZipRecruiter):\n"
        "YOUR #1 GOAL: SUBMIT AS MANY APPLICATIONS AS POSSIBLE. Searching is NOT the task — APPLYING is.\n"
        "Do NOT stop after viewing search results. You MUST click into jobs and submit applications.\n\n"
        "SEARCH URLS (construct directly — site forms are unreliable):\n"
        "  Indeed: https://www.indeed.com/jobs?q=KEYWORD&l=LOCATION\n"
        "  Indeed remote: https://www.indeed.com/jobs?q=KEYWORD&l=remote\n"
        "  Indeed remote filter: https://www.indeed.com/jobs?q=KEYWORD&l=LOCATION&sc=0kf%3Aattr(DSQF7)%3B\n"
        "  Indeed quick apply filter: add &fromage=1 (last 24h) or &fromage=3 (last 3 days)\n"
        "  Indeed urgently hiring: look for 'Urgently hiring' badge in results\n"
        "- NEVER use the 'search' action on a job site — it navigates to Google. Use browse with a search URL.\n\n"
        "INDEED APPLY LOOP (CRITICAL — THIS IS THE CORE WORKFLOW):\n"
        "1. Browse to search URL → snapshot to see job listings\n"
        "2. For EACH job in results:\n"
        "   a. Click the job title link (#N ref) to open the job detail panel/page\n"
        "   b. Snapshot → look for 'Apply now' or 'Easily apply' button\n"
        "   c. If 'Apply now' / 'Easily apply' exists → click it\n"
        "   d. If it says 'Apply on company site' or links to external site → SKIP, go to next job\n"
        "   e. After clicking Apply: snapshot → fill out the application form:\n"
        "      - Resume: if asked to upload, it should already be uploaded. If 'resume' field shown, skip or confirm existing.\n"
        "      - Contact info: fill name, email, phone from resume data you read earlier\n"
        "      - Work experience: use resume data. Fill job titles, companies, dates from resume.\n"
        "      - Education: use resume data.\n"
        "      - Skills/questions: answer based on resume. For 'years of experience' use numbers from resume.\n"
        "      - Cover letter: if optional, skip. If required, write 2-3 sentences based on resume + job title.\n"
        "      - Demographic questions: 'Prefer not to say' for all. Or: Male, age 25, Veteran: No.\n"
        "      - Salary expectations: if required, enter a reasonable mid-range for the role.\n"
        "      - 'How did you hear about this job?': select 'Indeed' or 'Job Board' or 'Online'.\n"
        "      - Checkboxes (terms, agreements): check them all.\n"
        "   f. Click 'Submit application' / 'Apply' / 'Submit' / 'Continue' through all pages\n"
        "   g. After submit: snapshot to confirm 'Application submitted' or similar success message\n"
        "   h. Navigate BACK to search results: use back action or browse to the search URL\n"
        "   i. Move to NEXT job listing. Repeat from step (a).\n"
        "3. After going through all jobs on page 1 → look for 'Next' page link → click it → repeat\n"
        "4. Run MULTIPLE SEARCHES with different keywords relevant to the resume:\n"
        "   - Search 1: primary skill (e.g. 'electrician', 'truck driver', 'security officer')\n"
        "   - Search 2: secondary skill (e.g. 'customer service', 'warehouse')\n"
        "   - Search 3: remote jobs (e.g. 'remote customer service', 'remote data entry')\n"
        "   - Search 4: general (e.g. 'hiring urgently', entry-level well-paying roles)\n"
        "5. NEVER stop after just one search. Do at least 3-4 different keyword searches.\n"
        "6. TRACK your applications: count them mentally. Goal is 10+ applications per session.\n\n"
        "INDEED SIGN-IN:\n"
        "- If not signed in, look for 'Sign in' link → click → 'Sign in with Google' → select the account\n"
        "- If Google auth shows account picker, click the correct email account\n"
        "- After sign-in, navigate back to job search URL\n\n"
        "FORM FILLING TIPS FOR JOB APPS:\n"
        "- After filling ANY search field, use keys action to press Enter:\n"
        f"  {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"keys\",\"text\":\"Enter\"}}}}\n"
        "- Multi-page applications: fill page → click Continue/Next → snapshot → fill next page → repeat\n"
        "- If a field is pre-filled (from Indeed profile), don't overwrite it — skip to next field\n"
        "- Phone format: (XXX) XXX-XXXX or just digits — Indeed usually accepts both\n"
        "- If 'additional documents' is optional, skip it\n"
        "- If you see 'Review your application' page → click Submit\n"
        "- If you hit a Cloudflare challenge, wait for it to resolve (see CLOUDFLARE section).\n\n"
        "FORMS (FAST):\n"
        "1. Navigate + snapshot. 2. Fill ALL fields in rapid succession with fill. 3. Dropdowns→select, checkboxes→check.\n"
        "4. Check FORM STATUS for errors. 5. CAPTCHA→inform user. 6. Click submit OR press Enter, then snapshot.\n"
        "   TIP: If clicking the submit button doesn't work, press Enter: {\"tool\":\"edge\",\"args\":{\"action\":\"keys\",\"text\":\"Enter\"}}\n"
        "7. Use fill with fast=true for speed. Do NOT snapshot between each fill — batch fills, THEN snapshot.\n"
        "8. CHECKBOXES: Use 'check' action for checkboxes — it has Playwright-native + JS fallback strategies.\n"
        "   If check fails, try 'click' on the same ref — works for card-style survey options.\n"
        "   If both fail, try click_at with the @x,y coords from the snapshot.\n"
        "9. SELECT ALL THAT APPLY: Use 'check_all' with text='#3,#5,#8' to check multiple at once.\n\n"
        "ACCOUNT VERIFICATION (CRITICAL — HANDLES EMAIL, PHONE, OR BOTH):\n"
        "Sites may require email verification, phone/SMS verification, or BOTH (in any order).\n\n"
        "STEP 1 — NOTE YOUR CURRENT TAB before starting verification:\n"
        f"  {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"tabs\",\"value\":\"list\"}}}}\n"
        "  Remember which tab number is your signup site (e.g. tab 1).\n\n"
        "STEP 2 — FOR EMAIL VERIFICATION ('verify your email' / 'check your inbox'):\n"
        "  Search: {\"tool\":\"macos_mail\",\"args\":{\"mode\":\"search\",\"query\":\"SITE_NAME\",\"limit\":5}}\n"
        "  Replace SITE_NAME with actual site (ySense, InboxDollars, etc).\n"
        "  The result has 'verification_info' with 'codes' and/or 'verify_links' auto-extracted.\n"
        "  • If 'verify_links' has a URL → open it DIRECTLY in edge:\n"
        "    {\"tool\":\"macos_mail\",\"args\":{\"mode\":\"open_link\",\"value\":\"THE_VERIFY_LINK_URL\"}}\n"
        "    This opens the link in Edge. Then snapshot edge to see the verification page.\n"
        "    If it has a 'Verify'/'Confirm' button → click it.\n"
        "  • If 'codes' has a code → type it into the verification input field in edge.\n"
        "  • If NOT FOUND → wait 15s, retry. Try query='verify'. Junk is searched automatically.\n\n"
        "STEP 3 — FOR PHONE/SMS VERIFICATION ('enter code sent to your phone'):\n"
        "  Search: {\"tool\":\"messages\",\"args\":{\"mode\":\"search\",\"query\":\"verify\",\"limit\":5}}\n"
        "  Or by contact: {\"tool\":\"messages\",\"args\":{\"mode\":\"search\",\"query\":\"code\",\"contact\":\"8648241536\"}}\n"
        "  • If VERIFICATION CODE → fill it in the browser input field.\n"
        "  • If VERIFICATION LINK → open in edge (same as email step above).\n"
        "  • If NOT FOUND → wait 15s, retry. Try 'code', site name as query.\n\n"
        "STEP 4 — RESUME IN EDGE (CRITICAL — DO THIS AFTER EVERY VERIFICATION):\n"
        f"  {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"tabs\",\"value\":\"switch N\"}}}}\n"
        "  Switch back to the original signup tab (N from step 1). Then snapshot.\n"
        "  The site may now show:\n"
        "  • 'Verified!' → proceed with task.\n"
        "  • Second verification needed → do step 2 or 3 for the OTHER type.\n"
        "  • Profile form → fill it out. Dashboard → signup complete.\n"
        "  If you opened a verification link in a new tab, close it after confirming:\n"
        f"  {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"tabs\",\"value\":\"close\"}}}}\n\n"
        "STEP 5 — IF BOTH VERIFICATIONS NEEDED:\n"
        "  Complete one verification → resume in edge (step 4) → site asks for second → repeat.\n"
        "  Always return to the signup tab between verifications.\n\n"
        "VERIFICATION TIPS:\n"
        "- ALWAYS open verification links in edge (never default browser).\n"
        "- ALWAYS switch back to the original tab after verification completes.\n"
        "- Mail UI: {\"tool\":\"macos_mail\",\"args\":{\"mode\":\"snapshot\"}} to see Mail.app.\n"
        "- Messages UI: {\"tool\":\"messages\",\"args\":{\"mode\":\"snapshot\"}} to see Messages app.\n"
        "- Both tools have click, click_at (Bezier mouse), scroll, open_link modes for full UI control.\n\n"
        "BROWSER TIPS:\n"
        "- After click, always snapshot. If #N→NOT_FOUND/STALE_REF, snapshot for fresh refs.\n"
        "- Surveys: read Q: lines, select answer, click Next/Continue. Be FAST — don't overthink.\n"
        "- Checkboxes that say '(styled-hidden, use check action)' → use check action first, click as fallback.\n"
        "- Custom dropdowns: click trigger first, then snapshot for options.\n"
        "- Scroll down if elements missing. Elements show @x,y — use click_at as fallback.\n"
        "- wait value='stable' after page updates. dismiss to clear overlays.\n"
        "- read (not snapshot) to extract+summarize. search to find things quickly.\n"
        "- drag for sliders. window value='popup'/'main' for popups.\n"
        "- keys: cmd+a, cmd+c, cmd+f. click value='double'/'right'.\n\n"
        "POPUP HANDLING (CRITICAL):\n"
        "- ALWAYS dismiss overlays FIRST before interacting with the page.\n"
        "- 'Restore pages' popup on Edge startup: use dismiss action immediately.\n"
        "- 'Recommended for you' survey popups: click the X button or 'Start Survey' button.\n"
        "- Cookie banners, modals, notification prompts: use dismiss action.\n"
        "- If page seems blocked/unresponsive: dismiss → snapshot → proceed.\n"
        "- NEVER get stuck on a popup — dismiss it or click through it immediately.\n"
        "- If dismiss doesn't work, try click_at on the X button coordinates.\n\n"
        "SPEED RULES:\n"
        "- Act FAST. Don't deliberate excessively — pick the obvious choice and go.\n"
        "- Don't snapshot more than needed. Fill multiple fields, THEN snapshot once.\n"
        "- After clicking Next/Submit, snapshot ONCE — don't wait repeatedly.\n"
        "- If a page is loading, one wait value='stable' is enough. Move on.\n"
        "- Skip reading page text unless you need specific info. Snapshot shows elements.\n"
        "- NEVER repeat a failed action. If click text didn't navigate → escalate to #N ref or browse.\n"
        "- NEVER call tabs list more than once per navigation. Remember your tab numbers.\n"
        "- After fill: do NOT snapshot between each field. Fill ALL fields, THEN snapshot.\n"
        "- Batch operations: fill name → fill email → fill phone → snapshot ONCE.\n\n"
        "SURVEYS (CRITICAL — BE FAST):\n"
        "1. dismiss first (clear popups). Snapshot. If empty/sparse → content is in iframe.\n"
        f"2. {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"iframe\",\"value\":\"list\"}}}} → then enter 0.\n"
        "3. After iframe enter, snapshot to see form. Read Q: lines, answer, click Next.\n"
        "4. Radio→click #N. CHECKBOX→check #N (preferred). If check fails→click #N. If both fail→click_at @x,y. Text→fill. Dropdown→select #N. Slider→drag.\n"
        "5. SELECT ALL THAT APPLY→check_all text='#3,#5,#8' (comma-separated refs for multiple checkboxes).\n"
        "   Card-style survey options (divs/labels that look like checkboxes) → click #N works, or click_at @x,y.\n"
        "6. NEVER snapshot 3+ times in a row. If empty, try: iframe enter, scroll, dismiss, wait.\n"
        "7. Default answers: age 25, birthday 12/26/2000, Male, employed, income $50k-75k.\n"
        "8. Answer QUICKLY — pick the first reasonable option. Don't overthink survey answers.\n"
        "9. After clicking Next/Continue, snapshot ONCE to see next question. Repeat.\n"
        "10. If 'Recommended for you' popup appears, click 'Start Survey' or X to dismiss.\n"
        "11. Multiple choice: just pick the first option unless question requires specific info.\n"
        "12. Checkbox escalation: check → click → click_at @x,y. If ALL fail, the element may be decorative.\n"
        "13. REFRESH BUTTON: Survey listing pages have a Refresh/Reload button — click it before selecting a survey.\n"
        "    Look for a circular arrow icon, 'Refresh' text, or a reload button in the snapshot.\n"
        "14. SURVEY CLICKS OPEN NEW TABS: Clicking a survey link often opens it in a new tab. After completing\n"
        "    a survey (or if it fails), close the survey tab and switch back to the survey list tab.\n\n"
        "SURVEY NAVIGATION (CRITICAL — DON'T GET STUCK):\n"
        "- On survey listing pages (e.g. ySense /surveys), click the LINK element (#N ref), NOT the price text.\n"
        "  Price text like '$1.25' is often a <span>, not a link — clicking it does nothing.\n"
        "  Look for link elements in ELEMENTS that contain survey URLs (e.g. 'Survey Link' or 'Start').\n"
        "- After clicking a survey link, IMMEDIATELY check: did the URL change? Did a new tab open?\n"
        f"  Check tabs: {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"tabs\",\"value\":\"list\"}}}}\n"
        "  If new tab opened → switch to it. If URL unchanged → click failed, try #N ref or browse.\n"
        "- If redirected to wrong page (e.g. /discover-new, homepage, /affiliates), use browse to go back:\n"
        f"  {{\"tool\":\"{browser}\",\"args\":{{\"action\":\"browse\",\"url\":\"https://www.ysense.com/surveys\"}}}}\n"
        "- NEVER click the same non-working element more than once. Escalate: text→#N ref→browse URL→click_at.\n\n"
        "SURVEY ANSWER STRATEGY (MAXIMIZE COMPLETION):\n"
        "- Surveys screen out respondents who don't match their target demographics. ANSWER STRATEGICALLY.\n"
        "- PRESCREEN QUESTIONS: These determine if you qualify. Pick MAINSTREAM answers that most surveys want:\n"
        "  * Employment: Employed full-time (most surveys want employed consumers)\n"
        "  * Industry: Do NOT pick Marketing/Advertising/Market Research — instant disqualification\n"
        "  * Education: Bachelor's degree or Some college\n"
        "  * Household income: $50,000-$74,999 (sweet spot — not too low, not too high)\n"
        "  * Marital status: Single or Married — pick whichever fits the provided persona\n"
        "  * Children: No (simpler)\n"
        "  * Race/ethnicity: Prefer not to say, or White/Caucasian (largest survey demo)\n"
        "  * Shopping habits: Yes to buying products, using brands, grocery shopping\n"
        "  * Technology: Own a smartphone, use social media, shop online\n"
        "  * Health: In good health, no special conditions\n"
        "  * Decision maker: Yes — 'I am the primary decision maker for household purchases'\n"
        "- OPINION/PREFERENCE QUESTIONS: Pick middle-of-the-road answers. Avoid extremes.\n"
        "  * Satisfaction: 'Somewhat satisfied' or 'Satisfied'\n"
        "  * Agreement: 'Somewhat agree' or 'Agree'\n"
        "  * Likelihood: 'Somewhat likely' or 'Likely'\n"
        "  * Frequency: 'Sometimes' or 'Often' (not Never or Always)\n"
        "- BRAND AWARENESS: When asked 'which brands have you heard of', select 3-5 popular/recognizable ones.\n"
        "  Don't select all and don't select none.\n"
        "- OPEN-ENDED TEXT: Write 1-2 short sentences. Be specific but generic. Example:\n"
        "  'I look for quality and value when shopping. Good customer reviews help me decide.'\n"
        "- NEVER answer that you work in market research, advertising, or media.\n"
        "- NEVER say you have taken a survey about this topic recently (if asked).\n"
        "- ALWAYS say you are the household decision maker for purchases.\n\n"
        "EMPTY SNAPSHOT RECOVERY:\n"
        "DO NOT retry snapshot — try different approaches:\n"
        "1. iframe list/enter (content in iframe)\n"
        "2. scroll down (below viewport)\n"
        "3. dismiss (overlay blocking)\n"
        "4. wait value='stable' (still loading)\n"
        "5. select_text (raw text fallback)\n\n"

        # ── SOFTWARE DEVELOPMENT / CODING AGENT ──────────────────────
        "SOFTWARE DEVELOPMENT (CODING AGENT MODE):\n"
        "You are a world-class coding agent. When asked to build software, write code, or create apps, "
        "follow these principles:\n\n"

        "CODING PRINCIPLES:\n"
        "1. PLAN FIRST: Break the project into files/components before writing any code.\n"
        "2. VERIFY COMMANDS: If unsure about a CLI tool's flags, run `tool --help` first.\n"
        "   NEVER guess CLI flags — always check. This prevents errors like wrong arguments.\n"
        "3. READ ERRORS CAREFULLY: When a command fails, read the FULL error output.\n"
        "   Fix the root cause, don't retry the same broken command.\n"
        "4. ITERATE: Write code → build/run → read errors → fix → repeat until working.\n"
        "5. USE write_file FOR CODE: Use macos_terminal write_file mode or nano_editor to create source files.\n"
        "   These are more reliable than piping content through shell commands.\n"
        "6. ONE STEP AT A TIME: Create directory structure first, then write files one by one, then build.\n"
        "7. CHECK OUTPUT: After every terminal command, use macos_terminal read mode to see the result.\n"
        "8. WAIT FOR COMPLETION: After running build commands, check state (busy/idle) before proceeding.\n\n"

        "TERMINAL CODING WORKFLOW:\n"
        "1. Create project directory: macos_terminal run 'mkdir -p ~/Developer/ProjectName'\n"
        "2. Create subdirectories: macos_terminal run 'mkdir -p ~/Developer/ProjectName/Sources'\n"
        "3. Write source files: macos_terminal write_file (file_path + content)\n"
        "   OR nano_editor (file_path + content) for visual feedback\n"
        "4. Run build commands: macos_terminal run 'cd ~/Developer/ProjectName && build_command'\n"
        "5. Read output: macos_terminal read (check for errors)\n"
        "6. Fix errors: read error → edit file → rebuild\n\n"

        "FILE CREATION — BEST PRACTICES:\n"
        "- Use write_file mode for creating source code (most reliable):\n"
        '  {"tool":"macos_terminal","args":{"mode":"write_file","file_path":"~/Dev/App/Sources/main.swift","content":"import SwiftUI\\n..."}}\n'
        "- Use nano_editor for creating files with visual nano feedback:\n"
        '  {"tool":"nano_editor","args":{"file_path":"~/Dev/App/Sources/main.swift","content":"import SwiftUI\\n..."}}\n'
        "- ALWAYS use absolute or ~/ paths. NEVER use relative paths.\n"
        "- Create parent directories BEFORE writing files.\n\n"

        "EDITING EXISTING FILES — CRITICAL WORKFLOW:\n"
        "⚠️ MANDATORY: To UPDATE or ADD TO an existing file, ALWAYS use nano_editor append.\n"
        "⚠️ NEVER use macos_terminal write_file on files that already exist — it DESTROYS all existing content!\n"
        "Step 1 — READ: nano_editor operation='read' to see current contents:\n"
        '   {"tool":"nano_editor","args":{"file_path":"/path/to/file.md","operation":"read"}}\n'
        "Step 2 — APPEND new content to end of file:\n"
        '   {"tool":"nano_editor","args":{"file_path":"/path/to/file.md","operation":"append","content":"\\n## New Section\\n..."}}\n'
        "   OR OVERWRITE with full modified content (include existing + changes):\n"
        '   {"tool":"nano_editor","args":{"file_path":"/path/to/file.md","operation":"overwrite","content":"...entire file..."}}\n'
        "RULES:\n"
        "- To UPDATE leads.md or any data file → nano_editor append. ALWAYS.\n"
        "- write_file = CREATE NEW FILES ONLY. It erases everything in existing files.\n"
        "- NEVER skip the read step — you must know what's in the file before editing.\n\n"

        "NANO EDITOR USAGE (for interactive editing in Terminal):\n"
        "- Open file: macos_terminal run 'nano /path/to/file'\n"
        "- Save file: macos_terminal keystroke 'ctrl+o return' (Write Out, confirm filename)\n"
        "- Exit nano: macos_terminal keystroke 'ctrl+x' (Exit)\n"
        "- Save and exit: macos_terminal keystroke 'ctrl+o return ctrl+x'\n"
        "- Cut line: ctrl+k | Paste: ctrl+u | Search: ctrl+w | Go to line: ctrl+shift+_ (ctrl+_)\n"
        "- Go to end: alt+/ | Go to start: alt+\\\n"
        "- Page down: ctrl+v | Page up: ctrl+y\n"
        "- BUT PREFER write_file/nano_editor tool over interactive nano — it's faster and more reliable.\n\n"

        # ── TUIST (iOS/macOS project management) ─────────────────────
        "TUIST — iOS/macOS PROJECT MANAGEMENT (CRITICAL REFERENCE):\n"
        "Tuist generates Xcode projects from Swift manifest files. NEVER guess flags — use this reference.\n\n"

        "TUIST COMMANDS:\n"
        "  tuist init [--path <dir>]           Initialize new Tuist project (INTERACTIVE — asks questions)\n"
        "                                       Creates: Project.swift, Tuist.swift, basic source files\n"
        "                                       NO --name, NO --platform flags! Just --path.\n"
        "  tuist generate run [--open/-o] [--path <dir>] [--configuration <config>]\n"
        "                                       Generate Xcode workspace from manifests\n"
        "                                       --open: auto-open in Xcode after generating\n"
        "                                       --no-binary-cache: skip cache, use source\n"
        "  tuist build run [scheme] [--generate] [--clean] [--path <dir>] [--device <device>] [--configuration <config>]\n"
        "                                       Build project (deprecated: prefer tuist xcodebuild build)\n"
        "                                       --generate: force regenerate before build\n"
        "                                       --platform: iOS, macOS, tvOS, watchOS\n"
        "  tuist run <scheme> [--path <dir>] [--device <device>] [--os <ver>] [--generate] [--clean] [--configuration <config>]\n"
        "                                       Run app on simulator or device\n"
        "                                       Example: tuist run MyApp --device 'iPhone 16'\n"
        "  tuist test run [scheme] [--path <dir>] [--device <device>] [--clean] [--configuration <config>]\n"
        "                                       Run tests. --skip-ui-tests, --test-targets\n"
        "  tuist edit [--path <dir>] [--permanent/-P]\n"
        "                                       Open Xcode to edit Project.swift manifests\n"
        "  tuist install [--path <dir>] [--update/-u]\n"
        "                                       Install Swift Package dependencies\n"
        "  tuist clean [--path <dir>]           Clean build artifacts and caches\n"
        "  tuist graph [--path <dir>]           Visualize dependency graph\n\n"

        "TUIST PROJECT CREATION WORKFLOW (step by step):\n"
        "1. mkdir -p ~/Developer/MyApp && cd ~/Developer/MyApp\n"
        "2. tuist init                    (interactive — select 'generated project' when prompted)\n"
        "   OR: manually create Project.swift + Tuist.swift (see templates below)\n"
        "3. Create source directories:    mkdir -p MyApp/Sources MyApp/Resources\n"
        "4. Write source files:           create .swift files in MyApp/Sources/\n"
        "5. tuist install                 (if using SPM dependencies)\n"
        "6. tuist generate run --open     (generates .xcworkspace and opens Xcode)\n"
        "7. tuist build run MyApp         (build from CLI)\n"
        "8. tuist run MyApp               (run on simulator)\n\n"

        "TUIST MANUAL PROJECT SETUP (when tuist init interactive mode is problematic):\n"
        "Create these files manually with write_file:\n\n"

        "FILE 1: Tuist.swift (project root — marks the Tuist project boundary)\n"
        "```\n"
        "import ProjectDescription\n"
        "let tuist = Tuist()\n"
        "```\n\n"

        "FILE 2: Project.swift (project root — defines targets, deps, settings)\n"
        "```\n"
        "import ProjectDescription\n\n"
        "let project = Project(\n"
        '    name: "MyApp",\n'
        "    targets: [\n"
        "        .target(\n"
        '            name: "MyApp",\n'
        "            destinations: .iOS,\n"
        "            product: .app,\n"
        '            bundleId: "com.example.MyApp",\n'
        "            infoPlist: .default,\n"
        '            sources: ["MyApp/Sources/**"],\n'
        '            resources: ["MyApp/Resources/**"],\n'
        "            dependencies: []\n"
        "        ),\n"
        "        .target(\n"
        '            name: "MyAppTests",\n'
        "            destinations: .iOS,\n"
        "            product: .unitTests,\n"
        '            bundleId: "com.example.MyAppTests",\n'
        "            infoPlist: .default,\n"
        '            sources: ["MyAppTests/Sources/**"],\n'
        '            dependencies: [.target(name: "MyApp")]\n'
        "        ),\n"
        "    ]\n"
        ")\n"
        "```\n\n"

        "FILE 3: MyApp/Sources/MyAppApp.swift (SwiftUI entry point)\n"
        "```\n"
        "import SwiftUI\n\n"
        "@main\n"
        "struct MyAppApp: App {\n"
        "    var body: some Scene {\n"
        "        WindowGroup {\n"
        "            ContentView()\n"
        "        }\n"
        "    }\n"
        "}\n"
        "```\n\n"

        "FILE 4: MyApp/Sources/ContentView.swift\n"
        "```\n"
        "import SwiftUI\n\n"
        "struct ContentView: View {\n"
        "    var body: some View {\n"
        '        Text("Hello, World!")\n'
        "    }\n"
        "}\n"
        "```\n\n"

        "TUIST DESTINATIONS (for .target destinations parameter):\n"
        "  .iOS         — iPhone + iPad\n"
        "  .macOS       — Mac app\n"
        "  .watchOS     — Apple Watch\n"
        "  .tvOS        — Apple TV\n"
        "  .visionOS    — Vision Pro\n"
        "  [.iPhone, .iPad, .mac]  — specific subset\n\n"

        "TUIST PRODUCT TYPES:\n"
        "  .app, .framework, .staticLibrary, .dynamicLibrary,\n"
        "  .unitTests, .uiTests, .appExtension, .appClip\n\n"

        "TUIST DEPENDENCIES:\n"
        "  .target(name: \"OtherTarget\")              — local target dependency\n"
        "  .external(name: \"Alamofire\")               — SPM package (define in Package.swift)\n"
        "  .sdk(name: \"ARKit\", type: .framework)      — system framework\n\n"

        "TUIST SPRITEKIT GAME (for games like endless runners):\n"
        "Project.swift uses same structure, but sources import SpriteKit.\n"
        "Entry point uses UIKit lifecycle with SKView instead of SwiftUI:\n"
        "```\n"
        "// GameScene.swift\n"
        "import SpriteKit\n"
        "class GameScene: SKScene {\n"
        "    override func didMove(to view: SKView) { /* setup game */ }\n"
        "    override func update(_ currentTime: TimeInterval) { /* game loop */ }\n"
        "}\n"
        "```\n\n"

        "TUIST COMMON ERRORS:\n"
        "- 'Unknown option --name' → tuist init has NO --name flag. Use tuist init, then edit Project.swift.\n"
        "- 'Unknown option --platform' → tuist init has NO --platform flag. Set in Project.swift destinations.\n"
        "- 'Manifest not found' → Project.swift missing. Create it in the project root.\n"
        "- 'No targets found' → sources glob pattern doesn't match any files. Check paths.\n"
        "- Build errors → read error output, fix Swift code, rebuild.\n\n"

        # ── GENERAL CLI TOOLS REFERENCE ──────────────────────────────
        "COMMON CLI TOOLS FOR DEVELOPMENT:\n"
        "  git init / git add . / git commit -m 'msg'  — version control\n"
        "  swift build / swift run / swift test          — Swift Package Manager\n"
        "  swift package init --type executable          — create SPM project\n"
        "  swift package init --type library             — create SPM library\n"
        "  xcodebuild -list                              — list schemes/targets\n"
        "  xcodebuild -scheme X -destination 'platform=iOS Simulator,name=iPhone 16' build\n"
        "  xcrun simctl list devices                     — list simulators\n"
        "  xcrun simctl boot 'iPhone 16'                 — boot simulator\n"
        "  brew install <pkg>                            — install via Homebrew\n"
        "  pip install <pkg> / pip3 install <pkg>        — Python packages\n"
        "  npm init / npm install / npm run              — Node.js\n"
        "  python3 script.py                             — run Python\n"
        "  node script.js                                — run JavaScript\n"
        "  cargo init / cargo build / cargo run          — Rust\n\n"

        "SWIFT/IOS CODING PATTERNS:\n"
        "- SwiftUI App: @main struct + App protocol + WindowGroup + Views\n"
        "- UIKit App: AppDelegate + SceneDelegate + UIViewController\n"
        "- SpriteKit Game: SKScene subclass + GameViewController with SKView\n"
        "- Combine: Publishers, Subscribers, @Published, ObservableObject\n"
        "- Concurrency: async/await, Task {}, actor\n"
        "- Data: @State, @Binding, @ObservedObject, @EnvironmentObject, @Observable\n"
        "- Navigation: NavigationStack, NavigationLink, .navigationDestination\n"
        "- Lists: List, ForEach, LazyVStack, LazyHStack\n"
        "- Networking: URLSession.shared.data(from:), JSONDecoder\n\n"

        "ERROR-DRIVEN DEVELOPMENT LOOP:\n"
        "1. Write code files using write_file/nano_editor\n"
        "2. Run build: macos_terminal run 'cd ~/Dev/Project && tuist build run' (or swift build, etc.)\n"
        "3. Wait for completion: macos_terminal state (check idle)\n"
        "4. Read output: macos_terminal read lines=40\n"
        "5. If errors: parse the error messages, identify file:line:column, fix the code\n"
        "6. Rewrite the fixed file with write_file/nano_editor\n"
        "7. Rebuild and repeat until success\n"
        "8. NEVER give up after one error — real coding agents iterate until it works.\n\n"

        # ── GAME DEV SDK (sprite + map + game-ide pipeline) ─────────
        "GAME DEVELOPMENT SDK:\n"
        "You have a complete GBA-style game asset pipeline. Use these tools together:\n\n"

        "1. sprite — Generate pixel art sprites (characters, terrain, buildings, objects, items).\n"
        "   {\"tool\":\"sprite\",\"args\":{\"type\":\"character\",\"name\":\"hero\",\"size\":16,\"palette\":\"forest\",\"project_dir\":\"~/Developer/MyGame\"}}\n"
        "   {\"tool\":\"sprite\",\"args\":{\"type\":\"terrain\",\"name\":\"tiles\",\"batch\":\"grass,water,dirt,stone,sand,path,flowers\",\"project_dir\":\"~/Developer/MyGame\"}}\n"
        "   {\"tool\":\"sprite\",\"args\":{\"type\":\"object\",\"name\":\"objects\",\"batch\":\"tree,rock,bush,chest,sign\",\"project_dir\":\"~/Developer/MyGame\"}}\n"
        "   {\"tool\":\"sprite\",\"args\":{\"type\":\"building\",\"name\":\"walls\",\"batch\":\"wall_stone,door,window,roof,fence\",\"project_dir\":\"~/Developer/MyGame\"}}\n"
        "   Palettes: forest, desert, castle, cave, ocean, town, dungeon, snow\n"
        "   Sizes: 8, 16, 32, 64 (16 default — GBA standard)\n\n"

        "2. map_sprite — Generate tile maps from sprites.\n"
        "   {\"tool\":\"map_sprite\",\"args\":{\"type\":\"city\",\"name\":\"starter_town\",\"size\":\"medium\",\"features\":\"houses,path,trees,fountain\",\"project_dir\":\"~/Developer/MyGame\"}}\n"
        "   Map types: city, route, dungeon, interior, cave, forest, beach, mountain\n"
        "   Sizes: small (20x15), medium (40x30), large (60x40), route (20x80), dungeon (30x30)\n"
        "   Features: river, bridge, houses, trees, path, fountain, walls, lake, shops, ruins\n"
        "   Output: {project_dir}/maps/{name}.png + .json (tilemap data with collision)\n\n"

        "3. game_ide — Visual IDE for game dev (launch to see sprites/maps/code).\n"
        "   {\"tool\":\"game_ide\",\"args\":{\"mode\":\"launch\",\"project_dir\":\"~/Developer/MyGame\"}}\n"
        "   {\"tool\":\"game_ide\",\"args\":{\"mode\":\"show_sprite\",\"path\":\"~/Developer/MyGame/sprites/hero.png\"}}\n"
        "   {\"tool\":\"game_ide\",\"args\":{\"mode\":\"open_file\",\"file_path\":\"~/Developer/MyGame/Sources/GameScene.swift\"}}\n"
        "   {\"tool\":\"game_ide\",\"args\":{\"mode\":\"run_command\",\"command\":\"tuist build run\"}}\n"
        "   {\"tool\":\"game_ide\",\"args\":{\"mode\":\"log\",\"text\":\"Building sprites...\"}}\n"
        "   Tabs: Sprites (preview), Map (rendered map), Simulate (game sim)\n"
        "   Bottom: file browser + code editor + console\n\n"

        "4. code_search — Headless code example fetcher (NO browser needed).\n"
        "   {\"tool\":\"code_search\",\"args\":{\"mode\":\"snippet\",\"query\":\"SpriteKit endless runner swift\"}}\n"
        "   {\"tool\":\"code_search\",\"args\":{\"mode\":\"github_search\",\"query\":\"tuist Project.swift iOS\",\"search_type\":\"code\"}}\n"
        "   {\"tool\":\"code_search\",\"args\":{\"mode\":\"github_file\",\"url\":\"https://github.com/user/repo/blob/main/file.swift\"}}\n"
        "   {\"tool\":\"code_search\",\"args\":{\"mode\":\"stackoverflow\",\"query\":\"SpriteKit collision detection\"}}\n"
        "   Use this when you need code examples, patterns, or references.\n\n"

        "GAME DEV WORKFLOW (full pipeline):\n"
        "1. Launch game_ide: {\"tool\":\"game_ide\",\"args\":{\"mode\":\"launch\",\"project_dir\":\"~/Developer/MyGame\"}}\n"
        "2. Create project: mkdir dirs + write Tuist.swift + Project.swift + source files\n"
        "3. Generate terrain sprites: sprite tool with batch for grass,water,dirt,etc.\n"
        "4. Generate character sprites: sprite tool for hero, NPCs\n"
        "5. Generate object sprites: sprite tool for trees, rocks, chests, etc.\n"
        "6. Generate map: map_sprite tool with features matching the game design\n"
        "7. Write game code (Swift/SpriteKit): GameScene.swift, player logic, etc.\n"
        "8. Build: tuist generate run --open, then tuist build run\n"
        "9. If stuck on code: use code_search to find examples from GitHub/SO\n"
        "10. Iterate: fix errors, add features, regenerate sprites/maps as needed\n\n"

        "SPRITEKIT iOS GAME STRUCTURE (use with Tuist):\n"
        "  Project.swift — Tuist manifest (destinations: .iOS, product: .app)\n"
        "  Sources/GameApp.swift — @main App entry point\n"
        "  Sources/GameScene.swift — SKScene subclass (main game logic)\n"
        "  Sources/GameViewController.swift — UIViewController hosting SKView\n"
        "  Sources/Player.swift — Player sprite node\n"
        "  Resources/sprites/ — PNG sprite assets\n"
        "  Resources/maps/ — Map data files\n\n"

        "Tools:\n"
    )
    return registry, header + "\n".join(prompt_lines)


# ═══════════════════════════════════════════════════════════════════
#  INIT  (module-level so ai.py can import immediately)
# ═══════════════════════════════════════════════════════════════════

registry, sys_prompt_addendum = load_tools()

# ═══════════════════════════════════════════════════════════════════
#  2.  TOOL-CALL PARSER
# ═══════════════════════════════════════════════════════════════════

def _extract_balanced_json(text: str) -> List[str]:
    """Extract brace-balanced JSON candidates, aware of JSON string literals.

    Unlike naive brace counting, this tracks whether we are inside a quoted
    string so that braces within "content" fields don't break extraction.
    """
    candidates = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            start = i
            in_str = False
            j = i
            while j < len(text):
                c = text[j]
                if in_str:
                    if c == '\\':
                        j += 2  # skip escaped char (\" \\ \n etc.)
                        continue
                    elif c == '"':
                        in_str = False
                else:
                    if c == '"':
                        in_str = True
                    elif c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            candidates.append(text[start:j+1])
                            i = j + 1
                            break
                j += 1
            else:
                # Unbalanced — try to recover by closing the JSON
                if depth > 0 and start < len(text):
                    # Find if there's a "tool" key near the start
                    partial = text[start:]
                    if '"tool"' in partial[:80]:
                        # Close any open string first, then close all open braces
                        suffix = ''
                        if in_str:
                            # Strip trailing backslash to avoid broken escape seq
                            if partial.endswith('\\'):
                                partial = partial[:-1]
                            suffix += '"'
                        suffix += '}' * depth
                        candidates.append(partial + suffix)
                i = max(i + 1, j)
        else:
            i += 1
    return candidates


def _repair_json_string(candidate: str) -> str:
    """Fix common LLM JSON issues: unescaped newlines/tabs inside string values."""
    # Fix unescaped control chars inside JSON strings
    result = []
    in_str = False
    i = 0
    while i < len(candidate):
        c = candidate[i]
        if in_str:
            if c == '\\':
                result.append(c)
                if i + 1 < len(candidate):
                    result.append(candidate[i + 1])
                    i += 2
                    continue
            elif c == '"':
                in_str = False
                result.append(c)
            elif c == '\n':
                result.append('\\n')
            elif c == '\r':
                result.append('\\r')
            elif c == '\t':
                result.append('\\t')
            else:
                result.append(c)
        else:
            if c == '"':
                in_str = True
            result.append(c)
        i += 1
    return ''.join(result)


def parse_tool_call(llm_response: str) -> Optional[Dict]:
    """Extract the first valid tool-call JSON from an LLM response.

    Returns {"tool": str, "args": dict} or None.
    Handles: braces inside string values, unescaped control chars,
    truncated JSON (max_tokens cutoff), and multi-line content fields.
    """
    if not llm_response:
        return None

    # Strip <think> blocks so reasoning doesn't confuse the parser
    text = re.sub(r"<think>.*?</think>", "", llm_response, flags=re.DOTALL).strip()

    # Find all brace-delimited candidates (string-aware extraction)
    candidates = _extract_balanced_json(text)

    # Try each candidate — pick the first valid tool call
    for candidate in candidates:
        data = None

        # Attempt 1: direct json.loads
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass

        # Attempt 2: repair unescaped control chars then retry
        if data is None:
            try:
                repaired = _repair_json_string(candidate)
                data = json.loads(repaired)
            except (json.JSONDecodeError, ValueError):
                pass

        # Attempt 3: ast.literal_eval (handles Python-style strings)
        if data is None:
            try:
                data = ast.literal_eval(candidate)
            except Exception:
                pass

        if isinstance(data, dict) and data.get("tool") in registry:
            return {"tool": data["tool"], "args": data.get("args", {})}

    # Fallback: regex extraction for tool name + args when JSON parsing fails entirely
    # This handles cases where content fields have deeply broken encoding
    tool_match = re.search(r'"tool"\s*:\s*"(\w+)"', text)
    if tool_match and tool_match.group(1) in registry:
        tool_name = tool_match.group(1)
        # Try to extract the args object
        args_match = re.search(r'"args"\s*:\s*(\{.*)', text, re.DOTALL)
        if args_match:
            args_text = args_match.group(1)
            # Try to parse args with balanced extraction + repair
            args_candidates = _extract_balanced_json(args_text)
            for ac in args_candidates:
                try:
                    args_data = json.loads(_repair_json_string(ac))
                    if isinstance(args_data, dict):
                        return {"tool": tool_name, "args": args_data}
                except Exception:
                    pass
            # Last resort: extract individual known args via regex
            args = {}
            for key in ("file_path", "operation", "action", "url", "query", "mode"):
                km = re.search(rf'"{key}"\s*:\s*"([^"]*)"', args_text)
                if km:
                    args[key] = km.group(1)
            # For content field, grab everything between "content": " and the last "
            cm = re.search(r'"content"\s*:\s*"(.*)', args_text, re.DOTALL)
            if cm:
                content = cm.group(1)
                # Find the end: last " before }} at end
                end = content.rfind('"')
                if end > 0:
                    args["content"] = content[:end].replace('\\n', '\n').replace('\\t', '\t')
                elif content.strip():
                    # Truncated content — no closing quote (max_tokens cutoff).
                    # Use everything we recovered; partial write > no write.
                    truncated = content.rstrip()
                    if truncated.endswith('\\'):
                        truncated = truncated[:-1]
                    args["content"] = truncated.replace('\\n', '\n').replace('\\t', '\t')
            if args:
                return {"tool": tool_name, "args": args}

    return None

# ═══════════════════════════════════════════════════════════════════
#  3.  TOOL EXECUTION  (one-shot + persistent server mode)
# ═══════════════════════════════════════════════════════════════════

def _server_communicate(proc: subprocess.Popen, args: Dict,
                        timeout: float = SERVER_READ_TIMEOUT) -> Optional[str]:
    """Send a JSON command to a server-mode process and read the response."""
    try:
        proc.stdin.write(json.dumps(args) + "\n")
        proc.stdin.flush()

        deadline = time.time() + timeout
        while time.time() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], 1.0)
            if ready:
                line = proc.stdout.readline().strip()
                if line:
                    return line
        return None  # timeout
    except Exception:
        return None


_BROWSER_DEAD_PHRASES = (
    "browser has been closed", "context or browser has been closed",
    "Target page, context or browser", "Target closed",
    "Connection closed", "Browser closed", "page has been closed",
)


def _is_browser_dead(resp_str: str) -> bool:
    """Check if a server response indicates the browser context died."""
    return any(p in resp_str for p in _BROWSER_DEAD_PHRASES)


def _get_server_timeout(args: Dict) -> float:
    """Action-aware timeout: long-running operations get more time."""
    action = args.get('action', '') if isinstance(args, dict) else ''
    if action == 'deep_research':
        return 360.0   # deep research can take 3-5 minutes
    elif action in ('lead_gen', 'maps_search'):
        return 240.0   # lead gen scans 100+ pages
    elif action == 'crawl':
        return 180.0   # crawl can spider many pages
    return SERVER_READ_TIMEOUT


def _restart_server(tool_name: str, path: str,
                    persistent: Dict[str, subprocess.Popen]) -> subprocess.Popen:
    """Kill existing server (if any) and start a fresh one."""
    old = persistent.pop(tool_name, None)
    if old:
        try:
            old.terminate()
            old.wait(timeout=3)
        except Exception:
            try:
                old.kill()
            except Exception:
                pass
    proc = subprocess.Popen(
        [sys.executable, path, "--server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    # Handshake with timeout — don't block forever if server fails to start
    _hs_deadline = time.time() + 10.0
    _hs_ok = False
    while time.time() < _hs_deadline:
        _ready, _, _ = select.select([proc.stdout], [], [], 1.0)
        if _ready:
            _hs_line = proc.stdout.readline()
            if _hs_line and _hs_line.strip():
                _hs_ok = True
                break
    if not _hs_ok:
        try:
            proc.kill()
        except Exception:
            pass
        raise RuntimeError(f"Server handshake timeout for {tool_name}")
    persistent[tool_name] = proc
    return proc


def execute_tool(tool_name: str, args: Dict,
                 persistent: Dict[str, subprocess.Popen]) -> str:
    """Run a tool and return its text output.

    For tools that support --server mode, the process is kept alive in
    *persistent* so subsequent calls skip the startup cost.
    Browser-dead errors trigger automatic server restart + retry.
    """
    tool_def = registry[tool_name]
    path     = tool_def["path"]

    # ── Guards: intercept file I/O misuse → redirect to nano_editor ──
    if isinstance(args, dict):
        # Guard 1: web fetch with local file path → nano_editor read
        if (tool_name == "web"
                and args.get("action") == "fetch"
                and isinstance(args.get("url", ""), str)):
            _fetch_url = args["url"]
            if (_fetch_url.startswith("/")
                    or _fetch_url.startswith("~/")
                    or _fetch_url.startswith("file://")):
                _fpath = _fetch_url
                if _fpath.startswith("file://"):
                    _fpath = _fpath[7:]
                print(f"  ⚡ Local file detected → nano_editor read ({_fpath})", flush=True)
                if "nano_editor" in registry:
                    return execute_tool("nano_editor", {"file_path": _fpath, "operation": "read", "show_nano": False}, persistent)
                # Fallback: direct read
                try:
                    _rp = os.path.expanduser(_fpath)
                    with open(os.path.abspath(_rp), 'r', encoding='utf-8') as _rf:
                        return f"File: {_fpath}\n---\n{_rf.read()}"
                except Exception as _re:
                    return f"Error reading file: {_re}"

        # Guard 2: macos_terminal read with file_path → nano_editor read
        #   macos_terminal 'read' mode reads TERMINAL SCREEN, not files.
        if (tool_name == "macos_terminal"
                and args.get("mode") == "read"
                and args.get("file_path")):
            _fp = args["file_path"]
            print(f"  ⚡ File read redirected → nano_editor read ({_fp})", flush=True)
            if "nano_editor" in registry:
                return execute_tool("nano_editor", {"file_path": _fp, "operation": "read", "show_nano": False}, persistent)
            try:
                _rp = os.path.expanduser(_fp)
                with open(os.path.abspath(_rp), 'r', encoding='utf-8') as _rf:
                    return f"File: {_fp}\n---\n{_rf.read()}"
            except Exception as _re:
                return f"Error reading file: {_re}"

        # Guard 3: macos_terminal run cat/head/tail → nano_editor read
        if (tool_name == "macos_terminal"
                and args.get("mode") == "run"
                and isinstance(args.get("command", ""), str)):
            _cmd = args["command"].strip()
            _cat_match = re.match(r'^(?:cat|head|tail)\s+(.+)$', _cmd)
            if _cat_match:
                _fp = _cat_match.group(1).strip().strip("'\"")
                print(f"  ⚡ cat/head/tail redirected → nano_editor read ({_fp})", flush=True)
                if "nano_editor" in registry:
                    return execute_tool("nano_editor", {"file_path": _fp, "operation": "read", "show_nano": False}, persistent)
                try:
                    _rp = os.path.expanduser(_fp)
                    with open(os.path.abspath(_rp), 'r', encoding='utf-8') as _rf:
                        return f"File: {_fp}\n---\n{_rf.read()}"
                except Exception as _re:
                    return f"Error reading file: {_re}"

        # Guard 4: write_file on existing files → nano append
        if (tool_name == "macos_terminal"
                and args.get("mode") == "write_file"
                and args.get("file_path")):
            _wf_path = os.path.expanduser(args["file_path"])
            _wf_path = os.path.abspath(_wf_path)
            if os.path.isfile(_wf_path) and os.path.getsize(_wf_path) > 0:
                print(f"  ⚡ write_file → nano_editor append (file exists: {args['file_path']})", flush=True)
                _nano_args = {
                    "file_path": args["file_path"],
                    "content": args.get("content", ""),
                    "operation": "append",
                    "show_nano": False
                }
                if "nano_editor" in registry:
                    return execute_tool("nano_editor", _nano_args, persistent)
                try:
                    with open(_wf_path, 'a', encoding='utf-8') as _af:
                        _af.write(args.get("content", ""))
                    return f"✅ Appended to existing file: {args['file_path']}"
                except Exception as _ae:
                    return f"❌ Append failed: {_ae}"

        # Guard 5: nano_editor → always disable show_nano for agent speed
        if tool_name == "nano_editor":
            args["show_nano"] = False

    # ── Server-mode: reuse existing process ─────────────────────
    if tool_def.get("server_mode"):
        proc = persistent.get(tool_name)

        # Detect crashed server process
        if proc and proc.poll() is not None:
            print(f"  🔄 {tool_name} server crashed (exit {proc.returncode}) — restarting...", flush=True)
            persistent.pop(tool_name, None)
            proc = None

        # If process exists and is alive, just send the command
        _timeout = _get_server_timeout(args)
        if proc and proc.poll() is None:
            resp = _server_communicate(proc, args, timeout=_timeout)
            if resp is not None:
                # Check for browser-dead error in response → restart server
                if _is_browser_dead(resp):
                    print(f"  🔄 {tool_name} browser died — restarting server...", flush=True)
                    try:
                        proc = _restart_server(tool_name, path, persistent)
                        resp2 = _server_communicate(proc, args, timeout=_timeout)
                        if resp2 is not None:
                            return resp2
                    except Exception as e:
                        return f"❌ Server restart failed: {e}"
                return resp
            # communication failed — kill and restart
            print(f"  🔄 {tool_name} server unresponsive — restarting...", flush=True)
            try:
                proc.terminate()
            except Exception:
                pass
            persistent.pop(tool_name, None)

        # Start a new server process
        try:
            proc = _restart_server(tool_name, path, persistent)
            resp = _server_communicate(proc, args, timeout=_timeout)
            if resp is not None:
                return resp
            return "⚠️  Server started but no response received."
        except Exception as e:
            return f"❌ Server start failed: {e}"

    # ── One-shot execution ──────────────────────────────────────
    cmd = [sys.executable, path, "--json", json.dumps(args)]
    is_gui = any(kw in tool_name.lower()
                 for kw in ("launch", "open", "calc", "fterminal"))

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=LAUNCH_TIMEOUT)
            if proc.returncode != 0:
                err = stderr.strip() or stdout.strip() or "Unknown Error"
                return f"❌ Tool error: {err}"
            return stdout.strip() or "✅ executed."
        except subprocess.TimeoutExpired:
            if is_gui:
                return f"✅ {tool_name} launched (GUI)."
            # Non-GUI but slow — keep waiting
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                return f"❌ Error: {stderr.strip()}"
            return stdout.strip()
    except Exception as e:
        return f"❌ System Error: {e}"


# ═══════════════════════════════════════════════════════════════════
#  4.  STOP LISTENER  (Ctrl+S, Ctrl+C, and stdin "stop" command)
# ═══════════════════════════════════════════════════════════════════

def _setup_terminal_for_ctrl_s():
    """Disable IXON flow control so Ctrl+S is available as a key instead of XOFF.
    Returns the original terminal settings for restoration, or None on failure.
    """
    try:
        import termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        new_settings = termios.tcgetattr(fd)
        # Disable IXON (Ctrl+S/Ctrl+Q flow control)
        new_settings[0] &= ~termios.IXON  # iflag
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)
        return old_settings
    except Exception:
        return None

def _restore_terminal(old_settings):
    """Restore original terminal settings."""
    if old_settings is None:
        return
    try:
        import termios
        fd = sys.stdin.fileno()
        termios.tcsetattr(fd, termios.TCSANOW, old_settings)
    except Exception:
        pass


class _StopListener(threading.Thread):
    """Daemon thread that watches for stop signals:
    - Typing 'stop', 'pause', 'halt', 'abort' on stdin
    - Ctrl+S (0x13 byte — requires IXON disabled)
    - Ctrl+C is handled via SIGINT handler separately
    """

    STOP_WORDS = {"stop", "pause", "halt", "abort"}

    def __init__(self, event: threading.Event):
        super().__init__(daemon=True)
        self.event = event
        self._active = True

    def run(self):
        while self._active:
            try:
                readable, _, _ = select.select([sys.stdin], [], [], 0.3)
                if readable:
                    # Read raw bytes to detect Ctrl+S (0x13)
                    try:
                        raw = os.read(sys.stdin.fileno(), 64)
                        if raw:
                            # Ctrl+S = 0x13
                            if b'\x13' in raw:
                                print("\n⏸️  Ctrl+S detected — pausing agent...", flush=True)
                                self.event.set()
                                return
                            # Normal text input
                            line = raw.decode('utf-8', errors='replace').strip().lower()
                            if line in self.STOP_WORDS:
                                self.event.set()
                                return
                    except (OSError, UnicodeDecodeError):
                        # Fallback to readline
                        line = sys.stdin.readline().strip().lower()
                        if line in self.STOP_WORDS:
                            self.event.set()
                            return
            except Exception:
                return

    def deactivate(self):
        self._active = False


# ═══════════════════════════════════════════════════════════════════
#  5.  AGENTIC LOOP  (the core engine)
# ═══════════════════════════════════════════════════════════════════

class AgentLoop:
    """ReAct-style multi-step execution engine.

    Runs an  observe → think → act  loop until:
      • The LLM responds conversationally (no tool call → task complete)
      • The user presses Ctrl+S, Ctrl+C, or types "stop"
      • The step ceiling is reached

    v2.0 improvements:
      • Loop/stall detection — auto-injects recovery guidance
      • Action history tracking — prevents infinite retry loops
      • Per-step timing — warns on slow steps
      • Ctrl+S/C graceful pause — terminal flow control disabled
      • Edge + Safari observation hints
      • Optimized for Qwen 3.5-9B MLX on Apple M4/16GB
    """

    def __init__(self, max_steps: int = MAX_AGENT_STEPS):
        self.max_steps        = max_steps
        self._stop_event      = threading.Event()
        self._persistent      : Dict[str, subprocess.Popen] = {}
        self._step            = 0
        self._running         = False
        self._listener        : Optional[_StopListener] = None
        self._action_history  : List[str] = []   # track recent actions for stall detection
        self._step_times      : List[float] = []  # timing per step
        self._term_settings   = None              # saved terminal settings for restoration
        self._prev_sigint     = None              # saved SIGINT handler

    # ── public ──────────────────────────────────────────────────

    def request_stop(self):
        """Thread-safe soft stop."""
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def step(self) -> int:
        return self._step

    @staticmethod
    def _smart_truncate(result: str, limit: int = OBSERVATION_CHARS) -> str:
        """Truncate tool output intelligently, preserving critical sections.

        For safari snapshots, preserves ELEMENTS (most important) and
        truncates TEXT content (least important) first.
        """
        if len(result) <= limit:
            return result

        # Try to parse as JSON and truncate the page/message text intelligently
        try:
            data = json.loads(result)
            if isinstance(data, dict) and "page" in data:
                page = data["page"]
                if isinstance(page, str):
                    # Account for JSON overhead, escaping, and page in both page+message
                    dupe = 2 if data.get("message") == page else 1
                    # JSON escaping expands \n → \\n etc., so use ~1.3x safety margin
                    target = max(500, int((limit * 0.9) / dupe))
                    if len(page) > target:
                        page = AgentLoop._truncate_page_text(page, target)
                    data["page"] = page
                    if "message" in data:
                        data["message"] = page
                    reser = json.dumps(data, ensure_ascii=False)
                    # Secondary trim if still over (due to escaping expansion)
                    if len(reser) > limit and len(page) > 200:
                        trim_by = (len(reser) - limit) // dupe + 50
                        page = page[:len(page) - trim_by] + "\n… [trimmed]"
                        data["page"] = page
                        if "message" in data:
                            data["message"] = page
                        reser = json.dumps(data, ensure_ascii=False)
                    if len(reser) <= limit:
                        return reser
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # Fallback: blind truncation
        return result[:limit] + f"\n… [truncated — {len(result)} chars total]"

    @staticmethod
    def _truncate_page_text(page: str, limit: int) -> str:
        """Truncate page snapshot text, cutting lowest-priority sections first.

        Priority (highest first): PAGE, ELEMENTS, FORM STATUS, DIALOG, IFRAMES, TEXT
        """
        if len(page) <= limit:
            return page

        # Split into sections by === markers
        sections = []
        current_header = ""
        current_lines = []
        for line in page.split("\n"):
            if line.startswith("=== ") and line.endswith(" ==="):
                if current_lines:
                    sections.append((current_header, "\n".join(current_lines)))
                current_header = line
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            sections.append((current_header, "\n".join(current_lines)))

        if len(sections) <= 1:
            return page[:limit] + "\n… [truncated]"

        # Priority order for keeping sections
        priority_keys = ["=== PAGE ===", "=== ELEMENTS", "=== FORM STATUS ===",
                         "=== DIALOG ===", "=== IFRAMES", "=== TEXT ==="]

        def section_priority(header):
            for i, key in enumerate(priority_keys):
                if header.startswith(key) or key.startswith(header[:20]):
                    return i
            return len(priority_keys)

        # Sort by priority (keep highest priority)
        sections.sort(key=lambda s: section_priority(s[0]))

        # Build output, cutting from lowest priority when over limit
        output_parts = []
        remaining = limit - 50  # reserve for truncation notice
        for header, content in sections:
            if len(content) <= remaining:
                output_parts.append(content)
                remaining -= len(content) + 1
            elif remaining > 200:
                output_parts.append(content[:remaining] + "\n… [section truncated]")
                remaining = 0
            # else: skip this section entirely

        result = "\n".join(output_parts)
        if len(page) > len(result):
            result += f"\n[truncated from {len(page)} chars]"
        return result

    def _detect_stall(self, tool_name: str, action: str,
                       result_str: str = "") -> Optional[str]:
        """Detect if the agent is stuck repeating the same action.
        Returns recovery guidance string, or None if no stall detected.

        v4.1: Also detects cycling patterns (A→B→A→B) and browser-dead loops.
        """
        # Email sends to different recipients are legitimate — exempt from stall detection
        if tool_name == "macos_mail" and action in ("send", "send_batch"):
            return None
        action_key = f"{tool_name}:{action}"
        self._action_history.append(action_key)

        # ── BROWSER-DEAD DETECTION (highest priority) ──────────
        if _is_browser_dead(result_str):
            if not hasattr(self, '_browser_dead_count'):
                self._browser_dead_count = 0
            self._browser_dead_count += 1
            if self._browser_dead_count >= 2:
                self._browser_dead_count = 0
                return (
                    "BROWSER DEAD: The browser context has been closed and reconnection failed. "
                    "STOP trying browser actions. Instead:\n"
                    "1. The browser server will auto-restart on next call — try ONE more new_tab\n"
                    "2. If that fails, inform the user the browser needs manual restart\n"
                    "DO NOT keep retrying — the browser is not responding."
                )
        else:
            if hasattr(self, '_browser_dead_count'):
                self._browser_dead_count = 0

        # ── CYCLING PATTERN DETECTION (A→B→A→B) ──────────────
        if len(self._action_history) >= 4:
            h = self._action_history[-4:]
            # Detect A-B-A-B pattern
            if h[0] == h[2] and h[1] == h[3] and h[0] != h[1]:
                self._action_history = []
                return (
                    f"CYCLING STALL: You are alternating between {h[0]} and {h[1]} with no progress. "
                    f"STOP this pattern immediately. Try a completely different approach:\n"
                    f"1. If browser isn't working, try a different tool or inform the user\n"
                    f"2. If navigation isn't working, use browse with a direct URL\n"
                    f"3. If the page won't load, try dismiss → wait → snapshot\n"
                    f"DO NOT repeat either action again."
                )
            # Detect A-B-C-A pattern (3-step cycle)
            if len(self._action_history) >= 6:
                h6 = self._action_history[-6:]
                if h6[0] == h6[3] and h6[1] == h6[4] and h6[2] == h6[5]:
                    self._action_history = []
                    return (
                        f"3-STEP CYCLING: You are repeating the pattern {h6[0]} → {h6[1]} → {h6[2]}. "
                        f"STOP immediately. Take a completely different approach to the task."
                    )

        # Track URL from results for click-no-navigate detection
        result_url = ""
        if result_str:
            try:
                rd = json.loads(result_str)
                result_url = rd.get("url", "")
            except Exception:
                pass

        # Detect click-no-navigate: agent clicks text/element but URL stays the same
        if action in ("click", "click_at") and result_url:
            if not hasattr(self, '_last_click_urls'):
                self._last_click_urls = []
            self._last_click_urls.append(result_url)
            # If last 2 clicks resulted in the same URL, the clicks aren't navigating
            if len(self._last_click_urls) >= 2 and len(set(self._last_click_urls[-2:])) == 1:
                self._last_click_urls = []
                return (
                    f"CLICK NOT NAVIGATING: You clicked but the URL didn't change ({result_url}). "
                    f"The element you're clicking is not a real link or is being intercepted.\n"
                    f"ESCALATE immediately:\n"
                    f"1. Look at ELEMENTS list for actual <a> link elements with -> URL (href)\n"
                    f"2. Click the #N ref of the link element (not the text content)\n"
                    f"3. If no link ref works, use browse action with the target URL directly\n"
                    f"4. Last resort: click_at with @x,y coordinates of the link element\n"
                    f"DO NOT click the same text again — it won't work."
                )
        else:
            # Reset click URL tracking on non-click actions
            if hasattr(self, '_last_click_urls'):
                self._last_click_urls = []

        # Detect navigation stall: repeated new_tab/browse to same-domain URLs
        if action in ("new_tab", "newtab", "open", "browse", "goto", "navigate"):
            nav_actions = [a for a in self._action_history[-4:] if "new_tab" in a or "browse" in a or "open" in a or "navigate" in a]
            if len(nav_actions) >= 2:
                self._action_history = []
                return (
                    f"NAVIGATION STALL: You keep navigating to URLs that redirect. STOP trying new_tab/browse with the same URL.\n"
                    f"Instead:\n"
                    f"1. LOOK at the current page's ELEMENTS list for navigation links\n"
                    f"2. CLICK on the appropriate nav link using its #N reference\n"
                    f"3. If the navigation menu has what you need (e.g., 'Surveys'), click that link\n"
                    f"4. If click doesn't work, try click_at with the link's @x,y coordinates\n"
                    f"5. Or try browse with the href URL shown next to the link in the ELEMENTS list\n"
                    f"DO NOT open a new tab to the same URL that already redirected you."
                )

        # Detect wrong-page stall: agent is clicking nav links but staying on wrong page
        if action == "click" and result_url:
            if not hasattr(self, '_wrong_page_count'):
                self._wrong_page_count = 0
                self._expected_url_pattern = ""
            # Detect if stuck on /discover-new, /affiliates, or other wrong pages
            wrong_pages = ["/discover-new", "/affiliates", "/offers"]
            for wp in wrong_pages:
                if wp in result_url:
                    self._wrong_page_count += 1
                    if self._wrong_page_count >= 2:
                        self._wrong_page_count = 0
                        return (
                            f"WRONG PAGE: You are stuck on {result_url} and clicking nav links isn't working.\n"
                            f"STOP clicking and use browse to navigate directly:\n"
                            f'  {{"tool":"edge","args":{{"action":"browse","url":"THE_CORRECT_URL"}}}}\n'
                            f"For surveys: browse to https://www.ysense.com/surveys\n"
                            f"DO NOT keep clicking links that don't navigate you to the right page."
                        )
                    break
            else:
                self._wrong_page_count = 0

        # ── VISION STALL DETECTION ──────────────────────────
        if action == "pixel_click" and result_str:
            if not hasattr(self, '_pixel_click_coords'):
                self._pixel_click_coords = []
            try:
                rd = json.loads(result_str)
                coords = rd.get("normalized_coords", "")
                if coords:
                    self._pixel_click_coords.append(coords)
                    if len(self._pixel_click_coords) >= 3:
                        if len(set(self._pixel_click_coords[-3:])) == 1:
                            self._pixel_click_coords = []
                            return (
                                f"REPEATED PIXEL CLICKS at {coords}: The element may not be clickable "
                                "or coordinates are wrong. Try:\n"
                                "1. Call vision_snapshot to refresh your view\n"
                                "2. Adjust coordinates by ±50\n"
                                "3. Fall back to traditional snapshot + click with #N ref"
                            )
            except Exception:
                pass
        elif action != "vision_snapshot":
            if hasattr(self, '_pixel_click_coords'):
                self._pixel_click_coords = []

        if action == "vision_snapshot" and result_str:
            if not hasattr(self, '_vision_error_count'):
                self._vision_error_count = 0
            if '"status": "error"' in result_str or '"error"' in result_str[:100]:
                self._vision_error_count += 1
                if self._vision_error_count >= 3:
                    self._vision_error_count = 0
                    return (
                        "VISION SNAPSHOTS FAILING: Switch to traditional snapshot action. "
                        "Use snapshot with #N refs instead of vision_snapshot."
                    )
            else:
                self._vision_error_count = 0

        # ── SEARCH-GOES-TO-GOOGLE DETECTION ──────────────────
        # When agent uses 'search' action while on a non-Google site, warn that it left the site
        if action == "search" and result_str:
            try:
                rd = json.loads(result_str)
                result_url = rd.get("url", "")
                if "google.com/search" in result_url:
                    # Agent was on a site (Indeed, etc) and search took them to Google
                    return (
                        "WRONG SEARCH: The 'search' action navigated to Google, taking you AWAY from the site you were on. "
                        "NEVER use 'search' action when you're on a specific site (Indeed, LinkedIn, etc).\n"
                        "Instead:\n"
                        "1. Use browse to navigate to the site's search URL directly (e.g. https://www.indeed.com/jobs?q=KEYWORD&l=LOCATION)\n"
                        "2. Or fill the search field and press Enter: {\"tool\":\"edge\",\"args\":{\"action\":\"keys\",\"text\":\"Enter\"}}\n"
                        "Go back to the site now with browse action."
                    )
            except Exception:
                pass

        # Only check last N actions
        recent = self._action_history[-STALL_THRESHOLD:]
        if len(recent) < STALL_THRESHOLD:
            return None

        # All recent actions identical → stall
        if len(set(recent)) == 1:
            stall_count = len(recent)
            self._action_history = []  # reset to avoid repeated warnings

            if action in ("snapshot", "look", "page", "observe"):
                return (
                    f"STALL DETECTED: You have called {action} {stall_count} times in a row "
                    f"with no progress. STOP repeating and try something different:\n"
                    f"1. Try iframe list/enter — content may be in an iframe\n"
                    f"2. Try scroll down — content may be below viewport\n"
                    f"3. Try dismiss — overlays may be blocking content\n"
                    f"4. Try wait with value='stable' — page may still be loading\n"
                    f"5. Try select_text — extract raw text as last resort\n"
                    f"6. Try click_at with coordinates from previous snapshots\n"
                    f"DO NOT call {action} again without trying one of these first."
                )
            elif action in ("read", "read_page", "summarize"):
                return (
                    f"STALL DETECTED: Repeated read attempts ({stall_count}x) with no content. "
                    f"The page content may not be loading via JS. Try:\n"
                    f"1. snapshot to see the page structure and elements\n"
                    f"2. scroll down to load lazy content, then read again\n"
                    f"3. wait value='stable' to let the page finish loading\n"
                    f"4. select_text to get raw text as fallback\n"
                    f"5. Use snapshot TEXT section content directly for your summary"
                )
            elif action in ("click", "click_at", "press"):
                return (
                    f"STALL DETECTED: Repeated click attempts ({stall_count}x). "
                    f"The element may not exist or is not interactive. Try:\n"
                    f"1. Snapshot to get fresh element refs\n"
                    f"2. Try a different element or text target\n"
                    f"3. Try click_at with @x,y coordinates\n"
                    f"4. Try scroll down to find the element"
                )
            elif action == "dismiss":
                return (
                    f"STALL DETECTED: Repeated dismiss ({stall_count}x). "
                    f"Overlays may already be cleared. Try:\n"
                    f"1. Snapshot to see the current page state\n"
                    f"2. Proceed with the task — the page may be ready"
                )
            else:
                return (
                    f"STALL DETECTED: Action '{action}' repeated {stall_count} times. "
                    f"Try a different approach to make progress."
                )

        return None

    def cleanup(self):
        """Terminate any persistent server processes."""
        for name, proc in list(self._persistent.items()):
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._persistent.clear()

    # ── main entry point ────────────────────────────────────────

    def run(self, messages: List[Dict], generate_fn: Callable) -> str:
        """Execute the agentic loop.

        Args
        ----
        messages :    conversation history (mutated in-place with new turns)
        generate_fn : callback(messages) → str  that streams LLM output
                      and returns the full response text

        Returns
        -------
        The final assistant response (conversational, no tool call).
        """
        self._stop_event.clear()
        self._running = True
        self._step    = 0
        self._action_history = []
        self._step_times = []

        # Enable Ctrl+S by disabling terminal flow control
        self._term_settings = _setup_terminal_for_ctrl_s()

        # Set up SIGINT (Ctrl+C) to pause instead of crash
        def _sigint_handler(sig, frame):
            print("\n⏸️  Ctrl+C detected — pausing agent...", flush=True)
            self._stop_event.set()
        self._prev_sigint = signal.signal(signal.SIGINT, _sigint_handler)

        # Start listening for "stop" / Ctrl+S on stdin
        self._listener = _StopListener(self._stop_event)
        self._listener.start()

        try:
            return self._loop(messages, generate_fn)
        finally:
            self._running = False
            if self._listener:
                self._listener.deactivate()
                self._listener = None
            # Restore terminal and SIGINT handler
            _restore_terminal(self._term_settings)
            self._term_settings = None
            if self._prev_sigint is not None:
                try:
                    signal.signal(signal.SIGINT, self._prev_sigint)
                except Exception:
                    pass
                self._prev_sigint = None

    # ── context window management ─────────────────────────────

    @staticmethod
    def _prune_context(messages: List[Dict], max_chars: int = 64000):
        """Prune old tool-result messages to stay within context window.

        v4.1: More aggressive pruning (64K default vs 80K) for faster inference.
        Keeps: system message (first), last 8 messages for continuity.
        Removes oldest/largest tool-result messages first.
        Also compresses old assistant reasoning (keep only tool calls).
        """
        total = sum(len(m.get("content", "")) for m in messages)
        if total <= max_chars:
            return

        # Always keep system message (index 0) and last 8 messages
        if len(messages) <= 9:
            return

        keep_tail = 8
        # Phase 1: Compress old tool results
        for i in range(1, len(messages) - keep_tail):
            content = messages[i].get("content", "")
            if len(content) < 300:
                continue
            if "[TOOL RESULT:" in content:
                messages[i]["content"] = "[earlier tool result pruned for context]"
            elif messages[i].get("role") == "assistant" and len(content) > 500:
                # Keep only tool-call JSON from old assistant messages
                tc_match = re.search(r'\{[^{}]*"tool"\s*:', content)
                if tc_match:
                    messages[i]["content"] = content[:100] + "... [reasoning pruned]"
                else:
                    messages[i]["content"] = content[:200] + "... [pruned]"
            total = sum(len(m.get("content", "")) for m in messages)
            if total <= max_chars:
                return

        # Phase 2: If still over, remove entire old messages (keep system + tail)
        while total > max_chars and len(messages) > keep_tail + 1:
            # Remove the oldest non-system message
            removed = messages.pop(1)
            total -= len(removed.get("content", ""))

    # ── memory helpers ─────────────────────────────────────────

    def _record_memory_step(self, tool_name: str, tool_args: Dict,
                            action: str, result: str, response: str):
        """Record a tool step in episode/training recorders if active."""
        if hasattr(self, '_episode') and self._episode:
            try:
                self._episode.record_tool_call(tool_name, action, result)
            except Exception:
                pass
        if hasattr(self, '_trainer') and self._trainer and self._trainer.is_active:
            try:
                # Extract reasoning: text before the JSON tool call
                reasoning = re.sub(r'\{.*', '', response, flags=re.DOTALL).strip()
                reasoning = re.sub(r'<think>.*?</think>', '', reasoning, flags=re.DOTALL).strip()
                self._trainer.record_step(tool_name, tool_args, result[:300], reasoning)
            except Exception:
                pass

    def _learn_from_stall(self, stall_guidance: str, tool_name: str, action: str):
        """Auto-learn from stall detection — save as semantic memory for future avoidance."""
        if not hasattr(self, 'memory') or not self.memory:
            return
        try:
            fact = f"Stall on {tool_name}:{action} — {stall_guidance[:200]}"
            self.memory.save_fact(fact, source="stall_recovery", category="debugging")
        except Exception:
            pass

    def _finalize_memory(self, outcome: str = "success"):
        """Finalize episode and training recordings + closed-loop write-after-work."""
        ep_id = None
        if hasattr(self, '_episode') and self._episode:
            try:
                ep_id = self._episode.finalize(outcome)
            except Exception:
                pass

            # ── Closed-loop: write-after-work learnings extraction ──
            if getattr(self, 'auto_learn', True) and hasattr(self, 'learnings') and self.learnings:
                try:
                    ep = self._episode
                    task_brief = ep.task[:80] if ep.task else "unknown"

                    if outcome == "success" and ep._stall_recoveries > 0:
                        # Task succeeded but had stalls — record what caused them
                        stall_tools = [f for f in ep.failures if "not found" in f or "404" in f]
                        if stall_tools:
                            self.learnings.add("mistake",
                                f"Stalls from {', '.join(stall_tools[:3])} — snapshot before clicking refs",
                                source_task=task_brief)
                    elif outcome == "success" and ep.step_count < 10 and not ep.failures:
                        # Very efficient success — record what worked
                        if ep.successes:
                            self.learnings.add("optimization",
                                f"Efficient path: {' → '.join(ep.successes[:5])}",
                                source_task=task_brief)
                    elif outcome != "success" and ep.failures:
                        # Task failed — record failure pattern
                        self.learnings.add("mistake",
                            f"Task failed: {', '.join(ep.failures[:3])}",
                            source_task=task_brief)

                    # Auto-promote + failure pattern detection
                    if self.memory and ep_id:
                        try:
                            self.memory.auto_promote(ep_id)
                            if outcome != "success":
                                self.memory.detect_failure_patterns(ep_id, self.learnings)
                        except Exception:
                            pass
                except Exception:
                    pass

            self._episode = None

        if hasattr(self, '_trainer') and self._trainer and self._trainer.is_active:
            try:
                proc_id = self._trainer.finalize(outcome)
                print(f"  {_GR}training: procedure saved → {proc_id}{_X}", flush=True)
            except Exception:
                pass
            self._trainer = None

    # ── inner loop ──────────────────────────────────────────────

    def _loop(self, messages: List[Dict], generate_fn: Callable) -> str:

        while self._step < self.max_steps:

            # ── prune context if needed ──────────────────────
            self._prune_context(messages)

            # ── check for user stop (Ctrl+S, Ctrl+C, or "stop") ──
            if self._stop_event.is_set():
                msg = "⏸️  Paused. Say \"continue\" to resume or give me new instructions."
                print(f"\n{msg}", flush=True)
                messages.append({"role": "assistant", "content": msg})
                self._finalize_memory("paused")
                return msg

            # ── STEP: generate LLM response ─────────────────────
            self._step += 1
            step_start = time.time()

            if self._step > 1:
                print(f"\n  {_R}{'─' * 46}{_X}", flush=True)
                step_display = f"  {_GR}step {self._step}"
                if self.max_steps <= 500:
                    step_display += f"/{self.max_steps}"
                print(f"{step_display}{_X}", flush=True)

            response = generate_fn(messages)

            # ── parse for tool call ─────────────────────────────
            tool_call = parse_tool_call(response)

            if tool_call is None:
                # ── Task continuity: check for unfinished file operations ──
                if (hasattr(self, '_pending_ops') and self._pending_ops
                        and not getattr(self, '_completion_check_done', False)):
                    self._completion_check_done = True
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"WAIT — you have not completed these operations: {self._pending_ops}. "
                            "Call the appropriate tool NOW to finish. Do NOT skip this step."
                        )
                    })
                    continue  # re-enter loop for one more chance
                # No tool call → LLM is speaking to the user → done
                messages.append({"role": "assistant", "content": response})
                # ── Memory: finalize recordings ──
                self._finalize_memory("success")
                return response

            # ── execute tool ────────────────────────────────────
            tool_name = tool_call["tool"]
            tool_args = tool_call["args"]
            action = tool_args.get("action", "") or tool_args.get("mode", "")

            # ── Vision: inject vision flag for auto-snapshot after navigation ──
            if (getattr(self, 'vision_enabled', False)
                    and tool_name in ("edge", "safari")
                    and action in ("new_tab", "newtab", "open", "browse", "goto", "navigate", "nav")):
                tool_args["vision"] = True

            # ── File tools: skip nano visual display for agent-driven writes ──
            if tool_name == "nano_editor" and tool_args.get("operation", "overwrite") != "read":
                tool_args.setdefault("show_nano", False)

            args_preview = json.dumps(tool_args, ensure_ascii=False)
            if len(args_preview) > 140:
                args_preview = args_preview[:140] + "…"
            print(f"\n  {_GR}{tool_name}({args_preview}){_X}", flush=True)

            result = execute_tool(tool_name, tool_args, self._persistent)

            # ── Memory: record step ──
            self._record_memory_step(tool_name, tool_args, action, result, response)

            # Track step timing
            step_elapsed = time.time() - step_start
            self._step_times.append(step_elapsed)

            # ── smart truncation (optimized for Qwen 3.5-9B context) ──
            is_content_action = (
                tool_name in ("safari", "edge")
                and action in ("read", "search", "select_text", "read_page", "extract", "summarize")
            )
            obs_limit = OBSERVATION_CHARS_CONTENT if is_content_action else OBSERVATION_CHARS
            if len(result) > obs_limit:
                result = self._smart_truncate(result, obs_limit)

            # Show a short preview
            preview = result[:200].replace("\n", "  ")
            if len(result) > 200:
                preview += "…"
            print(f"  {_DIM}{preview}{_X}", flush=True)

            # Step timing info
            if step_elapsed > STEP_TIME_WARN:
                print(f"  {_DIM}{step_elapsed:.1f}s{_X}", flush=True)

            # ── feed observation back to LLM ────────────────────
            messages.append({"role": "assistant", "content": response})

            # Build observation with step counter and context-sensitive hints
            step_tag = f"[Step {self._step}]" if self.max_steps > 500 else f"[Step {self._step}/{self.max_steps}]"
            budget_warn = ""
            remaining = self.max_steps - self._step
            if remaining <= 5:
                budget_warn = f" ⚠️ Only {remaining} steps remaining — wrap up or summarize progress."
            elif remaining <= 20:
                budget_warn = f" ({remaining} steps left)"
            elif self._step % 50 == 0:
                budget_warn = f" [Step {self._step} — running smoothly]"

            # ── TRUNCATION CONTINUATION ───────────────────────────
            # If the LLM response was truncated (max_tokens) during a file write,
            # inject a continuation hint so the model appends the rest.
            _truncation_hint = ""
            was_truncated = getattr(generate_fn, 'was_truncated', False)
            if was_truncated and tool_name in ("nano_editor", "macos_terminal"):
                written_content = tool_args.get("content", "")
                if written_content:
                    last_lines = written_content.strip().split('\n')[-3:]
                    last_preview = '\n'.join(last_lines)
                    _truncation_hint = (
                        f"\n⚠️ YOUR RESPONSE WAS TRUNCATED (max_tokens). "
                        f"Only {len(written_content)} chars were written. "
                        f"The file ends at:\n{last_preview}\n"
                        "CONTINUE by calling nano_editor with operation='append' "
                        "to add the REMAINING content. Pick up EXACTLY where you left off."
                    )

            # ── STALL DETECTION ──────────────────────────────────
            stall_guidance = self._detect_stall(tool_name, action, result)

            # Build pending ops reminder for task continuity
            _pending_reminder = ""
            if hasattr(self, '_pending_ops') and self._pending_ops:
                _pending_reminder = (
                    f" PENDING: {self._pending_ops}. "
                    "Do NOT respond conversationally until ALL file operations are done."
                )

            obs_suffix = (
                f"{step_tag} "
                "Analyze this result. If ALL parts of the task are complete "
                "(including any file updates/saves), respond to the user. "
                f"If more steps are needed, call the next tool.{_truncation_hint}{_pending_reminder}{budget_warn}"
            )

            # For browser actions, always remind to check the URL
            if tool_name in ("safari", "edge") and action in ("click", "click_at", "fill", "keys", "type"):
                try:
                    data = json.loads(result)
                    cur_url = data.get("url", "")
                    cur_title = data.get("title", "")
                    if cur_url:
                        obs_suffix = (
                            f"{step_tag} "
                            f"Current page: \"{cur_title}\" @ {cur_url}\n"
                            "CHECK: Is this the page you expected? Element #N refs are ONLY valid for THIS page. "
                            f"If this is wrong, navigate to the correct page first.{budget_warn}"
                        )
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # Inject stall recovery if detected
            if stall_guidance:
                obs_suffix = f"{step_tag} {stall_guidance}{budget_warn}"
                self._learn_from_stall(stall_guidance, tool_name, action)

            # ── CONTEXT-SENSITIVE HINTS (Safari + Edge) ──────────
            elif tool_name in ("safari", "edge") and action in ("snapshot", "observe", "look", "page"):
                try:
                    data = json.loads(result)
                    page = data.get("page", "") or data.get("message", "")
                    if not page or len(page.strip()) < 50 or page.count('[') < 3:
                        obs_suffix = (
                            f"{step_tag} "
                            "⚠️ EMPTY/SPARSE SNAPSHOT — very little content detected! "
                            "The form content is probably in an IFRAME. DO NOT retry snapshot. Instead:\n"
                            f"1. Try: {{\"tool\":\"{tool_name}\",\"args\":{{\"action\":\"iframe\",\"value\":\"list\"}}}} to see iframes\n"
                            f"2. Then: {{\"tool\":\"{tool_name}\",\"args\":{{\"action\":\"iframe\",\"value\":\"enter 0\"}}}} to enter the first iframe\n"
                            "3. Then take a snapshot to see the actual content.\n"
                            "Other options: scroll down, dismiss overlays, wait for page to stabilize, or use select_text."
                            f"{budget_warn}"
                        )
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            elif tool_name in ("safari", "edge") and ("NOT_FOUND" in result or "STALE_REF" in result):
                obs_suffix = (
                    f"{step_tag} "
                    "WARNING: Element was NOT FOUND — the page may have changed. "
                    "The snapshot above has REFRESHED element refs. "
                    "Use the updated #N numbers from the ELEMENTS list. "
                    f"If the element is gone, try a different approach.{budget_warn}"
                )
            elif tool_name in ("safari", "edge") and "SEARCH:" in result and "Found" in result:
                obs_suffix = (
                    f"{step_tag} "
                    "Search results received. Open the most relevant results in new tabs "
                    "(new_tab action with URL), then read each to gather information. "
                    f"Synthesize findings into a comprehensive answer with source links.{budget_warn}"
                )
            elif tool_name in ("safari", "edge") and action == "read":
                obs_suffix = (
                    f"{step_tag} "
                    "Page content extracted. If researching, note key facts and the URL. "
                    "Continue reading other tabs/pages or synthesize if you have enough. "
                    f"Include source URLs in your final response.{budget_warn}"
                )
            elif tool_name in ("safari", "edge") and "NEW_TAB_OPENED" in result:
                obs_suffix = (
                    f"{step_tag} "
                    "A new tab/popup was opened and auto-switched to. "
                    "Snapshot to see the new page content, then continue with the task."
                    f"{budget_warn}"
                )
            elif tool_name == "edge" and action == "dismiss":
                obs_suffix = (
                    f"{step_tag} "
                    "Overlays dismissed. The page should be clear now. "
                    "Snapshot to see available elements and proceed with the task immediately. "
                    f"Do NOT call dismiss again unless new popups appear.{budget_warn}"
                )

            # ── VISION MODE HINTS ─────────────────────────────────
            elif tool_name in ("safari", "edge") and action == "vision_snapshot":
                try:
                    data = json.loads(result)
                    if data.get("status") == "success":
                        vp = data.get("viewport", {})
                        obs_suffix = (
                            f"{step_tag} "
                            f"Vision snapshot captured ({vp.get('width','?')}x{vp.get('height','?')}). "
                            "Screenshot saved to disk. Use element coordinates from the snapshot "
                            "to interact via pixel_click/pixel_type with [x,y] on 0-1000 scale. "
                            f"[0,0]=top-left, [1000,1000]=bottom-right.{budget_warn}"
                        )
                except Exception:
                    pass

            elif tool_name in ("safari", "edge") and action in ("pixel_click", "pixel_type", "pixel_drag"):
                obs_suffix = (
                    f"{step_tag} "
                    "Pixel action executed. Check the result to verify the interaction worked. "
                    f"If needed, call vision_snapshot to see the updated page state.{budget_warn}"
                )

            elif tool_name == "screen_capture":
                obs_suffix = (
                    f"{step_tag} "
                    "Desktop screenshot captured. Use the element descriptions and coordinates "
                    "to interact with the screen. For browser interactions, prefer edge tool. "
                    f"For native app interactions, use pixel coordinates.{budget_warn}"
                )

            # ── FORCE_CLICK NO NAVIGATION DETECTION ─────────────────
            if tool_name in ("safari", "edge") and action in ("click", "click_at") and "FORCE_CLICK" in result:
                try:
                    data = json.loads(result)
                    result_url = data.get("url", "")
                    # Check if URL didn't change (click didn't navigate)
                    if result_url and len(messages) >= 3:
                        prev_url = ""
                        for prev_msg in reversed(messages[-5:]):
                            prev_content = prev_msg.get("content", "")
                            if '"url":' in prev_content:
                                um = re.search(r'"url"\s*:\s*"([^"]+)"', prev_content)
                                if um:
                                    prev_url = um.group(1)
                                    break
                        if prev_url and prev_url == result_url:
                            obs_suffix = (
                                f"{step_tag} "
                                "⚠️ FORCE_CLICKED but URL didn't change — the click did NOT navigate! "
                                "The element you clicked is not a real link. ESCALATE:\n"
                                "1. Find the actual <a> link element in ELEMENTS list (look for -> URL)\n"
                                "2. Click the #N ref of that link\n"
                                "3. Or use browse action to navigate directly to the target URL\n"
                                f"Do NOT click the same text again.{budget_warn}"
                            )
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # ── UNEXPECTED NAVIGATION DETECTION (click/fill/keys changed page) ──
            if tool_name in ("safari", "edge") and action in ("click", "click_at", "fill", "keys", "type"):
                try:
                    data = json.loads(result)
                    result_url = data.get("url", "")
                    result_title = data.get("title", "")
                    if result_url:
                        # Find previous URL from message history
                        prev_url = ""
                        for prev_msg in reversed(messages[-6:]):
                            pc = prev_msg.get("content", "")
                            um = re.search(r'"url"\s*:\s*"(https?://[^"]+)"', pc)
                            if um:
                                prev_url = um.group(1)
                                break
                        if prev_url and prev_url != result_url:
                            # URL changed — warn about stale element refs
                            from urllib.parse import urlparse
                            old_path = urlparse(prev_url).path
                            new_path = urlparse(result_url).path
                            if old_path != new_path:
                                obs_suffix = (
                                    f"{step_tag} "
                                    f"⚠️ PAGE CHANGED after {action}! URL went from:\n"
                                    f"  {prev_url}\n  → {result_url}\n"
                                    f"Current page: \"{result_title}\"\n"
                                    "ALL previous element #N refs are INVALID. "
                                    "You MUST use the NEW #N refs from the ELEMENTS list in this snapshot. "
                                    "Check the URL — if this is not the page you expected, "
                                    f"navigate back with browse action.{budget_warn}"
                                )
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # ── REDIRECT DETECTION ──────────────────────────────────
            if tool_name in ("safari", "edge") and "redirect_warning" in result:
                try:
                    data = json.loads(result)
                    rw = data.get("redirect_warning", "")
                    nm = data.get("navigation_menu", "")
                    if rw:
                        obs_suffix = (
                            f"{step_tag} "
                            f"⚠️ {rw}\n"
                            f"{nm}\n"
                            "IMPORTANT: Do NOT retry the same URL — use the navigation menu elements "
                            "visible in the ELEMENTS list to click to where you need to go. "
                            "Look for links in the nav bar at the top of the page."
                            f"{budget_warn}"
                        )
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # ── CAPTCHA DETECTION ──────────────────────────────────
            if tool_name in ("safari", "edge") and "CAPTCHA_DETECTED" in result:
                obs_suffix = (
                    f"{step_tag} "
                    "⚠️ CAPTCHA DETECTED on this page. CAPTCHAs cannot be solved automatically. "
                    "DO NOT attempt to click or interact with the CAPTCHA. Instead:\n"
                    "1. Go back to the previous page (back action)\n"
                    "2. Try a different survey/link that doesn't have CAPTCHA\n"
                    "3. Or inform the user that this page requires manual CAPTCHA solving.\n"
                    f"Do NOT waste steps trying to solve or bypass the CAPTCHA.{budget_warn}"
                )

            # ── ABOUT:BLANK RECOVERY ────────────────────────────────
            if tool_name in ("safari", "edge") and '"url": "about:blank"' in result:
                # Find last known good URL from message history
                last_url = ""
                for prev_msg in reversed(messages[-10:]):
                    pc = prev_msg.get("content", "")
                    um = re.search(r'"url"\s*:\s*"(https?://[^"]+)"', pc)
                    if um:
                        last_url = um.group(1)
                        break
                recovery_hint = ""
                if last_url:
                    recovery_hint = f" Re-navigate to the last known URL: browse to {last_url}"
                obs_suffix = (
                    f"{step_tag} "
                    "⚠️ BROWSER LOST PAGE — current URL is about:blank (browser crashed or server restarted). "
                    f"Navigate back to where you were.{recovery_hint}\n"
                    f"Use browse action to go to the target URL.{budget_warn}"
                )

            # ── CLOUDFLARE CHALLENGE DETECTION ──────────────────────
            if tool_name in ("safari", "edge") and "CLOUDFLARE_CHALLENGE" in result:
                obs_suffix = (
                    f"{step_tag} "
                    "⚠️ CLOUDFLARE CHALLENGE detected. The page is being verified. "
                    "Wait for it to auto-resolve: wait text='Just a moment' condition='disappear' max_wait=15\n"
                    f"Then snapshot to see the actual page. Do NOT navigate away.{budget_warn}"
                )

            # ── EMPTY READ DETECTION ────────────────────────────────
            if tool_name in ("safari", "edge") and action == "read":
                try:
                    data = json.loads(result)
                    content = data.get("content", "")
                    if not content or len(content.strip()) < 50:
                        obs_suffix = (
                            f"{step_tag} "
                            "⚠️ READ RETURNED EMPTY — the page may not have loaded fully. Try:\n"
                            "1. wait value='stable' — let page finish loading\n"
                            "2. scroll down — content may be below the fold\n"
                            "3. read again — content may have loaded by now\n"
                            "4. snapshot — see what's on the page and interact with it\n"
                            f"Do NOT retry read more than twice.{budget_warn}"
                        )
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            messages.append({
                "role": "user",
                "content": (
                    f"[TOOL RESULT: {tool_name}]\n"
                    f"{result}\n\n"
                    f"{obs_suffix}"
                ),
            })

            # Quick check for stop after tool execution
            if self._stop_event.is_set():
                msg = "⏸️  Paused after tool execution. Say \"continue\" to resume."
                print(f"\n{msg}", flush=True)
                messages.append({"role": "assistant", "content": msg})
                return msg

        # ── step ceiling reached ────────────────────────────────
        avg_time = sum(self._step_times[-100:]) / max(len(self._step_times[-100:]), 1)
        ceiling_msg = (
            f"Session complete: {self._step} steps executed (avg {avg_time:.1f}s/step). "
            "Let me know if you'd like me to continue."
        )
        print(f"\n⚠️  {ceiling_msg}", flush=True)
        messages.append({"role": "assistant", "content": ceiling_msg})
        return ceiling_msg


# ═══════════════════════════════════════════════════════════════════
#  6.  SWARM COORDINATOR  (parallel browser task execution)
# ═══════════════════════════════════════════════════════════════════

class SwarmWorker:
    """A deterministic browser worker that executes a pre-planned sequence of actions.

    Workers don't need their own LLM — they follow a plan from the coordinator.
    Each worker operates in its own browser tab. Since the browser server is
    single-threaded, workers interleave their commands with tab-switching.
    """

    def __init__(self, worker_id: int, tab_index: int = -1):
        self.worker_id = worker_id
        self.tab_index = tab_index  # assigned after new_tab opens
        self.status = "idle"  # idle, running, done, error
        self.results: List[Dict] = []
        self.error: Optional[str] = None
        self.plan: List[Dict] = []
        self.current_step = 0


class SwarmCoordinator:
    """Coordinates multiple browser workers for parallel task execution.

    Architecture:
      1. ANALYZE: LLM analyzes the user request and page state
      2. PLAN: LLM decomposes into independent sub-tasks
      3. DISPATCH: Workers execute sub-tasks in parallel (separate tabs)
      4. SYNTHESIZE: LLM reviews all results and produces final answer

    Hardware-aware: Uses 1 LLM + N browser workers (no extra LLM instances).
    The LLM reasons between phases; workers execute deterministically.
    """

    # Rule 5 variants: auto mode restricts interactive tasks, parallel mode forces plans
    _RULE_5_AUTO = (
        "5. SURVEYS/FORMS/INTERACTIVE TASKS: If the task requires filling forms, answering questions, "
        "or completing a multi-step interactive workflow (e.g. surveys, sign-ups, checkout), "
        "output a REGULAR TOOL CALL — NOT a swarm plan. These tasks need step-by-step LLM reasoning.\n"
    )
    _RULE_5_PARALLEL = (
        "5. PARALLEL MODE ACTIVE — You MUST create a swarm_plan with parallel workers for ALL tasks. "
        "NO EXCEPTIONS. For interactive tasks (surveys, forms, sign-ups): create workers that each "
        "open a DIFFERENT instance/page in a parallel tab. Workers handle initial setup (new_tab, dismiss, "
        "wait, snapshot/read). The LLM handles interactive follow-up after synthesis. "
        "NEVER output a regular tool call — ALWAYS output a swarm_plan JSON.\n"
    )

    PLAN_PROMPT_TEMPLATE = (
        "You are the SWARM COORDINATOR. A scout has already executed the first step.\n"
        "The TOOL RESULT above shows ACTUAL results with real data.\n\n"
        "Create a swarm plan to parallelize the remaining work.\n\n"
        "ABSOLUTE RULES — VIOLATION = TASK FAILURE:\n"
        "1. NEVER fabricate, guess, or construct URLs. You MUST copy-paste URLs EXACTLY from CLICKABLE LINKS below.\n"
        "2. If CLICKABLE LINKS has real URLs (-> http...), use MODE A (parallel new_tab workers).\n"
        "3. If CLICKABLE LINKS has NO URLs or only #N refs, use MODE B (sequential click workers on scout tab).\n"
        "4. Max 3 workers. If task CANNOT be parallelized, output a regular tool call instead.\n"
        "{rule_5}"
        "6. For TERMINAL tasks: each worker can run a different command independently.\n"
        "7. Specify 'tool' per step if using non-browser tools (e.g. macos_terminal).\n\n"
        "{links_section}"
        "\n"
        'MODE A — Parallel (when real URLs available):\n'
        '{{"swarm_plan": [\n'
        '  {{"worker_id": 0, "description": "Read: TITLE", "steps": [\n'
        '    {{"action": "new_tab", "args": {{"url": "EXACT_URL_FROM_LINKS"}}, "description": "Open article"}},\n'
        '    {{"action": "dismiss", "args": {{}}}},\n'
        '    {{"action": "wait", "args": {{"value": "stable"}}}},\n'
        '    {{"action": "read", "args": {{}}}}\n'
        '  ]}}\n'
        ']}}\n\n'
        'MODE B — Sequential click (when NO URLs available, only #N refs):\n'
        '{{"swarm_plan": [\n'
        '  {{"worker_id": 0, "description": "Read: TITLE", "mode": "click", "steps": [\n'
        '    {{"action": "click", "args": {{"text": "#REF_NUMBER"}}, "description": "Click article link"}},\n'
        '    {{"action": "wait", "args": {{"value": "stable"}}}},\n'
        '    {{"action": "read", "args": {{}}}},\n'
        '    {{"action": "back", "args": {{}}, "description": "Return to main page"}},\n'
        '    {{"action": "wait", "args": {{"value": "stable"}}}}\n'
        '  ]}}\n'
        ']}}\n\n'
        'Terminal example:\n'
        '{{"swarm_plan": [\n'
        '  {{"worker_id": 0, "description": "Check disk", "steps": [\n'
        '    {{"tool": "macos_terminal", "action": "run", "args": {{"command": "df -h"}}}}\n'
        '  ]}}\n'
        ']}}\n'
    )

    SYNTHESIZE_PROMPT = (
        "You are the SWARM COORDINATOR reviewing results from parallel browser workers.\n"
        "Each worker executed a sub-task independently. Review ALL results below and:\n"
        "1. Synthesize findings into a comprehensive answer\n"
        "2. Note any workers that failed or got incomplete results\n"
        "3. Decide if follow-up actions are needed (output a tool call) or if the task is complete (respond conversationally)\n\n"
        "Worker Results:\n"
    )

    @staticmethod
    def _extract_links(snapshot_text: str) -> List[Dict]:
        """Extract link elements with their refs, text, and URLs from a snapshot."""
        links = []
        for m in re.finditer(
            r'\[(\d+)\]\s+link\s+"([^"]+)"(?:\s+->\s+(\S+))?(?:\s+@(\d+),(\d+))?',
            snapshot_text
        ):
            entry = {"ref": int(m.group(1)), "text": m.group(2)}
            if m.group(3):
                entry["url"] = m.group(3)
            if m.group(4) and m.group(5):
                entry["coords"] = f"@{m.group(4)},{m.group(5)}"
            links.append(entry)
        return links

    @classmethod
    def build_plan_prompt(cls, scout_data: str, force_parallel: bool = False) -> str:
        """Build the plan prompt with extracted clickable links from scout data.

        Args:
            scout_data: The scout tool result (page snapshot)
            force_parallel: If True, replace Rule 5 to force swarm_plan output
                           (used when swarm_mode == "parallel")
        """
        links = cls._extract_links(scout_data)
        if not links:
            links_section = "CLICKABLE LINKS: None found in scout data. Use MODE B (click on elements) or a regular tool call.\n"
        else:
            has_urls = any("url" in l for l in links)
            lines = []
            for l in links[:20]:  # cap at 20 links
                if "url" in l:
                    lines.append(f'  #{l["ref"]} "{l["text"]}" -> {l["url"]}')
                else:
                    lines.append(f'  #{l["ref"]} "{l["text"]}" (no URL — use click #N)')
            header = "CLICKABLE LINKS (extracted from scout page):\n"
            if has_urls:
                header += "  ** URLs below are REAL — copy-paste them exactly into new_tab. DO NOT modify them. **\n"
            else:
                header += "  ** No direct URLs found — use MODE B (click #N on scout tab) **\n"
            links_section = header + "\n".join(lines) + "\n\n"

        # Select Rule 5 based on mode
        rule_5 = cls._RULE_5_PARALLEL if force_parallel else cls._RULE_5_AUTO

        return cls.PLAN_PROMPT_TEMPLATE.format(
            rule_5=rule_5,
            links_section=links_section,
        )

    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers
        self._persistent: Dict[str, subprocess.Popen] = {}
        self._workers: List[SwarmWorker] = []
        self._browser_tool = "edge"
        self._plan_cache: Dict[str, List[Dict]] = {}  # cache successful plans by task hash

    def _detect_browser(self):
        """Detect which browser tool is available."""
        if "edge" in registry:
            self._browser_tool = "edge"
        elif "safari" in registry:
            self._browser_tool = "safari"

    @staticmethod
    def get_safe_workers() -> int:
        """RAM-aware worker count. Queries Metal GPU memory on Apple Silicon."""
        try:
            import mlx.core as mx
            used_gb = mx.metal.get_active_memory() / (1024**3)
            free_gb = 16 - used_gb - 4.0  # reserve 4GB for Edge + macOS
            return max(1, min(3, int(free_gb // 1.8)))
        except Exception:
            return 3  # default if mlx not available

    def _parse_swarm_plan(self, llm_response: str) -> Optional[List[Dict]]:
        """Extract swarm plan JSON from LLM response."""
        # Strip think blocks
        text = re.sub(r"<think>.*?</think>", "", llm_response, flags=re.DOTALL).strip()

        # Find JSON with swarm_plan
        i = 0
        while i < len(text):
            if text[i] == '{':
                depth = 0
                start = i
                for j in range(i, len(text)):
                    if text[j] == '{':
                        depth += 1
                    elif text[j] == '}':
                        depth -= 1
                        if depth == 0:
                            try:
                                data = json.loads(text[start:j+1])
                                if "swarm_plan" in data:
                                    return data["swarm_plan"]
                            except (json.JSONDecodeError, ValueError):
                                pass
                            i = j + 1
                            break
                else:
                    i += 1
            else:
                i += 1
        return None

    def is_swarm_candidate(self, user_msg: str) -> bool:
        """Heuristic: does this task benefit from parallel execution?

        Returns True for multi-site tasks, research queries, comparison tasks, etc.
        """
        msg = user_msg.lower()

        # Multi-site indicators
        multi_site_patterns = [
            r'\band\b.*\band\b',  # "X and Y and Z"
            r'compare\b',
            r'research\b',
            r'multiple\b',
            r'all\s+(?:of|the)',
            r'each\s+(?:of|site|page)',
            r'several\b',
            r'both\b',
            r'\d+\s+(?:sites?|pages?|tabs?|articles?)',
        ]
        for pat in multi_site_patterns:
            if re.search(pat, msg):
                return True

        # Multiple URLs in the message
        url_count = len(re.findall(r'https?://\S+|www\.\S+|\w+\.(?:com|org|net|io)\b', msg))
        if url_count >= 2:
            return True

        return False

    def _exec_step(self, worker: SwarmWorker, step: Dict,
                    persistent: Dict[str, subprocess.Popen]) -> Dict:
        """Execute a single step for a worker, handling tab switching."""
        action = step.get("action", "")
        args = step.get("args", {})
        tool = step.get("tool", self._browser_tool)
        # Build readable description: prefer explicit, fall back to action summary
        desc = step.get("description", "")
        if not desc:
            args_preview = json.dumps(args, ensure_ascii=False)[:60]
            desc = f"{action}({args_preview})" if args else action

        # Build tool args — browser tools use "action" key, terminal tools may not
        if tool in ("edge", "safari"):
            tool_args = {"action": action, **args}
        else:
            # Non-browser tools: pass args directly, add action as mode/command if appropriate
            tool_args = dict(args)
            if action and "action" not in tool_args:
                tool_args["action"] = action

        print(f"  {_DIM}w{worker.worker_id} {desc}{_X}", flush=True)
        result_str = execute_tool(tool, tool_args, persistent)

        try:
            result_data = json.loads(result_str)
        except (json.JSONDecodeError, ValueError):
            result_data = {"raw": result_str[:2000]}

        return {
            "step": worker.current_step + 1,
            "action": action,
            "description": desc,
            "result": result_data,
        }

    @staticmethod
    def _is_404_or_error(result_data: Dict) -> bool:
        """Check if a navigation result indicates a 404, error, or dead-end page."""
        if not isinstance(result_data, dict):
            return False
        title = str(result_data.get("title", "")).lower()
        url = str(result_data.get("url", "")).lower()
        page = str(result_data.get("page", ""))[:500].lower()
        redirect = result_data.get("redirect_warning", "")
        # about:blank / new-tab dead ends (common when back() is called on a popup tab)
        if url in ("about:blank", "edge://newtab/", "edge://start/", "chrome://newtab/"):
            return True
        # 404 indicators
        if any(x in title for x in ("404", "not found", "page not found", "something has gone wrong", "error")):
            return True
        if "err=404" in url or "/404" in url:
            return True
        if any(x in page for x in ("we couldn't find that page", "page not found", "404")):
            return True
        if redirect:
            return True
        return False

    def execute_swarm(self, plan: List[Dict],
                      persistent: Dict[str, subprocess.Popen],
                      scout_url: str = None) -> List[Dict]:
        """Execute swarm plan — supports MODE A (parallel tabs) and MODE B (sequential clicks).

        MODE A (parallel): Workers each open their own tab via new_tab → interleaved execution.
        MODE B (click): Workers share the scout tab, clicking links sequentially (click → read → back).

        A worker's mode is auto-detected from its first step action.

        Args:
            scout_url: The URL of the scout page. Used to recover MODE B after browser restart.
        """
        self._detect_browser()
        self._persistent = persistent
        # RAM-aware worker count
        safe_workers = self.get_safe_workers()
        if safe_workers < self.max_workers:
            print(f"  {_DIM}RAM pressure: limiting to {safe_workers} workers{_X}", flush=True)
        num_workers = min(len(plan), safe_workers)

        # Detect mode: if any worker starts with click, it's MODE B (sequential on scout tab)
        is_click_mode = any(
            wp.get("mode") == "click"
            or (wp.get("steps") and wp["steps"][0].get("action") in ("click", "click_at"))
            for wp in plan[:num_workers]
        )

        mode_label = "sequential click" if is_click_mode else "parallel tabs"
        print(f"\n  {_R}{'─' * 46}{_X}", flush=True)
        print(f"  {_B}{_BR}swarm:{_X} {num_workers} workers ({mode_label})", flush=True)
        print(f"  {_R}{'─' * 46}{_X}", flush=True)

        # Create workers
        workers = []
        for i, worker_plan in enumerate(plan[:num_workers]):
            worker = SwarmWorker(i)
            worker.plan = worker_plan.get("steps", [])
            worker.status = "running"
            workers.append(worker)
            desc = worker_plan.get("description", f"Worker {i}")
            print(f"  {_GR}w{i}:{_X} {desc} {_DIM}({len(worker.plan)} steps){_X}", flush=True)

        print(f"  {_R}{'─' * 46}{_X}", flush=True)
        swarm_start = time.time()

        if is_click_mode:
            # ── MODE B: Sequential click workers on scout tab ──
            # KEY FIX: Track when clicks open new tabs (popups/redirects).
            # Survey sites like ySense open surveys in new tabs. If we detect
            # a new tab opened, replace any subsequent 'back' step with
            # 'tabs close' to return to the scout tab (back() on a popup tab
            # with no history goes to about:blank, breaking all subsequent workers).
            print(f"  {_DIM}mode: sequential click-and-read{_X}", flush=True)
            for worker in workers:
                if worker.status != "running":
                    continue
                desc = plan[worker.worker_id].get("description", f"Worker {worker.worker_id}")
                print(f"\n  {_BR}w{worker.worker_id}{_X} {desc}", flush=True)
                w_start = time.time()
                new_tab_opened = False  # Track if click opened a new tab/popup
                for step in worker.plan:
                    try:
                        action = step.get("action", "")

                        # If click opened a new tab, replace 'back' with tab close
                        if new_tab_opened and action == "back":
                            print(f"  {_DIM}w{worker.worker_id} closing popup tab (click opened new tab){_X}", flush=True)
                            try:
                                execute_tool(self._browser_tool,
                                             {"action": "tabs", "value": "close"}, persistent)
                            except Exception:
                                pass
                            new_tab_opened = False
                            worker.results.append({
                                "step": worker.current_step + 1,
                                "action": "tabs_close",
                                "description": "Closed popup tab → returned to scout page",
                                "result": {"status": "success"},
                            })
                            worker.current_step += 1
                            continue

                        result = self._exec_step(worker, step, persistent)
                        worker.results.append(result)
                        worker.current_step += 1

                        # Detect if click opened a new tab
                        if action in ("click", "click_at"):
                            result_data = result.get("result", {})
                            nav = str(result_data.get("navigation", ""))
                            if "NEW_TAB_OPENED" in nav:
                                new_tab_opened = True

                        # Check for 404/about:blank on navigation actions
                        if action in ("click", "click_at", "new_tab", "browse"):
                            if self._is_404_or_error(result.get("result", {})):
                                print(f"  {_R}w{worker.worker_id} 404/error — skipping{_X}", flush=True)
                                worker.error = "404 or error page"
                                worker.status = "error"
                                # Close popup tab if one was opened, otherwise try back
                                if new_tab_opened:
                                    try:
                                        execute_tool(self._browser_tool,
                                                     {"action": "tabs", "value": "close"}, persistent)
                                    except Exception:
                                        pass
                                else:
                                    try:
                                        execute_tool(self._browser_tool,
                                                     {"action": "back"}, persistent)
                                    except Exception:
                                        pass
                                break
                    except Exception as e:
                        worker.error = f"Step {worker.current_step + 1} failed: {e}"
                        worker.status = "error"
                        print(f"  {_R}w{worker.worker_id} error: {e}{_X}", flush=True)
                        break

                # If worker finished but popup tab still open, close it
                if new_tab_opened:
                    try:
                        execute_tool(self._browser_tool,
                                     {"action": "tabs", "value": "close"}, persistent)
                    except Exception:
                        pass

                if worker.status == "running":
                    worker.status = "done"
                w_elapsed = time.time() - w_start
                status_color = _GR if worker.status == "done" else _R
                print(f"  {status_color}w{worker.worker_id} {worker.status}{_X} {_DIM}({worker.current_step}/{len(worker.plan)} steps, {w_elapsed:.1f}s){_X}", flush=True)

                # ── MODE B RECOVERY: verify scout page is still loaded ──
                # After edge server restart, browser context is lost (page gone).
                # Re-navigate to scout URL before next worker can use #N refs.
                if scout_url:
                    try:
                        check = execute_tool(self._browser_tool,
                                             {"action": "snapshot"}, persistent)
                        check_data = json.loads(check) if check else {}
                        cur_url = check_data.get("url", "")
                        dead_urls = ("about:blank", "edge://newtab/", "edge://start/",
                                     "chrome://newtab/", "")
                        if cur_url in dead_urls or "error" in str(check_data.get("status", "")):
                            print(f"  {_DIM}scout page lost — re-navigating to {scout_url[:60]}{_X}",
                                  flush=True)
                            execute_tool(self._browser_tool,
                                         {"action": "browse", "url": scout_url}, persistent)
                            try:
                                execute_tool(self._browser_tool,
                                             {"action": "dismiss"}, persistent)
                            except Exception:
                                pass
                            try:
                                execute_tool(self._browser_tool,
                                             {"action": "wait", "value": "stable"}, persistent)
                            except Exception:
                                pass
                    except Exception:
                        pass

        else:
            # ── MODE A: Parallel tab workers (per-worker sequential) ──
            # Strategy: Open all tabs first, then run each worker's full plan
            # in its tab before moving to the next worker. This is more reliable
            # than round-robin interleaving because each worker completes fully
            # in its tab context without stale-ref issues from tab switching.
            print(f"  {_DIM}opening tabs...{_X}", flush=True)

            # Phase 1: Open all tabs (fast — assign tab indices from tabs list)
            initial_tab_count = 1
            try:
                tab_result = execute_tool(self._browser_tool, {"action": "tabs", "value": "list"}, persistent)
                tab_data = json.loads(tab_result) if tab_result else {}
                tabs_text = tab_data.get("tabs", "")
                initial_tab_count = len([l for l in tabs_text.split("\n") if l.strip()])
            except Exception:
                pass

            for idx, worker in enumerate(workers):
                if worker.plan and worker.plan[0].get("action") in ("new_tab", "browse"):
                    step = worker.plan[0]
                    try:
                        result = self._exec_step(worker, step, persistent)
                        worker.results.append(result)
                        worker.current_step = 1

                        if self._is_404_or_error(result.get("result", {})):
                            url_tried = step.get("args", {}).get("url", "?")
                            cleaned = url_tried.split('#')[0].split('?')[0].rstrip('/')
                            if cleaned and cleaned != url_tried.rstrip('/'):
                                print(f"  {_DIM}w{worker.worker_id} 404 — retrying cleaned URL{_X}", flush=True)
                                retry = self._exec_step(worker,
                                    {"action": "browse", "args": {"url": cleaned}}, persistent)
                                if not self._is_404_or_error(retry.get("result", {})):
                                    worker.results[-1] = retry
                                    worker.current_step = 1
                                else:
                                    print(f"  {_R}w{worker.worker_id} 404/error on {url_tried[:60]}{_X}", flush=True)
                                    worker.error = f"404/error: {url_tried}"
                                    worker.status = "error"
                                    continue
                            else:
                                print(f"  {_R}w{worker.worker_id} 404/error on {url_tried[:60]}{_X}", flush=True)
                                worker.error = f"404/error: {url_tried}"
                                worker.status = "error"
                                continue

                        try:
                            execute_tool(self._browser_tool, {"action": "dismiss"}, persistent)
                        except Exception:
                            pass

                        worker.tab_index = initial_tab_count + idx + 1
                        print(f"  {_GR}w{worker.worker_id} tab {worker.tab_index} opened{_X}", flush=True)
                    except Exception as e:
                        worker.error = f"Tab open failed: {e}"
                        worker.status = "error"
                        print(f"  {_R}w{worker.worker_id} error: {e}{_X}", flush=True)

            # Phase 2: Execute each worker's remaining steps fully in its tab
            for worker in workers:
                if worker.status != "running":
                    continue
                if worker.current_step >= len(worker.plan):
                    worker.status = "done"
                    continue

                desc = plan[worker.worker_id].get("description", f"Worker {worker.worker_id}")
                print(f"\n  {_BR}w{worker.worker_id}{_X} {desc}", flush=True)
                w_start = time.time()

                # Switch to this worker's tab
                if worker.tab_index > 0:
                    try:
                        execute_tool(self._browser_tool,
                                     {"action": "tabs", "value": f"switch {worker.tab_index}"},
                                     persistent)
                    except Exception:
                        pass

                # Execute all remaining steps for this worker
                while worker.current_step < len(worker.plan):
                    step = worker.plan[worker.current_step]
                    try:
                        result = self._exec_step(worker, step, persistent)
                        worker.results.append(result)
                        worker.current_step += 1

                        if step.get("action") in ("new_tab", "browse", "click"):
                            if self._is_404_or_error(result.get("result", {})):
                                print(f"  {_R}w{worker.worker_id} 404/error{_X}", flush=True)
                                worker.error = "404 or error page"
                                worker.status = "error"
                                break
                    except Exception as e:
                        worker.error = f"Step {worker.current_step + 1} failed: {e}"
                        worker.status = "error"
                        print(f"  {_R}w{worker.worker_id} error: {e}{_X}", flush=True)
                        break

                if worker.status == "running":
                    worker.status = "done"
                w_elapsed = time.time() - w_start
                status_color = _GR if worker.status == "done" else _R
                print(f"  {status_color}w{worker.worker_id} {worker.status}{_X} {_DIM}({worker.current_step}/{len(worker.plan)} steps, {w_elapsed:.1f}s){_X}", flush=True)

        # Mark remaining running workers as done
        for w in workers:
            if w.status == "running":
                w.status = "done"

        # Summary
        total_elapsed = time.time() - swarm_start
        results = []
        print(f"  {_R}{'─' * 46}{_X}", flush=True)
        for worker in workers:
            results.append({
                "worker_id": worker.worker_id,
                "status": worker.status,
                "error": worker.error,
                "results": worker.results,
                "completed_steps": worker.current_step,
                "total_steps": len(worker.plan),
            })
            sc = _GR if worker.status == "done" else _R
            print(f"  {sc}w{worker.worker_id} {worker.status}{_X} {_DIM}({worker.current_step}/{len(worker.plan)} steps){_X}", flush=True)

        failed_count = sum(1 for r in results if r["status"] == "error")
        ok = len(results) - failed_count
        print(f"  {_B}{_BR}swarm:{_X} {ok}/{len(results)} ok {_DIM}({total_elapsed:.1f}s){_X}", flush=True)
        if failed_count:
            print(f"  {_R}{failed_count} failed{_X}", flush=True)
        print(f"  {_R}{'─' * 46}{_X}\n", flush=True)

        return results

    def format_results_for_llm(self, results: List[Dict]) -> str:
        """Format worker results into a concise string for LLM synthesis."""
        parts = []
        for r in results:
            wid = r.get("worker_id", "?")
            status = r.get("status", "unknown")
            header = f"--- Worker {wid} [{status}] ---"

            if r.get("error"):
                parts.append(f"{header}\nError: {r['error']}")
                continue

            step_summaries = []
            for step_result in r.get("results", []):
                desc = step_result.get("description", "")
                action = step_result.get("action", "")
                result_data = step_result.get("result", {})

                # Extract key info from result
                if isinstance(result_data, dict):
                    # For snapshots/reads, extract page content summary
                    page = result_data.get("page", "")
                    content = result_data.get("content", "")
                    message = result_data.get("message", "")
                    url = result_data.get("url", "")
                    title = result_data.get("title", "")

                    info = ""
                    if title:
                        info += f"Title: {title}\n"
                    if url:
                        info += f"URL: {url}\n"
                    if content:
                        info += content[:1500]
                    elif page:
                        info += page[:1500]
                    elif message:
                        info += message[:1500]
                    else:
                        # Include any result keys
                        for k, v in result_data.items():
                            if k not in ("status", "page", "content", "message"):
                                info += f"{k}: {str(v)[:200]}\n"

                    step_summaries.append(f"  [{action}] {desc}\n{info}")
                else:
                    step_summaries.append(f"  [{action}] {desc}\n{str(result_data)[:500]}")

            parts.append(header + "\n" + "\n".join(step_summaries))

        full = "\n\n".join(parts)
        # Truncate to fit in context
        if len(full) > OBSERVATION_CHARS_CONTENT * 2:
            full = full[:OBSERVATION_CHARS_CONTENT * 2] + "\n... [truncated]"
        return full

    def cleanup(self):
        """Clean up worker resources."""
        self._workers.clear()


# ═══════════════════════════════════════════════════════════════════
#  7.  SWARM-ENABLED AGENT LOOP
# ═══════════════════════════════════════════════════════════════════

class SwarmAgentLoop(AgentLoop):
    """Extended AgentLoop with swarm coordination capability.

    When a task is detected as parallelizable, the coordinator:
    1. Asks the LLM to produce a swarm plan
    2. Dispatches parallel workers
    3. Feeds results back to LLM for synthesis
    4. Falls back to standard sequential mode if planning fails

    For non-parallelizable tasks, behaves identically to AgentLoop.
    """

    def __init__(self, max_steps: int = MAX_AGENT_STEPS, max_workers: int = 3,
                 swarm_enabled: bool = True):
        super().__init__(max_steps)
        self.swarm_enabled = swarm_enabled
        self.swarm_mode = "sequential"  # "auto", "parallel", "sequential" — sequential is default
        self.coordinator = SwarmCoordinator(max_workers=max_workers)
        self._swarm_active = False
        # ── Vision Mode ──
        self.vision_enabled = False  # activated by "use vision" or "vision" in user prompt
        # ── Memory & Training ──
        self.memory = None           # MemoryStore instance (set from ai.py)
        self.learnings = None        # Learnings instance (set from ai.py)
        self.training_mode = False   # -train flag active
        self.auto_learn = True       # auto-learning (write after work)
        self._trainer = None         # TrainingRecorder (active during -train)
        self._episode = None         # EpisodeRecorder (always active)
        self._base_system_prompt = None  # stored on first run for bounded injection
        # ── Task continuity ──
        self._pending_ops = ""       # detected pending file operations from user prompt
        self._completion_check_done = False  # one-shot re-prompt gate

    @staticmethod
    def _extract_pending_operations(task: str) -> str:
        """Scan user prompt for file operations that must be completed before task ends."""
        if not task:
            return ""
        ops = []
        lower = task.lower()
        # Detect file write/update requests
        patterns = [
            ("update ", "update file"), ("save ", "save to file"), ("write ", "write to file"),
            ("edit ", "edit file"), ("append ", "append to file"), ("create ", "create file"),
            ("modify ", "modify file"), ("overwrite ", "overwrite file"),
        ]
        for kw, desc in patterns:
            if kw in lower:
                ops.append(desc)
                break  # one match is enough
        # Detect specific file references
        file_exts = (".md", ".txt", ".csv", ".json", ".py", ".swift", ".js", ".html")
        for ext in file_exts:
            if ext in lower:
                # Extract the filename
                fm = re.search(rf'[\w/~.-]+{re.escape(ext)}', task, re.IGNORECASE)
                if fm:
                    ops.append(f"write to {fm.group(0)}")
                    break
        return "; ".join(ops) if ops else ""

    def _validate_plan(self, plan: List[Dict], scout_data: str) -> Optional[List[Dict]]:
        """Fast plan validation — no LLM call, pure heuristic.

        Checks:
        1. Workers have valid steps
        2. MODE A URLs actually exist in scout data (anti-hallucination)
        3. Removes workers with fabricated URLs
        4. Adds dismiss/wait steps to workers missing them
        """
        valid_workers = []
        # Extract all real URLs from scout data
        real_urls = set(re.findall(r'https?://[^\s"\'<>]+', scout_data))

        for wp in plan:
            steps = wp.get("steps", [])
            if not steps:
                continue

            # Check MODE A: if first step is new_tab, validate URL
            first = steps[0]
            if first.get("action") in ("new_tab", "browse"):
                url = first.get("args", {}).get("url", "")
                if url:
                    # Check exact match first, then prefix match (min 50 chars)
                    url_clean = url.rstrip('/')
                    exact_match = any(url_clean == ru.rstrip('/') for ru in real_urls)
                    prefix_match = any(
                        url_clean.startswith(ru.rstrip('/')[:50])
                        for ru in real_urls if len(ru) >= 20
                    )
                    if not exact_match and not prefix_match:
                        # URL not from scout data — check domain as fallback
                        domain_match = re.search(r'://([^/]+)', url)
                        scout_domains = set()
                        for ru in real_urls:
                            dm = re.search(r'://([^/]+)', ru)
                            if dm:
                                scout_domains.add(dm.group(1))
                        if domain_match and domain_match.group(1) not in scout_domains:
                            print(f"  {_DIM}plan: dropping worker with hallucinated URL: {url[:60]}{_X}", flush=True)
                            continue

            # Ensure workers have dismiss after navigation
            has_nav = any(s.get("action") in ("new_tab", "browse") for s in steps)
            has_dismiss = any(s.get("action") == "dismiss" for s in steps)
            if has_nav and not has_dismiss:
                # Insert dismiss after first navigation step
                for i, s in enumerate(steps):
                    if s.get("action") in ("new_tab", "browse"):
                        steps.insert(i + 1, {"action": "dismiss", "args": {}})
                        break

            valid_workers.append(wp)

        if not valid_workers:
            print(f"  {_DIM}plan: all workers invalid — falling back to sequential{_X}", flush=True)
            return None

        return valid_workers

    def run(self, messages: List[Dict], generate_fn: Callable) -> str:
        """Execute with swarm awareness — always on.

        Every task goes through the swarm pipeline:
        1. Scout: LLM makes first tool call, we execute it
        2. Plan: LLM sees real page data, creates worker plan (or opts out)
        3. If swarm plan → parallel execution → synthesis
        4. If no swarm plan → seamless fallback to sequential loop
        """
        # ── Memory: extract task from last user message ──
        user_task = ""
        for m in reversed(messages):
            if m["role"] == "user":
                user_task = m["content"]
                break

        # ── Task continuity: detect pending file operations ──
        self._pending_ops = self._extract_pending_operations(user_task)
        self._completion_check_done = False

        # ── Memory: store base system prompt on first run (for bounded injection) ──
        if self._base_system_prompt is None and messages and messages[0]["role"] == "system":
            self._base_system_prompt = messages[0]["content"]

        # ── Memory: recall + learnings → bounded injection (replaces old append) ──
        if messages and messages[0]["role"] == "system" and self._base_system_prompt:
            injection_parts = []

            # Recall relevant memories
            if self.memory and user_task:
                try:
                    recalled = self.memory.recall(user_task, top_k=3, min_score=0.15)
                    if recalled:
                        injection_parts.append(self.memory.format_recall_for_prompt(recalled))
                        for mem in recalled:
                            if mem.get("type") == "procedural":
                                self.memory.record_procedure_use(mem["id"])
                except Exception:
                    pass

            # Inject learnings (closed-loop: read before work)
            if self.learnings and user_task:
                try:
                    learnings_block = self.learnings.to_prompt(query=user_task)
                    if learnings_block:
                        injection_parts.append(learnings_block)
                except Exception:
                    pass

            # Rebuild system message = base + injections (bounded, not cumulative)
            messages[0]["content"] = self._base_system_prompt
            if injection_parts:
                messages[0]["content"] += "\n\n" + "\n\n".join(injection_parts)

        # ── Memory: start episode recorder ──
        if self.memory and user_task:
            try:
                from memory import EpisodeRecorder
                self._episode = EpisodeRecorder(self.memory, user_task)
            except Exception:
                self._episode = None

        # ── Memory: start training recorder if -train mode ──
        if self.training_mode and self.memory and user_task:
            try:
                from memory import TrainingRecorder
                self._trainer = TrainingRecorder(self.memory, user_task)
            except Exception:
                self._trainer = None

        if not self.swarm_enabled:
            return super().run(messages, generate_fn)

        # Swarm always on — every task enters the swarm pipeline
        return self._run_swarm(messages, generate_fn)

    def _run_swarm(self, messages: List[Dict], generate_fn: Callable) -> str:
        """Execute in swarm mode: scout → plan → dispatch → synthesize.

        Phase 0 SCOUT: LLM makes first tool call. We execute it + auto-dismiss + snapshot.
        Phase 1 PLAN:  LLM receives scout results + extracted CLICKABLE LINKS + plan prompt.
                       Creates worker plan using REAL URLs (MODE A) or click refs (MODE B).
        Phase 2 EXEC:  Workers execute — parallel tabs or sequential clicks.
        Phase 3 SYNTH: LLM synthesizes worker results. May trigger follow-up loop.
        """
        self._stop_event.clear()
        self._running = True
        self._swarm_active = True
        self._step = 0
        self._action_history = []

        # Terminal + signal setup
        self._term_settings = _setup_terminal_for_ctrl_s()
        def _sigint_handler(sig, frame):
            print("\nCtrl+C — pausing swarm...", flush=True)
            self._stop_event.set()
        self._prev_sigint = signal.signal(signal.SIGINT, _sigint_handler)

        self._listener = _StopListener(self._stop_event)
        self._listener.start()

        try:
            # ── Phase 0: SCOUT ────────────────────────────────────
            scout_response = generate_fn(messages)

            scout_tool = parse_tool_call(scout_response)
            if not scout_tool:
                # No tool call — check if this is an empty/thinking response (retry once)
                clean = re.sub(r"<think>.*?</think>", "", scout_response, flags=re.DOTALL).strip()
                if len(clean) < 20:
                    # Likely empty/thinking — re-prompt
                    print("\n(Scout produced no action — retrying...)", flush=True)
                    messages.append({"role": "assistant", "content": scout_response})
                    messages.append({
                        "role": "user",
                        "content": "Please proceed with the task. Call the appropriate tool to begin."
                    })
                    scout_response = generate_fn(messages)
                    scout_tool = parse_tool_call(scout_response)

                if not scout_tool:
                    # Genuinely conversational — return it
                    messages.append({"role": "assistant", "content": scout_response})
                    return scout_response

            tool_name = scout_tool["tool"]
            tool_args = scout_tool["args"]
            action = tool_args.get("action", "") or tool_args.get("mode", "")

            args_preview = json.dumps(tool_args, ensure_ascii=False)
            if len(args_preview) > 140:
                args_preview = args_preview[:140] + "..."
            print(f"\n  {_GR}scout:{_X} {_DIM}{tool_name}({args_preview}){_X}", flush=True)

            scout_start = time.time()
            scout_result = execute_tool(tool_name, tool_args, self._persistent)

            # For browser navigation, auto-dismiss + re-snapshot for clean data
            is_browser = tool_name in ("edge", "safari")
            if is_browser and action in ("new_tab", "newtab", "browse", "open", "goto", "navigate"):
                # v4.2: no delay — speed is priority
                try:
                    execute_tool(tool_name, {"action": "dismiss"}, self._persistent)
                except Exception:
                    pass
                scout_result = execute_tool(tool_name, {"action": "snapshot"}, self._persistent)

            scout_elapsed = time.time() - scout_start

            # Truncate scout data — use larger limit to preserve link URLs
            scout_data = self._smart_truncate(scout_result, 18000)

            # Preview
            preview = scout_data[:200].replace("\n", "  ")
            if len(scout_data) > 200:
                preview += "..."
            print(f"  {_GR}scout result ({scout_elapsed:.1f}s):{_X} {_DIM}{preview}{_X}", flush=True)

            if self._stop_event.is_set():
                msg = "Paused during scout phase."
                messages.append({"role": "assistant", "content": msg})
                return msg

            # ── Phase 1: PLAN ─────────────────────────────────────
            # Build plan prompt with extracted links from scout data
            # force_parallel=True replaces Rule 5 to mandate swarm_plan output
            force_parallel = (self.swarm_mode == "parallel")
            plan_prompt = SwarmCoordinator.build_plan_prompt(scout_data, force_parallel=force_parallel)

            # User mode override: if user forced sequential, skip planning entirely
            if self.swarm_mode == "sequential":
                print(f"\n  {_GR}swarm: sequential mode (user preference){_X}\n", flush=True)
                messages.append({"role": "assistant", "content": scout_response})
                messages.append({
                    "role": "user",
                    "content": (
                        f"[TOOL RESULT: {tool_name}]\n{scout_data}\n\n"
                        "Continue with the task step by step. Execute the next tool call."
                    )
                })
                self._swarm_active = False
                return self._loop(messages, generate_fn)

            messages.append({"role": "assistant", "content": scout_response})
            messages.append({
                "role": "user",
                "content": (
                    f"[TOOL RESULT: {tool_name}]\n{scout_data}\n\n"
                    f"{plan_prompt}"
                )
            })

            print(f"\n  {_GR}planning...{_X} ", end="", flush=True)
            plan_response = generate_fn(messages)

            # Try to parse swarm plan
            plan = self.coordinator._parse_swarm_plan(plan_response)

            # ── Plan validation (fast, no extra LLM call) ──────────
            if plan:
                plan = self._validate_plan(plan, scout_data)

            if not plan:
                print(f"\n  {_GR}swarm: sequential mode (task requires step-by-step execution){_X}\n", flush=True)
                messages.append({"role": "assistant", "content": plan_response})

                tc = parse_tool_call(plan_response)
                if tc:
                    r = execute_tool(tc["tool"], tc["args"], self._persistent)
                    if len(r) > OBSERVATION_CHARS:
                        r = self._smart_truncate(r, OBSERVATION_CHARS)
                    messages.append({
                        "role": "user",
                        "content": f"[TOOL RESULT: {tc['tool']}]\n{r}\n\nContinue with the task."
                    })

                self._swarm_active = False
                return self._loop(messages, generate_fn)

            # ── Phase 2: EXECUTE workers ──────────────────────────
            if self._stop_event.is_set():
                # Save checkpoint for resume
                self._save_checkpoint(plan, scout_data)
                msg = "⏸️  Paused before swarm dispatch. Say 'continue' to resume."
                messages.append({"role": "assistant", "content": msg})
                return msg

            # Extract scout URL for MODE B recovery after server restart
            _scout_url = None
            try:
                _url_match = re.search(r'"url"\s*:\s*"([^"]+)"', scout_data)
                if _url_match:
                    _scout_url = _url_match.group(1)
            except Exception:
                pass
            worker_results = self.coordinator.execute_swarm(plan, self._persistent,
                                                            scout_url=_scout_url)

            # Check if ALL workers failed — if so, fall back to sequential loop
            all_failed = all(r.get("status") == "error" for r in worker_results)
            if all_failed:
                print(f"  {_R}swarm: all workers failed — falling back to sequential{_X}\n", flush=True)
                messages.append({"role": "assistant", "content": plan_response})
                messages.append({
                    "role": "user",
                    "content": (
                        "All swarm workers FAILED (404 errors or redirects). "
                        "The URLs were likely incorrect or fabricated. "
                        "Switch to clicking on articles directly using their #N element refs from the page snapshot. "
                        "Use click action with #N to open each article, read it, then use back to return.\n"
                        "Continue with the task using click-based navigation."
                    )
                })
                self._swarm_active = False
                return self._loop(messages, generate_fn)

            # ── Phase 3: SYNTHESIZE ───────────────────────────────
            results_text = self.coordinator.format_results_for_llm(worker_results)

            # Build pending ops reminder for synthesize
            _synth_pending = ""
            if self._pending_ops:
                _synth_pending = (
                    f"\n\nIMPORTANT — PENDING OPERATIONS: {self._pending_ops}. "
                    "You MUST call a tool to complete these before responding conversationally. "
                    "Do NOT skip file updates."
                )

            messages.append({"role": "assistant", "content": plan_response})
            messages.append({
                "role": "user",
                "content": (
                    f"{SwarmCoordinator.SYNTHESIZE_PROMPT}\n"
                    f"{results_text}\n\n"
                    "Synthesize these results into a complete response for the user. "
                    "If follow-up browser actions or FILE OPERATIONS are needed, call a tool. "
                    "If ALL parts of the task are complete (including file updates), "
                    f"respond conversationally with the findings.{_synth_pending}"
                ),
            })

            print(f"  {_GR}synthesizing...{_X} ", end="", flush=True)
            synth_response = generate_fn(messages)

            tool_call = parse_tool_call(synth_response)
            if tool_call:
                messages.append({"role": "assistant", "content": synth_response})
                print("\nSWARM: Follow-up actions requested. Switching to sequential.\n", flush=True)
                self._swarm_active = False

                tn = tool_call["tool"]
                ta = tool_call["args"]
                # Inject show_nano=False for file writes in synthesis too
                if tn == "nano_editor" and ta.get("operation", "overwrite") != "read":
                    ta.setdefault("show_nano", False)
                r = execute_tool(tn, ta, self._persistent)
                if len(r) > OBSERVATION_CHARS:
                    r = self._smart_truncate(r, OBSERVATION_CHARS)
                messages.append({
                    "role": "user",
                    "content": f"[TOOL RESULT: {tn}]\n{r}\n\nContinue with the task."
                })

                return self._loop(messages, generate_fn)
            else:
                # Check pending ops before allowing exit
                if self._pending_ops and not self._completion_check_done:
                    self._completion_check_done = True
                    messages.append({"role": "assistant", "content": synth_response})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"WAIT — you still need to: {self._pending_ops}. "
                            "Call the tool NOW to complete the file operation."
                        )
                    })
                    self._swarm_active = False
                    return self._loop(messages, generate_fn)
                messages.append({"role": "assistant", "content": synth_response})
                self._finalize_memory("success")
                return synth_response

        finally:
            self._running = False
            self._swarm_active = False
            if self._listener:
                self._listener.deactivate()
                self._listener = None
            _restore_terminal(self._term_settings)
            self._term_settings = None
            if self._prev_sigint is not None:
                try:
                    signal.signal(signal.SIGINT, self._prev_sigint)
                except Exception:
                    pass
                self._prev_sigint = None

    def _save_checkpoint(self, plan: List[Dict], scout_data: str):
        """Save swarm state for resume after pause."""
        try:
            checkpoint = {
                "plan": plan,
                "scout_data": scout_data[:5000],
                "timestamp": time.time(),
                "swarm_mode": self.swarm_mode,
            }
            with open(CHECKPOINT_FILE, "w") as f:
                json.dump(checkpoint, f, indent=1)
            print(f"  {_DIM}checkpoint saved{_X}", flush=True)
        except Exception:
            pass

    @staticmethod
    def _load_checkpoint() -> Optional[Dict]:
        """Load saved swarm checkpoint if exists and is recent (<30 min)."""
        try:
            if not os.path.exists(CHECKPOINT_FILE):
                return None
            with open(CHECKPOINT_FILE, "r") as f:
                checkpoint = json.load(f)
            # Only use checkpoints less than 30 minutes old
            age = time.time() - checkpoint.get("timestamp", 0)
            if age > 1800:
                os.remove(CHECKPOINT_FILE)
                return None
            return checkpoint
        except Exception:
            return None

    @staticmethod
    def _clear_checkpoint():
        """Remove checkpoint file after successful resume."""
        try:
            if os.path.exists(CHECKPOINT_FILE):
                os.remove(CHECKPOINT_FILE)
        except Exception:
            pass

    def cleanup(self):
        """Clean up swarm and persistent resources."""
        self.coordinator.cleanup()
        super().cleanup()


# ═══════════════════════════════════════════════════════════════════
#  8.  PUBLIC API  (backward compatible)
# ═══════════════════════════════════════════════════════════════════

_agent_loop = SwarmAgentLoop()


def get_system_prompt_addendum() -> str:
    """Return the tool-instruction block for the system prompt."""
    return sys_prompt_addendum


def get_agent_loop() -> SwarmAgentLoop:
    """Return the singleton SwarmAgentLoop instance."""
    return _agent_loop


def route_intent(llm_response: str) -> Optional[str]:
    """Legacy single-step routing — still works for backward compat."""
    tc = parse_tool_call(llm_response)
    if tc:
        return execute_tool(tc["tool"], tc["args"], _agent_loop._persistent)
    return None
