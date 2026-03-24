# Amber Vision Architecture

Pixel-based navigation system for the Amber AI agent. Enables coordinate-based
interaction with browser pages and native macOS desktop, similar to Claude Computer Use.

## Status

- **Default:** OFF (activated per-session by user)
- **Activation:** Say `vision`, `vision on`, or `use vision` before/in a prompt
- **Deactivation:** Say `vision off` or `no vision`
- **Model:** Qwen 3.5-9B (text-only); vision data is structured text, not raw images

---

## Architecture Overview

```
User: "use vision, go to example.com and click the signup button"
                    |
                    v
            [ai.py] Detects "use vision" -> sets agent_loop.vision_enabled = True
            Injects VISION MODE system prompt instructions
                    |
                    v
            [agent.py] AgentLoop._loop() executes tool calls
            Vision stall detection active (repeated pixel_click, etc.)
            Vision-specific observation hints appended to results
                    |
                    v
    +-----------+---+---+-----------+
    |                               |
    v                               v
[edge.py]                   [screen_capture.py]
Browser Vision              Desktop Vision
vision_snapshot             capture (full screen)
pixel_click                 window (app window)
pixel_type                  click (screen coords)
pixel_drag                  type (keyboard input)
                            key (key combos)
```

### How It Works (Text-Only Model)

Since Qwen 3.5-9B cannot process images directly, vision mode works through
**structured text descriptions** of screen contents:

1. **vision_snapshot** (browser): Takes a JPEG screenshot AND extracts the ARIA
   accessibility tree with element coordinates. The model receives:
   - Element descriptions with `[x,y]` normalized coords (0-1000 scale)
   - Full ARIA snapshot text (same as regular snapshot, but with coords emphasized)
   - Screenshot saved to `~/Desktop/edge_agent/vision_*.jpg` (for user inspection)

2. **screen_capture** (desktop): Captures the screen AND queries macOS for visible
   window positions via AppleScript. The model receives:
   - Window list with app names, titles, positions, and sizes (normalized 0-1000)
   - Screenshot saved to `~/Desktop/amber_vision/screen_*.jpg`

3. The model uses the structured text to estimate where to click, then uses
   `pixel_click`/`pixel_type`/`pixel_drag` with coordinate arguments.

### Future Upgrade Path

When a Vision-Language Model (VLM) becomes available on MLX:
- Replace `mlx-community/Qwen3.5-9B-MLX-4bit` with `mlx-community/Qwen2.5-VL-7B-Instruct-4bit`
- Update `ai.py` to pass screenshot base64 in message content blocks
- The vision actions in `edge.py` and `screen_capture.py` remain unchanged
- The model would then SEE the screenshots instead of reading text descriptions

---

## Coordinate System

All vision actions use a **0-1000 normalized scale**, independent of actual screen
or viewport resolution.

```
[0,0] ─────────────── [1000,0]
  |                      |
  |       [500,500]      |
  |      (center)        |
  |                      |
[0,1000] ────────────── [1000,1000]
```

**Conversion formula:**
```
actual_x = normalized_x * viewport_width / 1000
actual_y = normalized_y * viewport_height / 1000
```

**Precision:** +/- 50 units on the normalized scale is acceptable for most elements.

---

## Browser Vision (edge.py)

### vision_snapshot

Captures the browser viewport as JPEG and returns ARIA tree with coordinates.

**Request:**
```json
{"tool": "edge", "args": {"action": "vision_snapshot"}}
```

**Response:**
```json
{
  "status": "success",
  "action": "vision_snapshot",
  "screenshot_path": "~/Desktop/edge_agent/vision_20260308_143022.jpg",
  "viewport": {"width": 1440, "height": 900},
  "url": "https://example.com",
  "title": "Example Page",
  "elements": [
    {"ref": "#1", "description": "link \"Sign Up\"", "coords": "[650,120]", "raw_coords": "@936,108"},
    {"ref": "#2", "description": "textbox \"Email\"", "coords": "[500,300]", "raw_coords": "@720,270"},
    {"ref": "#3", "description": "button \"Submit\"", "coords": "[500,400]", "raw_coords": "@720,360"}
  ],
  "element_count": 3,
  "page": "... full ARIA snapshot text ...",
  "message": "Vision snapshot: path (1440x900). 3 interactive elements detected."
}
```

### pixel_click

Click at normalized viewport coordinates.

**Request:**
```json
{"tool": "edge", "args": {"action": "pixel_click", "text": "650,120"}}
```

**Response:** Standard snapshot result with `click_result` and `normalized_coords` fields.

**Features:**
- Human-like Bezier mouse movement (reuses existing `_bezier_move`)
- Auto-detects new tabs/popups after click
- Returns updated page snapshot

### pixel_type

Click at coordinates to focus, then type text.

**Request:**
```json
{"tool": "edge", "args": {"action": "pixel_type", "text": "500,300", "value": "user@email.com"}}
```

