#!/usr/bin/env python3
import sys, os, json, subprocess, time, pathlib, traceback, socket, importlib.util

# --- CONFIGURATION ---
SCRIPT_PATH = pathlib.Path(__file__).resolve()
LOG_FILE = "/tmp/fdatasette_debug.log"
SOCKET_PATH = "/tmp/fdatasette_sock.ipc"
DATASETTE_PORT = 8001

# --- DYNAMIC PATHS ---
def find_app_root():
    repo_apps = SCRIPT_PATH.parent.parent / "apps"
    if (repo_apps / "datasette").exists(): return repo_apps / "datasette"
    return SCRIPT_PATH.parent

APP_ROOT = find_app_root()
TOOLS_DIR = APP_ROOT / "tools"
MANUALS_DIR = APP_ROOT / "manuals"

# --- THE OPERATOR THEME ---
DARK_CSS = """
body { background-color: #000000; color: #00ff00; font-family: "Courier New", monospace; margin: 0; padding: 20px; }
a { color: #00ff00; text-decoration: none; border-bottom: 1px dotted #004400; cursor: pointer; }
a:hover { background-color: #00ff00; color: #000; }
h1, h2, h3 { color: #ccffcc; text-transform: uppercase; border-bottom: 1px solid #00ff00; letter-spacing: 1px; }
table { border-collapse: collapse; width: 100%; border: 1px solid #004400; background: #0a0a0a; margin-top: 10px; }
th { background-color: #002200; color: #00ff00; border-bottom: 2px solid #00ff00; padding: 10px; text-align: left; }
td { border-bottom: 1px solid #003300; padding: 8px; color: #ccffcc; }
tr:hover td { background-color: #003300; color: #fff; }
.manual-index { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 15px; margin-top: 20px; }
.manual-card { border: 1px solid #00ff00; padding: 15px; background: #001100; transition: 0.2s; }
.manual-card:hover { background: #002200; box-shadow: 0 0 15px #00ff00; }
.manual-card h4 { margin: 0 0 10px 0; color: #fff; }
.section { border: 1px solid #004400; padding: 20px; margin-bottom: 20px; background: #050505; }
.back-btn { display: inline-block; padding: 5px 10px; border: 1px solid #00ff00; margin-bottom: 20px; background: #002200; }
"""

def log_debug(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"[{time.strftime('%X')}] {msg}\n")

def ensure_datasette_installed():
    if importlib.util.find_spec("datasette") is None:
        try: subprocess.check_call([sys.executable, "-m", "pip", "install", "datasette"])
        except: pass

# --- MANUAL SYSTEM ---
def get_manuals_list():
    if not MANUALS_DIR.exists(): return []
    return sorted(list(MANUALS_DIR.glob("*.html")))

def generate_manual_index():
    manuals = get_manuals_list()
    html = """<html><head><style>""" + DARK_CSS + """</style></head><body>
    <h1>// FIELD MANUALS: SYSTEM INDEX</h1>
    <p>Select a module to view operational parameters.</p>
    <div class="manual-index">"""
    
    if not manuals:
        html += f"<p>No manuals found in <code>{MANUALS_DIR}</code></p>"
    
    for m in manuals:
        html += f"""
        <div class="manual-card">
            <h4>{m.stem.replace('_', ' ').upper()}</h4>
            <a href="?manual={m.name}">[ ACCESS FILE ]</a>
        </div>"""
    
    html += "</div></body></html>"
    return html

def load_manual_content(filename):
    filename = filename.strip().split('&')[0]
    path = MANUALS_DIR / filename
    
    if not path.exists():
        return f"<html><head><style>{DARK_CSS}</style></head><body><h1>Error: File Not Found</h1><p>{path}</p></body></html>"
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return f"<html><head><style>{DARK_CSS}</style></head><body><h1>Read Error</h1><p>{e}</p></body></html>"
    
    return f"""<html><head><style>{DARK_CSS}</style></head><body>
    <a href="?index=true" class="back-btn">&lt; // RETURN TO INDEX</a>
    {content}
    </body></html>"""

