#!/usr/bin/env python3
import sys, subprocess, json, argparse, re, urllib.parse

# --- METADATA ---
TOOL_METADATA = {
    "name": "browser",
    "description": "Search the web or read a specific URL.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "read"],
                "description": "Search Google/DDG or read a URL."
            },
            "query": {
                "type": "string",
                "description": "Search query or URL."
            }
        },
        "required": ["action", "query"]
    }
}

# --- LOGIC ---
def clean_html(html):
    # Remove scripts and styles
    text = re.sub(r'<script.*?>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.DOTALL)
    # Remove tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:2000]

def search(query):
    print(f"🔍 Searching for '{query}'...")
    # Use DuckDuckGo HTML version
    q = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={q}"
    
    try:
        cmd = ["curl", "-s", "-L", "-A", "Mozilla/5.0", url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(clean_html(res.stdout))
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            if data.get("action") == "search": search(data.get("query"))
            # Add read logic here if desired
        except: pass
