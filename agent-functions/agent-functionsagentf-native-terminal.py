#!/usr/bin/env python3
import sys, subprocess, json, argparse, time

# --- METADATA ---
TOOL_METADATA = {
    "name": "macos_terminal",
    "description": "Advanced control for native macOS Terminal. Modes: 'run' (executes command), 'read' (gets output), 'state' (checks if busy/cwd).",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to run (only for 'run' mode)."
            },
            "mode": {
                "type": "string",
                "enum": ["run", "read", "state"],
                "default": "run",
                "description": "run: execute command; read: get terminal text; state: check if busy."
            },
            "target": {
                "type": "string",
                "enum": ["window", "tab", "current"],
                "default": "current"
            },
            "lines": {
                "type": "integer",
                "default": 20,
                "description": "Number of recent lines to read in 'read' mode."
            }
        }
    }
}

# --- LOGIC ---

def run_applescript(script):
    cmd = ["osascript", "-e", script]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout.strip(), res.stderr.strip()

def handle_terminal(data):
    mode = data.get("mode", "run")
    target = data.get("target", "current")
    cmd_text = data.get("command", "")
    line_count = data.get("lines", 20)

    if mode == "run":
        # Logic to handle window/tab creation
        target_logic = 'do script "{}"' if target == "window" else 'do script "{}" in window 1'
        if target == "tab":
            run_applescript('tell application "Terminal" to activate\ntell application "System Events" to keystroke "t" using command down')
            time.sleep(0.5)
        
        script = f'''
        tell application "Terminal"
            activate
            do script "{cmd_text.replace('"', '\\"')}" in front window
        end tell
        '''
        out, err = run_applescript(script)
        return f"Executed: {cmd_text}"

    elif mode == "read":
        # Scrapes the actual text content of the terminal
        script = f'''
        tell application "Terminal"
            tell front window
                get contents of selected tab
            end tell
        end tell
        '''
        out, err = run_applescript(script)
        if out:
            lines = out.split("\n")
            recent = "\n".join(lines[-line_count:])
            return f"--- TERMINAL OUTPUT ---\n{recent}"
        return "No output found or window not open."

    elif mode == "state":
        # Checks if terminal is currently processing (busy)
        script = '''
        tell application "Terminal"
            tell front window
                set is_busy to busy of selected tab
                return is_busy
            end tell
        end tell
        '''
        out, err = run_applescript(script)
        status = "Busy (Running command)" if out == "true" else "Idle (Waiting for input)"
        return f"Terminal Status: {status}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            input_data = json.loads(args.json)
            result = handle_terminal(input_data)
            print(result)
        except Exception as e:
            print(f"Error: {str(e)}")
