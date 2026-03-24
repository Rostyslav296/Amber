#!/usr/bin/env python3
import sys
import subprocess
import json
import argparse
import time
import re
import os
import random
import subprocess as _sp
from textwrap import dedent
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

# --- TOOL METADATA ---
TOOL_METADATA = {
    "name": "macos_mail",
    "description": """macOS Mail.app controller — send, search, read, and manage emails.
Key modes:
• mode="search" → CRITICAL for email verification: search inbox by subject/sender query. Returns full email body + auto-extracted verification codes & links.
• mode="read" → read latest N emails with full body content
• mode="send" → instantly sends a new email (to, subject, body, cc, bcc, attachments)
• mode="send_batch" → BULK SEND: send same email to many recipients SEPARATELY (one email per address). Pass "to" as array of all addresses. Tracks sent/failed, handles hundreds. Returns summary with per-address status.
• mode="compose" → opens a new draft visible in Mail
• mode="summary" → executive summary with importance ranking
UI INTERACTION (for navigating Mail.app and clicking verification links):
• mode="snapshot" → see Mail.app UI (message list, selected email, controls with #N refs + @x,y coords)
• mode="click" → click elements in Mail by text or #N ref (5-strategy accessibility cascade)
• mode="click_at" → click at coordinates with Bézier mouse movement (value="x,y")
• mode="scroll" → scroll Mail.app message list (value: up/down/top/bottom)
• mode="hover" → hover over element at coordinates (value="x,y")
• mode="open_link" → open URL in Microsoft Edge browser (value: URL)
VERIFICATION: Use search → get verification_info → open link in edge or fill code in browser.""",
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["send", "send_batch", "compose", "reply", "forward", "search", "mark", "move", "read", "state", "summary", "snapshot", "click", "click_at", "scroll", "hover", "open_link"],
                "default": "send",
                "description": "Action. 'search' to find verification emails. 'read' for latest emails. 'send' to send one email. 'send_batch' to send same email to MANY recipients separately (pass 'to' as array)."
            },
            "to": {
                "type": ["string", "array"],
                "description": "Recipient email(s) — string or list",
                "items": {"type": "string"}
            },
            "subject": {"type": "string", "description": "Subject line"},
            "body": {"type": "string", "description": "Full email body"},
            "cc": {"type": ["string", "array"], "items": {"type": "string"}},
            "bcc": {"type": ["string", "array"], "items": {"type": "string"}},
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filenames or paths. Auto-searches Desktop/Downloads/Documents/~"
            },
            "message_id": {"type": "string", "description": "For reply/forward only"},
            "query": {"type": "string", "description": "Search query — matches subject and sender (for mode=search)"},
            "limit": {"type": "integer", "default": 10, "description": "Max results to return"},
            "mailbox": {"type": "string", "default": "inbox", "description": "Mailbox: inbox, junk, sent, trash"},
            "unread_only": {"type": "boolean", "default": False},
            "is_html": {"type": "boolean", "default": False},
            "text": {"type": "string", "description": "Element ref #N or text (for click mode)"},
            "value": {"type": "string", "description": "Direction for scroll (up/down), coordinates for click_at/hover (x,y), URL for open_link"}
        },
        "required": ["mode"]
    }
}

# --- HELPERS ---
def parse_addresses(field: Any) -> List[str]:
    if isinstance(field, str):
        return [x.strip() for x in field.split(",") if x.strip()]
    if isinstance(field, (list, tuple)):
        return [str(x).strip() for x in field if str(x).strip()]
    return []

def resolve_attachment(filename: str) -> Optional[str]:
    if not filename:
        return None
    p = Path(str(filename).strip())
    if p.is_absolute() and p.is_file():
        return str(p)
    name_lower = p.name.lower()
    search_dirs = [Path.home()/d for d in ("Desktop", "Downloads", "Documents")] + [Path.home(), Path("/tmp"), Path.cwd()]
    candidates = []
    for d in search_dirs:
        if not d.is_dir():
            continue
        exact = d / p.name
        if exact.is_file():
            candidates.append((exact.stat().st_mtime, str(exact)))
            continue
        for f in d.iterdir():
            if f.is_file() and name_lower in f.name.lower():
                candidates.append((f.stat().st_mtime, str(f)))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None

def resolve_attachments(atts: List[str]) -> Tuple[List[str], List[dict]]:
    resolved, status = [], []
    for a in atts or []:
        path = resolve_attachment(a)
        status.append({"filename": Path(path).name if path else a, "status": "success" if path else "not_found"})
        if path:
            resolved.append(path)
    return resolved, status

