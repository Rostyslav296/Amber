#!/usr/bin/env python3
"""
telegram-link.py v2.0 — Telegram Sidecar + Agent Tool for Amber

Two modes:
  1. Sidecar mode: Started from ai.py when user types "telegram" in chat.
     Attaches to the EXISTING model/agent — no second model load.
     Mirrors all terminal output to Telegram. Injects Telegram messages
     into the terminal chat loop.

  2. Tool mode: Auto-discovered by agent.py. Agent can send messages
     to Telegram via {"tool":"telegram","args":{"action":"send","message":"..."}}.

Architecture (sidecar):
  iPhone Telegram ──► Bot API ──► Long Polling Thread ──► inject_queue
                                                               │
  Mac Terminal ◄── ai.py chat loop ◄───────────────────────────┘
       │                   │
       ▼                   ▼
  Terminal stdout ──► TeeStream ──► Telegram message edits (streaming)

Config: ~/.amber_telegram.json (auto-created on first "telegram" command)
"""

# ── Fix sys.path ──
import sys, os
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir in sys.path:
    sys.path.remove(_script_dir)

import json, re, time, threading, logging, ssl, argparse
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from typing import Dict, Any, Optional, List
from queue import Queue, Empty

# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────
SCRIPT_VERSION = "v2.0"
CONFIG_PATH = os.path.expanduser("~/.amber_telegram.json")
LOG_FILE = os.path.expanduser("~/.amber_telegram.log")
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"
MSG_CHAR_LIMIT = 4096
STREAM_UPDATE_INTERVAL = 0.5

logging.basicConfig(
    level=logging.DEBUG, filename=LOG_FILE, filemode='a',
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("telegram_link")

# ──────────────────────────────────────────────────────────────
# TOOL_METADATA — agent.py auto-discovery
# ──────────────────────────────────────────────────────────────
TOOL_METADATA = {
    "name": "telegram",
    "description": (
        "Send messages to Telegram. Use to notify the user on their phone. "
        "Config: ~/.amber_telegram.json\n"
        "Actions:\n"
        "  send — Send a text message. Args: message (required), chat_id (optional).\n"
        "\nExamples:\n"
        '  {"action":"send","message":"Task complete! Found 15 leads."}\n'
        '  {"action":"send","message":"Build succeeded — 0 errors."}\n'
    ),
    "priority": 50,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["send"],
                "description": "Action to perform."
            },
            "message": {
                "type": "string",
                "description": "Message text to send."
            },
            "chat_id": {
                "type": "integer",
                "description": "Telegram chat ID (uses default from config if omitted)."
            }
        },
        "required": ["action", "message"]
    }
}

# ── SSL context ──
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Config load error: {e}")
        return None


def save_config(config):
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
        os.chmod(CONFIG_PATH, 0o600)
    except Exception as e:
        log.error(f"Config save error: {e}")


def ensure_config(token=None):
    """Load config, creating it if needed. Returns config dict or None."""
    config = load_config()
    if config and config.get('token') and not config['token'].startswith('YOUR_'):
        return config
    if token:
        config = {"token": token, "allowed_users": [], "auto_register": True}
        save_config(config)
        return config
    return None


# ══════════════════════════════════════════════════════════════
#  TELEGRAM BOT API (urllib, zero deps)
# ══════════════════════════════════════════════════════════════

class TelegramAPI:
    def __init__(self, token):
        self.token = token

    def _call(self, method, data=None, timeout=60):
        url = TELEGRAM_API_URL.format(token=self.token, method=method)
        body = None
        headers = {}
        if data:
            body = json.dumps(data).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = Request(url, data=body, headers=headers)
        try:
            resp = urlopen(req, timeout=timeout, context=_ssl_ctx)
            return json.loads(resp.read().decode())
        except HTTPError as e:
            err = ""
            try:
                err = e.read().decode('utf-8', errors='replace')[:500]
            except Exception:
                pass
            log.error(f"Telegram {method} {e.code}: {err}")
            return {"ok": False, "error_code": e.code, "description": err}
        except Exception as e:
            log.error(f"Telegram {method}: {e}")
            return {"ok": False, "description": str(e)}

    def send_message(self, chat_id, text):
        if len(text) > MSG_CHAR_LIMIT:
            text = text[:MSG_CHAR_LIMIT - 20] + "\n\n... [truncated]"
        return self._call('sendMessage', {'chat_id': chat_id, 'text': text})

    def edit_message(self, chat_id, message_id, text):
        if len(text) > MSG_CHAR_LIMIT:
            text = text[:MSG_CHAR_LIMIT - 20] + "\n\n... [truncated]"
        return self._call('editMessageText', {
            'chat_id': chat_id, 'message_id': message_id, 'text': text,
        }, timeout=10)

    def get_updates(self, offset=None, timeout=30):
        data = {'timeout': timeout, 'allowed_updates': ['message']}
        if offset is not None:
            data['offset'] = offset
        return self._call('getUpdates', data, timeout=timeout + 10)

    def get_me(self):
        return self._call('getMe')


