#!/usr/bin/env python3
"""
preview.py — macOS Preview.app controller for the AI agent.

Opens, reads, navigates, and interacts with PDFs and images in Preview.app.
Uses AppleScript + System Events accessibility for UI control, Python for
text extraction (Quartz/pdftotext/textutil), and Vision framework for OCR.
"""

import sys, subprocess, json, argparse, time, os, re, tempfile, datetime
from typing import Dict, Any, List, Tuple, Optional

# --- TOOL METADATA ---
TOOL_METADATA = {
    "name": "preview",
    "description": (
        "macOS Preview.app controller — open, read, navigate, and interact with PDFs and images.\n"
        "Modes:\n"
        "  open      → Open file in Preview (new window). Args: file_path\n"
        "  list      → List all open Preview documents/windows\n"
        "  info      → File metadata (pages, dimensions, size). Args: file_path or uses current doc\n"
        "  read      → Extract text from PDF (or OCR image). Args: file_path, value='3' for page 3\n"
        "  navigate  → Go to page N in PDF. Args: value='5'\n"
        "  search    → Find text in document. Args: query='search term'\n"
        "  export    → Convert file format. Args: value='png', file_path='/output/path'\n"
        "  close     → Close front document\n"
        "UI INTERACTION:\n"
        "  snapshot  → Screenshot + UI elements with #N refs and @x,y coords\n"
        "  click     → Click element by #N ref or text label\n"
        "  click_at  → Bezier click at screen coords (value='x,y')\n"
        "  scroll    → Scroll document (value: up/down/top/bottom)\n"
        "  zoom      → Zoom (value: in/out/fit/actual)"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["open", "list", "info", "read", "navigate", "search",
                         "export", "close", "snapshot", "click", "click_at",
                         "scroll", "zoom"],
                "default": "open",
                "description": "Action to perform"
            },
            "file_path": {
                "type": "string",
                "description": "File path to open, read, get info, or export output path"
            },
            "value": {
                "type": "string",
                "description": "Page number (navigate), format (export), direction (scroll/zoom), coordinates (click_at)"
            },
            "query": {
                "type": "string",
                "description": "Search text for search mode"
            },
            "text": {
                "type": "string",
                "description": "Element ref #N or text label for click mode"
            }
        },
        "required": ["mode"]
    }
}

SCREENSHOT_DIR = os.path.expanduser("~/Developer/llm/Vision_artifacts")

# Persist window ID and ref map across invocations (each call is a new process)
_WINDOW_ID_FILE = os.path.join(tempfile.gettempdir(), "preview_launched_window_id")
_REF_MAP_FILE = os.path.join(tempfile.gettempdir(), "preview_ref_map.json")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def run_applescript(script: str, timeout: int = 30) -> Tuple[str, str]:
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return "", f"Error: {e}"

def _osa(script: str, timeout: int = 30) -> str:
    out, _ = run_applescript(script, timeout)
    return out

def _osa_jxa(script: str, timeout: int = 15) -> str:
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR:{e}"

def escape_applescript(s: str) -> str:
    return str(s or "").replace("\\", "\\\\").replace('"', '\\"')

def _resolve_path(file_path: str) -> Optional[str]:
    if not file_path:
        return None
    p = os.path.expanduser(file_path.strip())
    p = os.path.abspath(p)
    if os.path.isfile(p):
        return p
    name = os.path.basename(file_path)
    for d in ("Desktop", "Downloads", "Documents", "Developer"):
        candidate = os.path.join(os.path.expanduser("~"), d, name)
        if os.path.isfile(candidate):
            return candidate
    return p

def _is_preview_running() -> bool:
    out, _ = run_applescript(
        'tell application "System Events" to return (name of processes) contains "Preview"'
    )
    return out.lower() == "true"

def _get_preview_window_ids() -> List[int]:
    out, _ = run_applescript(
        'tell application "Preview" to return id of every window'
    )
    if not out:
        return []
    try:
        return [int(x.strip()) for x in out.split(",") if x.strip()]
    except (ValueError, TypeError):
        return []

def _get_launched_window_id() -> Optional[int]:
    try:
        with open(_WINDOW_ID_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError, TypeError):
        return None

def _set_launched_window_id(wid: int):
    try:
        with open(_WINDOW_ID_FILE, "w") as f:
            f.write(str(wid))
    except Exception:
        pass

def _save_ref_map(ref_map: dict):
    try:
        with open(_REF_MAP_FILE, "w") as f:
            json.dump(ref_map, f)
    except Exception:
        pass

def _load_ref_map() -> Dict[int, dict]:
    try:
        with open(_REF_MAP_FILE, "r") as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {}

def _get_current_doc_path() -> Optional[str]:
    out = _osa('''
        tell application "Preview"
            if (count of documents) > 0 then
                return path of front document
            end if
        end tell
    ''', 5)
    if out and os.path.exists(out):
        return out
    return None

