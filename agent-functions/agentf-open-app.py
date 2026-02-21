#!/usr/bin/env python3
import sys, subprocess, json, argparse

# --- METADATA ---
TOOL_METADATA = {
    "name": "openapp",
    "description": "Launches native macOS applications.",
    "parameters": {
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "The name of the application (e.g. 'Notes', 'Discord')"
            }
        },
        "required": ["app_name"]
    }
}

# --- LOGIC ---
def launch(app_name):
    # Try 1: Exact Match
    cmd = ["open", "-a", app_name]
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    if res.returncode == 0:
        print(f"Launched {app_name}")
        return

    # Try 2: Add .app extension
    if not app_name.endswith(".app"):
        cmd = ["open", "-a", app_name + ".app"]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0:
            print(f"Launched {app_name}.app")
            return

    print(f"Failed to launch {app_name}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    parser.add_argument("raw", nargs="?", help="Legacy arg")
    args = parser.parse_args()

    name = None
    if args.json:
        try:
            name = json.loads(args.json).get("app_name")
        except: pass
    elif args.raw:
        name = args.raw

    if name:
        launch(name)