**Features:**
- Clicks to focus the field first
- Selects existing text (Cmd+A) before typing
- Human-like typing delay (30ms per character)

### pixel_drag

Drag from one coordinate to another. Useful for sliders, drag-and-drop interfaces.

**Request:**
```json
{"tool": "edge", "args": {"action": "pixel_drag", "text": "100,500", "value": "900,500"}}
```

**Features:**
- Smooth drag with interpolated steps
- Bezier approach to start position
- Mouse down -> interpolated moves -> mouse up

---

## Desktop Vision (screen_capture.py)

For native macOS application control beyond the browser.

### capture

Full desktop screenshot with visible window information.

**Request:**
```json
{"tool": "screen_capture", "args": {"action": "capture"}}
```

**Response:**
```json
{
  "status": "success",
  "screenshot_path": "~/Desktop/amber_vision/screen_20260308_143022.jpg",
  "screen_resolution": "1440x900",
  "viewport": {"width": 1440, "height": 900},
  "visible_windows": [
    {
      "app": "Microsoft Edge",
      "title": "Google - Search",
      "position": "[0,30]",
      "size": "[1000,970]",
      "raw_position": "@0,27",
      "raw_size": "1440x873"
    },
    {
      "app": "Terminal",
      "title": "zsh",
      "position": "[200,100]",
      "size": "[500,400]"
    }
  ],
  "window_count": 2
}
```

### window

Capture a specific app window by name.

**Request:**
```json
{"tool": "screen_capture", "args": {"action": "window", "text": "Terminal"}}
```

### click

Click at normalized screen coordinates. Uses `cliclick` if available, falls back to AppleScript.

**Request:**
```json
{"tool": "screen_capture", "args": {"action": "click", "text": "500,300"}}
```

### type

Type text at the current cursor position via AppleScript keystroke.

**Request:**
```json
{"tool": "screen_capture", "args": {"action": "type", "value": "hello world"}}
```

### key

Press key combinations (shortcuts).

**Request:**
```json
{"tool": "screen_capture", "args": {"action": "key", "value": "cmd+c"}}
```

**Supported modifiers:** `cmd`/`command`, `ctrl`/`control`, `alt`/`option`, `shift`

**Supported special keys:** `enter`/`return`, `tab`, `escape`/`esc`, `delete`/`backspace`,
`space`, `up`, `down`, `left`, `right`

---

## Agent Integration (agent.py)

### Vision Flag

```python
class SwarmAgentLoop(AgentLoop):
    def __init__(self, ...):
        self.vision_enabled = False  # Off by default
```

Set via ai.py when user says "use vision" or "vision" in their prompt.

### Vision Stall Detection

The `_detect_stall()` method includes vision-specific patterns:

1. **Repeated pixel_click at same coordinates (3x):** Suggests wrong coordinates or
   non-clickable element. Recovery: take new vision_snapshot, adjust coords by +/-50,
   or fall back to traditional #N ref click.

2. **Repeated vision_snapshot errors (3x):** Suggests vision system is unavailable.
   Recovery: switch to traditional snapshot action with #N refs.

### Vision Observation Hints

When vision actions execute, the agent loop appends context-sensitive hints:

- **vision_snapshot success:** "Vision snapshot captured. Use element coordinates to
  interact via pixel_click/pixel_type..."
- **pixel_click/pixel_type/pixel_drag:** "Pixel action executed. Call vision_snapshot
  to see the updated page state."
- **screen_capture:** "Desktop screenshot captured. Use element descriptions and
  coordinates to interact."

---

## Entry Point (ai.py)

### Activation Detection

```python
# In the main loop, before processing user input:
if re.search(r'\buse\s+vision\b|\bvision\s+mode\b', raw_lower):
    agent_loop.vision_enabled = True
elif raw_lower.strip() in ("vision on", "vision"):
    agent_loop.vision_enabled = True
elif raw_lower.strip() in ("vision off", "no vision"):
    agent_loop.vision_enabled = False
```

### Dynamic System Prompt

When vision is enabled, the system prompt is augmented with:

```
VISION MODE (ACTIVE): You have pixel-based navigation.
Use vision_snapshot to capture the page with element coordinates.
Then use pixel_click/pixel_type/pixel_drag with normalized [x,y] coords (0-1000 scale).
[0,0]=top-left, [1000,1000]=bottom-right.
For desktop-wide vision, use screen_capture tool.
You can still use traditional snapshot + #N refs as fallback.
WORKFLOW: vision_snapshot -> identify element positions -> pixel_click/pixel_type -> repeat
```

This prompt is injected once into the system message when vision mode activates.

---

## Usage Examples

### Browser Vision Workflow

