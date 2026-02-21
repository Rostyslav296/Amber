#!/usr/bin/env python3
import sys, subprocess, json, argparse

# --- METADATA ---
TOOL_METADATA = {
    "name": "speak",
    "description": "Speaks text out loud using system TTS.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to speak."
            },
            "voice": {
                "type": "string",
                "description": "Optional voice (e.g. 'Samantha', 'Fred')."
            }
        },
        "required": ["text"]
    }
}

# --- LOGIC ---
def speak(text, voice=None):
    print(f"🗣️ Speaking: \"{text}\"")
    cmd = ["say", text]
    if voice:
        cmd += ["-v", voice]
        
    try:
        subprocess.run(cmd)
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            speak(data.get("text"), data.get("voice"))
        except: pass
