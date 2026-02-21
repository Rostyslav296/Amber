#!/usr/bin/env python3
import sys, subprocess, json, argparse, os

# --- METADATA ---
TOOL_METADATA = {
    "name": "filefind",
    "description": "Searches for files on the local computer by name.",
    "parameters": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "The name or partial name of the file."
            }
        },
        "required": ["filename"]
    }
}

# --- LOGIC ---
def find_file(query):
    print(f"Searching for '{query}'...")
    cmd = ["mdfind", "-name", query]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        files = [f for f in res.stdout.split('\n') if f]
        
        if not files:
            print("No files found.")
            return

        # Return top 5
        for f in files[:5]:
            print(f"📄 {f}")
        if len(files) > 5:
            print(f"...and {len(files)-5} more.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    parser.add_argument("raw", nargs="?", help="Legacy arg")
    args = parser.parse_args()

    query = None
    if args.json:
        try:
            query = json.loads(args.json).get("filename")
        except: pass
    elif args.raw:
        query = args.raw

    if query:
        find_file(query)