def _get_preview_cg_window_id() -> Optional[str]:
    """Get CGWindowID for Preview's front window (needed for screencapture -l)."""
    result = _osa_jxa('''ObjC.import('CoreGraphics');
ObjC.import('Foundation');
var list = ObjC.deepUnwrap(
  $.CGWindowListCopyWindowInfo(
    $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, 0));
var result = "";
for (var i = 0; i < list.length; i++) {
  if (list[i].kCGWindowOwnerName === "Preview" && list[i].kCGWindowLayer === 0) {
    result = String(list[i].kCGWindowNumber);
    break;
  }
}
result;''')
    if result and result.isdigit():
        return result
    return None


# ─── Bezier Click (same pattern as email.py) ─────────────────────────────────

def _bezier_click_at(x: int, y: int, double: bool = False) -> dict:
    dbl = ""
    if double:
        dbl = (
            "var d2=$.CGEventCreateMouseEvent($(),$.kCGEventLeftMouseDown,point,$.kCGMouseButtonLeft);\n"
            "$.CGEventSetIntegerValueField(d2,$.kCGMouseEventClickState,2);\n"
            "$.CGEventPost($.kCGSessionEventTap,d2);delay(0.03);\n"
            "var u2=$.CGEventCreateMouseEvent($(),$.kCGEventLeftMouseUp,point,$.kCGMouseButtonLeft);\n"
            "$.CGEventSetIntegerValueField(u2,$.kCGMouseEventClickState,2);\n"
            "$.CGEventPost($.kCGSessionEventTap,u2);\n"
        )
    result = _osa_jxa(f'''ObjC.import('CoreGraphics');
var curEvt=$.CGEventCreate($());var curPos=$.CGEventGetLocation(curEvt);
var sx=curPos.x,sy=curPos.y,tx={x},ty={y};var steps=15;
var cp1x=sx+(tx-sx)*0.3+(Math.random()*30-15);
var cp1y=sy+(ty-sy)*0.1+(Math.random()*20-10);
var cp2x=sx+(tx-sx)*0.7+(Math.random()*20-10);
var cp2y=sy+(ty-sy)*0.9+(Math.random()*15-7);
for(var i=1;i<=steps;i++){{var t=i/steps,u=1-t;
var bx=u*u*u*sx+3*u*u*t*cp1x+3*u*t*t*cp2x+t*t*t*tx;
var by=u*u*u*sy+3*u*u*t*cp1y+3*u*t*t*cp2y+t*t*t*ty;
var mv=$.CGEventCreateMouseEvent($(),$.kCGEventMouseMoved,$.CGPointMake(bx,by),0);
$.CGEventPost($.kCGSessionEventTap,mv);delay(0.012);}}
var point=$.CGPointMake(tx,ty);
var down=$.CGEventCreateMouseEvent($(),$.kCGEventLeftMouseDown,point,$.kCGMouseButtonLeft);
$.CGEventPost($.kCGSessionEventTap,down);delay(0.05);
var up=$.CGEventCreateMouseEvent($(),$.kCGEventLeftMouseUp,point,$.kCGMouseButtonLeft);
$.CGEventPost($.kCGSessionEventTap,up);{dbl}"CLICKED";''')
    ok = "CLICKED" in result or "ERROR" not in result
    if ok:
        return {"success": True, "message": f"Clicked at {x},{y}"}
    _osa(f'tell application "System Events" to click at {{{x}, {y}}}')
    return {"success": True, "message": f"Clicked at {x},{y} (fallback)"}


# ─── File Metadata ────────────────────────────────────────────────────────────