def escape_applescript(s: str) -> str:
    return str(s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\r")

def run_applescript(script: str, timeout_sec: int = 45) -> Tuple[str, str]:
    wrapped = f'with timeout of {timeout_sec} seconds\n{script}\nend timeout'
    try:
        r = subprocess.run(["osascript", "-e", wrapped], capture_output=True, text=True, timeout=timeout_sec + 5)
        return r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return "", f"Timeout or error: {e}"

def ensure_mail_running() -> bool:
    script = dedent('''
        tell application "System Events"
            if not (exists process "Mail") then
                tell application "Mail" to launch
                delay 1.5
            end if
            tell application "Mail" to activate
        end tell
    ''')
    _, err = run_applescript(script, 12)
    time.sleep(1.0)
    return err == ""

# --- MAILBOX MAPPING ---
MAILBOX_MAP = {
    "inbox": "inbox",
    "junk": "junk mailbox",
    "sent": "sent mailbox",
    "trash": "trash mailbox",
}

def _mailbox_expr(mailbox: str) -> str:
    return MAILBOX_MAP.get(mailbox.lower().strip(), "inbox")

# --- SEARCH EMAILS ---
def build_search_email_script(query: str, limit: int = 10, mailbox: str = "inbox") -> str:
    esc_q = escape_applescript(query)
    mb = _mailbox_expr(mailbox)
    return dedent(f'''
        tell application "Mail"
            set lf to ASCII character 10
            set output to ""
            set cnt to 0

            -- Use 'whose' clause for fast native search (searches ALL messages)
            set matchedSubj to (every message of {mb} whose subject contains "{esc_q}")
            set matchedSndr to (every message of {mb} whose sender contains "{esc_q}")

            -- Collect IDs to deduplicate
            set seenIDs to {{}}
            set allMatches to {{}}

            repeat with m in matchedSubj
                if cnt >= {limit} then exit repeat
                set mid to id of m
                if mid is not in seenIDs then
                    set end of seenIDs to mid
                    set end of allMatches to m
                    set cnt to cnt + 1
                end if
            end repeat
            repeat with m in matchedSndr
                if cnt >= {limit} then exit repeat
                set mid to id of m
                if mid is not in seenIDs then
                    set end of seenIDs to mid
                    set end of allMatches to m
                    set cnt to cnt + 1
                end if
            end repeat

            repeat with m in allMatches
                try
                    set bod to content of m as string
                    if (length of bod) > 5000 then
                        set bod to text 1 thru 5000 of bod
                    end if
                    set output to output & "<<<EMAIL>>>" & lf
                    set output to output & "ID:" & (id of m) & lf
                    set output to output & "MSGID:" & (message id of m) & lf
                    set output to output & "FROM:" & (sender of m) & lf
                    set output to output & "SUBJECT:" & (subject of m) & lf
                    set output to output & "DATE:" & (date received of m as string) & lf
                    set output to output & "UNREAD:" & (read status of m is false) & lf
                    set output to output & "<<<BODY>>>" & lf
                    set output to output & bod & lf
                    set output to output & "<<<END>>>" & lf
                end try
            end repeat
            return output
        end tell
    ''')

# --- READ EMAILS ---
def build_read_email_script(limit: int = 5, mailbox: str = "inbox") -> str:
    mb = _mailbox_expr(mailbox)
    return dedent(f'''
        tell application "Mail"
            set lf to ASCII character 10
            set output to ""
            set cnt to 0

            -- Get recent messages by date (last 7 days)
            set cutoff to (current date) - (7 * 24 * 60 * 60)
            set recentMsgs to (every message of {mb} whose date received > cutoff)
            set mc to count of recentMsgs

            -- If no recent messages, fall back to getting any messages
            if mc = 0 then
                set recentMsgs to messages of {mb}
                set mc to count of recentMsgs
            end if

            -- Read from end (newest) of the filtered list
            set i to mc
            repeat while i >= 1
                if cnt >= {limit} then exit repeat
                set m to item i of recentMsgs
                try
                    set bod to content of m as string
                    if (length of bod) > 5000 then
                        set bod to text 1 thru 5000 of bod
                    end if
                    set output to output & "<<<EMAIL>>>" & lf
                    set output to output & "ID:" & (id of m) & lf
                    set output to output & "MSGID:" & (message id of m) & lf
                    set output to output & "FROM:" & (sender of m) & lf
                    set output to output & "SUBJECT:" & (subject of m) & lf
                    set output to output & "DATE:" & (date received of m as string) & lf
                    set output to output & "UNREAD:" & (read status of m is false) & lf
                    set output to output & "<<<BODY>>>" & lf
                    set output to output & bod & lf
                    set output to output & "<<<END>>>" & lf
                    set cnt to cnt + 1
                end try
                set i to i - 1
            end repeat
            return output
        end tell
    ''')

# --- PARSE EMAIL OUTPUT ---
def parse_email_output(raw: str) -> List[dict]:
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    emails = []
    for block in raw.split('<<<EMAIL>>>'):
        block = block.strip()
        if not block or '<<<BODY>>>' not in block:
            continue
        header_part, body_part = block.split('<<<BODY>>>', 1)
        body = body_part.replace('<<<END>>>', '').strip()

        email = {'body': body}
        for line in header_part.strip().split('\n'):
            if ':' not in line:
                continue
            key, val = line.split(':', 1)
            key = key.strip().lower()
            val = val.strip()
            if key == 'id':
                email['id'] = val
            elif key == 'msgid':
                email['message_id'] = val
            elif key == 'from':
                email['sender'] = val
            elif key == 'subject':
                email['subject'] = val
            elif key == 'date':
                email['date'] = val
            elif key == 'unread':
                email['unread'] = val.lower() == 'true'
        emails.append(email)
    return emails

# --- EXTRACT LINKS FROM HTML SOURCE ---
def _get_html_links(email_id: str) -> List[str]:
    """Get href URLs from an email's HTML source by AppleScript message ID."""
    script = f'''tell application "Mail"
    set allMsgs to (every message of inbox whose id is {email_id})
    if (count of allMsgs) > 0 then
        set m to item 1 of allMsgs
        set src to source of m as string
        return src
    end if
    return ""
end tell'''
    out, _ = run_applescript(script, 30)
    if not out:
        return []
    # Extract href URLs from HTML source
    hrefs = re.findall(r'href="(https?://[^"]+)"', out)
    return hrefs

# --- EXTRACT VERIFICATION INFO ---
def extract_verification_info(body: str, html_links: List[str] = None) -> dict:
    info = {}

    # Codes near verification keywords
    code_patterns = [
        r'(?:verification|verify|code|otp|pin|token|confirm|security)\s*(?:is|:|=|\s)\s*(\d{4,8})',
        r'(\d{4,8})\s*(?:is your|verification|code|otp|pin)',
        r'(?:enter|use|type)\s+(\d{4,8})',
    ]
    codes = []
    for pat in code_patterns:
        codes.extend(re.findall(pat, body, re.IGNORECASE))

    # Fallback: standalone 5-8 digit numbers
    if not codes:
        all_nums = re.findall(r'\b(\d{5,8})\b', body)
        codes = [n for n in all_nums if not n.startswith('20')][:3]

    if codes:
        info['codes'] = list(dict.fromkeys(codes))[:3]

    # Verification/confirmation URLs — check body text first, then HTML links
    all_urls = re.findall(r'https?://[^\s<>"\')\]]+', body)
    if html_links:
        all_urls.extend(html_links)
    verify_kw = ['verify', 'confirm', 'activate', 'validate', 'token', 'auth',
                 'click', 'email-verification', 'account/verify', 'registration']
    verify_urls = [u for u in all_urls if any(kw in u.lower() for kw in verify_kw)]

    if verify_urls:
        # Deduplicate preserving order
        info['verify_links'] = list(dict.fromkeys(verify_urls))[:3]
    elif all_urls:
        info['all_links'] = list(dict.fromkeys(all_urls))[:5]

    return info

# --- SUMMARY MODE ---
def build_summary_script(limit: int, unread_only: bool) -> str:
    unread_filter = "read status of m is false and " if unread_only else ""
    return dedent(f'''
        tell application "Mail"
            set jsonOut to "["
            set cnt to 0
            set allMsgs to messages of inbox
            set msgCount to count of allMsgs
            set i to msgCount
            repeat while i >= 1
                if cnt ≥ {limit} then exit repeat
                set m to item i of allMsgs
                if {unread_filter}true then
                    set snippet to text 1 thru 380 of (content of m as string)
                    set line to "{{\\"id\\":" & id of m & ",\\"message_id\\":\\"" & message id of m & "\\",\\"sender\\":\\"" & my escape(sender of m) & "\\",\\"subject\\":\\"" & my escape(subject of m) & "\\",\\"date\\":\\"" & my escape(date received of m as string) & "\\",\\"unread\\":" & (read status of m is false) & ",\\"flagged\\":" & (flagged status of m) & ",\\"has_attachments\\":" & (count of attachments of m > 0) & ",\\"snippet\\":\\"" & my escape(snippet) & "\\"}}"
                    set jsonOut to jsonOut & line & (if cnt < {limit}-1 then "," else "")
                    set cnt to cnt + 1
                end if
                set i to i - 1
            end repeat
            return jsonOut & "]"
        end tell

        on escape(t)
            set AppleScript's text item delimiters to "\\""
            set items to every text item of t
            set AppleScript's text item delimiters to "\\\\\\""
            set t to items as string
            set AppleScript's text item delimiters to ""
            return t
        end escape
    ''')

def calculate_importance(msg: dict) -> int:
    score = 0
    text = (msg.get("subject", "") + " " + msg.get("sender", "") + " " + msg.get("snippet", "")).lower()
    high_kw = ["urgent", "action required", "due", "invoice", "balance", "payment", "handbook", "sign", "contract", "meeting", "call", "reply needed", "bank", "employer", "hr", "boss"]
    for kw in high_kw:
        if kw in text:
            score += 3
    if msg.get("unread"):
        score += 4
    if msg.get("flagged"):
        score += 3
    if msg.get("has_attachments"):
        score += 2
    return min(score + 2, 12)

def generate_executive_summary(emails: List[dict]) -> dict:
    for e in emails:
        e["importance"] = calculate_importance(e)
    emails.sort(key=lambda x: (x["importance"], x.get("date", "")), reverse=True)

    high, footnotes = [], []
    for e in emails[:15]:
        sender_short = e["sender"].split("<")[0].strip() or e["sender"]
        subj_short = e["subject"][:65] + ("..." if len(e["subject"]) > 65 else "")
        item = f"• {sender_short}: {subj_short}"
        if e["importance"] >= 7:
            high.append(item)
        else:
            footnotes.append(item)

    summary_text = "\n".join(high[:7])
    if footnotes:
        summary_text += "\n\nFootnotes:\n" + "\n".join(f"  {f}" for f in footnotes[:8])

    return {
        "executive_summary": summary_text or "No messages found.",
        "important_count": len([e for e in emails if e["importance"] >= 7]),
        "total_shown": len(emails),
        "emails": emails[:20]
    }

# --- SEND / COMPOSE IMPLEMENTATION ---
def build_send_script(to: str, subject: str, body: str, attachments: List[str],
                      cc: str = "", bcc: str = "", is_html: bool = False, compose_only: bool = False) -> str:
    to_addrs = parse_addresses(to)
    cc_addrs = parse_addresses(cc)
    bcc_addrs = parse_addresses(bcc)

    to_block = "\n".join([f'                make new to recipient at end of to recipients with properties {{address:"{escape_applescript(a)}"}}' for a in to_addrs])
    cc_block = "\n".join([f'                make new cc recipient at end of cc recipients with properties {{address:"{escape_applescript(a)}"}}' for a in cc_addrs]) if cc_addrs else ""
    bcc_block = "\n".join([f'                make new bcc recipient at end of bcc recipients with properties {{address:"{escape_applescript(a)}"}}' for a in bcc_addrs]) if bcc_addrs else ""

    attach_block = "\n".join([f'                make new attachment with properties {{file name:"{escape_applescript(p)}"}} at end of attachments' for p in attachments])

    visible = "set visible of newMessage to true" if compose_only else ""
    send_cmd = "" if compose_only else "send newMessage"

    html_prop = ", is html:true" if is_html else ""

    return dedent(f'''
        tell application "Mail"
            set newMessage to make new outgoing message with properties {{subject:"{escape_applescript(subject)}", content:"{escape_applescript(body)}"{html_prop}}}
            tell newMessage
                {visible}
                {to_block}
                {cc_block}
                {bcc_block}
                {attach_block}
            end tell
            {send_cmd}
            return "OK"
        end tell
    ''')

# --- UI INTERACTION (Mail.app accessibility + CoreGraphics) ---

def _osa_simple(script: str, timeout: int = 30) -> str:
    out, _ = run_applescript(script, timeout)
    return out

def _osa_jxa(script: str, timeout: int = 15) -> str:
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script],
                          capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR:{e}"