# ══════════════════════════════════════════════════════════════
#  TOOL MODE — Agent sends messages
# ══════════════════════════════════════════════════════════════

def handle_action(data):
    action = data.get('action', '')
    config = load_config()
    if not config or config.get('token', '').startswith('YOUR_'):
        return "Error: Telegram not configured. Type 'telegram' in chat to set up."

    api = TelegramAPI(config['token'])

    if action == 'send':
        message = data.get('message', '')
        if not message:
            return "Error: message required"
        chat_id = data.get('chat_id')
        if not chat_id:
            users = config.get('allowed_users', [])
            chat_id = users[0] if users else None
        if not chat_id:
            return "Error: No chat_id and no default user. Send a message to the bot first."
        result = api.send_message(chat_id, message)
        if result.get('ok'):
            return f"Sent to Telegram (chat {chat_id})"
        return f"Send failed: {result.get('description', 'unknown')}"

    return f"Unknown action: {action}. Available: send"


# ══════════════════════════════════════════════════════════════
#  TEE STREAM — stdout capture for Telegram streaming
# ══════════════════════════════════════════════════════════════

class TeeStream:
    """Wraps stdout to capture output for Telegram while printing to terminal."""

    _ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mGKHJsu]')

    def __init__(self, original):
        self.original = original
        self._captured = ""
        self._lock = threading.Lock()
        self._dirty = False

    def write(self, s):
        self.original.write(s)
        clean = self._ANSI_RE.sub('', s)
        clean = re.sub(r'\*+', '', clean)
        if clean:
            with self._lock:
                self._captured += clean
                self._dirty = True

    def flush(self):
        self.original.flush()

    def fileno(self):
        return self.original.fileno()

    def isatty(self):
        return self.original.isatty()

    @property
    def encoding(self):
        return getattr(self.original, 'encoding', 'utf-8')

    def get_text(self):
        with self._lock:
            return self._captured

    def check_dirty(self):
        with self._lock:
            if self._dirty:
                self._dirty = False
                return True
            return False

    def reset(self):
        with self._lock:
            self._captured = ""
            self._dirty = False


# ══════════════════════════════════════════════════════════════
#  SIDECAR — attaches to existing ai.py runtime (no model load)
# ══════════════════════════════════════════════════════════════