def _quick_file_info(file_path: str) -> dict:
    info = {}
    try:
        stat = os.stat(file_path)
        info["file_size"] = f"{stat.st_size:,} bytes"
        info["file_name"] = os.path.basename(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        info["file_type"] = ext.lstrip(".")

        result = subprocess.run(
            ["mdls", "-name", "kMDItemNumberOfPages",
             "-name", "kMDItemPixelWidth", "-name", "kMDItemPixelHeight",
             "-name", "kMDItemContentType", "-name", "kMDItemTitle",
             "-name", "kMDItemPageWidth", "-name", "kMDItemPageHeight",
             file_path],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "= " in line and "(null)" not in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"')
                    if key == "kMDItemNumberOfPages":
                        try: info["pages"] = int(val)
                        except ValueError: pass
                    elif key == "kMDItemPixelWidth":
                        try: info["width"] = int(val)
                        except ValueError: pass
                    elif key == "kMDItemPixelHeight":
                        try: info["height"] = int(val)
                        except ValueError: pass
                    elif key == "kMDItemContentType":
                        info["content_type"] = val
                    elif key == "kMDItemTitle":
                        info["title"] = val
                    elif key == "kMDItemPageWidth":
                        try: info["page_width"] = round(float(val), 1)
                        except ValueError: pass
                    elif key == "kMDItemPageHeight":
                        try: info["page_height"] = round(float(val), 1)
                        except ValueError: pass

        if "width" not in info and ext in (".png", ".jpg", ".jpeg", ".gif",
                                            ".tiff", ".bmp", ".heic", ".webp"):
            result = subprocess.run(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", file_path],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split("\n"):
                if "pixelWidth" in line:
                    try: info["width"] = int(line.split(":")[-1].strip())
                    except ValueError: pass
                elif "pixelHeight" in line:
                    try: info["height"] = int(line.split(":")[-1].strip())
                    except ValueError: pass
    except Exception:
        pass
    return info


# ─── Text Extraction ─────────────────────────────────────────────────────────

def _extract_pdf_text_jxa(file_path: str, page: int = None) -> Optional[str]:
    """Extract text from PDF using JXA + PDFKit (native macOS, no deps)."""
    escaped = file_path.replace("\\", "\\\\").replace("'", "\\'")
    if page is not None:
        script = f'''ObjC.import('Quartz');
ObjC.import('Foundation');
var url = $.NSURL.fileURLWithPath('{escaped}');
var doc = $.PDFDocument.alloc.initWithURL(url);
if (!doc) {{ "ERROR:Could not load PDF"; }} else {{
  var pc = doc.pageCount;
  var idx = {page - 1};
  if (idx < 0 || idx >= pc) {{ "RANGE:" + pc; }}
  else {{
    var pg = doc.pageAtIndex(idx);
    var s = pg ? pg.string : null;
    "PAGE:" + pc + ":" + (s ? s.js : "");
  }}
}}'''
    else:
        script = f'''ObjC.import('Quartz');
ObjC.import('Foundation');
var url = $.NSURL.fileURLWithPath('{escaped}');
var doc = $.PDFDocument.alloc.initWithURL(url);
if (!doc) {{ "ERROR:Could not load PDF"; }} else {{
  var pc = doc.pageCount;
  var lim = Math.min(pc, 50);
  var parts = [];
  for (var i = 0; i < lim; i++) {{
    var pg = doc.pageAtIndex(i);
    if (pg) {{
      var s = pg.string;
      parts.push("--- Page " + (i+1) + "/" + pc + " ---\\n" + (s ? s.js : ""));
    }}
  }}
  parts.join("\\n\\n");
}}'''
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True, text=True, timeout=60
        )
        out = result.stdout.strip()
        if result.returncode == 0 and out and not out.startswith("ERROR:"):
            if page is not None:
                if out.startswith("RANGE:"):
                    pc = out.split(":")[1]
                    return f"Page {page} out of range (document has {pc} pages)"
                if out.startswith("PAGE:"):
                    parts = out.split(":", 2)
                    pc = parts[1]
                    text = parts[2] if len(parts) > 2 else ""
                    if text.strip():
                        return f"--- Page {page}/{pc} ---\n{text}"
                    return None  # no text on page, fall through to OCR
            else:
                if out.strip():
                    return out
    except Exception:
        pass
    return None


def _extract_pdf_text_ocr(file_path: str, page: int = None) -> Optional[str]:
    """OCR a PDF by rendering pages to images and running Vision framework.
    Native macOS — no dependencies required."""
    escaped = file_path.replace("\\", "\\\\").replace("'", "\\'")
    if page is not None:
        first_page = page - 1
        last_page = page
    else:
        first_page = 0
        last_page = -1  # sentinel: script will use min(pageCount, 10)
    script = f'''ObjC.import('Quartz');
ObjC.import('Foundation');
ObjC.import('Vision');
ObjC.import('AppKit');
var url = $.NSURL.fileURLWithPath('{escaped}');
var doc = $.PDFDocument.alloc.initWithURL(url);
if (!doc) {{ "ERROR:Could not load PDF"; }} else {{
  var pc = doc.pageCount;
  var first = {first_page};
  var last = {last_page};
  if (last < 0) last = Math.min(pc, 10);
  if (first < 0 || first >= pc) {{ "RANGE:" + pc; }}
  else {{
    var parts = [];
    for (var i = first; i < Math.min(last, pc); i++) {{
      var pg = doc.pageAtIndex(i);
      if (!pg) continue;
      var bounds = pg.boundsForBox($.kPDFDisplayBoxMediaBox);
      var w = bounds.size.width * 2;
      var h = bounds.size.height * 2;
      var cs = $.CGColorSpaceCreateDeviceRGB();
      var ctx = $.CGBitmapContextCreate($(), w, h, 8, w * 4, cs, 1);
      $.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1);
      $.CGContextFillRect(ctx, $.CGRectMake(0, 0, w, h));
      $.CGContextScaleCTM(ctx, 2, 2);
      pg.drawWithBoxToContext($.kPDFDisplayBoxMediaBox, ctx);
      var cgImg = $.CGBitmapContextCreateImage(ctx);
      if (!cgImg) continue;
      var handler = $.VNImageRequestHandler.alloc.initWithCGImageOptions(cgImg, $());
      var request = $.VNRecognizeTextRequest.alloc.init;
      request.recognitionLevel = 1;
      handler.performRequestsError([request], $());
      var results = request.results;
      var lines = [];
      for (var j = 0; j < results.count; j++) {{
        var obs = results.objectAtIndex(j);
        var cands = obs.topCandidates(1);
        if (cands.count > 0) lines.push(cands.objectAtIndex(0).string.js);
      }}
      if (lines.length > 0) parts.push("--- Page " + (i+1) + "/" + pc + " ---\\n" + lines.join("\\n"));
    }}
    parts.join("\\n\\n");
  }}
}}'''
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True, text=True, timeout=120
        )
        out = result.stdout.strip()
        if result.returncode == 0 and out and not out.startswith("ERROR:"):
            if out.startswith("RANGE:"):
                pc = out.split(":")[1]
                return f"Page {page} out of range (document has {pc} pages)"
            if out.strip():
                return out
    except Exception:
        pass
    return None


def _extract_pdf_text(file_path: str, page: int = None) -> Optional[str]:
    """Extract text from PDF. Tries: PyObjC → JXA/PDFKit → pdftotext → JXA/Vision OCR."""
    # Method 1: Quartz (PyObjC) — works if PyObjC is installed in venv
    try:
        from Quartz import PDFDocument
        from Foundation import NSURL
        url = NSURL.fileURLWithPath_(file_path)
        doc = PDFDocument.alloc().initWithURL_(url)
        if doc:
            pc = doc.pageCount()
            if page is not None:
                if 1 <= page <= pc:
                    pg = doc.pageAtIndex_(page - 1)
                    text = pg.string() if pg else ""
                    return f"--- Page {page}/{pc} ---\n{text}" if text else None
                return f"Page {page} out of range (document has {pc} pages)"
            chunks = []
            for i in range(min(pc, 50)):
                pg = doc.pageAtIndex_(i)
                if pg and pg.string():
                    chunks.append(f"--- Page {i+1}/{pc} ---\n{pg.string()}")
            if chunks:
                return "\n\n".join(chunks)
    except ImportError:
        pass
    except Exception:
        pass

    # Method 2: JXA + PDFKit (native macOS, no deps)
    text = _extract_pdf_text_jxa(file_path, page)
    if text:
        return text

    # Method 3: pdftotext (poppler via Homebrew)
    try:
        args = ["pdftotext", "-layout"]
        if page:
            args += ["-f", str(page), "-l", str(page)]
        args += [file_path, "-"]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except FileNotFoundError:
        pass

    # Method 4: Vision OCR (renders PDF pages to images, then OCRs — for scanned PDFs)
    text = _extract_pdf_text_ocr(file_path, page)
    if text:
        return text

    return None


def _extract_doc_text(file_path: str) -> Optional[str]:
    """Extract text from rich text formats via textutil."""
    try:
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", file_path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except Exception:
        pass
    return None


def _extract_image_text(file_path: str) -> Optional[str]:
    """OCR an image using macOS Vision framework via JXA."""
    escaped = file_path.replace("'", "\\'")
    script = f'''ObjC.import('Vision');
ObjC.import('AppKit');
var img = $.NSImage.alloc.initWithContentsOfFile('{escaped}');
if (!img) {{ "ERROR:Could not load image"; }} else {{
var tiffData = img.TIFFRepresentation;
var bitmap = $.NSBitmapImageRep.imageRepWithData(tiffData);
var cgImg = bitmap.CGImage;
var handler = $.VNImageRequestHandler.alloc.initWithCGImageOptions(cgImg, $());
var request = $.VNRecognizeTextRequest.alloc.init;
request.recognitionLevel = 1;
handler.performRequestsError([request], $());
var results = request.results;
var texts = [];
for (var i = 0; i < results.count; i++) {{
  var obs = results.objectAtIndex(i);
  var cands = obs.topCandidates(1);
  if (cands.count > 0) texts.push(cands.objectAtIndex(0).string.js);
}}
texts.join("\\n");
}}'''
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True, text=True, timeout=30
        )
        text = result.stdout.strip()
        if text and "ERROR" not in text:
            return text
    except Exception:
        pass
    return None


