#!/usr/bin/env python3
import sys, os, json, re, subprocess, time, pathlib, glob
from typing import Optional, Union

# --- METADATA ---
# Simple and strict to guide the Agent correctly.
TOOL_METADATA = {
    "name": "fterminal",
    "description": "Opens the FTerminal. The session starts in the 'tools' directory.\nTo launch: {\"command\": \"\"}\nTo list files: {\"command\": \"ls\"}\nTo run script: {\"command\": \"python3 script.py\"}",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to run."
            }
        },
        "required": ["command"]
    }
}

# --- CONFIGURATION ---
SCRIPT_PATH = pathlib.Path(__file__).resolve()

def find_tools_dir():
    """Locate the tools directory without creating it."""
    # Check Dev Path
    dev = SCRIPT_PATH.parent.parent / "apps" / "fterminal" / "tools"
    if dev.exists(): return dev
    # Check Relative
    rel = SCRIPT_PATH.parent / "tools"
    if rel.exists(): return rel
    # Default to current if not found (let the Agent figure it out)
    return pathlib.Path.cwd()

TOOLS_DIR = find_tools_dir()
SOCKET_NAME = f"FTerminal_Sock_{os.environ.get('USER', 'user')}"

# --- RESOLVER (Minimalist) ---
def resolve_command(cmd_input: Union[str, list, dict]) -> str:
    """Cleans input but DOES NOT rewrite paths."""
    if not cmd_input: return ""
    
    # 1. Handle sloppy types from Agent
    text = ""
    if isinstance(cmd_input, list) and cmd_input:
        text = str(cmd_input[0])
    elif isinstance(cmd_input, dict):
        text = str(cmd_input.get("command", "") or cmd_input.get("cmd", ""))
    else:
        text = str(cmd_input)

    text = text.strip()
    
    # 2. Clean JSON string artifacts (e.g. '["ls"]')
    if text.startswith("[") and text.endswith("]"):
        try:
            val = json.loads(text)
            if isinstance(val, list) and val: text = val[0]
        except: pass
    
    # 3. Clean 'Launch' verbs
    text = re.sub(r"^(launch|execute|exec|run|cmd|shell)(\s*:\s*|\s+)", "", text, flags=re.IGNORECASE).strip()

    return text

# --- CLIENT ---
def send_command(command_str: str):
    clean_cmd = resolve_command(command_str)
    try:
        from PySide6.QtNetwork import QLocalSocket
        socket = QLocalSocket()
        socket.connectToServer(SOCKET_NAME)
        if not socket.waitForConnected(500): return False
        
        # If clean_cmd is empty, just focus; otherwise send command
        payload = {"cmd": clean_cmd} if clean_cmd else {"focus": True}
        socket.write(json.dumps(payload).encode('utf-8'))
        socket.flush()
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return True
    except: return False