class TelegramSidecar:
    """Attaches Telegram to a running Amber session.

    - Polls Telegram for incoming messages
    - Injects them into the ai.py chat loop via inject_queue
    - Wraps sys.stdout with TeeStream to mirror output to Telegram
    - Does NOT load its own model — reuses ai.py's model/agent

    Usage from ai.py:
        sidecar = TelegramSidecar(token)
        sidecar.start()
        # In chat loop: check sidecar.inject_queue for Telegram messages
        # Before agent.run(): sidecar.begin_response(chat_id)
        # After agent.run():  sidecar.end_response()
    """

    def __init__(self, token):
        self.api = TelegramAPI(token)
        self.token = token
        self.inject_queue = Queue()  # Telegram messages → ai.py chat loop
        self._poll_thread = None
        self._stop = threading.Event()
        self._tee = None
        self._original_stdout = None
        self._updater_thread = None
        self._updater_stop = None
        self._current_chat_id = None
        self._current_msg_id = None
        self._last_sent_len = 0
        self.active = False
        self.allowed_users = set()
        self.auto_register = True
        self.bot_username = "?"

        # Load allowed users from config
        config = load_config()
        if config:
            self.allowed_users = set(config.get('allowed_users', []))
            self.auto_register = config.get('auto_register', True)

    def start(self):
        """Verify bot and start polling thread."""
        me = self.api.get_me()
        if not me.get('ok'):
            return False, f"Invalid token: {me.get('description', '?')}"

        self.bot_username = me['result'].get('username', '?')
        self.active = True
        self._stop.clear()

        # Install TeeStream on stdout permanently while sidecar is active
        self._original_stdout = sys.stdout
        self._tee = TeeStream(self._original_stdout)
        sys.stdout = self._tee

        # Start polling thread
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True
        )
        self._poll_thread.start()

        return True, self.bot_username

    def stop(self):
        """Stop polling and restore stdout."""
        self._stop.set()
        self.active = False
        if self._tee and self._original_stdout:
            sys.stdout = self._original_stdout
            self._tee = None

    def begin_response(self, chat_id=None):
        """Call before agent_loop.run() to start Telegram streaming.

        If chat_id is provided, sends a 'Thinking...' placeholder and
        starts the background updater that edits the message with new output.
        If chat_id is None (terminal-initiated), just resets the capture buffer.
        """
        if self._tee:
            self._tee.reset()
        self._current_chat_id = chat_id
        self._current_msg_id = None
        self._last_sent_len = 0

        if chat_id:
            # Send placeholder
            result = self.api.send_message(chat_id, "Thinking...")
            if result.get('ok'):
                self._current_msg_id = result['result']['message_id']

        # Start updater thread
        self._updater_stop = threading.Event()
        self._updater_thread = threading.Thread(
            target=self._stream_updater, daemon=True
        )
        self._updater_thread.start()

    def end_response(self):
        """Call after agent_loop.run() to finalize the Telegram message."""
        # Stop updater
        if self._updater_stop:
            self._updater_stop.set()
        if self._updater_thread:
            self._updater_thread.join(timeout=2)
            self._updater_thread = None

        if not self._tee:
            return

        # Send final complete text
        final = self._tee.get_text().strip()
        if not final:
            return

        chat_id = self._current_chat_id
        msg_id = self._current_msg_id

        # If response was triggered from terminal (no chat_id), broadcast to
        # the first registered user so they can see terminal activity too
        if not chat_id:
            config = load_config()
            users = config.get('allowed_users', []) if config else []
            if users:
                chat_id = users[0]
            else:
                return  # no one to send to

        if not msg_id:
            # No placeholder was sent (terminal-initiated), send new message
            self._send_full_text(chat_id, final)
            return

        # Update existing placeholder with final text
        if len(final) <= MSG_CHAR_LIMIT:
            try:
                self.api.edit_message(chat_id, msg_id, final)
            except Exception:
                pass
        else:
            self._send_full_text(chat_id, final, msg_id)

    def _send_full_text(self, chat_id, text, edit_msg_id=None):
        """Send or edit a long text, splitting into multiple messages."""
        if edit_msg_id:
            try:
                self.api.edit_message(
                    chat_id, edit_msg_id,
                    text[:MSG_CHAR_LIMIT - 20] + "\n\n..."
                )
            except Exception:
                pass
            remaining = text[MSG_CHAR_LIMIT - 20:]
        else:
            self.api.send_message(chat_id, text[:MSG_CHAR_LIMIT])
            remaining = text[MSG_CHAR_LIMIT:]

        while remaining:
            chunk = remaining[:MSG_CHAR_LIMIT]
            remaining = remaining[MSG_CHAR_LIMIT:]
            self.api.send_message(chat_id, chunk)

    def _stream_updater(self):
        """Background thread: periodically edits Telegram message with new output."""
        while not self._updater_stop.is_set():
            self._updater_stop.wait(STREAM_UPDATE_INTERVAL)
            if not self._tee or not self._current_msg_id:
                continue
            if self._tee.check_dirty():
                current = self._tee.get_text().strip()
                if current and len(current) > self._last_sent_len:
                    display = current
                    if len(display) > MSG_CHAR_LIMIT - 25:
                        display = "..." + display[-(MSG_CHAR_LIMIT - 28):]
                    try:
                        self.api.edit_message(
                            self._current_chat_id,
                            self._current_msg_id,
                            display,
                        )
                        self._last_sent_len = len(current)
                    except Exception as e:
                        log.error(f"Stream edit failed: {e}")

    def _poll_loop(self):
        """Long-poll Telegram for incoming messages."""
        offset = None
        while not self._stop.is_set():
            try:
                updates = self.api.get_updates(offset=offset, timeout=30)
                if not updates.get('ok'):
                    log.error(f"getUpdates failed: {updates}")
                    self._stop.wait(5)
                    continue

                for update in updates.get('result', []):
                    offset = update['update_id'] + 1

                    msg = update.get('message', {})
                    chat_id = msg.get('chat', {}).get('id')
                    user_id = msg.get('from', {}).get('id')
                    username = msg.get('from', {}).get('username', '?')
                    text = msg.get('text', '')

                    if not chat_id or not text:
                        continue

                    # Whitelist check
                    if self.allowed_users and user_id not in self.allowed_users:
                        self.api.send_message(
                            chat_id,
                            f"Not authorized. Your ID: {user_id}\n"
                            f"Add to {CONFIG_PATH}"
                        )
                        continue

                    # Auto-register
                    if not self.allowed_users and self.auto_register:
                        self.allowed_users.add(user_id)
                        config = load_config() or {}
                        config['allowed_users'] = list(self.allowed_users)
                        save_config(config)
                        self.api.send_message(
                            chat_id,
                            f"Registered! User ID: {user_id}\n"
                            "You can now send messages to Amber."
                        )
                        # Print to terminal
                        orig = self._original_stdout or sys.stdout
                        orig.write(
                            f"\n  \033[90mtelegram: registered user "
                            f"{user_id} (@{username})\033[0m\n"
                        )
                        orig.flush()
                        continue

                    # Handle Telegram-only commands
                    tl = text.lower().strip()
                    if tl in ('/reset', '/clear'):
                        # Signal to ai.py via queue with special marker
                        self.inject_queue.put({
                            'chat_id': chat_id,
                            'text': '/reset',
                            'username': username,
                        })
                        continue
                    if tl in ('/help', '/start'):
                        self.api.send_message(chat_id, (
                            "Amber AI Agent\n\n"
                            "Send any message to chat with Amber.\n\n"
                            "Commands:\n"
                            "/reset - Clear conversation\n"
                            "/help - This help\n\n"
                            "Everything you send here runs through "
                            "the same Amber agent as the Mac terminal."
                        ))
                        continue

                    # Inject into ai.py chat loop
                    self.inject_queue.put({
                        'chat_id': chat_id,
                        'text': text,
                        'username': username,
                    })

                    # Print to terminal
                    orig = self._original_stdout or sys.stdout
                    orig.write(
                        f"\n  \033[90m[Telegram @{username}] "
                        f"{text[:80]}\033[0m\n"
                    )
                    orig.flush()

            except Exception as e:
                if self._stop.is_set():
                    break
                log.error(f"Poll error: {e}", exc_info=True)
                self._stop.wait(5)


