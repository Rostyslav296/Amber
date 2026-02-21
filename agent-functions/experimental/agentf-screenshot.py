#!/usr/bin/env python3
import sys, subprocess, json, argparse, os, datetime

# --- METADATA ---
TOOL_METADATA = {
    "name": "screenshot",
    "description": "Takes a screenshot of the main screen and saves it to Desktop.",
    "parameters": {
        "type": "object",
        "properties": {
            "delay": {
                "type": "integer",
                "description": "Seconds to wait before snapping (default 0)."
            }
        },
        "required": []
    }
}

# --- LOGIC ---
def take_screenshot(delay=0):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.expanduser(f"~/Desktop/Amber_Shot_{timestamp}.png")
    
    if delay > 0:
        print(f"📸 Waiting {delay}s...")
        subprocess.run(["sleep", str(delay)])

    print("📸 Snapping...")
    # 'screencapture' is a native macOS CLI tool
    cmd = ["screencapture", "-x", path]
    
    try:
        subprocess.run(cmd)
        print(f"✅ Screenshot saved: {path}")
        # On macOS, we can even open it to preview
        subprocess.run(["open", path])
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    delay = 0
    if args.json:
        try:
            data = json.loads(args.json)
            delay = data.get("delay", 0)
        except: pass
        
    take_screenshot(delay)