# ─── Core Functions ───────────────────────────────────────────────────────────

def open_file(file_path: str) -> dict:
    resolved = _resolve_path(file_path)
    if not resolved or not os.path.isfile(resolved):
        return {"success": False, "message": f"File not found: {file_path}"}

    was_running = _is_preview_running()
    ids_before = _get_preview_window_ids() if was_running else []

    escaped = escape_applescript(resolved)
    run_applescript(f'''
        tell application "Preview"
            activate
            open POSIX file "{escaped}"
        end tell
    ''', 15)

    time.sleep(1.0)
    ids_after = _get_preview_window_ids()
    new_ids = [wid for wid in ids_after if wid not in ids_before]

    wid = None
    if new_ids:
        wid = new_ids[0]
    elif ids_after:
        wid = ids_after[0]
    if wid:
        _set_launched_window_id(wid)

    info = _quick_file_info(resolved)
    return {
        "success": True,
        "message": f"Opened in Preview: {os.path.basename(resolved)}",
        "file_path": resolved,
        "window_id": wid,
        **info
    }


def list_documents() -> dict:
    doc_out = _osa('''
        tell application "Preview"
            set docList to ""
            repeat with d in documents
                set docList to docList & name of d & "|||" & (path of d) & linefeed
            end repeat
            return docList
        end tell
    ''', 10)

    docs = []
    if doc_out:
        for line in doc_out.split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("|||")
            name = parts[0] if parts else "Unknown"
            path = parts[1] if len(parts) > 1 else ""
            docs.append({"name": name, "path": path})

    win_out = _osa('''
        tell application "Preview"
            set winList to ""
            repeat with w in windows
                set winList to winList & (id of w as text) & "|||" & name of w & linefeed
            end repeat
            return winList
        end tell
    ''', 10)

    windows = []
    if win_out:
        for line in win_out.split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("|||")
            if len(parts) >= 2:
                wid = int(parts[0]) if parts[0].isdigit() else 0
                windows.append({"id": wid, "title": parts[1]})

    return {
        "success": True,
        "documents": docs,
        "windows": windows,
        "count": len(docs),
        "message": f"{len(docs)} document(s) open in Preview"
    }