def _bezier_click_at(x: int, y: int, double: bool = False) -> dict:
    dbl = ""
    if double:
        dbl = (
            "var d2 = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseDown, point, $.kCGMouseButtonLeft);\n"
            "$.CGEventSetIntegerValueField(d2, $.kCGMouseEventClickState, 2);\n"
            "$.CGEventPost($.kCGSessionEventTap, d2); delay(0.03);\n"
            "var u2 = $.CGEventCreateMouseEvent($(), $.kCGEventLeftMouseUp, point, $.kCGMouseButtonLeft);\n"
            "$.CGEventSetIntegerValueField(u2, $.kCGMouseEventClickState, 2);\n"
            "$.CGEventPost($.kCGSessionEventTap, u2);\n"
        )
    result = _osa_jxa(f'''ObjC.import('CoreGraphics');
var curEvt = $.CGEventCreate($());
var curPos = $.CGEventGetLocation(curEvt);
var sx = curPos.x, sy = curPos.y, tx = {x}, ty = {y};
var steps = 15;
var cp1x = sx+(tx-sx)*0.3+(Math.random()*30-15);
var cp1y = sy+(ty-sy)*0.1+(Math.random()*20-10);
var cp2x = sx+(tx-sx)*0.7+(Math.random()*20-10);
var cp2y = sy+(ty-sy)*0.9+(Math.random()*15-7);
for(var i=1;i<=steps;i++){{var t=i/steps,u=1-t;
var bx=u*u*u*sx+3*u*u*t*cp1x+3*u*t*t*cp2x+t*t*t*tx;
var by=u*u*u*sy+3*u*u*t*cp1y+3*u*t*t*cp2y+t*t*t*ty;
var mv=$.CGEventCreateMouseEvent($(),$.kCGEventMouseMoved,$.CGPointMake(bx,by),0);
$.CGEventPost($.kCGSessionEventTap,mv);delay(0.012);}}
var point=$.CGPointMake(tx,ty);
var down=$.CGEventCreateMouseEvent($(),$.kCGEventLeftMouseDown,point,$.kCGMouseButtonLeft);
$.CGEventPost($.kCGSessionEventTap,down);delay(0.05);
var up=$.CGEventCreateMouseEvent($(),$.kCGEventLeftMouseUp,point,$.kCGMouseButtonLeft);
$.CGEventPost($.kCGSessionEventTap,up);
{dbl}"CLICKED";''')
    ok = "CLICKED" in result or "ERROR" not in result
    if ok:
        return {"success": True, "message": f"Clicked at {x},{y}"}
    _osa_simple(f'tell application "System Events" to click at {{{x}, {y}}}')
    return {"success": True, "message": f"Clicked at {x},{y} (fallback)"}

