#!/usr/bin/env python3
"""
game-ide.py — iOS App Development IDE agent function.

PySide6 GUI with:
  - Top: Visual canvas tabs (Preview | Assets | Screenshots) — shows app previews, assets, simulator screenshots
  - Bottom: IDE (file browser | code editor | console)
  - Socket server: agent sends commands (open_file, save_file, run_command, set_project, etc.)

Build pipeline uses iOSBuilder CLI (~/Developer/iosbuilder/build/iosbuilder).

Architecture follows fterminal.py pattern:
  - First invocation launches GUI via subprocess (--internal-server)
  - Subsequent calls send JSON commands via Unix socket

Workflow for building iOS apps:
  1. init_project — create new project from template (swiftui or single-view)
  2. open_file / save_file — edit Swift source files (or use nano.py)
  3. build — compile the project, check for errors
  4. run — build and launch on iOS simulator
  5. archive — create .ipa for distribution
  6. sideload — install .ipa on physical USB device
  7. devices — list connected simulators and physical devices
"""

import sys, json, argparse, os, subprocess, time, pathlib, glob
from typing import Optional

IOSBUILDER_BIN = os.path.expanduser("~/Developer/iosbuilder/build/iosbuilder")

TOOL_METADATA = {
    "name": "game_ide",
    "description": (
        "iOS app development IDE with visual preview and code editor. "
        "Uses iOSBuilder CLI for building, running, archiving iOS apps. "
        "Modes: launch (open IDE), open_file, save_file, show_sprite, show_map, "
        "run_command, set_project, init_project, build, run, archive, clean, "
        "sideload, devices, status, log. "
        "The IDE has an integrated code editor, file browser, console, and visual preview. "
        "Agent can stream progress updates to the IDE log panel. "
        "Launch: {\"mode\":\"launch\",\"project_dir\":\"~/Developer/MyApp\"} "
        "Open file: {\"mode\":\"open_file\",\"file_path\":\"~/Dev/MyApp/Sources/App.swift\"} "
        "Build: {\"mode\":\"build\",\"project_dir\":\"~/Developer/MyApp\"} "
        "Run on simulator: {\"mode\":\"run\",\"project_dir\":\"~/Developer/MyApp\"} "
        "Archive IPA: {\"mode\":\"archive\",\"project_dir\":\"~/Developer/MyApp\"} "
        "Sideload to device: {\"mode\":\"sideload\",\"project_dir\":\"~/Developer/MyApp\"} "
        "List devices: {\"mode\":\"devices\"} "
        "Init new project: {\"mode\":\"init_project\",\"name\":\"MyApp\",\"template\":\"swiftui\"} "
        "Run cmd: {\"mode\":\"run_command\",\"command\":\"iosbuilder build\"}"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["launch", "open_file", "save_file", "show_sprite", "show_map",
                         "run_command", "set_project", "init_project", "build", "run",
                         "archive", "clean", "sideload", "devices", "status", "log"],
                "default": "launch",
                "description": "IDE command mode."
            },
            "file_path": {"type": "string", "description": "File path for open/save."},
            "content": {"type": "string", "description": "File content for save_file."},
            "command": {"type": "string", "description": "Shell command for run_command."},
            "project_dir": {"type": "string", "description": "Project root directory."},
            "text": {"type": "string", "description": "Log message for log mode."},
            "name": {"type": "string", "description": "Project/sprite/map name."},
            "template": {"type": "string", "description": "Template name for init_project (swiftui or single-view)."},
            "path": {"type": "string", "description": "Image path for show_sprite/show_map."},
            "config": {"type": "string", "description": "Build config: debug or release.", "default": "debug"},
            "platform": {"type": "string", "description": "Target: simulator or device.", "default": "simulator"},
        }
    }
}

SCRIPT_PATH = pathlib.Path(__file__).resolve()
SOCKET_NAME = f"/tmp/GameIDE_Sock_{os.environ.get('USER', 'user')}"

# ═══════════════════════════════════════════════════════════════
#  CLIENT — send command to running IDE
# ═══════════════════════════════════════════════════════════════