def get_file_info(file_path: str = None) -> dict:
    if not file_path:
        file_path = _get_current_doc_path()
    if not file_path:
        return {"success": False, "message": "No file path and no document open in Preview"}
    resolved = _resolve_path(file_path)
    if not resolved or not os.path.isfile(resolved):
        return {"success": False, "message": f"File not found: {file_path}"}
    info = _quick_file_info(resolved)
    info["success"] = True
    info["file_path"] = resolved
    info["message"] = f"Info for {os.path.basename(resolved)}"
    return info


def read_content(file_path: str = None, page: int = None) -> dict:
    if not file_path:
        file_path = _get_current_doc_path()
    if not file_path:
        return {"success": False, "message": "No file specified and no document open"}
    resolved = _resolve_path(file_path)
    if not resolved or not os.path.isfile(resolved):
        return {"success": False, "message": f"File not found: {file_path}"}

    ext = os.path.splitext(resolved)[1].lower()
    info = _quick_file_info(resolved)

    # PDF
    if ext == ".pdf":
        text = _extract_pdf_text(resolved, page)
        if text:
            return {"success": True, "text": text,
                    "message": f"Extracted text from {os.path.basename(resolved)}", **info}
        return {"success": False,
                "message": "Could not extract text from PDF (may be image-only or protected). Use snapshot mode to view visually.",
                **info}

    # Images — OCR
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".tiff", ".bmp", ".heic", ".webp"):
        text = _extract_image_text(resolved)
        if text:
            return {"success": True, "text": text,
                    "message": f"OCR text from {os.path.basename(resolved)}", **info}
        return {"success": True, "text": "",
                "message": "Image loaded. No text detected via OCR. Use snapshot mode to view.",
                **info}

    # Rich text docs
    if ext in (".rtf", ".doc", ".docx", ".rtfd", ".odt"):
        text = _extract_doc_text(resolved)
        if text:
            return {"success": True, "text": text,
                    "message": f"Read {os.path.basename(resolved)}", **info}

    # Plain text
    if ext in (".txt", ".md", ".csv", ".json", ".xml", ".html", ".log"):
        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(100000)
            return {"success": True, "text": text,
                    "message": f"Read {os.path.basename(resolved)}", **info}
        except Exception:
            pass

    return {"success": False, "message": f"Cannot extract text from {ext} files. Use snapshot mode.",
            **info}


def navigate_to_page(page_num: int) -> dict:
    _osa('tell application "Preview" to activate')
    time.sleep(0.2)

    # Option+Cmd+G = Go to Page in Preview
    result = _osa(f'''
        tell application "System Events"
            tell process "Preview"
                keystroke "g" using {{option down, command down}}
                delay 0.5
                keystroke "{page_num}"
                delay 0.2
                keystroke return
            end tell
        end tell
    ''', 10)

    return {"success": True, "message": f"Navigated to page {page_num}"}


def search_text(query: str) -> dict:
    _osa('tell application "Preview" to activate')
    time.sleep(0.2)
    escaped = escape_applescript(query)
    _osa(f'''
        tell application "System Events"
            tell process "Preview"
                keystroke "f" using command down
                delay 0.5
                keystroke "{escaped}"
                delay 0.3
                keystroke return
            end tell
        end tell
    ''', 10)
    return {"success": True, "message": f"Searching for '{query}' in Preview"}


