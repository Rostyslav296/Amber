#!/usr/bin/env python3
import sys, subprocess, json, argparse, os, datetime

# --- METADATA ---
TOOL_METADATA = {
    "name": "camera",
    "description": "Takes a picture using the webcam.",
    "parameters": {
        "type": "object",
        "properties": {
            "delay": {
                "type": "integer",
                "description": "Seconds to wait before snapping (default 1)."
            }
        },
        "required": []
    }
}

# --- LOGIC ---
def take_photo(delay=1):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"Selfie_{timestamp}.jpg"
    path = os.path.expanduser(f"~/Desktop/{filename}")
    
    print(f"📸 Smiling! Taking photo in {delay}s...")
    
    # 'imagesnap' is the standard CLI for macOS cameras
    cmd = ["imagesnap", "-w", str(delay), path]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if "Capturing" in res.stdout or os.path.exists(path):
            print(f"✅ Photo saved to Desktop: {filename}")
            subprocess.run(["open", path])
        else:
            print("❌ Error: 'imagesnap' not found. Run: brew install imagesnap")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            take_photo(data.get("delay", 1))
        except: pass