def get_landing_page():
    return """<html><body style="background:#000;color:#0f0;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh;">
        <div style="border:1px solid #0f0;padding:40px;text-align:center;box-shadow:0 0 20px #0f0;">
            <h1>[ SYSTEM READY ]</h1><p>Awaiting Target Database...</p><p style="font-size:10px;opacity:0.7">Secure Channel: Est.</p></div></body></html>"""

# --- MAIN APP ---
TOOL_METADATA = {
    "name": "open_datasette_app",
    "description": "LAUNCHER. Opens the FDatasette Intelligence Dashboard.",
    "parameters": {"type": "object", "properties": {"database": {"type": "string"}}}
}

def find_database(name_input):
    if not name_input: return None
    path = pathlib.Path(name_input)
    if path.exists(): return path.resolve()
    cwd = pathlib.Path.cwd()
    for ext in [".db", ".sqlite"]:
        candidate = cwd / f"{name_input}{ext}"
        if candidate.exists(): return candidate.resolve()
    return None

def send_command(db_path: str):
    try:
        from PySide6.QtNetwork import QLocalSocket
        sock = QLocalSocket()
        sock.connectToServer(SOCKET_PATH)
        if not sock.waitForConnected(500): return False
        payload = {"db_path": str(db_path)} if db_path else {"focus": True}
        sock.write(json.dumps(payload).encode('utf-8'))
        sock.flush(); sock.waitForBytesWritten(1000); sock.disconnectFromServer()
        return True
    except: return False

