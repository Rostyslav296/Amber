#!/usr/bin/env python3
import sys, subprocess, json, argparse, urllib.parse

# --- METADATA ---
TOOL_METADATA = {
    "name": "github",
    "description": "Inspects public GitHub repositories. Can list files or read a specific file.",
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "The repository (e.g. 'openai/whisper')."
            },
            "action": {
                "type": "string",
                "enum": ["list", "read"],
                "description": "List files or read a file content."
            },
            "path": {
                "type": "string",
                "description": "File path to read (only for 'read' action)."
            }
        },
        "required": ["repo", "action"]
    }
}

# --- LOGIC ---
def list_files(repo):
    # Uses the GitHub API (public access) to get the file tree
    url = f"https://api.github.com/repos/{repo}/contents"
    print(f"🐙 Listing files in '{repo}'...")
    
    try:
        cmd = ["curl", "-s", "-L", "-H", "User-Agent: AmberAI", url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(res.stdout)
        
        if isinstance(data, list):
            for item in data[:15]: # Limit to 15 items
                icon = "📁" if item['type'] == 'dir' else "📄"
                print(f"{icon} {item['name']}")
        elif "message" in data:
            print(f"❌ GitHub Error: {data['message']}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

def read_file(repo, path):
    # Fetches the raw file content
    url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
    # Fallback to master if main fails? Let's just try URL
    print(f"📄 Fetching '{path}' from '{repo}'...")
    
    try:
        cmd = ["curl", "-s", "-L", url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        if "404: Not Found" in res.stdout:
            # Try 'master' branch fallback
            url = f"https://raw.githubusercontent.com/{repo}/master/{path}"
            cmd = ["curl", "-s", "-L", url]
            res = subprocess.run(cmd, capture_output=True, text=True)

        print(res.stdout[:2000]) # Truncate to save context
        if len(res.stdout) > 2000:
            print("\n... (truncated)")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            repo = data.get("repo")
            action = data.get("action")
            path = data.get("path", "")
            
            if action == "list": list_files(repo)
            elif action == "read": read_file(repo, path)
        except: pass
