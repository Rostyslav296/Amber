#!/usr/bin/env python3
import sys, subprocess, json, argparse, os

# --- METADATA ---
TOOL_METADATA = {
    "name": "discord",
    "description": "Sends a message to a Discord channel.",
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The message to post."
            }
        },
        "required": ["message"]
    }
}

# --- CONFIG ---
# Replace this or set env var: export DISCORD_WEBHOOK_URL="https://..."
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# --- LOGIC ---
def send_discord(msg):
    if not WEBHOOK_URL:
        print("❌ Error: DISCORD_WEBHOOK_URL not set in agentf-discord.py or environment.")
        return

    payload = json.dumps({"content": msg})
    print("👾 Sending to Discord...")
    
    cmd = [
        "curl", "-s", "-H", "Content-Type: application/json",
        "-d", payload,
        WEBHOOK_URL
    ]
    
    try:
        subprocess.run(cmd, check=True)
        print("✅ Sent.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            send_discord(data.get("message"))
        except: pass
