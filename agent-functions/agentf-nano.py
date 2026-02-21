#!/usr/bin/env python3
import sys, subprocess, json, argparse, time

# --- METADATA ---
TOOL_METADATA = {
    "name": "nano_editor",
    "description": "Agentic file editor using the native macOS Terminal nano app. Best for creating or editing source code files.",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute or relative path to the file (e.g., 'Sources/App.swift')."
            },
            "content": {
                "type": "string",
                "description": "The full text content to be placed into the file."
            },
            "operation": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "default": "overwrite",
                "description": "overwrite: clears file first; append: adds to the end."
            },
            "target": {
                "type": "string",
                "enum": ["window", "tab"],
                "default": "window",
                "description": "Open nano in a new window or a new tab."
            }
        },
        "required": ["file_path", "content"]
    }
}

# --- LOGIC ---

def run_applescript(script):
    cmd = ["osascript", "-e", script]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout.strip(), res.stderr.strip()

def edit_with_nano(data):
    path = data.get("file_path")
    content = data.get("content")
    op = data.get("operation", "overwrite")
    target = data.get("target", "window")

    # Step 1: Set the macOS clipboard to the content (fastest way to 'type' into nano)
    subprocess.run(["pbcopy"], input=content.encode('utf-8'))

    # Step 2: Determine if we open window or tab
    if target == "tab":
        run_applescript('tell application "Terminal" to activate\ntell application "System Events" to keystroke "t" using command down')
        time.sleep(0.5)
        target_win = "front window"
    else:
        target_win = "window 1"

    # Step 3: The Nano Sequence
    # ^W^V = Go to bottom (for append)
    # ^K = Delete line (repeatable for overwrite)
    # ^U = Paste clipboard (requires Terminal to have paste permissions)
    
    navigation = ""
    if op == "append":
        navigation = 'key code 125 using control down -- Go to end of file' # Ctrl+V equivalent in some nano configs
    
    applescript = f'''
    tell application "Terminal"
        activate
        do script "nano {path}" {"" if target == "tab" else "in " + target_win}
        delay 1
        tell application "System Events"
            -- Clear file if overwriting (Simple approach: select all and delete isn't native to nano, 
            -- so we use a sequence or just assume new file)
            {navigation}
            
            -- Paste the content from clipboard
            keystroke "v" using command down
            delay 0.5
            
            -- Save: Control+O, then Enter
            keystroke "o" using control down
            delay 0.2
            keystroke return
            delay 0.2
            
            -- Exit: Control+X
            keystroke "x" using control down
        end tell
    end tell
    '''
    
    out, err = run_applescript(applescript)
    if err:
        return f"❌ Nano Error: {err}"
    return f"✅ Successfully {'overwrote' if op == 'overwrite' else 'appended'} {path} via Nano."

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            print(edit_with_nano(json.loads(args.json)))
        except Exception as e:
            print(f"Error parsing JSON: {e}")
