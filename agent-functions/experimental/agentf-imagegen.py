#!/usr/bin/env python3
import sys, subprocess, json, argparse, os

# --- METADATA ---
TOOL_METADATA = {
    "name": "image_gen",
    "description": "Generate an image using AI (DALL-E 3).",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Description of the image."
            }
        },
        "required": ["prompt"]
    }
}

# --- CONFIG ---
API_KEY = os.environ.get("OPENAI_API_KEY", "")

# --- LOGIC ---
def generate_image(prompt):
    if not API_KEY:
        print("❌ Error: OPENAI_API_KEY not set.")
        return

    print(f"🎨 Generating: '{prompt}'...")
    
    payload = {
        "model": "dall-e-3",
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024"
    }
    
    cmd = [
        "curl", "-s", "https://api.openai.com/v1/images/generations",
        "-H", "Content-Type: application/json",
        "-H", f"Authorization: Bearer {API_KEY}",
        "-d", json.dumps(payload)
    ]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(res.stdout)
        
        if "data" in data:
            url = data["data"][0]["url"]
            print(f"✅ Image ready: {url}")
            # Auto-open in browser
            subprocess.run(["open", url])
        else:
            print(f"❌ API Error: {data}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            generate_image(data.get("prompt"))
        except: pass
