#!/usr/bin/env python3
import sys, subprocess, json, argparse, urllib.parse

# --- METADATA ---
TOOL_METADATA = {
    "name": "bear",
    "description": "Create a note in Bear App.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Note title."
            },
            "text": {
                "type": "string",
                "description": "Note content (Markdown supported)."
            },
            "tags": {
                "type": "string",
                "description": "Comma-separated tags (e.g. 'work,todo')."
            }
        },
        "required": ["text"]
    }
}

# --- LOGIC ---
def create_bear_note(text, title=None, tags=None):
    # Construct X-Callback-URL
    base_url = "bear://x-callback-url/create"
    params = {"text": text}
    if title: params["title"] = title
    if tags: params["tags"] = tags
    
    final_url = f"{base_url}?{urllib.parse.urlencode(params)}"
    
    print(f"🐻 Creating Bear Note...")
    try:
        # 'open' command handles custom URL schemes on macOS
        subprocess.run(["open", final_url])
        print("✅ Sent to Bear.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            create_bear_note(
                data.get("text"),
                data.get("title"),
                data.get("tags")
            )
        except: pass