def export_file(fmt: str, output_path: str = None) -> dict:
    src_path = _get_current_doc_path()
    if not src_path:
        return {"success": False, "message": "No document open in Preview to export"}

    fmt = fmt.lower().strip(".")
    if not output_path:
        base = os.path.splitext(os.path.basename(src_path))[0]
        output_path = os.path.join(SCREENSHOT_DIR, f"{base}_export.{fmt}")
    output_path = os.path.expanduser(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # sips for image/PDF conversion
    if fmt in ("png", "jpg", "jpeg", "tiff", "gif", "bmp", "pdf"):
        sips_fmt = {"jpg": "jpeg", "tif": "tiff"}.get(fmt, fmt)
        try:
            result = subprocess.run(
                ["sips", "-s", "format", sips_fmt, src_path, "--out", output_path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and os.path.isfile(output_path):
                return {"success": True, "output_path": output_path,
                        "message": f"Exported to {fmt}: {output_path}",
                        "file_size": f"{os.path.getsize(output_path):,} bytes"}
        except Exception:
            pass

    # textutil for text format conversion
    if fmt in ("txt", "html", "rtf", "doc", "docx"):
        try:
            result = subprocess.run(
                ["textutil", "-convert", fmt, "-output", output_path, src_path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and os.path.isfile(output_path):
                return {"success": True, "output_path": output_path,
                        "message": f"Exported to {fmt}: {output_path}"}
        except Exception:
            pass

    return {"success": False, "message": f"Could not export to {fmt}. Format may not be supported."}


def close_document() -> dict:
    result = _osa('''
        tell application "Preview"
            if (count of documents) > 0 then
                set docName to name of front document
                close front document
                return "Closed: " & docName
            else
                return "NO_DOC"
            end if
        end tell
    ''', 10)
    if "NO_DOC" in result:
        return {"success": False, "message": "No document open to close"}
    return {"success": True, "message": result}


# ─── UI Snapshot ──────────────────────────────────────────────────────────────

def preview_snapshot() -> dict:
    ref_map = {}
    ref_n = 1

    _osa('tell application "Preview" to activate')
    time.sleep(0.3)

    win = _osa('''tell application "System Events"
        tell process "Preview"
            if (count of windows) = 0 then return "NO_WINDOW"
            return title of window 1
        end tell
    end tell''')
    if "NO_WINDOW" in win or "ERROR" in win:
        return {"success": False, "message": "Preview window not found. Open a file first."}

    output = f"=== PREVIEW APP ===\nDocument: {win}\n"

    # Screenshot of Preview window
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_path = os.path.join(SCREENSHOT_DIR, f"preview_snap_{ts}.jpg")

    cg_wid = _get_preview_cg_window_id()
    try:
        if cg_wid:
            subprocess.run(["screencapture", "-x", "-t", "jpg", "-l", cg_wid, snap_path],
                           timeout=10, capture_output=True)
        else:
            subprocess.run(["screencapture", "-x", "-t", "jpg", snap_path],
                           timeout=10, capture_output=True)
    except Exception:
        subprocess.run(["screencapture", "-x", "-t", "jpg", snap_path],
                       timeout=10, capture_output=True)

    if os.path.isfile(snap_path):
        output += f"Screenshot: {snap_path}\n"

    # Toolbar buttons
    buttons = _osa('''tell application "System Events"
        tell process "Preview"
            tell window 1
                set output to ""
                try
                    repeat with tb in (every toolbar)
                        try
                            repeat with b in (every button of tb)
                                try
                                    set bLabel to ""
                                    try
                                        set bLabel to title of b
                                    end try
                                    if bLabel is "" or bLabel is missing value then
                                        try
                                            set bLabel to description of b
                                        end try
                                    end if
                                    if bLabel is not "" and bLabel is not missing value then
                                        set {bx, by} to position of b
                                        set output to output & bLabel & "@@" & bx & "," & by & linefeed
                                    end if
                                end try
                            end repeat
                            repeat with g in (every group of tb)
                                try
                                    repeat with b in (every button of g)
                                        try
                                            set bLabel to ""
                                            try
                                                set bLabel to title of b
                                            end try
                                            if bLabel is "" or bLabel is missing value then
                                                try
                                                    set bLabel to description of b
                                                end try
                                            end if
                                            if bLabel is not "" and bLabel is not missing value then
                                                set {bx, by} to position of b
                                                set output to output & bLabel & "@@" & bx & "," & by & linefeed
                                            end if
                                        end try
                                    end repeat
                                end try
                            end repeat
                        end try
                    end repeat
                end try
                return output
            end tell
        end tell
    end tell''', 10)

    if buttons and "ERROR" not in buttons:
        output += "\n=== TOOLBAR ===\n"
        seen = set()
        for line in buttons.replace('\r', '\n').split('\n'):
            line = line.strip()
            if not line or line in seen:
                continue
            seen.add(line)
            pos = ""
            if '@@' in line:
                line, pos = line.rsplit('@@', 1)
            if line.strip():
                entry = f"[{ref_n}] button \"{line.strip()}\""
                if pos:
                    entry += f" @{pos}"
                output += entry + "\n"
                ref_map[ref_n] = {"type": "button", "label": line.strip(), "position": pos}
                ref_n += 1

    # Sidebar (PDF page thumbnails / table of contents)
    sidebar = _osa('''tell application "System Events"
        tell process "Preview"
            tell window 1
                set output to ""
                try
                    repeat with sa in (every scroll area)
                        try
                            repeat with ol in (every outline of sa)
                                try
                                    repeat with r in (every row of ol)
                                        try
                                            set rText to ""
                                            repeat with c in (every static text of r)
                                                try
                                                    set v to value of c
                                                    if v is not missing value and v is not "" then
                                                        set rText to rText & v & " "
                                                    end if
                                                end try
                                            end repeat
                                            if rText is not "" then
                                                set {rx, ry} to position of r
                                                set sel to selected of r
                                                set prefix to ""
                                                if sel then set prefix to ">"
                                                set output to output & prefix & rText & "@@" & rx & "," & ry & linefeed
                                            end if
                                        end try
                                    end repeat
                                end try
                            end repeat
                        end try
                        try
                            set imgCount to count of (every image of sa)
                            if imgCount > 0 then
                                set output to output & "THUMBNAILS:" & imgCount & linefeed
                            end if
                        end try
                    end repeat
                end try
                return output
            end tell
        end tell
    end tell''', 10)

    if sidebar and "ERROR" not in sidebar:
        has_content = False
        for line in sidebar.replace('\r', '\n').split('\n'):
            line = line.strip()
            if not line:
                continue
            if not has_content:
                output += "\n=== SIDEBAR ===\n"
                has_content = True
            if line.startswith("THUMBNAILS:"):
                count = line.split(":")[1] if ":" in line else "?"
                output += f"  ({count} page thumbnails)\n"
                continue
            pos = ""
            if '@@' in line:
                line, pos = line.rsplit('@@', 1)
            selected = line.startswith('>')
            if selected:
                line = line[1:]
            marker = " [CURRENT]" if selected else ""
            entry = f"[{ref_n}] {line.strip()}{marker}"
            if pos:
                entry += f" @{pos}"
            output += entry + "\n"
            ref_map[ref_n] = {"type": "sidebar", "text": line.strip(), "position": pos}
            ref_n += 1

    # Visible text content in the document area
    content = _osa('''tell application "System Events"
        tell process "Preview"
            tell window 1
                set output to ""
                try
                    repeat with sg in (every splitter group)
                        try
                            repeat with sa in (every scroll area of sg)
                                try
                                    repeat with t in (every static text of entire contents of sa)
                                        try
                                            set v to value of t
                                            if v is not missing value and v is not "" and (length of v) > 1 then
                                                set {tx, ty} to position of t
                                                set output to output & v & "@@" & tx & "," & ty & linefeed
                                            end if
                                        end try
                                    end repeat
                                end try
                            end repeat
                        end try
                    end repeat
                end try
                -- Also check direct scroll areas (non-splitter layout)
                try
                    repeat with sa in (every scroll area)
                        try
                            repeat with t in (every static text of entire contents of sa)
                                try
                                    set v to value of t
                                    if v is not missing value and v is not "" and (length of v) > 1 then
                                        if output does not contain v then
                                            set {tx, ty} to position of t
                                            set output to output & v & "@@" & tx & "," & ty & linefeed
                                        end if
                                    end if
                                end try
                            end repeat
                        end try
                    end repeat
                end try
                return output
            end tell
        end tell
    end tell''', 15)

    if content and "ERROR" not in content:
        output += "\n=== VISIBLE TEXT ===\n"
        for line in content.replace('\r', '\n').split('\n'):
            line = line.strip()
            if not line:
                continue
            pos = ""
            if '@@' in line:
                line, pos = line.rsplit('@@', 1)
            if line.strip():
                entry = f"[{ref_n}] {line.strip()[:200]}"
                if pos:
                    entry += f" @{pos}"
                output += entry + "\n"
                ref_map[ref_n] = {"type": "text", "text": line.strip(), "position": pos}
                ref_n += 1

    # Menus (key actions)
    output += "\n=== KEY SHORTCUTS ===\n"
    output += "  Cmd+F = Search | Opt+Cmd+G = Go to Page\n"
    output += "  Cmd++ = Zoom In | Cmd+- = Zoom Out | Cmd+0 = Zoom to Fit\n"
    output += "  Arrow keys = Scroll | Page Up/Down = Navigate pages\n"

    # Persist ref map for cross-invocation click support
    _save_ref_map(ref_map)

    return {
        "success": True,
        "snapshot": output,
        "refs": ref_n - 1,
        "screenshot_path": snap_path if os.path.isfile(snap_path) else None
    }


# ─── Click ────────────────────────────────────────────────────────────────────

def preview_click(ref_or_text: str) -> dict:
    ref_map = _load_ref_map()

    _osa('tell application "Preview" to activate')
    time.sleep(0.2)

    ref_match = re.match(r'#?(\d+)$', ref_or_text.strip())
    if ref_match:
        ref_n = int(ref_match.group(1))
        if ref_n not in ref_map:
            return {"success": False, "message": f"Ref #{ref_n} not found. Take snapshot first."}
        elem = ref_map[ref_n]

        pos = elem.get('position', '')
        if pos and ',' in pos:
            try:
                x, y = pos.split(',', 1)
                return _bezier_click_at(int(x.strip()), int(y.strip()))
            except ValueError:
                pass

        label = elem.get('label') or elem.get('text', '')[:50]
        if label:
            return _click_preview_text(label)
        return {"success": False, "message": f"Cannot click ref #{ref_n}"}

    return _click_preview_text(ref_or_text)


def _click_preview_text(text: str) -> dict:
    safe = escape_applescript(text)
    result = _osa(f'''tell application "Preview" to activate
delay 0.2
tell application "System Events"
    tell process "Preview"
        tell window 1
            set foundIt to false
            try
                click button "{safe}"
                set foundIt to true
            end try
            if not foundIt then
                try
                    repeat with b in (every button of entire contents)
                        try
                            if (title of b) contains "{safe}" or (description of b) contains "{safe}" then
                                click b
                                set foundIt to true
                                exit repeat
                            end if
                        end try
                    end repeat
                end try
            end if
            if not foundIt then
                try
                    repeat with t in (every static text of entire contents)
                        try
                            if (value of t) contains "{safe}" then
                                click t
                                set foundIt to true
                                exit repeat
                            end if
                        end try
                    end repeat
                end try
            end if
            if not foundIt then
                try
                    repeat with el in (every UI element of entire contents)
                        try
                            if (description of el) contains "{safe}" then
                                click el
                                set foundIt to true
                                exit repeat
                            end if
                        end try
                    end repeat
                end try
            end if
            return foundIt
        end tell
    end tell
end tell''', 15)
    if "true" in result.lower():
        return {"success": True, "message": f"Clicked '{text}'"}
    return {"success": False, "message": f"Element '{text}' not found in Preview UI"}


# ─── Scroll / Zoom ────────────────────────────────────────────────────────────

def preview_scroll(direction: str = "down") -> dict:
    _osa('tell application "Preview" to activate')
    time.sleep(0.2)
    direction = direction.lower().strip()

    if direction in ("top", "bottom"):
        key = 115 if direction == "top" else 119
        _osa(f'''tell application "System Events"
            tell process "Preview"
                key code {key} using command down
            end tell
        end tell''')
    elif direction in ("left", "right"):
        key = 123 if direction == "left" else 124
        _osa(f'''tell application "System Events"
            tell process "Preview"
                repeat 5 times
                    key code {key}
                    delay 0.05
                end repeat
            end tell
        end tell''')
    else:
        # up/down — page scroll
        key = 116 if direction == "up" else 121
        _osa(f'''tell application "System Events"
            tell process "Preview"
                key code {key}
            end tell
        end tell''')

    return {"success": True, "message": f"Scrolled {direction}"}


def preview_zoom(direction: str = "in") -> dict:
    _osa('tell application "Preview" to activate')
    time.sleep(0.2)
    direction = direction.lower().strip()

    shortcuts = {
        "in":     'keystroke "+" using command down',
        "out":    'keystroke "-" using command down',
        "fit":    'keystroke "0" using command down',
        "actual": 'keystroke "=" using command down',
    }
    cmd = shortcuts.get(direction)
    if not cmd:
        return {"success": False, "message": f"Unknown zoom: {direction}. Use: in, out, fit, actual"}

    _osa(f'''tell application "System Events"
        tell process "Preview"
            {cmd}
        end tell
    end tell''')
    return {"success": True, "message": f"Zoomed {direction}"}


# ─── Main Handler ─────────────────────────────────────────────────────────────

def handle_preview(data: Dict[str, Any]) -> str:
    mode = data.get("mode") or data.get("action", "open")
    file_path = data.get("file_path", "")
    value = data.get("value", "")
    query = data.get("query", "")
    text = data.get("text", "")

    result = {"success": False, "mode": mode, "message": ""}

    if mode == "open":
        if not file_path:
            result["message"] = "'file_path' required for open mode"
            return json.dumps(result, ensure_ascii=False, indent=2)
        result = open_file(file_path)

    elif mode == "list":
        if not _is_preview_running():
            result["message"] = "Preview is not running"
            return json.dumps(result, ensure_ascii=False, indent=2)
        result = list_documents()

    elif mode == "info":
        result = get_file_info(file_path or None)

    elif mode == "read":
        page = None
        if value:
            try:
                page = int(value)
            except ValueError:
                pass
        result = read_content(file_path or None, page)

    elif mode == "navigate":
        if not value:
            result["message"] = "'value' (page number) required"
            return json.dumps(result, ensure_ascii=False, indent=2)
        try:
            result = navigate_to_page(int(value))
        except ValueError:
            result["message"] = f"Invalid page number: {value}"
            return json.dumps(result, ensure_ascii=False, indent=2)

    elif mode == "search":
        if not query:
            result["message"] = "'query' required for search mode"
            return json.dumps(result, ensure_ascii=False, indent=2)
        result = search_text(query)

    elif mode == "export":
        if not value:
            result["message"] = "'value' (format: png, jpg, pdf, txt) required"
            return json.dumps(result, ensure_ascii=False, indent=2)
        result = export_file(value, file_path or None)

    elif mode == "close":
        result = close_document()

    elif mode == "snapshot":
        if not _is_preview_running():
            result["message"] = "Preview is not running. Open a file first."
            return json.dumps(result, ensure_ascii=False, indent=2)
        result = preview_snapshot()

    elif mode == "click":
        if not text:
            result["message"] = "'text' required for click (#N ref or element text)"
            return json.dumps(result, ensure_ascii=False, indent=2)
        result = preview_click(text)

    elif mode == "click_at":
        if ',' not in str(value):
            result["message"] = "'value' must be 'x,y' screen coordinates"
            return json.dumps(result, ensure_ascii=False, indent=2)
        try:
            x, y = str(value).split(',', 1)
            result = _bezier_click_at(int(x.strip()), int(y.strip()))
        except ValueError:
            result["message"] = f"Invalid coordinates: {value}"
            return json.dumps(result, ensure_ascii=False, indent=2)

    elif mode == "scroll":
        result = preview_scroll(value or "down")

    elif mode == "zoom":
        result = preview_zoom(value or "in")

    else:
        result["message"] = f"Unknown mode: {mode}"

    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    args = parser.parse_args()

    try:
        print(handle_preview(json.loads(args.json)))
    except Exception as e:
        print(json.dumps({"success": False, "message": f"Error: {str(e)}"}))
