#!/usr/bin/env python3
import sys, subprocess, json, argparse, os

# --- METADATA ---
TOOL_METADATA = {
    "name": "notion",
    "description": "Adds a To-Do item to your Notion page.",
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The content of the to-do item."
            }
        },
        "required": ["task"]
    }
}

# --- CONFIG ---
API_KEY = os.environ.get("NOTION_KEY", "")
PAGE_ID = os.environ.get("NOTION_PAGE_ID", "")

# --- LOGIC ---
def add_todo(task):
    if not API_KEY or not PAGE_ID:
        print("❌ Error: NOTION_KEY or NOTION_PAGE_ID missing.")
        return

    print(f"📝 Adding to Notion: '{task}'...")
    
    # Notion API Block Structure
    payload = {
        "children": [
            {
                "object": "block",
                "type": "to_do",
                "to_do": {
                    "rich_text": [{"type": "text", "text": {"content": task}}]
                }
            }
        ]
    }
    
    cmd = [
        "curl", "-s", "-X", "PATCH",
        f"https://api.notion.com/v1/blocks/{PAGE_ID}/children",
        "-H", f"Authorization: Bearer {API_KEY}",
        "-H", "Content-Type: application/json",
        "-H", "Notion-Version: 2022-06-28",
        "-d", json.dumps(payload)
    ]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if '"object":"list"' in res.stdout:
            print("✅ Successfully added to Notion.")
        else:
            print(f"❌ Notion API Error: {res.stdout[:100]}")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            add_todo(data.get("task"))
        except: pass
