#!/usr/bin/env python3
import sys, subprocess, json, argparse

# --- METADATA ---
TOOL_METADATA = {
    "name": "imessage",
    "description": "Send an iMessage or SMS to a phone number or contact.",
    "parameters": {
        "type": "object",
        "properties": {
            "contact": {
                "type": "string",
                "description": "Phone number or Contact Name."
            },
            "message": {
                "type": "string",
                "description": "The message to send."
            }
        },
        "required": ["contact", "message"]
    }
}

# --- LOGIC ---
def send_message(contact, msg):
    # This AppleScript finds the buddy and sends the text
    script = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{contact}" of targetService
        send "{msg}" to targetBuddy
    end tell
    '''
    cmd = ["osascript", "-e", script]
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    if res.returncode == 0:
        print(f"✅ Message sent to {contact}.")
    else:
        print(f"❌ Failed to send. Ensure contact '{contact}' exists and is reachable via iMessage.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            send_message(data.get("contact"), data.get("message"))
        except: pass