# ══════════════════════════════════════════════════════════════
#  SERVER MODE (for agent.py tool discovery)
# ══════════════════════════════════════════════════════════════

def server_loop():
    log.info(f"telegram-link {SCRIPT_VERSION} server started")
    print(json.dumps({
        "status": "ready", "version": SCRIPT_VERSION, "tool": "telegram",
    }), flush=True)

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                result = handle_action(data)
                print(json.dumps({
                    "status": "success", "page": result,
                }, ensure_ascii=False), flush=True)
            except json.JSONDecodeError as e:
                print(json.dumps({
                    "status": "error", "message": f"Invalid JSON: {e}",
                }), flush=True)
            except Exception as e:
                log.error(f"Server error: {e}", exc_info=True)
                print(json.dumps({
                    "status": "error", "message": str(e),
                }), flush=True)
        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            print(json.dumps({
                "status": "error", "message": str(e),
            }), flush=True)


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Amber Telegram Link"
    )
    parser.add_argument('--server', action='store_true',
                        help='Server mode (agent.py tool)')
    parser.add_argument('--json', type=str,
                        help='One-shot JSON action')
    parser.add_argument('--setup', type=str, metavar='TOKEN',
                        help='Create config with bot token')
    args = parser.parse_args()

    if args.setup:
        ensure_config(args.setup)
        print(f"Config: {CONFIG_PATH}")
        sys.exit(0)

    if args.json:
        data = json.loads(args.json)
        print(handle_action(data))
        sys.exit(0)

    if args.server:
        try:
            server_loop()
        except KeyboardInterrupt:
            pass
        sys.exit(0)

    print(f"telegram-link.py {SCRIPT_VERSION}")
    print("Usage:")
    print("  In Amber chat, type: telegram")
    print("  Or: --server / --json / --setup TOKEN")