def _hover_at(x: int, y: int) -> dict:
    _osa_jxa(f'''ObjC.import('CoreGraphics');
var curEvt=$.CGEventCreate($());var curPos=$.CGEventGetLocation(curEvt);
var sx=curPos.x,sy=curPos.y,tx={x},ty={y};var steps=12;
var cp1x=sx+(tx-sx)*0.3+(Math.random()*20-10);
var cp1y=sy+(ty-sy)*0.1+(Math.random()*15-7);
var cp2x=sx+(tx-sx)*0.7+(Math.random()*15-7);
var cp2y=sy+(ty-sy)*0.9+(Math.random()*10-5);
for(var i=1;i<=steps;i++){{var t=i/steps,u=1-t;
var bx=u*u*u*sx+3*u*u*t*cp1x+3*u*t*t*cp2x+t*t*t*tx;
var by=u*u*u*sy+3*u*u*t*cp1y+3*u*t*t*cp2y+t*t*t*ty;
var mv=$.CGEventCreateMouseEvent($(),$.kCGEventMouseMoved,$.CGPointMake(bx,by),0);
$.CGEventPost($.kCGSessionEventTap,mv);delay(0.015);}}
"HOVERED";''')
    return {"success": True, "message": f"Hovered at {x},{y}"}

_mail_ref_map: Dict[int, dict] = {}

