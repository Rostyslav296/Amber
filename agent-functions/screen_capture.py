#!/usr/bin/env python3
"""
screen_capture.py — macOS Desktop Vision Capture for Amber Agent

Captures the full macOS desktop (or a specific window) and returns structured
information about visible windows and their positions. Designed for vision-based
agent control similar to Claude's Computer Use.

Uses macOS native tools:
- screencapture: Capture screenshots
- osascript/AppleScript: Get window positions and app info
- CoreGraphics (via Quartz): Screen resolution

Coordinate system: 0-1000 normalized scale (same as edge.py vision actions).
"""

import subprocess
import sys
import json
import argparse
import os
import time
import datetime

TOOL_METADATA = {
    "name": "screen_capture",
    "description": """macOS Desktop Vision Capture — full screen or window screenshots.

Captures the desktop screen and returns structured window information.
Used for vision-based navigation of native macOS applications.

ACTIONS:
  capture    → Full desktop screenshot. Returns: path, screen dimensions, visible windows
  window     → Capture specific app window. Args: text="App Name"
  click      → Click at screen coordinates. Args: text="x,y" (0-1000 normalized scale)
  type       → Type text at current cursor. Args: value="text to type"
  key        → Press key combo. Args: value="cmd+c" or "enter" or "tab"

COORDINATE SYSTEM: 0-1000 normalized scale.
  [0,0] = top-left of screen, [1000,1000] = bottom-right.

EXAMPLES:
  {"tool":"screen_capture","args":{"action":"capture"}}
  {"tool":"screen_capture","args":{"action":"window","text":"Safari"}}
  {"tool":"screen_capture","args":{"action":"click","text":"500,300"}}
  {"tool":"screen_capture","args":{"action":"type","value":"hello world"}}
  {"tool":"screen_capture","args":{"action":"key","value":"cmd+c"}}""",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "enum": ["capture", "window", "click", "type", "key"],
                "description": "Action to perform"
            },
            "text": {
                "type": "string",
                "description": "App name for window capture, or 'x,y' coords for click"
            },
            "value": {
                "type": "string",
                "description": "Text to type, or key combo to press"
            }
        },
        "required": ["action"]
    }
}

SCREENSHOT_DIR = os.path.expanduser("~/Developer/llm/amber_vision")


def get_screen_resolution():
    """Get primary display resolution via system_profiler."""
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        displays = data.get("SPDisplaysDataType", [{}])
        for gpu in displays:
            for disp in gpu.get("spdisplays_ndrvs", []):
                res = disp.get("_spdisplays_resolution", "")
                if "x" in res.lower():
                    parts = res.lower().split("x")
                    w = int(parts[0].strip().split()[0])
                    h = int(parts[1].strip().split()[0])
                    return w, h
    except Exception:
        pass
    return 1440, 900  # M4 MacBook Air default


def get_visible_windows():
    """Get list of visible windows with positions via AppleScript."""
    script = '''
    set windowList to {}
    tell application "System Events"
        set allProcesses to every process whose visible is true
        repeat with proc in allProcesses
            try
                set procName to name of proc
                set allWindows to every window of proc
                repeat with win in allWindows
                    try
                        set winName to name of win
                        set winPos to position of win
                        set winSize to size of win
                        set end of windowList to procName & "|" & winName & "|" & (item 1 of winPos as text) & "," & (item 2 of winPos as text) & "|" & (item 1 of winSize as text) & "," & (item 2 of winSize as text)
                    end try
                end repeat
            end try
        end repeat
    end tell
    set AppleScript's text item delimiters to "\\n"
    return windowList as text
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10
        )
        windows = []
        screen_w, screen_h = get_screen_resolution()
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) >= 4:
                app = parts[0]
                title = parts[1][:60]
                pos = parts[2].split(",")
                size = parts[3].split(",")
                try:
                    x, y = int(pos[0]), int(pos[1])
                    w, h = int(size[0]), int(size[1])
                    # Normalize to 0-1000 scale
                    nx = int(x * 1000 / screen_w)
                    ny = int(y * 1000 / screen_h)
                    nw = int(w * 1000 / screen_w)
                    nh = int(h * 1000 / screen_h)
                    windows.append({
                        "app": app,
                        "title": title,
                        "position": f"[{nx},{ny}]",
                        "size": f"[{nw},{nh}]",
                        "raw_position": f"@{x},{y}",
                        "raw_size": f"{w}x{h}",
                    })
                except (ValueError, IndexError):
                    pass
        return windows
    except Exception as e:
        return [{"error": str(e)}]


def capture_screen(app_name=None):
    """Capture screenshot and return structured info."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SCREENSHOT_DIR, f"screen_{ts}.jpg")

    try:
        if app_name:
            # Capture specific window
            subprocess.run(
                ["screencapture", "-x", "-t", "jpg", "-l",
                 _get_window_id(app_name), path],
                timeout=10
            )
        else:
            # Full screen capture
            subprocess.run(
                ["screencapture", "-x", "-t", "jpg", path],
                timeout=10
            )

        if not os.path.exists(path):
            return {"status": "error", "message": "Screenshot capture failed"}

        screen_w, screen_h = get_screen_resolution()
        windows = get_visible_windows()

        return {
            "status": "success",
            "action": "capture",
            "screenshot_path": path,
            "screen_resolution": f"{screen_w}x{screen_h}",
            "viewport": {"width": screen_w, "height": screen_h},
            "visible_windows": windows[:20],
            "window_count": len(windows),
            "message": (
                f"Desktop captured: {path} ({screen_w}x{screen_h}). "
                f"{len(windows)} visible windows. "
                "Use click action with normalized [x,y] coords (0-1000) to interact."
            ),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _get_window_id(app_name):
    """Get CGWindowID for a named app (for targeted capture)."""
    try:
        script = f'''
        tell application "System Events"
            set targetProc to first process whose name is "{app_name}"
            set targetWin to first window of targetProc
            return id of targetWin
        end tell
        '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return "0"


