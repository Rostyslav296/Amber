#!/usr/bin/env python3
import sys, subprocess, json, argparse, time, os, tempfile

# --- METADATA ---
TOOL_METADATA = {
    "name": "macos_terminal",
    "description": (
        "Native macOS Terminal.app control. Use this (NOT fterminal) for launching terminal windows. "
        "The agent runs in its own Terminal window — this tool opens NEW windows to avoid conflicts. "
        "Modes: "
        "'run' (executes shell command in a NEW Terminal.app window — safe, won't touch agent's own window), "
        "'read' (gets terminal output from the launched window), "
        "'state' (checks if busy/idle), "
        "'keystroke' (sends keystrokes to interactive apps like nano/vim — supports ctrl+o, ctrl+x, return, escape, arrow keys, raw text), "
        "'write_file' (writes file content directly — reliable file creation without nano)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to run (for 'run' mode)."
            },
            "mode": {
                "type": "string",
                "enum": ["run", "read", "state", "keystroke", "write_file"],
                "default": "run",
                "description": (
                    "run: execute shell command; read: get terminal text; state: check if busy; "
                    "keystroke: send key sequences to interactive apps (e.g. 'ctrl+o return ctrl+x'); "
                    "write_file: write content to a file via shell (no nano needed)."
                )
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
            },
            "keys": {
                "type": "string",
                "description": (
                    "Space-separated key sequence for 'keystroke' mode. "
                    "Supports: ctrl+<key>, cmd+<key>, shift+<key>, "
                    "return, escape, tab, up, down, left, right, delete, backspace, space, "
                    "home, end, pageup, pagedown, f1-f12, "
                    "text:<string> (type raw text), delay:<seconds> (pause). "
                    "Examples: 'ctrl+o return ctrl+x' (save+exit nano), "
                    "'text:hello_world return', 'ctrl+c', 'escape :wq return' (vim save+exit)."
                )
            },
            "file_path": {
                "type": "string",
                "description": "File path for 'write_file' mode."
            },
            "content": {
                "type": "string",
                "description": "File content for 'write_file' mode."
            }
        }
    }
}

# --- KEY MAPPINGS ---
# AppleScript key codes for special keys
SPECIAL_KEYS = {
    'return':    'keystroke return',
    'enter':     'keystroke return',
    'escape':    'key code 53',
    'esc':       'key code 53',
    'tab':       'keystroke tab',
    'delete':    'key code 51',
    'backspace': 'key code 51',
    'forwarddelete': 'key code 117',
    'up':        'key code 126',
    'down':      'key code 125',
    'left':      'key code 123',
    'right':     'key code 124',
    'space':     'keystroke " "',
    'home':      'key code 115',
    'end':       'key code 119',
    'pageup':    'key code 116',
    'pagedown':  'key code 121',
    'f1':  'key code 122', 'f2':  'key code 120', 'f3':  'key code 99',
    'f4':  'key code 118', 'f5':  'key code 96',  'f6':  'key code 97',
    'f7':  'key code 98',  'f8':  'key code 100', 'f9':  'key code 101',
    'f10': 'key code 109', 'f11': 'key code 103', 'f12': 'key code 111',
}

MODIFIER_MAP = {
    'ctrl':    'control down',
    'control': 'control down',
    'cmd':     'command down',
    'command': 'command down',
    'shift':   'shift down',
    'alt':     'option down',
    'option':  'option down',
}

# --- LOGIC ---

def run_applescript(script):
    cmd = ["osascript", "-e", script]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout.strip(), res.stderr.strip()

def is_terminal_running():
    out, _ = run_applescript(
        'tell application "System Events" to return (name of processes) contains "Terminal"'
    )
    return out.lower() == "true"

def get_terminal_window_count():
    out, _ = run_applescript(
        'tell application "Terminal" to return count of windows'
    )
    try:
        return int(out)
    except (ValueError, TypeError):
        return 0

def get_terminal_window_ids():
    """Get list of Terminal.app window IDs."""
    out, _ = run_applescript(
        'tell application "Terminal" to return id of every window'
    )
    if not out:
        return []
    # AppleScript returns comma-separated IDs like "1234, 5678"
    try:
        return [int(x.strip()) for x in out.split(",") if x.strip()]
    except (ValueError, TypeError):
        return []

# Track the last window we opened so we can target it for read/state/keystroke.
# Persisted to a temp file since each tool invocation is a new process.
_WINDOW_ID_FILE = os.path.join(tempfile.gettempdir(), "macos_terminal_launched_window_id")

