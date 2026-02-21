#!/usr/bin/env python3
import sys, os, json, argparse, datetime

# --- METADATA ---
TOOL_METADATA = {
    "name": "obsidian",
    "description": "Append a thought or task to today's Obsidian Daily Note.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to append."
            }
        },
        "required": ["text"]
    }
}

# --- CONFIG ---
VAULT_PATH = os.environ.get("OBSIDIAN_VAULT_PATH", os.path.expanduser("~/Documents/Obsidian Vault"))

# --- LOGIC ---
def append_daily(text):
    today = datetime.date.today().strftime("%Y-%m-%d")
    # Daily notes are usually in a folder or root, adjust as needed
    note_path = os.path.join(VAULT_PATH, f"{today}.md")
    
    if not os.path.exists(VAULT_PATH):
        print(f"❌ Error: Vault not found at {VAULT_PATH}")
        return

    print(f"📓 Appending to {today}.md...")
    
    try:
        timestamp = datetime.datetime.now().strftime("%H:%M")
        entry = f"\n- [{timestamp}] {text}"
        
        with open(note_path, "a", encoding="utf-8") as f:
            f.write(entry)
        print("✅ Saved to Obsidian.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            append_daily(data.get("text"))
        except: pass
