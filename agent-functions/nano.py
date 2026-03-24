#!/usr/bin/env python3
"""
nano.py — Reliable file editor using native macOS Terminal + nano.

Two-phase approach:
  Phase 1: Write file content to disk via Python (guaranteed reliable).
  Phase 2: Open nano in Terminal for visual feedback, then auto-save+exit.

This ensures files are ALWAYS written correctly, while still giving the user
visible nano activity in the Terminal window.
"""
import sys, subprocess, json, argparse, time, os, tempfile

# --- METADATA ---
TOOL_METADATA = {
    "name": "nano_editor",
    "description": (
        "Reliable file editor using native macOS Terminal nano. "
        "Creates, reads, or edits files with guaranteed write + visible nano feedback. "
        "Best for creating source code files, config files, and project files. "
        "Supports read (return file contents), overwrite (replace entire file), and append modes. "
        "ALWAYS use read operation BEFORE overwrite to see existing content. "
        "Files are written reliably — nano is shown for visual feedback."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute or relative path to the file (e.g., '~/Developer/build/Sources/App.swift')."
            },
            "content": {
                "type": "string",
                "description": "The full text content to write to the file."
            },
            "operation": {
                "type": "string",
                "enum": ["read", "overwrite", "append"],
                "default": "overwrite",
                "description": "read: return file contents (no content param needed); overwrite: replace entire file; append: add to end."
            },
            "show_nano": {
                "type": "boolean",
                "default": True,
                "description": "If true, opens nano in Terminal to show the file visually (then auto-exits). If false, writes silently."
            }
        },
        "required": ["file_path"]
    }
}

# --- LOGIC ---

def run_applescript(script):
    cmd = ["osascript", "-e", script]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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

def write_file_direct(path, content, operation):
    """Phase 1: Write file content directly via Python. Guaranteed reliable."""
    resolved = os.path.expanduser(path)
    abs_path = os.path.abspath(resolved)

    # Ensure parent directory exists
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    mode = 'a' if operation == 'append' else 'w'
    with open(abs_path, mode, encoding='utf-8') as f:
        f.write(content)

    return abs_path

def verify_file(abs_path, content, operation):
    """Verify the file was written correctly."""
    if not os.path.exists(abs_path):
        return False, "File does not exist after write"

    with open(abs_path, 'r', encoding='utf-8') as f:
        actual = f.read()

    if operation == 'overwrite':
        if actual == content:
            return True, "Content matches"
        return False, f"Content mismatch (expected {len(content)} chars, got {len(actual)})"
    else:
        if actual.endswith(content):
            return True, "Appended content matches"
        return False, "Appended content not found at end of file"

def show_in_nano(abs_path):
    """Phase 2: Open nano in Terminal for visual feedback, then auto-save+exit."""
    terminal_running = is_terminal_running()

    escaped_path = abs_path.replace("'", "'\\''")

    # Open nano with the file in the terminal (no activate to avoid focus stealing)
    if terminal_running and get_terminal_window_count() > 0:
        script = f'''
        tell application "Terminal"
            do script "nano '{escaped_path}'" in front window
        end tell
        '''
    else:
        script = f'''
        tell application "Terminal"
            activate
            do script "nano '{escaped_path}'"
        end tell
        '''

    out, err = run_applescript(script)
    if err:
        return f"Warning: Could not open nano: {err}"

    # Wait for nano to load
    time.sleep(1.5)

    # Scroll to bottom so user sees content, then exit
    # No activate — avoids focus stealing
    exit_script = '''
    delay 0.2
    tell application "System Events"
        tell process "Terminal"
            -- Go to end of file (Alt+/ in nano = go to end)
            keystroke "/" using option down
            delay 0.3
            -- Exit nano: Ctrl+X
            keystroke "x" using control down
            delay 0.3
        end tell
    end tell
    '''

    out, err = run_applescript(exit_script)
    if err:
        # Fallback: try simpler exit
        time.sleep(0.5)
        simple_exit = '''
        delay 0.1
        tell application "System Events"
            tell process "Terminal"
                keystroke "x" using control down
                delay 0.5
                keystroke "n"
            end tell
        end tell
        '''
        run_applescript(simple_exit)

    return "OK"


def read_file(path):
    """Read and return file contents."""
    resolved = os.path.expanduser(path)
    abs_path = os.path.abspath(resolved)
    if not os.path.exists(abs_path):
        return f"Error: File not found: {abs_path}"
    try:
        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read()
        line_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
        size = os.path.getsize(abs_path)
        return f"File: {abs_path} ({line_count} lines, {size} bytes)\n---\n{content}"
    except Exception as e:
        return f"Error reading file: {e}"


def edit_with_nano(data):
    path = data.get("file_path", "")
    content = data.get("content", "")
    operation = data.get("operation", "overwrite")
    show = data.get("show_nano", True)

    if not path:
        return "Error: file_path is required."

    # Read operation — return file contents, no write
    if operation == "read":
        return read_file(path)

    if not content:
        return "Error: content is required for overwrite/append operations."

    # Phase 1: Write file directly (guaranteed)
    try:
        abs_path = write_file_direct(path, content, operation)
    except Exception as e:
        return f"Error writing file: {e}"

    # Verify
    ok, msg = verify_file(abs_path, content, operation)
    if not ok:
        return f"Error: File write verification failed — {msg}"

    file_size = os.path.getsize(abs_path)
    line_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
    op_label = 'Wrote' if operation == 'overwrite' else 'Appended to'

    # Phase 2: Show in nano (optional visual feedback)
    if show:
        nano_result = show_in_nano(abs_path)
        if nano_result != "OK":
            return f"{op_label} {path} ({line_count} lines, {file_size} bytes). {nano_result}"

    return f"{op_label} {path} ({line_count} lines, {file_size} bytes)"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            print(edit_with_nano(json.loads(args.json)))
        except Exception as e:
            print(f"Error: {e}")