# --- SERVER ---
def run_server_process(initial_cmd=None):
    try:
        from PySide6.QtCore import Qt, QProcess, QTimer, QProcessEnvironment
        from PySide6.QtGui import QPalette, QColor, QTextCursor
        from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                                     QHBoxLayout, QTextEdit, QLineEdit, QPushButton, QLabel)
        from PySide6.QtNetwork import QLocalServer
    except:
        print("Error: PySide6 missing.")
        return

    # Cleanup stale socket
    if sys.platform != "win32":
        try: os.unlink(f"/tmp/{SOCKET_NAME}")
        except: pass

    class TerminalWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("FTerminal")
            self.resize(1000, 660)
            
            # Dark Theme
            p = QPalette()
            p.setColor(QPalette.Window, QColor(18,18,18))
            p.setColor(QPalette.WindowText, Qt.white)
            p.setColor(QPalette.Base, QColor(12,12,12))
            p.setColor(QPalette.Text, Qt.white)
            p.setColor(QPalette.Button, QColor(28,28,28))
            p.setColor(QPalette.ButtonText, Qt.white)
            self.setPalette(p)

            root = QWidget(); self.setCentralWidget(root)
            v = QVBoxLayout(root); v.setContentsMargins(10,10,10,10)

            # Header
            lbl = QLabel(f"FTerminal (Native)")
            lbl.setStyleSheet("color:#00ff00; font-weight:bold; font-size:16px;")
            v.addWidget(lbl)
            
            sub = QLabel(f"CWD: {TOOLS_DIR}")
            sub.setStyleSheet("color:#666; font-size:11px;")
            v.addWidget(sub)

            # Terminal Display
            self.term = QTextEdit()
            self.term.setReadOnly(True)
            self.term.setStyleSheet("background:#000; color:#0f0; font-family:Monaco; border:1px solid #333;")
            v.addWidget(self.term)

            # Input Area
            h = QHBoxLayout()
            self.inp = QLineEdit()
            self.inp.setPlaceholderText("Enter command...")
            self.inp.setStyleSheet("background:#222; color:#0f0; border:1px solid #333; padding:5px;")
            self.inp.returnPressed.connect(self.on_send)
            h.addWidget(self.inp)
            v.addLayout(h)

            # Shell Process
            self.proc = QProcess()
            self.proc.setProgram("/bin/zsh")
            self.proc.setArguments(["-l"])
            self.proc.setProcessChannelMode(QProcess.MergedChannels)
            
            # CRITICAL: Set the Working Directory to the tools folder
            # This allows 'run hello_world.py' to work without paths.
            self.proc.setWorkingDirectory(str(TOOLS_DIR))
            
            self.proc.readyReadStandardOutput.connect(self.read_output)
            
            # Setup Path
            env = QProcessEnvironment.systemEnvironment()
            env.insert("TERM", "xterm-256color")
            # Ensure current directory is in PATH for direct execution
            curr_path = env.value("PATH", "")
            env.insert("PATH", f"{TOOLS_DIR}:{os.path.dirname(sys.executable)}:{curr_path}")
            self.proc.setProcessEnvironment(env)
            
            self.proc.start()

            # Server
            self.server = QLocalServer()
            self.server.removeServer(SOCKET_NAME)
            self.server.listen(SOCKET_NAME)
            self.server.newConnection.connect(self.handle_conn)

            # Auto-Run Initial Command
            if initial_cmd:
                QTimer.singleShot(500, lambda: self.write_cmd(initial_cmd))
            else:
                # Just show a helpful prompt
                QTimer.singleShot(500, lambda: self.append_text(f"[SYSTEM] Ready in {TOOLS_DIR.name}.\n"))

        def read_output(self):
            data = self.proc.readAllStandardOutput().data().decode(errors="replace")
            self.append_text(data)

        def append_text(self, text):
            self.term.moveCursor(QTextCursor.End)
            self.term.insertPlainText(text)
            self.term.moveCursor(QTextCursor.End)

        def on_send(self):
            txt = self.inp.text()
            self.inp.clear()
            self.write_cmd(txt)

        def write_cmd(self, cmd):
            # No magic rewriting. Just pass it to shell.
            self.append_text(f"\n➜ {cmd}\n")
            if self.proc.state() == QProcess.Running:
                self.proc.write((cmd + "\n").encode())

        def handle_conn(self):
            client = self.server.nextPendingConnection()
            if client.waitForReadyRead(100):
                try:
                    raw = client.readAll().data().decode()
                    data = json.loads(raw)
                    if data.get("cmd"): self.write_cmd(data["cmd"])
                    else:
                        self.activateWindow()
                        self.raise_()
                except: pass
            client.disconnectFromServer()

    app = QApplication.instance() or QApplication(sys.argv)
    win = TerminalWindow()
    win.show()
    sys.exit(app.exec())

# --- MAIN ENTRY ---
def main():
    args = sys.argv[1:]
    
    # 1. Server Mode
    if "--internal-server" in args:
        init = None
        if "--init" in args: init = args[args.index("--init")+1]
        run_server_process(init)
        return

    # 2. Robust Args Parsing (Fixes 3rd Attempt Lag)
    cmd = ""
    if "--json" in args:
        try:
            # We treat the input purely as data to extract a string from
            raw = args[args.index("--json")+1]
            data = json.loads(raw)
            cmd = resolve_command(data) # Handles List/Dict/Str
        except: pass
    else:
        # Fallback for manual CLI usage
        cmd = " ".join([x for x in args if not x.startswith("--")])

    # 3. Try Sending to Existing
    if send_command(cmd):
        print("✅ Sent.")
        sys.exit(0)

    # 4. Launch New Window
    print("🚀 Launching...")
    env = os.environ.copy()
    s_args = [sys.executable, __file__, "--internal-server"]
    
    # Pass initial command if it exists
    if cmd: s_args += ["--init", cmd]

    subprocess.Popen(s_args, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if __name__ == "__main__":
    main()
