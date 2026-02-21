#!/usr/bin/env python3
import sys, subprocess, json, argparse, os

# --- METADATA ---
TOOL_METADATA = {
    "name": "slack",
    "description": "Sends a message to Slack.",
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The text to send."
            }
        },
        "required": ["message"]
    }
}

# --- CONFIG ---
WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# --- LOGIC ---
def send_slack(msg):
    if not WEBHOOK_URL:
        print("❌ Error: SLACK_WEBHOOK_URL not set.")
        return

    print("📢 Sending to Slack...")
    payload = json.dumps({"text": msg})
    
    cmd = [
        "curl", "-s", "-X", "POST", "-H", "Content-type: application/json",
        "--data", payload, WEBHOOK_URL
    ]
    
    subprocess.run(cmd)
    print("✅ Sent.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            send_slack(data.get("message"))
        except: pass