def mail_snapshot() -> dict:
    global _mail_ref_map
    _mail_ref_map = {}
    ref_n = 1
    _osa_simple('tell application "Mail" to activate')
    time.sleep(0.3)

    win = _osa_simple('''tell application "System Events"
    tell process "Mail"
        if (count of windows) = 0 then return "NO_WINDOW"
        return title of window 1
    end tell
end tell''')
    if "NO_WINDOW" in win or "ERROR" in win:
        return {"success": False, "message": "Mail window not found."}

    output = f"=== MAIL APP ===\nWindow: {win}\n"

    # Message list
    msg_list = _osa_simple('''tell application "System Events"
    tell process "Mail"
        tell window 1
            set output to ""
            try
                set allTables to every table of every scroll area of every splitter group
                repeat with tbl in allTables
                    try
                        set allRows to rows of tbl
                        set showCount to count of allRows
                        if showCount > 30 then set showCount to 30
                        repeat with i from 1 to showCount
                            set r to item i of allRows
                            try
                                set cellTexts to ""
                                repeat with c in (UI elements of r)
                                    try
                                        set v to value of c
                                        if v is not missing value and v is not "" then
                                            set cellTexts to cellTexts & v & "|||"
                                        end if
                                    end try
                                end repeat
                                if cellTexts is not "" then
                                    set sel to selected of r
                                    set {rx, ry} to position of r
                                    set prefix to ""
                                    if sel then set prefix to ">"
                                    set output to output & prefix & cellTexts & "@@" & rx & "," & ry & linefeed
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

    if msg_list and "ERROR" not in msg_list:
        output += "\n=== MESSAGE LIST ===\n"
        for line in msg_list.replace('\r', '\n').split('\n'):
            line = line.strip()
            if not line:
                continue
            pos = ""
            if '@@' in line:
                line, pos = line.rsplit('@@', 1)
            selected = line.startswith('>')
            if selected:
                line = line[1:]
            parts = [p.strip() for p in line.split('|||') if p.strip()]
            if parts:
                marker = " [SELECTED]" if selected else ""
                display = " | ".join(parts[:3])
                entry = f"[{ref_n}] {display}{marker}"
                if pos:
                    entry += f" @{pos}"
                output += entry + "\n"
                _mail_ref_map[ref_n] = {"type": "message_row", "parts": parts, "position": pos}
                ref_n += 1

    # Selected message content
    content = _osa_simple('''tell application "System Events"
    tell process "Mail"
        tell window 1
            set output to ""
            try
                repeat with sg in (every splitter group)
                    try
                        repeat with g in (every group of sg)
                            try
                                repeat with sa in (every scroll area of g)
                                    try
                                        set allTexts to every static text of entire contents of sa
                                        repeat with t in allTexts
                                            try
                                                set v to value of t
                                                if v is not missing value and v is not "" and (length of v) > 2 then
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
                end repeat
            end try
            return output
        end tell
    end tell
end tell''', 15)

    if content and "ERROR" not in content:
        output += "\n=== EMAIL CONTENT ===\n"
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
                _mail_ref_map[ref_n] = {"type": "content", "text": line.strip(), "position": pos}
                ref_n += 1
                urls = re.findall(r'https?://[^\s<>"\')\]]+', line)
                for url in urls:
                    output += f"  [{ref_n}] link: {url}\n"
                    _mail_ref_map[ref_n] = {"type": "link", "url": url, "position": pos}
                    ref_n += 1

    # Buttons
    buttons = _osa_simple('''tell application "System Events"
    tell process "Mail"
        tell window 1
            set output to ""
            try
                repeat with b in (every button of entire contents)
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
                            set output to output & "button|||" & bLabel & "@@" & bx & "," & by & linefeed
                        end if
                    end try
                end repeat
            end try
            return output
        end tell
    end tell
end tell''', 10)

    if buttons and "ERROR" not in buttons:
        output += "\n=== CONTROLS ===\n"
        for line in buttons.replace('\r', '\n').split('\n'):
            line = line.strip()
            if not line:
                continue
            pos = ""
            if '@@' in line:
                line, pos = line.rsplit('@@', 1)
            parts = line.split('|||')
            if len(parts) >= 2:
                entry = f"[{ref_n}] {parts[0]} \"{parts[1]}\""
                if pos:
                    entry += f" @{pos}"
                output += entry + "\n"
                _mail_ref_map[ref_n] = {"type": "control", "role": parts[0], "label": parts[1], "position": pos}
                ref_n += 1

    all_text = "\n".join(d.get('text', '') for d in _mail_ref_map.values() if d.get('type') == 'content')
    vinfo = extract_verification_info(all_text)
    if vinfo:
        output += "\n=== VERIFICATION INFO DETECTED ===\n"
        if 'codes' in vinfo:
            output += f"Codes: {', '.join(vinfo['codes'])}\n"
        if 'verify_links' in vinfo:
            output += f"Links: {', '.join(vinfo['verify_links'])}\n"

    return {"success": True, "snapshot": output, "refs": ref_n - 1}

def mail_click(ref_or_text: str) -> dict:
    global _mail_ref_map
    _osa_simple('tell application "Mail" to activate')
    time.sleep(0.2)

    ref_match = re.match(r'#?(\d+)$', ref_or_text.strip())
    if ref_match:
        ref_n = int(ref_match.group(1))
        if ref_n not in _mail_ref_map:
            return {"success": False, "message": f"Ref #{ref_n} not found. Snapshot first."}
        elem = _mail_ref_map[ref_n]

        if elem.get('type') == 'link' and elem.get('url'):
            subprocess.run(["open", "-a", "Microsoft Edge", elem['url']], capture_output=True)
            return {"success": True, "message": f"Opened in Edge: {elem['url']}", "url": elem['url']}

        pos = elem.get('position', '')
        if pos and ',' in pos:
            try:
                x, y = pos.split(',', 1)
                return _bezier_click_at(int(x.strip()), int(y.strip()))
            except ValueError:
                pass

        label = elem.get('label') or elem.get('text', '')[:50]
        if label:
            return _click_mail_text(label)
        return {"success": False, "message": f"Cannot click ref #{ref_n}"}

    return _click_mail_text(ref_or_text)

def _click_mail_text(text: str) -> dict:
    safe = escape_applescript(text)
    result = _osa_simple(f'''tell application "Mail" to activate
delay 0.2
tell application "System Events"
    tell process "Mail"
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
                    repeat with lnk in (every link of entire contents)
                        try
                            set lv to ""
                            try
                                set lv to value of lnk
                            end try
                            if lv contains "{safe}" or (title of lnk) contains "{safe}" then
                                click lnk
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
    return {"success": False, "message": f"Element '{text}' not found in Mail UI"}

def mail_scroll(direction: str = "down") -> dict:
    _osa_simple('tell application "Mail" to activate')
    time.sleep(0.2)
    if direction in ("top", "bottom"):
        key = 126 if direction == "top" else 125
        _osa_simple(f'''tell application "System Events"
    tell process "Mail"
        key code {key} using {{command down}}
    end tell
end tell''')
    else:
        key = 126 if direction == "up" else 125
        _osa_simple(f'''tell application "System Events"
    tell process "Mail"
        repeat 5 times
            key code {key}
            delay 0.05
        end repeat
    end tell
end tell''')
    return {"success": True, "message": f"Scrolled {direction}"}

def mail_open_link(url: str) -> dict:
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    subprocess.run(["open", "-a", "Microsoft Edge", url], capture_output=True)
    return {"success": True, "message": f"Opened in Edge: {url}", "url": url}

# --- MAIN HANDLER ---
def handle_mail(data: Dict[str, Any]) -> str:
    result: Dict[str, Any] = {
        "success": False,
        "mode": data.get("mode", "send"),
        "message": "",
        "data": {}
    }

    if not ensure_mail_running():
        result["message"] = "Failed to launch Mail.app"
        return json.dumps(result, ensure_ascii=False, indent=2)

    mode = result["mode"]
    resolved_paths, attach_status = resolve_attachments(data.get("attachments", []))

    # SEND / COMPOSE
    if mode in ("send", "compose"):
        to = data.get("to")
        if not to:
            result["message"] = "'to' is required for send/compose"
            return json.dumps(result, ensure_ascii=False, indent=2)

        subject = data.get("subject") or "(no subject)"
        body = data.get("body") or ""
        if not body:
            result["message"] = "'body' is required"
            return json.dumps(result, ensure_ascii=False, indent=2)

        script = build_send_script(
            to=to,
            subject=subject,
            body=body,
            attachments=resolved_paths,
            cc=data.get("cc", ""),
            bcc=data.get("bcc", ""),
            is_html=data.get("is_html", False),
            compose_only=(mode == "compose")
        )

        out, err = run_applescript(script, 30)
        if err or "OK" not in out:
            result["message"] = f"Send error: {err or out}"
        else:
            result["success"] = True
            result["message"] = f"Email {mode}d successfully"
            result["data"] = {"attachments": attach_status}

    # SEND_BATCH — send same email to many recipients separately
    elif mode == "send_batch":
        to_raw = data.get("to")
        if not to_raw:
            result["message"] = "'to' is required for send_batch — pass array of email addresses"
            return json.dumps(result, ensure_ascii=False, indent=2)

        subject = data.get("subject") or "(no subject)"
        body = data.get("body") or ""
        if not body:
            result["message"] = "'body' is required"
            return json.dumps(result, ensure_ascii=False, indent=2)

        # Parse all addresses from string or list
        if isinstance(to_raw, str):
            # Support space-separated, comma-separated, or newline-separated
            all_addrs = [a.strip() for a in re.split(r'[,\s\n]+', to_raw) if a.strip() and '@' in a.strip()]
        elif isinstance(to_raw, (list, tuple)):
            all_addrs = []
            for item in to_raw:
                item = str(item).strip()
                # Each item could still be comma/space separated
                all_addrs.extend([a.strip() for a in re.split(r'[,\s\n]+', item) if a.strip() and '@' in a.strip()])
        else:
            all_addrs = []

        if not all_addrs:
            result["message"] = "No valid email addresses found in 'to' field"
            return json.dumps(result, ensure_ascii=False, indent=2)

        # Deduplicate while preserving order
        seen = set()
        unique_addrs = []
        for addr in all_addrs:
            addr_lower = addr.lower()
            if addr_lower not in seen:
                seen.add(addr_lower)
                unique_addrs.append(addr)

        sent = []
        failed = []
        is_html = data.get("is_html", False)
        cc = data.get("cc", "")
        bcc = data.get("bcc", "")

        for i, addr in enumerate(unique_addrs):
            script = build_send_script(
                to=addr,
                subject=subject,
                body=body,
                attachments=resolved_paths,
                cc=cc,
                bcc=bcc,
                is_html=is_html,
                compose_only=False
            )
            out, err = run_applescript(script, 30)
            if err or "OK" not in out:
                failed.append({"email": addr, "error": (err or out)[:200]})
            else:
                sent.append(addr)

            # Brief pause between sends to avoid overwhelming Mail.app
            if i < len(unique_addrs) - 1:
                time.sleep(0.5)

        result["success"] = len(failed) == 0
        result["message"] = f"Batch complete: {len(sent)}/{len(unique_addrs)} sent successfully"
        if failed:
            result["message"] += f", {len(failed)} failed"
        result["data"] = {
            "total": len(unique_addrs),
            "sent_count": len(sent),
            "failed_count": len(failed),
            "sent": sent,
            "failed": failed,
            "attachments": attach_status
        }

    # SUMMARY
    elif mode == "summary":
        limit = min(data.get("limit", 25), 40)
        unread_only = data.get("unread_only", False)
        script = build_summary_script(limit, unread_only)
        out, err = run_applescript(script)

        try:
            emails = json.loads(out) if out.strip().startswith("[") else []
            summary = generate_executive_summary(emails)
            result["success"] = True
            result["data"] = summary
            result["message"] = f"Executive summary of {len(emails)} emails generated"
        except Exception as e:
            result["message"] = f"Summary failed: {str(e)}"

    # SEARCH — find emails matching query in subject/sender
    elif mode == "search":
        query = data.get("query")
        if not query:
            result["message"] = "'query' is required for search mode"
            return json.dumps(result, ensure_ascii=False, indent=2)

        limit = min(data.get("limit", 10), 20)
        mailbox = data.get("mailbox", "inbox")
        unread_only = data.get("unread_only", False)

        script = build_search_email_script(query, limit, mailbox)
        out, err = run_applescript(script, 90)

        emails = parse_email_output(out)

        # Auto-search junk if inbox returned nothing
        if not emails and mailbox == "inbox":
            junk_script = build_search_email_script(query, limit, "junk")
            junk_out, _ = run_applescript(junk_script, 90)
            junk_emails = parse_email_output(junk_out)
            if junk_emails:
                emails = junk_emails
                for e in emails:
                    e['mailbox'] = 'junk'

        if unread_only:
            emails = [e for e in emails if e.get('unread', False)]

        for email in emails:
            body = email.get('body', '')
            vinfo = extract_verification_info(body)
            # If no verify links found in plain text, check HTML source
            if not vinfo.get('verify_links') and email.get('id'):
                html_links = _get_html_links(email['id'])
                if html_links:
                    vinfo = extract_verification_info(body, html_links)
            if vinfo:
                email['verification_info'] = vinfo

        result["success"] = True
        result["data"] = {"emails": emails, "count": len(emails)}
        result["message"] = f"Found {len(emails)} email(s) matching '{query}'"

    # READ — read latest emails with full body
    elif mode == "read":
        limit = min(data.get("limit", 5), 20)
        mailbox = data.get("mailbox", "inbox")
        unread_only = data.get("unread_only", False)

        script = build_read_email_script(limit, mailbox)
        out, err = run_applescript(script, 60)

        emails = parse_email_output(out)
        if unread_only:
            emails = [e for e in emails if e.get('unread', False)]

        for email in emails:
            vinfo = extract_verification_info(email.get('body', ''))
            if vinfo:
                email['verification_info'] = vinfo

        result["success"] = True
        result["data"] = {"emails": emails, "count": len(emails)}
        result["message"] = f"Read {len(emails)} email(s) from {mailbox}"

    # SNAPSHOT — Mail.app UI
    elif mode == "snapshot":
        result = mail_snapshot()
        return json.dumps(result, ensure_ascii=False, indent=2)

    # CLICK — element in Mail.app
    elif mode == "click":
        text = data.get("text")
        if not text:
            result["message"] = "'text' required for click (#N ref or text)"
            return json.dumps(result, ensure_ascii=False, indent=2)
        result = mail_click(text)
        return json.dumps(result, ensure_ascii=False, indent=2)

    # CLICK_AT — Bézier click at coordinates
    elif mode == "click_at":
        value = data.get("value", "")
        if ',' not in str(value):
            result["message"] = "'value' must be 'x,y' coordinates"
            return json.dumps(result, ensure_ascii=False, indent=2)
        try:
            x, y = str(value).split(',', 1)
            result = _bezier_click_at(int(x.strip()), int(y.strip()))
        except ValueError:
            result["message"] = f"Invalid coordinates: {value}"
        return json.dumps(result, ensure_ascii=False, indent=2)

    # SCROLL — Mail.app
    elif mode == "scroll":
        direction = data.get("value", "down")
        result = mail_scroll(direction)
        return json.dumps(result, ensure_ascii=False, indent=2)

    # HOVER — move mouse to coordinates
    elif mode == "hover":
        value = data.get("value", "")
        if ',' not in str(value):
            result["message"] = "'value' must be 'x,y' coordinates"
            return json.dumps(result, ensure_ascii=False, indent=2)
        try:
            x, y = str(value).split(',', 1)
            result = _hover_at(int(x.strip()), int(y.strip()))
        except ValueError:
            result["message"] = f"Invalid coordinates: {value}"
        return json.dumps(result, ensure_ascii=False, indent=2)

    # OPEN_LINK — open URL in Edge
    elif mode == "open_link":
        url = data.get("value") or data.get("url", "")
        if not url:
            result["message"] = "'value' (URL) required for open_link"
            return json.dumps(result, ensure_ascii=False, indent=2)
        result = mail_open_link(url)
        return json.dumps(result, ensure_ascii=False, indent=2)

    # Other modes
    else:
        result["success"] = True
        result["message"] = f"Mode '{mode}' acknowledged"

    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    args = parser.parse_args()

    try:
        print(handle_mail(json.loads(args.json)))
    except Exception as e:
        print(json.dumps({"success": False, "message": f"Python error: {str(e)}"}))
