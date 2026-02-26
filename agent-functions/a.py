#!/usr/bin/env python3
import sys, subprocess, json, argparse, os

# --- METADATA ---
TOOL_METADATA = {
    "name": "agent_orchestrator",
    "description": "Master Orchestrator for AgentF. 'a' or 'upgrade' boots full AgentF in FTerminal. 'i' launches iOS suite.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["upgrade", "a", "i_background", "i", "clone", "nano_edit"],
                "description": "'a' or 'upgrade' = start FTerminal + agent. 'i' = iOS monitoring."
            },
            "path": {"type": "string", "default": "~/Developer/llm"},
            "content": {"type": "string", "description": "For nano_edit."}
        },
        "required": ["action"]
    }
}

# --- CONFIGURATION ---
BASE_DIR = os.path.expanduser("~/Developer/llm")
AGENT_FUNCTIONS_DIR = os.path.join(BASE_DIR, "agent-functions")
AGENTF_TERMINAL_PATH = os.path.join(AGENT_FUNCTIONS_DIR, "agentf-terminal.py")

# --- CORE LOGIC ---

def run_applescript(script):
    cmd = ["osascript", "-e", script]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout.strip()

def launch_i_background(path="~/Developer/llm"):
    full_path = os.path.expanduser(path)
    cmd_text = f"cd '{full_path}' && python3 i.py"
    
    applescript = f'''
    tell application "Terminal"
        activate
        do script "{cmd_text}"
    end tell
    '''
    run_applescript(applescript)
    return "📱 iOS Monitoring Suite launched in new Terminal window."

def upgrade_to_agent(path="~/Developer/llm"):
    full_path = os.path.expanduser(path)
    launch_cmd = f"clear && cd '{full_path}' && ./chat.sh"
    payload = json.dumps({"command": launch_cmd})
    
    if not os.path.exists(AGENTF_TERMINAL_PATH):
        return f"❌ agentf-terminal.py not found at:\n{AGENTF_TERMINAL_PATH}\n\nMake sure the file exists in agent-functions folder."
    
    try:
        result = subprocess.run(
            [sys.executable, AGENTF_TERMINAL_PATH, "--json", payload],
            capture_output=True,
            text=True,
            timeout=12
        )
        
        if result.returncode == 0:
            return f"🚀 FTerminal + Agent launched successfully (from agent-functions folder)."
        else:
            return f"⚠️ FTerminal started with warnings:\n{result.stderr.strip() or result.stdout.strip()}"
    except Exception as e:
        return f"❌ Failed to launch FTerminal: {e}"

def edit_file(path, content):
    full_path = os.path.expanduser(path)
    try:
        subprocess.run(["pbcopy"], input=content.encode('utf-8'), check=True)
        applescript = f'''
        tell application "Terminal"
            activate
            do script "nano '{full_path}'"
            delay 1.2
            tell application "System Events"
                keystroke "v" using command down
                delay 0.6
                keystroke "o" using control down
                delay 0.2
                keystroke return
                delay 0.2
                keystroke "x" using control down
            end tell
        end tell
        '''
        run_applescript(applescript)
        return f"✅ Saved {full_path}."
    except Exception as e:
        return f"❌ Edit failed: {e}"

# --- EXECUTION ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON data from Agent")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            raw_action = str(data.get("action", "")).strip()
            action = raw_action.lower()
            target_path = data.get("path", "~/Developer/llm")
            
            print(f"🔧 Orchestrator received: '{raw_action}'")

            if action in ["a", "upgrade"]:
                print(upgrade_to_agent(target_path))
            
            elif action in ["i", "i_background"]:
                print(launch_i_background(target_path))
                
            elif action == "nano_edit":
                print(edit_file(data.get("path"), data.get("content")))
                
            elif action == "clone":
                applescript = f'tell application "Terminal" to do script "cd {BASE_DIR} && ls -la"'
                run_applescript(applescript)
                print("👯 Terminal window cloned.")
                
            else:
                print(f"⚠️ Unknown action: {raw_action}")
                print("Valid actions: a, upgrade, i, clone, nano_edit")
                
        except Exception as e:
            print(f"🚨 Orchestration Error: {e}")
    else:
        print("Usage: python3 a.py --json '{\"action\":\"a\"}'")