def run_server_process(initial_db=None):
    ensure_datasette_installed()
    try:
        from PySide6.QtCore import QUrl, QTimer, Qt
        from PySide6.QtGui import QAction, QIcon
        from PySide6.QtWidgets import (QApplication, QMainWindow, QToolBar, QVBoxLayout, QWidget, QLineEdit, QStyle)
        from PySide6.QtNetwork import QLocalServer
        from PySide6.QtWebEngineWidgets import QWebEngineView
        try: from PySide6.QtWebEngineCore import QWebEnginePage
        except ImportError: from PySide6.QtWebEngineWidgets import QWebEnginePage
            
    except ImportError as e:
        log_debug(f"CRITICAL IMPORT ERROR: {e}")
        return

    if sys.platform != "win32" and os.path.exists(SOCKET_PATH):
        try: os.unlink(SOCKET_PATH)
        except: pass

    static_dir = pathlib.Path("/tmp/fdatasette_static")
    static_dir.mkdir(exist_ok=True)
    with open(static_dir / "dark.css", "w") as f: f.write(DARK_CSS)
    with open("/tmp/fdatasette_metadata.json", "w") as f: json.dump({"extra_css_urls": ["/static/dark.css"]}, f)

    # --- CUSTOM BROWSER PAGE (Intercepts Manual Links) ---
    class ManualInterceptor(QWebEnginePage):
        def acceptNavigationRequest(self, url, _type, isMainFrame):
            u = url.toString()
            
            # Helper to safely load HTML with a Base URL (Fixes White Screen)
            def safe_load(html):
                self.triggerAction(QWebEnginePage.Stop)
                self.setHtml(html, QUrl("http://fdatasette.local/"))

            if "?manual=" in u:
                fname = u.split("?manual=")[1]
                QTimer.singleShot(10, lambda: safe_load(load_manual_content(fname)))
                return False
                
            if "?index=true" in u:
                QTimer.singleShot(10, lambda: safe_load(generate_manual_index()))
                return False
                
            return True

    class DatasetteWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("FDatasette Intelligence Console")
            self.resize(1200, 800)
            self.datasette_proc = None
            
            self.setStyleSheet("""
                QMainWindow { background-color: #000; }
                QToolBar { background-color: #050505; border-bottom: 2px solid #00ff00; spacing:10px; }
                QLineEdit { background-color: #001100; color: #00ff00; border: 1px solid #004400; font-family: monospace; padding:5px; }
                QToolButton { color: #00ff00; font-weight: bold; background: #002200; border: 1px solid #004400; border-radius: 3px; }
                QToolButton:hover { background-color: #00ff00; color: #000; }
            """)

            root = QWidget(); self.setCentralWidget(root)
            layout = QVBoxLayout(root); layout.setContentsMargins(0,0,0,0)

            self.toolbar = QToolBar(); self.addToolBar(self.toolbar); self.toolbar.setMovable(False)
            
            self.act_help = QAction(" [? MANUALS] ", self)
            
            # Fix: Stop current page, then load index with Base URL
            def open_manuals():
                self.browser.page().triggerAction(QWebEnginePage.Stop)
                self.browser.setHtml(generate_manual_index(), QUrl("http://fdatasette.local/"))
            
            self.act_help.triggered.connect(open_manuals)
            self.toolbar.addAction(self.act_help)

            dummy = QWidget(); dummy.setFixedWidth(20); self.toolbar.addWidget(dummy)
            self.url_bar = QLineEdit(); self.url_bar.setReadOnly(True); self.toolbar.addWidget(self.url_bar)

            self.browser = QWebEngineView()
            self.browser.setPage(ManualInterceptor(self.browser))
            self.browser.setStyleSheet("background: #000;")
            layout.addWidget(self.browser)

            self.server = QLocalServer()
            self.server.listen(SOCKET_PATH)
            self.server.newConnection.connect(self.handle_connection)

            if initial_db: self.load_database(initial_db)
            else: self.browser.setHtml(get_landing_page())

        def load_database(self, db_path):
            if self.datasette_proc: 
                try: self.datasette_proc.kill()
                except: pass
            
            db_name = os.path.basename(db_path)
            self.setWindowTitle(f"Accessing: {db_name}")
            self.url_bar.setText(f"🚀 INJECTING: {db_name}...")

            env = os.environ.copy()
            if TOOLS_DIR.exists(): env["PATH"] = f"{TOOLS_DIR}:{env.get('PATH', '')}"

            cmd = [sys.executable, "-m", "datasette", "serve", db_path, 
                   "-p", str(DATASETTE_PORT), "--cors", 
                   "--metadata", "/tmp/fdatasette_metadata.json",
                   "--static", "static:/tmp/fdatasette_static"]
            
            self.datasette_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
            QTimer.singleShot(1500, lambda: self.browser.load(QUrl(f"http://127.0.0.1:{DATASETTE_PORT}")))
            QTimer.singleShot(1500, lambda: self.url_bar.setText(f"SECURE LINK ESTABLISHED: {db_name}"))

        def handle_connection(self):
            client = self.server.nextPendingConnection()
            if client.waitForReadyRead(100):
                try:
                    data = json.loads(client.readAll().data().decode())
                    if data.get("db_path"): self.load_database(data["db_path"])
                    self.activateWindow(); self.raise_()
                except: pass
            client.disconnectFromServer()

    app = QApplication.instance() or QApplication(sys.argv)
    win = DatasetteWindow()
    win.show()
    sys.exit(app.exec())

# --- MAIN ---
def main():
    args = sys.argv[1:]
    if "--internal-server" in args:
        init = None
        if "--init" in args: 
            try: init = args[args.index("--init") + 1]
            except: pass
        run_server_process(init)
        return

    target_db = ""
    if "--json" in args:
        try: target_db = json.loads(args[args.index("--json") + 1]).get("database", "")
        except: pass
    if not target_db and args and not args[0].startswith("--"): target_db = args[0]
        
    resolved = find_database(target_db)
    if resolved and send_command(str(resolved)): sys.exit(0)

    print("🚀 Initializing Console...")
    server_args = [sys.executable, __file__, "--internal-server"]
    if resolved: server_args += ["--init", str(resolved)]
    env = os.environ.copy()
    
    if sys.platform == "win32": subprocess.Popen(server_args, env=env, creationflags=subprocess.DETACHED_PROCESS)
    else: subprocess.Popen(server_args, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    os._exit(0)

if __name__ == "__main__":
    main()