#!/usr/bin/env python3
import sys, subprocess, json, argparse

# --- METADATA ---
TOOL_METADATA = {
    "name": "email",
    "description": "Manages email via Himalaya CLI. Can 'list' inbox, 'read' a specific email, or 'send' a new email.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "send"],
                "description": "Action to perform."
            },
            "id": {
                "type": "string",
                "description": "Email ID (required for 'read')."
            },
            "to": {
                "type": "string",
                "description": "Recipient email (required for 'send')."
            },
            "subject": {
                "type": "string",
                "description": "Subject line (required for 'send')."
            },
            "body": {
                "type": "string",
                "description": "Email body (required for 'send')."
            }
        },
        "required": ["action"]
    }
}

# --- LOGIC ---
def list_emails():
    print("📧 Fetching Inbox...")
    try:
        # Defaults to outputting JSON for easy parsing
        cmd = ["himalaya", "message", "list", "--output", "json", "--page", "0", "--page-size", "5"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        if res.returncode != 0:
            print("❌ Himalaya Error. Is it configured? Run 'himalaya' in terminal.")
            return

        emails = json.loads(res.stdout)
        for e in emails:
            print(f"ID: {e['id']} | From: {e['sender']} | Sub: {e['subject']}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

def read_email(msg_id):
    print(f"📧 Reading Email {msg_id}...")
    cmd = ["himalaya", "message", "read", msg_id]
    subprocess.run(cmd)

def send_email(to_addr, subject, body):
    print(f"📧 Sending to {to_addr}...")
    # Himalaya accepts the body via stdin
    msg = f"Subject: {subject}\n\n{body}"
    cmd = ["himalaya", "message", "send", "--receiver", to_addr, "--subject", subject]
    
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate(input=body)
        
        if proc.returncode == 0:
            print(f"✅ Email sent to {to_addr}")
        else:
            print(f"❌ Failed: {err}")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            action = data.get("action")
            
            if action == "list": list_emails()
            elif action == "read": read_email(data.get("id"))
            elif action == "send": send_email(data.get("to"), data.get("subject"), data.get("body"))
        except: pass