def send_command(payload: dict) -> bool:
    """Send JSON command to running IDE via Unix socket."""
    try:
        import socket
        if not os.path.exists(SOCKET_NAME):
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(SOCKET_NAME)
        s.send(json.dumps(payload).encode('utf-8'))
        try:
            resp = s.recv(4096).decode('utf-8')
            s.close()
            return True
        except Exception:
            s.close()
            return True
    except Exception:
        return False


def _find_iosbuilder():
    """Find iosbuilder binary."""
    if os.path.isfile(IOSBUILDER_BIN):
        return IOSBUILDER_BIN
    # Try PATH
    import shutil
    found = shutil.which("iosbuilder")
    if found:
        return found
    return IOSBUILDER_BIN  # fallback


def _run_iosbuilder(args, cwd=None, timeout=120):
    """Run iosbuilder CLI command and return (success, output)."""
    bin_path = _find_iosbuilder()
    cmd = [bin_path] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                cwd=cwd, timeout=timeout)
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except FileNotFoundError:
        return False, f"iosbuilder not found at {bin_path}. Build it: cd ~/Developer/iosbuilder && ./build.sh"
    except Exception as e:
        return False, str(e)


# ═══════════════════════════════════════════════════════════════
#  SERVER — PySide6 GUI Application
# ═══════════════════════════════════════════════════════════════