def _get_launched_window_id():
    """Read the persisted launched window ID."""
    try:
        with open(_WINDOW_ID_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError, TypeError):
        return None

def _set_launched_window_id(wid):
    """Persist the launched window ID."""
    try:
        with open(_WINDOW_ID_FILE, "w") as f:
            f.write(str(wid))
    except Exception:
        pass

def _parse_key_token(token):
    """Parse a single key token into an AppleScript keystroke command.

    Returns an AppleScript line like:
      keystroke "o" using control down
      key code 53
      keystroke "hello world"
    """
    token_lower = token.lower()

    # Handle text:<content> — type raw text
    if token_lower.startswith('text:'):
        raw = token[5:]  # preserve original case
        escaped = raw.replace('\\', '\\\\').replace('"', '\\"')
        return f'keystroke "{escaped}"'

    # Handle delay:<seconds>
    if token_lower.startswith('delay:'):
        try:
            secs = float(token[6:])
            return f'delay {secs}'
        except ValueError:
            return 'delay 0.2'

    # Handle modifier combos: ctrl+o, cmd+v, ctrl+shift+k
    if '+' in token_lower:
        parts = token_lower.split('+')
        key_part = parts[-1]
        mod_parts = parts[:-1]

        modifiers = []
        for m in mod_parts:
            if m in MODIFIER_MAP:
                modifiers.append(MODIFIER_MAP[m])

        if not modifiers:
            # No valid modifiers found, treat whole thing as text
            escaped = token.replace('\\', '\\\\').replace('"', '\\"')
            return f'keystroke "{escaped}"'

        modifier_str = ', '.join(modifiers)
        if len(modifiers) > 1:
            modifier_str = '{' + modifier_str + '}'

        # Check if the key part is a special key
        if key_part in SPECIAL_KEYS:
            base_cmd = SPECIAL_KEYS[key_part]
            # Convert 'keystroke return' to 'keystroke return using ...'
            # Convert 'key code N' to 'key code N using ...'
            return f'{base_cmd} using {modifier_str}'
        else:
            # Single character key
            escaped = key_part.replace('\\', '\\\\').replace('"', '\\"')
            return f'keystroke "{escaped}" using {modifier_str}'

    # Handle plain special keys: return, escape, tab, etc.
    if token_lower in SPECIAL_KEYS:
        return SPECIAL_KEYS[token_lower]

    # Handle single character or short text
    escaped = token.replace('\\', '\\\\').replace('"', '\\"')
    return f'keystroke "{escaped}"'


def _build_keystroke_script(keys_str):
    """Parse a key sequence string and build a complete AppleScript."""
    # Tokenize: split on spaces, but respect text:<...> with quotes
    tokens = []
    i = 0
    chars = keys_str
    while i < len(chars):
        if chars[i] == ' ':
            i += 1
            continue
        # Check for text: prefix with possible quoting
        if chars[i:].lower().startswith('text:'):
            # Grab everything after text: until next unquoted space or end
            start = i
            i += 5  # skip 'text:'
            if i < len(chars) and chars[i] == '"':
                # Quoted text
                i += 1
                while i < len(chars) and chars[i] != '"':
                    if chars[i] == '\\' and i + 1 < len(chars):
                        i += 2
                    else:
                        i += 1
                if i < len(chars):
                    i += 1  # skip closing quote
                tokens.append(chars[start:i])
            else:
                # Unquoted text — grab until next space
                while i < len(chars) and chars[i] != ' ':
                    i += 1
                tokens.append(chars[start:i])
        else:
            # Regular token
            start = i
            while i < len(chars) and chars[i] != ' ':
                i += 1
            tokens.append(chars[start:i])

    # Build AppleScript lines
    as_lines = []
    for token in tokens:
        line = _parse_key_token(token)
        if line:
            as_lines.append(f'            {line}')

    if not as_lines:
        return None

    body = '\n'.join(as_lines)
    # No 'activate' — avoids focus stealing when agent terminal is in use
    script = f'''
    tell application "System Events"
        tell process "Terminal"
{body}
        end tell
    end tell
    '''
    return script


