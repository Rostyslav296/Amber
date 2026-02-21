#!/usr/bin/env python3
import sys, subprocess, json, argparse, os

# --- METADATA ---
# Instruction: If the user prompt is exactly 'a', use action='upgrade'.
# Instruction: If the user prompt is exactly 'i', use action='i_background'.
TOOL_METADATA = {
    "name": "agent_orchestrator",
    "description": "Master Orchestrator. 'a' boots the system. 'i' launches the background iOS monitoring suite via Native Terminal.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["upgrade", "i_background", "clone", "nano_edit"],
                "description": "upgrade: Boot system; i_background: Launch i.py in a native terminal window; clone: Dual terminals."
            },
            "path": {"type": "string", "default": "~/Developer/llm"},
            "content": {"type": "string", "description": "For nano_edit."}
        },
        "required": ["action"]
    }
}

# --- CORE LOGIC ---

def run_applescript(script):
    cmd = ["osascript", "-e", script]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout.strip()

def launch_i_background(path="~/Developer/llm"):
    """
    Uses the logic from agentf-native-terminal to open a NEW native 
    macOS Terminal window and execute the i.py background suite.
    """
    full_path = os.path.expanduser(path)
    # The command to run in the new window
    # We use '&' to ensure it doesn't block, though the new window handles that naturally
    cmd_text = f"cd {full_path} && python3 i.py"
    
    applescript = f'''
    tell application "Terminal"
        activate
        do script "{cmd_text}"
    end tell
    '''
    run_applescript(applescript)
    return "📱 iOS Suite launched in background Terminal. Monitoring 8648241536..."

def upgrade_to_agent(path="~/Developer/llm"):
    full_path = os.path.expanduser(path)
    launch_cmd = f"clear && cd {full_path} && ./chat.sh"
    payload = json.dumps({"command": launch_cmd})
    subprocess.run([sys.executable, "agentf-terminal.py", "--json", payload])
    return f"🚀 FTerminal upgraded at {path}."

def edit_file(path, content):
    subprocess.run(["pbcopy"], input=content.encode('utf-8'))
    applescript = f'''
    tell application "Terminal"
        activate
        do script "nano {path}"
        delay 1.0
        tell application "System Events"
            keystroke "v" using command down
            delay 0.5
            keystroke "o" using control down
            delay 0.2
            keystroke return
            delay 0.2
            keystroke "x" using control down
        end tell
    end tell
    '''
    run_applescript(applescript)
    return f"✅ Saved {path}."

# --- EXECUTION ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON data from Agent")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            action = data.get("action")
            target_path = data.get("path", "~/Developer/llm")
            
            if action == "upgrade":
                print(upgrade_to_agent(target_path))
            
            elif action == "i_background":
                print(launch_i_background(target_path))
                
            elif action == "nano_edit":
                print(edit_file(data.get("path"), data.get("content")))
                
            elif action == "clone":
                # Logic to open extra windows if needed
                applescript = 'tell application "Terminal" to do script "ls"'
                run_applescript(applescript)
                print("👯 Window cloned.")
                
        except Exception as e:
            print(f"Orchestration Error: {e}")
