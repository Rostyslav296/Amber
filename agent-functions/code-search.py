#!/usr/bin/env python3
"""
code-search.py — Headless code example fetcher.

Searches GitHub, StackOverflow, and generic websites for code examples
WITHOUT launching a browser. Uses urllib/requests for fast HTTP requests.

Modes:
  github_search  — search GitHub repos/code via API
  github_file    — fetch raw file from GitHub
  stackoverflow  — search Stack Overflow for answers
  web_fetch      — fetch and extract text from any URL
  snippet        — search multiple sources, return best code snippets
"""

import sys, json, argparse, os, re, html
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote_plus
from urllib.error import URLError, HTTPError

TOOL_METADATA = {
    "name": "code_search",
    "description": (
        "Headless code example fetcher — NO browser needed. "
        "Searches GitHub, StackOverflow, and web for code examples. "
        "Modes: github_search (search repos/code), github_file (fetch raw file), "
        "stackoverflow (search Q&A), web_fetch (get page text), snippet (multi-source search). "
        "Use when struggling with coding — fetch examples fast. "
        "Example: {\"mode\":\"snippet\",\"query\":\"SpriteKit endless runner swift\"} "
        "GitHub: {\"mode\":\"github_search\",\"query\":\"tuist Project.swift iOS\",\"search_type\":\"code\"}"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["github_search", "github_file", "stackoverflow", "web_fetch", "snippet"],
                "default": "snippet",
                "description": "Search mode."
            },
            "query": {
                "type": "string",
                "description": "Search query (e.g., 'SpriteKit game loop swift', 'tuist Project.swift example')."
            },
            "url": {
                "type": "string",
                "description": "URL to fetch (for github_file and web_fetch modes)."
            },
            "search_type": {
                "type": "string",
                "enum": ["repositories", "code"],
                "default": "code",
                "description": "GitHub search type."
            },
            "language": {
                "type": "string",
                "default": "swift",
                "description": "Programming language filter."
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "description": "Max results to return."
            }
        },
        "required": ["query"]
    }
}

HEADERS = {
    'User-Agent': 'AmberAgent/1.0 (code-search)',
    'Accept': 'application/json',
}

TIMEOUT = 15

def _fetch(url, headers=None):
    """Fetch URL and return text content."""
    hdrs = dict(HEADERS)
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            data = resp.read()
            try:
                return data.decode('utf-8')
            except UnicodeDecodeError:
                return data.decode('latin-1')
    except HTTPError as e:
        return f"HTTP {e.code}: {e.reason}"
    except URLError as e:
        return f"URL Error: {e.reason}"
    except Exception as e:
        return f"Error: {e}"


def _strip_html(text):
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════
#  GITHUB SEARCH
# ═══════════════════════════════════════════════════════════════

def github_search(query, search_type='code', language='swift', max_results=5):
    """Search GitHub via REST API (no auth needed for basic searches)."""
    params = {'q': query}
    if language and search_type == 'code':
        params['q'] += f' language:{language}'
    params['per_page'] = min(max_results, 10)

    url = f"https://api.github.com/search/{search_type}?{urlencode(params)}"
    headers = {'Accept': 'application/vnd.github.v3+json'}

    raw = _fetch(url, headers)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return f"GitHub API error: {raw[:200]}"

    if 'message' in data and 'items' not in data:
        return f"GitHub API: {data['message']}"

    items = data.get('items', [])
    if not items:
        return f"No GitHub results for: {query}"

    results = []
    for item in items[:max_results]:
        if search_type == 'code':
            repo = item.get('repository', {}).get('full_name', '?')
            path = item.get('path', '?')
            html_url = item.get('html_url', '')
            # Get raw content URL
            sha = item.get('sha', '')
            raw_url = item.get('git_url', '')
            results.append(f"[{repo}] {path}\n  URL: {html_url}")
        else:
            name = item.get('full_name', item.get('name', '?'))
            desc = item.get('description', '')[:100]
            stars = item.get('stargazers_count', 0)
            url = item.get('html_url', '')
            results.append(f"[{name}] ★{stars} — {desc}\n  URL: {url}")

    return f"GitHub {search_type} results ({len(results)}):\n\n" + "\n\n".join(results)


def github_file(url):
    """Fetch raw file content from GitHub."""
    # Convert github.com URL to raw URL
    raw_url = url
    if 'github.com' in url and '/blob/' in url:
        raw_url = url.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
    elif 'github.com' in url and '/tree/' in url:
        raw_url = url.replace('github.com', 'raw.githubusercontent.com').replace('/tree/', '/')

    content = _fetch(raw_url)
    if len(content) > 10000:
        content = content[:10000] + f"\n\n... [truncated, {len(content)} chars total]"
    return f"File content ({raw_url}):\n\n{content}"