def handle_terminal(data):
    mode = data.get("mode", "run")
    target = data.get("target", "current")
    cmd_text = data.get("command", "")
    line_count = data.get("lines", 20)

    if mode == "run":
        terminal_running = is_terminal_running()

        if target == "tab":
            # New tab: activate needed for Cmd+T keystroke
            run_applescript(
                'tell application "Terminal" to activate\n'
                'delay 0.3\n'
                'tell application "System Events" to keystroke "t" using command down'
            )
            time.sleep(0.7)
            escaped_cmd = cmd_text.replace('\\', '\\\\').replace('"', '\\"')
            script = f'''
            tell application "Terminal"
                do script "{escaped_cmd}" in front window
            end tell
            '''
            out, err = run_applescript(script)
            return f"Executed in new tab: {cmd_text}"

        elif target == "current":
            # Send command to the window we previously launched (not the agent's own)
            launched_id = _get_launched_window_id()
            if launched_id is not None:
                current_ids = get_terminal_window_ids()
                if launched_id in current_ids:
                    escaped_cmd = cmd_text.replace('\\', '\\\\').replace('"', '\\"')
                    script = f'''
                    tell application "Terminal"
                        do script "{escaped_cmd}" in window id {launched_id}
                    end tell
                    '''
                    out, err = run_applescript(script)
                    if not err:
                        return f"Executed in launched window (id {launched_id}): {cmd_text}"
                # Window gone — fall through to open a new one

        # Default: ALWAYS open a new window to avoid hitting the agent's own window.
        # The agent itself runs in Terminal.app, so "front window" would be the agent's window.
        ids_before = get_terminal_window_ids() if terminal_running else []
        escaped_cmd = cmd_text.replace('\\', '\\\\').replace('"', '\\"')
        script = f'''
        tell application "Terminal"
            activate
            do script "{escaped_cmd}"
        end tell
        '''
        out, err = run_applescript(script)

        # Verify a new window was actually created
        time.sleep(0.5)
        ids_after = get_terminal_window_ids()
        new_ids = [wid for wid in ids_after if wid not in ids_before]
        if new_ids:
            _set_launched_window_id(new_ids[0])
            return f"Opened new Terminal window (id {new_ids[0]}): {cmd_text}"
        elif len(ids_after) > len(ids_before):
            _set_launched_window_id(ids_after[-1])
            return f"Opened new Terminal window: {cmd_text}"
        elif not ids_before and ids_after:
            _set_launched_window_id(ids_after[0])
            return f"Opened new Terminal window: {cmd_text}"
        else:
            return f"WARNING: Terminal command sent but new window may not have opened. Check Terminal.app. Command: {cmd_text}"

    elif mode == "keystroke":
        keys = data.get("keys", "")
        if not keys:
            return "Error: 'keys' parameter required for keystroke mode."

        script = _build_keystroke_script(keys)
        if not script:
            return "Error: Could not parse key sequence."

        out, err = run_applescript(script)
        if err:
            return f"Keystroke error: {err}"
        return f"Sent keystrokes: {keys}"

    elif mode == "write_file":
        file_path = data.get("file_path", "")
        content = data.get("content", "")
        if not file_path:
            return "Error: 'file_path' required for write_file mode."

        # Resolve ~ and ensure parent directory exists
        resolved_path = os.path.expanduser(file_path)
        abs_path = os.path.abspath(resolved_path)
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # Write directly via Python — guaranteed reliable, no Terminal flashing
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)

        line_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
        return f"File written: {file_path} ({line_count} lines, {len(content)} chars)"

    elif mode == "read":
        # Target the launched window if we have one, otherwise front window
        launched_id = _get_launched_window_id()
        if launched_id and launched_id in get_terminal_window_ids():
            window_target = f"window id {launched_id}"
        else:
            window_target = "front window"
        script = f'''
        tell application "Terminal"
            tell {window_target}
                get contents of selected tab
            end tell
        end tell
        '''
        out, err = run_applescript(script)
        if out:
            lines = out.split("\n")
            recent = "\n".join(lines[-line_count:])
            return f"--- TERMINAL OUTPUT (targeting {window_target}) ---\n{recent}"
        return "No output found or window not open."

    elif mode == "state":
        # Target the launched window if we have one, otherwise front window
        launched_id = _get_launched_window_id()
        if launched_id and launched_id in get_terminal_window_ids():
            window_target = f"window id {launched_id}"
        else:
            window_target = "front window"
        script = f'''
        tell application "Terminal"
            tell {window_target}
                set is_busy to busy of selected tab
                return is_busy
            end tell
        end tell
        '''
        out, err = run_applescript(script)
        status = "Busy (Running command)" if out == "true" else "Idle (Waiting for input)"
        return f"Terminal Status: {status} (targeting {window_target})"

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