def click_at(coords_str):
    """Click at normalized screen coordinates using cliclick."""
    try:
        parts = coords_str.replace('[', '').replace(']', '').split(',')
        x_norm = int(parts[0].strip())
        y_norm = int(parts[1].strip())

        screen_w, screen_h = get_screen_resolution()
        actual_x = int(x_norm * screen_w / 1000)
        actual_y = int(y_norm * screen_h / 1000)

        # Use AppleScript to click (doesn't require cliclick)
        script = f'''
        tell application "System Events"
            click at {{{actual_x}, {actual_y}}}
        end tell
        '''
        # Try cliclick first (more reliable), fall back to AppleScript
        try:
            subprocess.run(
                ["cliclick", f"c:{actual_x},{actual_y}"],
                timeout=5, capture_output=True
            )
        except FileNotFoundError:
            subprocess.run(
                ["osascript", "-e", script],
                timeout=5, capture_output=True
            )

        time.sleep(0.3)
        return {
            "status": "success",
            "action": "click",
            "clicked_at": f"({actual_x},{actual_y})",
            "normalized_coords": f"({x_norm},{y_norm})",
            "message": f"Clicked at screen ({actual_x},{actual_y}) [normalized: {x_norm},{y_norm}]"
        }
    except Exception as e:
        return {"status": "error", "action": "click", "error": str(e)}


def type_text(text):
    """Type text using AppleScript keystroke."""
    try:
        # Escape special chars for AppleScript
        safe_text = text.replace('\\', '\\\\').replace('"', '\\"')
        script = f'''
        tell application "System Events"
            keystroke "{safe_text}"
        end tell
        '''
        subprocess.run(["osascript", "-e", script], timeout=10, capture_output=True)
        return {
            "status": "success",
            "action": "type",
            "typed": text[:50],
            "message": f"Typed {len(text)} characters"
        }
    except Exception as e:
        return {"status": "error", "action": "type", "error": str(e)}


def press_key(combo):
    """Press key combination via AppleScript."""
    try:
        # Parse combo: "cmd+c" -> keystroke "c" using command down
        modifiers = []
        key = combo
        if "+" in combo:
            parts = combo.split("+")
            key = parts[-1]
            for mod in parts[:-1]:
                mod = mod.lower().strip()
                if mod in ("cmd", "command"):
                    modifiers.append("command down")
                elif mod in ("ctrl", "control"):
                    modifiers.append("control down")
                elif mod in ("alt", "option"):
                    modifiers.append("option down")
                elif mod in ("shift",):
                    modifiers.append("shift down")

        key_map = {
            "enter": "return", "return": "return",
            "tab": "tab", "escape": "escape", "esc": "escape",
            "delete": "delete", "backspace": "delete",
            "space": "space",
            "up": "up arrow", "down": "down arrow",
            "left": "left arrow", "right": "right arrow",
        }

        key_name = key_map.get(key.lower(), key)

        if key_name in ("return", "tab", "escape", "delete", "space",
                         "up arrow", "down arrow", "left arrow", "right arrow"):
            if modifiers:
                mod_str = ", ".join(modifiers)
                script = f'tell application "System Events" to key code {_key_code(key_name)} using {{{mod_str}}}'
            else:
                script = f'tell application "System Events" to keystroke return' if key_name == "return" else \
                         f'tell application "System Events" to key code {_key_code(key_name)}'
        else:
            if modifiers:
                mod_str = ", ".join(modifiers)
                script = f'tell application "System Events" to keystroke "{key}" using {{{mod_str}}}'
            else:
                script = f'tell application "System Events" to keystroke "{key}"'

        subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)
        return {
            "status": "success",
            "action": "key",
            "pressed": combo,
            "message": f"Pressed: {combo}"
        }
    except Exception as e:
        return {"status": "error", "action": "key", "error": str(e)}


def _key_code(key_name):
    """Map key names to macOS key codes."""
    codes = {
        "return": 36, "tab": 48, "escape": 53, "delete": 51,
        "space": 49, "up arrow": 126, "down arrow": 125,
        "left arrow": 123, "right arrow": 124,
    }
    return codes.get(key_name, 36)


def process(payload):
    """Main dispatcher."""
    action = payload.get("action", "capture")
    text = payload.get("text", "")
    value = payload.get("value", "")

    if action == "capture":
        return capture_screen()
    elif action == "window":
        return capture_screen(app_name=text)
    elif action == "click":
        return click_at(text)
    elif action == "type":
        return type_text(value or text)
    elif action == "key":
        return press_key(value or text)
    else:
        return {"status": "error", "message": f"Unknown action: {action}"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="macOS Screen Capture for Amber Vision")
    parser.add_argument("--json", help="JSON payload")
    parser.add_argument("--server", action="store_true", help="Server mode (stdin/stdout)")
    args = parser.parse_args()

    if args.server:
        print(json.dumps({"status": "ready", "metadata": TOOL_METADATA}), flush=True)
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                payload = json.loads(line.strip())
                result = process(payload)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception as e:
                print(json.dumps({"status": "error", "message": str(e)}), flush=True)
    elif args.json:
        payload = json.loads(args.json)
        result = process(payload)
        print(json.dumps(result, indent=2))
    else:
        result = capture_screen()
        print(json.dumps(result, indent=2))