# ═══════════════════════════════════════════════════════════════
#  STACKOVERFLOW SEARCH
# ═══════════════════════════════════════════════════════════════

def stackoverflow_search(query, max_results=5):
    """Search Stack Overflow via API."""
    params = {
        'order': 'desc',
        'sort': 'relevance',
        'intitle': query,
        'site': 'stackoverflow',
        'filter': '!6WPIomnMOOD*e',  # includes answer body
        'pagesize': min(max_results, 5),
    }
    url = f"https://api.stackexchange.com/2.3/search/advanced?{urlencode(params)}"

    raw = _fetch(url)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return f"SO API error: {raw[:200]}"

    items = data.get('items', [])
    if not items:
        return f"No StackOverflow results for: {query}"

    results = []
    for item in items[:max_results]:
        title = _strip_html(item.get('title', ''))
        answered = item.get('is_answered', False)
        score = item.get('score', 0)
        link = item.get('link', '')
        tags = ', '.join(item.get('tags', [])[:5])
        status = "✓ Answered" if answered else "○ Unanswered"
        results.append(f"[{status}, score:{score}] {title}\n  Tags: {tags}\n  URL: {link}")

    return f"StackOverflow results ({len(results)}):\n\n" + "\n\n".join(results)


# ═══════════════════════════════════════════════════════════════
#  GENERIC WEB FETCH
# ═══════════════════════════════════════════════════════════════

def web_fetch(url, max_chars=8000):
    """Fetch URL and extract readable text."""
    content = _fetch(url, {'Accept': 'text/html,text/plain,*/*'})

    # Try to extract main content
    # Remove scripts and styles
    content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
    content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)

    # Extract code blocks first
    code_blocks = re.findall(r'<code[^>]*>(.*?)</code>', content, re.DOTALL | re.IGNORECASE)
    code_blocks += re.findall(r'<pre[^>]*>(.*?)</pre>', content, re.DOTALL | re.IGNORECASE)

    # Strip remaining HTML
    text = _strip_html(content)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)

    if len(text) > max_chars:
        text = text[:max_chars] + "... [truncated]"

    result = f"Page text ({url}):\n{text}"

    if code_blocks:
        code_text = "\n\n---CODE BLOCKS---\n"
        for i, block in enumerate(code_blocks[:5]):
            clean = _strip_html(block).strip()
            if clean:
                code_text += f"\n[Block {i+1}]:\n{clean[:2000]}\n"
        result += code_text

    return result


# ═══════════════════════════════════════════════════════════════
#  MULTI-SOURCE SNIPPET SEARCH
# ═══════════════════════════════════════════════════════════════

def snippet_search(query, language='swift', max_results=5):
    """Search multiple sources for code examples."""
    results = []

    # 1. GitHub code search
    try:
        gh = github_search(query, 'code', language, max_results=3)
        results.append("=== GITHUB CODE ===\n" + gh)
    except Exception as e:
        results.append(f"=== GITHUB CODE ===\nError: {e}")

    # 2. GitHub repo search
    try:
        repos = github_search(query + f" language:{language}", 'repositories', language, max_results=3)
        results.append("\n=== GITHUB REPOS ===\n" + repos)
    except Exception as e:
        results.append(f"\n=== GITHUB REPOS ===\nError: {e}")

    # 3. StackOverflow
    try:
        so = stackoverflow_search(f"{query} {language}", max_results=3)
        results.append("\n=== STACKOVERFLOW ===\n" + so)
    except Exception as e:
        results.append(f"\n=== STACKOVERFLOW ===\nError: {e}")

    return "\n".join(results)


# ═══════════════════════════════════════════════════════════════
#  MAIN HANDLER
# ═══════════════════════════════════════════════════════════════

def handle_code_search(data):
    mode = data.get('mode', 'snippet')
    query = data.get('query', '')
    url = data.get('url', '')
    language = data.get('language', 'swift')
    max_results = data.get('max_results', 5)
    search_type = data.get('search_type', 'code')

    if mode == 'github_search':
        if not query:
            return "Error: query required for github_search"
        return github_search(query, search_type, language, max_results)

    elif mode == 'github_file':
        if not url:
            return "Error: url required for github_file"
        return github_file(url)

    elif mode == 'stackoverflow':
        if not query:
            return "Error: query required for stackoverflow"
        return stackoverflow_search(query, max_results)

    elif mode == 'web_fetch':
        if not url:
            return "Error: url required for web_fetch"
        return web_fetch(url)

    elif mode == 'snippet':
        if not query:
            return "Error: query required for snippet"
        return snippet_search(query, language, max_results)

    return f"Unknown mode: {mode}"


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', help='JSON args')
    args = parser.parse_args()
    if args.json:
        try:
            print(handle_code_search(json.loads(args.json)))
        except Exception as e:
            print(f"Error: {e}")
