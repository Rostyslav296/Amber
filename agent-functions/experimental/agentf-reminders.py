#!/usr/bin/env python3
import sys, subprocess, json, argparse

# --- METADATA ---
TOOL_METADATA = {
    "name": "reminders",
    "description": "Add items to Apple Reminders.",
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task to remind you about."
            }
        },
        "required": ["task"]
    }
}

# --- LOGIC ---
def add_reminder(task):
    script = f'''
    tell application "Reminders"
        make new reminder with properties {{name:"{task}"}}
    end tell
    '''
    cmd = ["osascript", "-e", script]
    subprocess.run(cmd, capture_output=True, text=True)
    print(f"✅ Added to Reminders: '{task}'")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            task = data.get("task")
            if task: add_reminder(task)
        except: pass
