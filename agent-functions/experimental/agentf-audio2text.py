#!/usr/bin/env python3
import sys, subprocess, json, argparse, os

# --- METADATA ---
TOOL_METADATA = {
    "name": "transcribe",
    "description": "Transcribe an audio file to text (using Local Whisper).",
    "parameters": {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Path to the .mp3 or .wav file."
            }
        },
        "required": ["filepath"]
    }
}

# --- LOGIC ---
def transcribe(filepath):
    path = os.path.expanduser(filepath)
    if not os.path.exists(path):
        print(f"❌ Error: File '{path}' not found.")
        return

    print(f"🎙️ Transcribing '{path}' (this may take a moment)...")
    
    try:
        # Check if we have the mlx-whisper CLI
        # Install via: pip install mlx-whisper
        cmd = ["mlx_whisper", path, "--model", "tiny"]
        
        # If mlx isn't found, you could fallback to standard 'whisper'
        # cmd = ["whisper", path, "--model", "base"]
        
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        if res.returncode == 0:
            print("\n📝 Transcription Result:\n")
            print(res.stdout)
        else:
            print("❌ Error: Ensure 'mlx-whisper' is installed (pip install mlx-whisper).")
            print(f"Details: {res.stderr}")
            
    except FileNotFoundError:
        print("❌ Error: 'mlx_whisper' command not found. Run: pip install mlx-whisper")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            transcribe(data.get("filepath"))
        except: pass
