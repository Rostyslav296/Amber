#!/usr/bin/env python3
import sys, subprocess, json, argparse

# --- METADATA ---
TOOL_METADATA = {
    "name": "1password",
    "description": "Fetch item details from 1Password.",
    "parameters": {
        "type": "object",
        "properties": {
            "item": {
                "type": "string",
                "description": "The name of the item to look up."
            }
        },
        "required": ["item"]
    }
}

# --- LOGIC ---
def get_item(item_name):
    print(f"🔐 Accessing 1Password for '{item_name}'...")
    try:
        # 'op item get' fetches the details
        cmd = ["op", "item", "get", item_name, "--format", "json"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        if res.returncode != 0:
            print("❌ Error: Item not found or 1Password locked. Run 'op signin'.")
            return

        data = json.loads(res.stdout)
        
        # Extract fields safely
        print(f"--- {data['title']} ---")
        for field in data.get('fields', []):
            if field.get('value'):
                print(f"{field['label']}: {field['value']}")
                
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            get_item(data.get("item"))
        except: pass