def run_server(project_dir=None):
    try:
        from PySide6.QtCore import (Qt, QProcess, QSize, QRect, QTimer,
                                     QThread, Signal, QObject)
        from PySide6.QtGui import (QAction, QFont, QKeySequence, QTextCursor, QColor,
                                    QPalette, QPainter, QFontMetrics, QTextFormat,
                                    QPixmap, QImage)
        from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QSplitter,
                                        QTreeView, QFileSystemModel, QPlainTextEdit, QTextEdit,
                                        QTabWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
                                        QPushButton, QLabel, QToolBar, QStatusBar, QScrollArea,
                                        QGraphicsView, QGraphicsScene, QGraphicsPixmapItem)
    except ImportError:
        print("Error: PySide6 required. pip install PySide6")
        return

    import socket
    import threading

    iosbuilder = _find_iosbuilder()

    # ── Dark palette ──
    def set_dark(app):
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor("#1a1a2e"))
        pal.setColor(QPalette.WindowText, QColor("#d4d4d4"))
        pal.setColor(QPalette.Base, QColor("#111317"))
        pal.setColor(QPalette.AlternateBase, QColor("#1a1a2e"))
        pal.setColor(QPalette.Text, QColor("#d6dde6"))
        pal.setColor(QPalette.Button, QColor("#252538"))
        pal.setColor(QPalette.ButtonText, QColor("#cfd3da"))
        pal.setColor(QPalette.Highlight, QColor("#3a5f8a"))
        pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        app.setPalette(pal)

    # ── Line-numbered code editor ──
    class _LineArea(QWidget):
        def __init__(self, editor):
            super().__init__(editor)
            self.editor = editor
        def sizeHint(self):
            return QSize(self.editor._line_width(), 0)
        def paintEvent(self, ev):
            self.editor._paint_lines(ev)

    class CodeEditor(QPlainTextEdit):
        def __init__(self):
            super().__init__()
            self._la = _LineArea(self)
            self.blockCountChanged.connect(self._upd_w)
            self.updateRequest.connect(self._upd_area)
            self.cursorPositionChanged.connect(self._hl_line)
            self._upd_w(0)
            self._hl_line()

        def _line_width(self):
            d = max(3, len(str(max(1, self.blockCount()))))
            return 10 + QFontMetrics(self.font()).horizontalAdvance("9") * d + 6

        def _upd_w(self, _):
            self.setViewportMargins(self._line_width(), 0, 0, 0)

        def _upd_area(self, rect, dy):
            if dy:
                self._la.scroll(0, dy)
            else:
                self._la.update(0, rect.y(), self._la.width(), rect.height())

        def resizeEvent(self, e):
            super().resizeEvent(e)
            cr = self.contentsRect()
            self._la.setGeometry(QRect(cr.left(), cr.top(), self._line_width(), cr.height()))

        def _paint_lines(self, ev):
            p = QPainter(self._la)
            p.fillRect(ev.rect(), QColor("#1e1e2e"))
            fg = QColor("#6e6e8e")
            block = self.firstVisibleBlock()
            bn = block.blockNumber()
            top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
            bot = top + int(self.blockBoundingRect(block).height())
            fm = QFontMetrics(self.font())
            while block.isValid() and top <= ev.rect().bottom():
                if block.isVisible() and bot >= ev.rect().top():
                    p.setPen(fg)
                    p.drawText(0, top, self._la.width() - 6, fm.height(), Qt.AlignRight, str(bn + 1))
                block = block.next()
                top = bot
                bot = top + int(self.blockBoundingRect(block).height())
                bn += 1

        def _hl_line(self):
            if self.isReadOnly():
                return
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(58, 95, 138, 50))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            self.setExtraSelections([sel])

    # ── Console widget ──
    class Console(QWidget):
        def __init__(self, font):
            super().__init__()
            v = QVBoxLayout(self)
            v.setContentsMargins(0, 0, 0, 0)
            self.out = QPlainTextEdit()
            self.out.setReadOnly(True)
            self.out.setFont(font)
            self.out.setStyleSheet("background:#0a0a14; color:#3adf6a;")
            self.out.setMaximumBlockCount(5000)
            v.addWidget(self.out, 1)
            row = QHBoxLayout()
            self.cmd = QLineEdit()
            self.cmd.setPlaceholderText("Command...")
            self.cmd.setStyleSheet("background:#151520; color:#3adf6a; border:1px solid #333; padding:4px;")
            run_btn = QPushButton("Run")
            run_btn.setStyleSheet("background:#2a3a5a; color:#aabbdd; padding:4px 12px;")
            row.addWidget(self.cmd, 1)
            row.addWidget(run_btn)
            v.addLayout(row)

            self.proc = QProcess(self)
            self.proc.setProgram("/bin/zsh")
            self.proc.setArguments(["-l"])
            self.proc.setProcessChannelMode(QProcess.MergedChannels)
            self.proc.readyReadStandardOutput.connect(self._read)
            self.proc.start()

            run_btn.clicked.connect(self._send)
            self.cmd.returnPressed.connect(self._send)

        def write(self, text):
            self.out.moveCursor(QTextCursor.End)
            self.out.insertPlainText(text)
            self.out.moveCursor(QTextCursor.End)

        def _read(self):
            data = self.proc.readAllStandardOutput().data().decode(errors="ignore")
            if data:
                self.write(data)

        def _send(self):
            text = self.cmd.text().strip()
            self.cmd.clear()
            if not text:
                return
            self.write(f"\n> {text}\n")
            if self.proc.state() == QProcess.Running:
                self.proc.write((text + "\n").encode())

        def run_external(self, cmd):
            self.write(f"\n> {cmd}\n")
            if self.proc.state() == QProcess.Running:
                self.proc.write((cmd + "\n").encode())

    # ── Visual canvas for previews ──
    class VisualCanvas(QGraphicsView):
        def __init__(self):
            super().__init__()
            self.scene = QGraphicsScene()
            self.setScene(self.scene)
            self.setStyleSheet("background:#0d0d1a; border:1px solid #2a2a4a;")
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self._pixmap_item = None

        def show_image(self, img_path):
            self.scene.clear()
            pix = QPixmap(img_path)
            if not pix.isNull():
                self._pixmap_item = self.scene.addPixmap(pix)
                self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)

        def show_pixmap(self, pixmap):
            self.scene.clear()
            if not pixmap.isNull():
                self._pixmap_item = self.scene.addPixmap(pixmap)
                self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)

        def wheelEvent(self, ev):
            factor = 1.15 if ev.angleDelta().y() > 0 else 0.87
            self.scale(factor, factor)

    # ── Socket server thread ──
    class SocketHandler(QObject):
        command_received = Signal(dict)

        def __init__(self, sock_path):
            super().__init__()
            self.sock_path = sock_path
            self._running = True

        def run(self):
            if os.path.exists(self.sock_path):
                os.unlink(self.sock_path)
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(self.sock_path)
            srv.listen(5)
            srv.settimeout(1.0)
            while self._running:
                try:
                    conn, _ = srv.accept()
                    data = conn.recv(65536)
                    if data:
                        try:
                            cmd = json.loads(data.decode('utf-8'))
                            self.command_received.emit(cmd)
                            conn.send(b'{"ok":true}')
                        except Exception:
                            conn.send(b'{"ok":false}')
                    conn.close()
                except socket.timeout:
                    continue
                except Exception:
                    continue
            srv.close()
            if os.path.exists(self.sock_path):
                os.unlink(self.sock_path)

        def stop(self):
            self._running = False

    # ── Main window ──
    class GameIDEWindow(QMainWindow):
        def __init__(self, project_dir=None):
            super().__init__()
            self.project_dir = project_dir or os.path.expanduser("~/Developer/MyApp")
            self.current_file = None
            self.modified = False
            self.iosbuilder = iosbuilder
            self.setWindowTitle(f"iOSBuilder IDE — {os.path.basename(self.project_dir)}")
            self.resize(1280, 860)

            mono = QFont("Menlo", 12)

            # Central widget
            central = QWidget()
            self.setCentralWidget(central)
            root_layout = QVBoxLayout(central)
            root_layout.setContentsMargins(0, 0, 0, 0)

            # Main vertical splitter: visual top, IDE bottom
            main_split = QSplitter(Qt.Vertical)
            root_layout.addWidget(main_split)

            # ── TOP: Visual canvas with tabs ──
            visual_tabs = QTabWidget()
            visual_tabs.setStyleSheet(
                "QTabBar::tab { background:#1a1a2e; color:#8888aa; padding:6px 16px; }"
                "QTabBar::tab:selected { background:#252538; color:#4ae68a; border-bottom:2px solid #4ae68a; }"
            )

            self.preview_canvas = VisualCanvas()
            self.assets_canvas = VisualCanvas()
            self.screenshot_canvas = VisualCanvas()

            visual_tabs.addTab(self.preview_canvas, "Preview")
            visual_tabs.addTab(self.assets_canvas, "Assets")
            visual_tabs.addTab(self.screenshot_canvas, "Screenshots")

            # Log panel (agent progress)
            self.log_panel = QPlainTextEdit()
            self.log_panel.setReadOnly(True)
            self.log_panel.setMaximumHeight(80)
            self.log_panel.setFont(QFont("Menlo", 10))
            self.log_panel.setStyleSheet("background:#0a0a14; color:#5a8aaa; border:1px solid #1a1a3a;")

            visual_container = QWidget()
            vc_layout = QVBoxLayout(visual_container)
            vc_layout.setContentsMargins(0, 0, 0, 0)
            vc_layout.addWidget(visual_tabs, 1)
            vc_layout.addWidget(self.log_panel)

            main_split.addWidget(visual_container)

            # ── BOTTOM: IDE (file tree | editor | console) ──
            ide_split = QSplitter(Qt.Horizontal)

            # File browser
            self.fs_model = QFileSystemModel()
            self.fs_model.setReadOnly(False)
            self.fs_model.setRootPath(self.project_dir)
            self.tree = QTreeView()
            self.tree.setModel(self.fs_model)
            self.tree.setRootIndex(self.fs_model.index(self.project_dir))
            self.tree.setMinimumWidth(200)
            self.tree.setMaximumWidth(300)
            self.tree.doubleClicked.connect(self._open_from_tree)
            self.tree.setStyleSheet("background:#111317; color:#aabbcc;")
            for col in [1, 2, 3]:
                self.tree.hideColumn(col)
            ide_split.addWidget(self.tree)

            # Editor
            editor_container = QWidget()
            ec_layout = QVBoxLayout(editor_container)
            ec_layout.setContentsMargins(0, 0, 0, 0)

            self.file_label = QLabel("untitled")
            self.file_label.setStyleSheet("color:#4ae68a; padding:4px; background:#151520;")
            ec_layout.addWidget(self.file_label)

            self.editor = CodeEditor()
            self.editor.setFont(mono)
            self.editor.setStyleSheet("background:#111317; color:#d6dde6; border:none;")
            self.editor.textChanged.connect(self._on_modified)
            ec_layout.addWidget(self.editor, 1)

            ide_split.addWidget(editor_container)

            # Console
            self.console = Console(mono)
            ide_split.addWidget(self.console)
            ide_split.setStretchFactor(0, 0)
            ide_split.setStretchFactor(1, 2)
            ide_split.setStretchFactor(2, 1)

            main_split.addWidget(ide_split)
            main_split.setStretchFactor(0, 2)
            main_split.setStretchFactor(1, 1)

            # ── Toolbar ──
            tb = QToolBar()
            tb.setStyleSheet("background:#1a1a2e; border-bottom:1px solid #2a2a4a;")
            self.addToolBar(tb)

            actions = [
                ("New", self._new_file),
                ("Open", self._open_file_dialog),
                ("Save", self._save_file),
                ("Save As", self._save_as),
                ("|", None),
                ("Build (F5)", self._build_project),
                ("Run", self._run_project),
                ("Archive", self._archive_project),
                ("Sideload", self._sideload_project),
                ("Clean", self._clean_project),
                ("|", None),
                ("Init Project", self._init_project),
                ("Simulator", lambda: self.console.run_external(
                    f"'{self.iosbuilder}' simulator list")),
                ("Devices", lambda: self.console.run_external(
                    f"'{self.iosbuilder}' devices")),
                ("|", None),
                ("Refresh Preview", self._refresh_preview),
            ]
            for name, fn in actions:
                if name == "|":
                    tb.addSeparator()
                else:
                    a = QAction(name, self)
                    if fn:
                        a.triggered.connect(fn)
                    if name == "Build (F5)":
                        a.setShortcut(Qt.Key_F5)
                    elif name == "Save":
                        a.setShortcut(QKeySequence.Save)
                    elif name == "Open":
                        a.setShortcut(QKeySequence.Open)
                    elif name == "New":
                        a.setShortcut(QKeySequence.New)
                    tb.addAction(a)

            self.setStatusBar(QStatusBar())
            self.statusBar().setStyleSheet("color:#6e6e8e;")
            self._update_status()

            # ── Socket server ──
            self.sock_handler = SocketHandler(SOCKET_NAME)
            self.sock_thread = QThread()
            self.sock_handler.moveToThread(self.sock_thread)
            self.sock_handler.command_received.connect(self._handle_command)
            self.sock_thread.started.connect(self.sock_handler.run)
            self.sock_thread.start()

            # Initial load
            self.log("iOSBuilder IDE started. Project: " + self.project_dir)
            QTimer.singleShot(500, self._refresh_preview)

            # Set console working dir
            if project_dir:
                self.console.run_external(f"cd '{self.project_dir}'")

        def log(self, text):
            self.log_panel.moveCursor(QTextCursor.End)
            self.log_panel.insertPlainText(text + "\n")
            self.log_panel.moveCursor(QTextCursor.End)

        # ── iOSBuilder build commands ──
        def _build_project(self):
            self.log("Building project...")
            self.console.run_external(
                f"cd '{self.project_dir}' && '{self.iosbuilder}' build")

        def _run_project(self):
            self.log("Building and running on simulator...")
            self.console.run_external(
                f"cd '{self.project_dir}' && '{self.iosbuilder}' run")

        def _archive_project(self):
            self.log("Archiving IPA...")
            self.console.run_external(
                f"cd '{self.project_dir}' && '{self.iosbuilder}' archive")

        def _sideload_project(self):
            self.log("Sideloading to connected device...")
            self.console.run_external(
                f"cd '{self.project_dir}' && '{self.iosbuilder}' sideload")

        def _clean_project(self):
            self.log("Cleaning build...")
            self.console.run_external(
                f"cd '{self.project_dir}' && '{self.iosbuilder}' clean")

        def _init_project(self):
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "New iOS Project", "Project name:")
            if ok and name:
                tpl, ok2 = QInputDialog.getItem(self, "Template",
                    "Select template:", ["swiftui", "single-view"], 0, False)
                if ok2:
                    parent_dir = os.path.dirname(self.project_dir)
                    self.console.run_external(
                        f"cd '{parent_dir}' && "
                        f"'{self.iosbuilder}' init '{name}' --template '{tpl}'")
                    new_dir = os.path.join(parent_dir, name)
                    QTimer.singleShot(2000, lambda: self._switch_project(new_dir))

        def _switch_project(self, new_dir):
            if os.path.isdir(new_dir):
                self.project_dir = new_dir
                self.fs_model.setRootPath(new_dir)
                self.tree.setRootIndex(self.fs_model.index(new_dir))
                self.setWindowTitle(f"iOSBuilder IDE — {os.path.basename(new_dir)}")
                self.log(f"Switched to project: {new_dir}")
                self.console.run_external(f"cd '{new_dir}'")

        # ── Command handler (from agent socket) ──
        def _handle_command(self, cmd):
            mode = cmd.get('cmd', cmd.get('mode', ''))

            if mode == 'show_sprite' or mode == 'show_preview':
                path = cmd.get('path', '')
                if path and os.path.exists(path):
                    self.preview_canvas.show_image(path)
                    self.log(f"Preview loaded: {os.path.basename(path)}")

            elif mode == 'show_map' or mode == 'show_assets':
                path = cmd.get('path', '')
                if path and os.path.exists(path):
                    self.assets_canvas.show_image(path)
                    self.log(f"Assets loaded: {os.path.basename(path)}")

            elif mode == 'show_screenshot':
                path = cmd.get('path', '')
                if path and os.path.exists(path):
                    self.screenshot_canvas.show_image(path)
                    self.log(f"Screenshot loaded: {os.path.basename(path)}")

            elif mode == 'open_file':
                fpath = os.path.expanduser(cmd.get('file_path', cmd.get('path', '')))
                if fpath and os.path.isfile(fpath):
                    self._load_file(fpath)

            elif mode == 'save_file':
                fpath = os.path.expanduser(cmd.get('file_path', ''))
                content = cmd.get('content', '')
                if fpath:
                    os.makedirs(os.path.dirname(os.path.abspath(fpath)), exist_ok=True)
                    with open(fpath, 'w', encoding='utf-8') as f:
                        f.write(content)
                    self.log(f"Saved: {fpath}")
                    if self.current_file and os.path.abspath(fpath) == os.path.abspath(self.current_file):
                        self.editor.setPlainText(content)
                        self.modified = False

            elif mode == 'run_command':
                command = cmd.get('command', '')
                if command:
                    self.console.run_external(command)

            elif mode == 'build':
                config = cmd.get('config', 'debug')
                platform = cmd.get('platform', 'simulator')
                pdir = os.path.expanduser(cmd.get('project_dir', self.project_dir))
                extra = ""
                if config == 'release':
                    extra += " --release"
                self.console.run_external(
                    f"cd '{pdir}' && '{self.iosbuilder}' build{extra}")

            elif mode == 'run':
                pdir = os.path.expanduser(cmd.get('project_dir', self.project_dir))
                self.console.run_external(
                    f"cd '{pdir}' && '{self.iosbuilder}' run")

            elif mode == 'archive':
                pdir = os.path.expanduser(cmd.get('project_dir', self.project_dir))
                self.console.run_external(
                    f"cd '{pdir}' && '{self.iosbuilder}' archive")

            elif mode == 'sideload':
                pdir = os.path.expanduser(cmd.get('project_dir', self.project_dir))
                self.console.run_external(
                    f"cd '{pdir}' && '{self.iosbuilder}' sideload")

            elif mode == 'clean':
                pdir = os.path.expanduser(cmd.get('project_dir', self.project_dir))
                self.console.run_external(
                    f"cd '{pdir}' && '{self.iosbuilder}' clean")

            elif mode == 'init_project':
                name = cmd.get('name', 'MyApp')
                tpl = cmd.get('template', 'swiftui')
                pdir = os.path.expanduser(cmd.get('project_dir', self.project_dir))
                parent_dir = os.path.dirname(pdir) if os.path.isdir(pdir) else pdir
                self.console.run_external(
                    f"cd '{parent_dir}' && '{self.iosbuilder}' init '{name}' --template '{tpl}'")
                new_dir = os.path.join(parent_dir, name)
                QTimer.singleShot(2000, lambda: self._switch_project(new_dir))

            elif mode == 'set_project':
                pdir = os.path.expanduser(cmd.get('project_dir', cmd.get('path', '')))
                if pdir and os.path.isdir(pdir):
                    self._switch_project(pdir)

            elif mode == 'devices':
                self.console.run_external(
                    f"'{self.iosbuilder}' devices && '{self.iosbuilder}' simulator list")

            elif mode == 'log':
                self.log(cmd.get('text', ''))

            elif mode == 'status':
                self.log(f"Status: running | Project: {self.project_dir}")

            elif mode == 'focus':
                self.activateWindow()
                self.raise_()

        # ── File operations ──
        def _load_file(self, path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.editor.setPlainText(content)
                self.current_file = path
                self.modified = False
                self._update_status()
                self._set_tree_root(os.path.dirname(path))
            except Exception as e:
                self.log(f"Error opening {path}: {e}")

        def _set_tree_root(self, dirpath):
            if os.path.isdir(dirpath):
                self.fs_model.setRootPath(dirpath)
                self.tree.setRootIndex(self.fs_model.index(dirpath))

        def _open_from_tree(self, idx):
            path = self.fs_model.filePath(idx)
            if os.path.isfile(path):
                self._load_file(path)
            elif os.path.isdir(path):
                self._set_tree_root(path)

        def _new_file(self):
            self.editor.setPlainText("")
            self.current_file = None
            self.modified = False
            self._update_status()

        def _open_file_dialog(self):
            from PySide6.QtWidgets import QFileDialog
            fn, _ = QFileDialog.getOpenFileName(self, "Open", self.project_dir)
            if fn:
                self._load_file(fn)

        def _save_file(self):
            if not self.current_file:
                return self._save_as()
            try:
                with open(self.current_file, 'w', encoding='utf-8') as f:
                    f.write(self.editor.toPlainText())
                self.modified = False
                self._update_status("Saved")
                self.log(f"Saved: {self.current_file}")
            except Exception as e:
                self.log(f"Save error: {e}")

        def _save_as(self):
            from PySide6.QtWidgets import QFileDialog
            fn, _ = QFileDialog.getSaveFileName(self, "Save As", self.project_dir)
            if fn:
                self.current_file = fn
                self._save_file()

        def _on_modified(self):
            self.modified = True
            self._update_status()

        def _update_status(self, msg=""):
            name = os.path.basename(self.current_file) if self.current_file else "untitled"
            mod = " *" if self.modified else ""
            self.file_label.setText(f"  {name}{mod}")
            try:
                cur = self.editor.textCursor()
                ln = cur.blockNumber() + 1
                col = cur.positionInBlock() + 1
                self.statusBar().showMessage(f"{name}{mod} | Line {ln}, Col {col}  {msg}")
            except Exception:
                self.statusBar().showMessage(f"{name}{mod}  {msg}")

        # ── Visual refresh ──
        def _refresh_preview(self):
            # Look for screenshots in outputs or build directory
            for search_dir in ['outputs', '.build', 'screenshots']:
                full = os.path.join(self.project_dir, search_dir)
                if not os.path.isdir(full):
                    continue
                pngs = sorted(glob.glob(os.path.join(full, '**', '*.png'), recursive=True))
                if pngs:
                    self.preview_canvas.show_image(pngs[-1])
                    self.log(f"Preview: loaded from {search_dir}")
                    return
            # Also check for asset catalogs
            assets_dir = os.path.join(self.project_dir, 'Resources')
            if os.path.isdir(assets_dir):
                pngs = sorted(glob.glob(os.path.join(assets_dir, '**', '*.png'), recursive=True))
                if pngs:
                    self.assets_canvas.show_image(pngs[-1])

        def closeEvent(self, ev):
            self.sock_handler.stop()
            self.sock_thread.quit()
            self.sock_thread.wait(2000)
            super().closeEvent(ev)

    # ── Launch ──
    app = QApplication.instance() or QApplication(sys.argv)
    set_dark(app)
    win = GameIDEWindow(project_dir)
    win.show()
    sys.exit(app.exec())


# ═══════════════════════════════════════════════════════════════
#  MAIN ENTRY
# ═══════════════════════════════════════════════════════════════

def handle_game_ide(data):
    mode = data.get('mode', 'launch')

    # Modes that can run headless (no GUI needed)
    if mode == 'build':
        project_dir = os.path.expanduser(data.get('project_dir', '.'))
        config = data.get('config', 'debug')
        args = ['build']
        if config == 'release':
            args.append('--release')
        ok, output = _run_iosbuilder(args, cwd=project_dir)
        # Also send to IDE if running
        send_command({"cmd": "log", "text": output})
        return f"Build {'succeeded' if ok else 'FAILED'}.\n{output}"

    if mode == 'run':
        project_dir = os.path.expanduser(data.get('project_dir', '.'))
        ok, output = _run_iosbuilder(['run'], cwd=project_dir)
        send_command({"cmd": "log", "text": output})
        if ok:
            return f"SUCCESS: App built and launched on simulator.\n{output}"
        return f"FAILED: Build or launch failed.\n{output}"

    if mode == 'archive':
        project_dir = os.path.expanduser(data.get('project_dir', '.'))
        ok, output = _run_iosbuilder(['archive'], cwd=project_dir)
        send_command({"cmd": "log", "text": output})
        return f"Archive {'succeeded' if ok else 'FAILED'}.\n{output}"

    if mode == 'sideload':
        project_dir = os.path.expanduser(data.get('project_dir', '.'))
        ok, output = _run_iosbuilder(['sideload'], cwd=project_dir)
        send_command({"cmd": "log", "text": output})
        return f"Sideload {'succeeded' if ok else 'FAILED'}.\n{output}"

    if mode == 'clean':
        project_dir = os.path.expanduser(data.get('project_dir', '.'))
        ok, output = _run_iosbuilder(['clean'], cwd=project_dir)
        return f"Clean {'succeeded' if ok else 'FAILED'}.\n{output}"

    if mode == 'init_project':
        name = data.get('name', 'MyApp')
        tpl = data.get('template', 'swiftui')
        project_dir = os.path.expanduser(data.get('project_dir', '~/Developer'))
        ok, output = _run_iosbuilder(
            ['init', name, '--template', tpl],
            cwd=project_dir)
        if ok:
            new_path = os.path.join(project_dir, name)
            send_command({"cmd": "set_project", "path": new_path})
            return f"Project '{name}' created at {new_path}.\n{output}"
        return f"Init FAILED.\n{output}"

    if mode == 'devices':
        ok1, sim_out = _run_iosbuilder(['simulator', 'list'])
        ok2, dev_out = _run_iosbuilder(['devices'])
        return f"Simulators:\n{sim_out}\n\nPhysical Devices:\n{dev_out}"

    if mode == 'status':
        project_dir = os.path.expanduser(data.get('project_dir', '.'))
        ok, output = _run_iosbuilder(['status'], cwd=project_dir)
        return output if ok else f"Status check failed: {output}"

    if mode == 'launch' or mode == '':
        project_dir = os.path.expanduser(data.get('project_dir', '~/Developer/MyApp'))

        # Try sending focus command to existing IDE
        if send_command({"cmd": "focus"}):
            if project_dir:
                send_command({"cmd": "set_project", "path": project_dir})
            return "iOSBuilder IDE already running — focused."

        # Launch new IDE window
        env = os.environ.copy()
        args = [sys.executable, str(SCRIPT_PATH), "--internal-server"]
        if project_dir:
            args += ["--project", project_dir]
        subprocess.Popen(args, env=env, start_new_session=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        return f"iOSBuilder IDE launched. Project: {project_dir}"

    # All other modes send to running IDE
    if not send_command({"cmd": mode, **data}):
        # IDE not running — launch it first
        project_dir = os.path.expanduser(data.get('project_dir', '~/Developer/MyApp'))
        env = os.environ.copy()
        args = [sys.executable, str(SCRIPT_PATH), "--internal-server"]
        if project_dir:
            args += ["--project", project_dir]
        subprocess.Popen(args, env=env, start_new_session=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        # Retry
        send_command({"cmd": mode, **data})
        return f"iOSBuilder IDE launched and command '{mode}' sent."

    return f"iOSBuilder IDE: {mode} executed."


def main():
    args_list = sys.argv[1:]

    if "--internal-server" in args_list:
        project = None
        if "--project" in args_list:
            idx = args_list.index("--project")
            if idx + 1 < len(args_list):
                project = args_list[idx + 1]
        run_server(project)
        return

    # Normal tool invocation
    if "--json" in args_list:
        try:
            idx = args_list.index("--json")
            raw = args_list[idx + 1]
            data = json.loads(raw)
            print(handle_game_ide(data))
        except Exception as e:
            print(f"Error: {e}")
    else:
        # Default: just launch
        print(handle_game_ide({"mode": "launch"}))


if __name__ == "__main__":
    main()