```
User: use vision, go to example.com and fill out the contact form

Agent:
1. {"tool": "edge", "args": {"action": "new_tab", "url": "https://example.com"}}
2. {"tool": "edge", "args": {"action": "vision_snapshot"}}
   -> Sees: textbox "Name" at [300,250], textbox "Email" at [300,350], button "Send" at [300,450]
3. {"tool": "edge", "args": {"action": "pixel_type", "text": "300,250", "value": "John Doe"}}
4. {"tool": "edge", "args": {"action": "pixel_type", "text": "300,350", "value": "john@email.com"}}
5. {"tool": "edge", "args": {"action": "pixel_click", "text": "300,450"}}
   -> Form submitted
```

### Desktop Vision Workflow

```
User: use vision, open Notes app and create a new note

Agent:
1. {"tool": "app_opener", "args": {"name": "Notes"}}
2. {"tool": "screen_capture", "args": {"action": "capture"}}
   -> Sees: Notes window at [100,50] size [600,700]
3. {"tool": "screen_capture", "args": {"action": "click", "text": "150,80"}}
   -> Clicks "New Note" button area
4. {"tool": "screen_capture", "args": {"action": "type", "value": "Meeting notes for today"}}
```

### Mixed Browser + Desktop

```
User: vision, take what's on the browser and paste it into Notes

Agent:
1. {"tool": "edge", "args": {"action": "vision_snapshot"}}
   -> Sees browser content
2. {"tool": "edge", "args": {"action": "keys", "value": "cmd+a"}}
3. {"tool": "edge", "args": {"action": "keys", "value": "cmd+c"}}
4. {"tool": "screen_capture", "args": {"action": "click", "text": "300,400"}}
   -> Clicks Notes app window
5. {"tool": "screen_capture", "args": {"action": "key", "value": "cmd+v"}}
```

---

## File Reference

| File | Component | Description |
|------|-----------|-------------|
| `agent-functions/edge.py` | `vision_snapshot()` | Browser JPEG + ARIA coords |
| `agent-functions/edge.py` | `pixel_click()` | Browser pixel click |
| `agent-functions/edge.py` | `pixel_type()` | Browser pixel click + type |
| `agent-functions/edge.py` | `pixel_drag()` | Browser pixel drag |
| `agent-functions/screen_capture.py` | `capture_screen()` | Desktop screenshot + windows |
| `agent-functions/screen_capture.py` | `click_at()` | Desktop pixel click |
| `agent-functions/screen_capture.py` | `type_text()` | Desktop keyboard input |
| `agent-functions/screen_capture.py` | `press_key()` | Desktop key combos |
| `agent.py` | `SwarmAgentLoop.vision_enabled` | Vision on/off flag |
| `agent.py` | `_detect_stall()` | Vision stall detection |
| `agent.py` | `_loop()` | Vision observation hints |
| `ai.py` | Main loop | Vision mode detection + prompt injection |

---

## Performance Considerations

| Factor | Impact | Mitigation |
|--------|--------|------------|
| JPEG screenshots | ~100-300KB per capture | Saved to disk, not held in RAM |
| ARIA tree extraction | ~50-200ms per snapshot | Reuses existing snapshot infrastructure |
| AppleScript window queries | ~200-500ms | Cached per capture call |
| Coordinate conversion | Negligible | Simple arithmetic |
| Model context | Vision descriptions ~500-2000 tokens | Smart truncation (80 element cap) |

**RAM Budget (M4 16GB):**
- Qwen 3.5-9B 4-bit: ~5GB
- Edge browser: ~2-3GB
- Vision overhead: <100MB (screenshots go to disk)
- Available headroom: ~6-8GB

---

## Limitations

1. **Text-only model:** Qwen 3.5-9B cannot see screenshots. It works from structured
   text descriptions (ARIA tree, window list). Accuracy depends on how well the ARIA
   tree represents the page. Canvas elements, image-only content, and custom WebGL
   UIs will have poor or no text descriptions.

2. **Coordinate estimation:** The model estimates click positions from element
   descriptions. For small or closely-spaced elements, precision may be insufficient.
   Tolerance is approximately +/-50 on the 1000-unit scale.

3. **Desktop interaction:** macOS Accessibility permissions must be granted to
   Terminal.app for AppleScript automation (System Preferences > Privacy & Security >
   Accessibility).

4. **No OCR:** The current system does not perform OCR on screenshots. Text visible
   only in images (not in the DOM/ARIA tree) will not be available to the model.

5. **Vision mode is per-session:** Vision mode resets to OFF when Amber restarts.
   There is no persistent vision setting.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "vision_snapshot failed" | Check if Edge browser is running. Try regular `snapshot` as fallback. |
| Clicks miss target | Take new `vision_snapshot`, verify coordinates. Adjust by +/-50. Fall back to `click` with `#N` ref. |
| screen_capture permission denied | Grant Terminal Accessibility access in System Preferences. |
| No elements in vision_snapshot | Page may use canvas/images. Try `read` or `select_text` instead. |
| Vision mode not activating | Say `use vision` or `vision on` (exact phrases). Check ai.py detection regex. |
