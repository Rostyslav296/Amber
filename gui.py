#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, sys, pathlib, datetime, traceback
from typing import Optional

# Added QProcessEnvironment to the imports
from PySide6.QtCore import Qt, QTimer, QProcess, QProcessEnvironment
from PySide6.QtGui import QPalette, QColor, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QLabel, QMessageBox
)

# ------------------------------------------------------------
# App basics
# ------------------------------------------------------------
APP_NAME = "AgentF"
RES_DIR  = pathlib.Path(__file__).resolve().parent
INSTALLER = RES_DIR / "installer.py"

LOG_DIR = pathlib.Path.home() / "Library" / "Logs" / APP_NAME
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / f"{APP_NAME}_{datetime.datetime.now():%Y-%m-%d_%H-%M-%S}.log"
_log_fh = open(LOG_PATH, "a", buffering=1, encoding="utf-8", errors="replace")

def log(*a):
    print(" ".join(map(str, a)), file=_log_fh, flush=True)

def apply_dark(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor(18,18,18))
    p.setColor(QPalette.WindowText, Qt.white)
    p.setColor(QPalette.Base, QColor(12,12,12))
    p.setColor(QPalette.Text, Qt.white)
    p.setColor(QPalette.Button, QColor(28,28,28))
    p.setColor(QPalette.ButtonText, Qt.white)
    p.setColor(QPalette.Highlight, QColor(53,132,228))
    p.setColor(QPalette.HighlightedText, Qt.white)
    app.setPalette(p)

# ------------------------------------------------------------
# Terminal Window (QProcess-based, like m-ide.py ConsoleTab)
# ------------------------------------------------------------
class TerminalWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 660)

        # UI
        root = QWidget(self); self.setCentralWidget(root)
        v = QVBoxLayout(root); v.setContentsMargins(10,10,10,10); v.setSpacing(8)

        title = QLabel(f"{APP_NAME} — Terminal")
        title.setStyleSheet("font-size:18px;font-weight:600;color:#eee;")
        v.addWidget(title)

        self.term = QTextEdit(self)
        self.term.setReadOnly(True)
        self.term.setStyleSheet(
            "background:#0b0b0b;color:#e6e6e6;"
            "font-family: ui-monospace, Menlo, Monaco, Consolas, 'SF Mono', monospace;"
            "font-size: 13px; line-height: 1.35;"
        )
        v.addWidget(self.term, 1)

        row = QHBoxLayout(); row.setSpacing(8)
        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Type a command and press Enter…")
        self.input.returnPressed.connect(self.on_send)
        self.btnClear = QPushButton("Clear", self); self.btnClear.clicked.connect(self.on_clear)
        row.addWidget(self.input, 1); row.addWidget(self.btnClear)
        v.addLayout(row)

        info = QLabel(f"Resources: {RES_DIR}   ·   Log: {LOG_PATH}")
        info.setStyleSheet("color:#9a9a9a;font-size:12px;")
        v.addWidget(info)

        # Shell process (exactly like m-ide ConsoleTab)
        self.proc = QProcess(self)
        self.proc.setProgram("/bin/zsh")
        self.proc.setArguments(["-l"])                   # login shell (loads your env like Terminal)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)  # stdout+stderr together
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(
            lambda code, _sig: self._append(f"\n[console exited with code {code}]\n")
        )

        # Clean environment: avoid user site injections from frozen app
        # FIX: Using QProcessEnvironment.systemEnvironment() here
        env = QProcessEnvironment.systemEnvironment()
        for bad in ("PYTHONHOME","PYTHONSAFEPATH","PYTHONPATH","PYTHONEXECUTABLE","PYTHONUSERBASE"):
            try: env.remove(bad)
            except Exception: pass
            
        # Ensure terminal-like behavior
        env.insert("TERM", "xterm-256color")
        env.insert("PYTHONNOUSERSITE", "1")
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("AGENTF_BOOTSTRAPPED", "1")
        
        # --- CRITICAL FIX: Disable Tokenizer Parallelism in UI Terminal ---
        env.insert("TOKENIZERS_PARALLELISM", "false")
        
        self.proc.setProcessEnvironment(env)

        # Start in app Resources
        self.proc.setWorkingDirectory(str(RES_DIR))
        self.proc.start()

        self._append("[Terminal started: /bin/zsh -l]\n")

    # ---- stream helpers
    def _append(self, s: str):
        self.term.moveCursor(QTextCursor.End)
        self.term.insertPlainText(s)
        self.term.moveCursor(QTextCursor.End)
        log(s)

    def _read(self):
        data = self.proc.readAllStandardOutput().data().decode(errors="replace")
        if data:
            self._append(data)

    # ---- actions
    def on_send(self):
        text = self.input.text()
        self.input.clear()
        if self.proc.state() == QProcess.Running:
            # Echo for responsiveness; shell will also echo its own prompt/line as configured
            if text:
                self._append(f"{text}\n")
            self.proc.write((text + "\n").encode())

    def on_clear(self):
        self.term.clear()

    def closeEvent(self, ev):
        try:
            if self.proc and self.proc.state() == QProcess.Running:
                self.proc.terminate()
        except Exception:
            pass
        super().closeEvent(ev)

# ------------------------------------------------------------
# Bootstrap (runs installer, then shows the terminal)
# ------------------------------------------------------------
class BootstrapWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Preparing…")
        self.resize(720, 260)

        root = QWidget(self); self.setCentralWidget(root)
        v = QVBoxLayout(root); v.setContentsMargins(10,10,10,10); v.setSpacing(8)

        self.status = QLabel("Checking dependencies…")
        self.status.setStyleSheet("font-size:15px;color:#e6e6e6;")
        v.addWidget(self.status)

        self.logBox = QTextEdit(self)
        self.logBox.setReadOnly(True)
        self.logBox.setMinimumHeight(140)
        self.logBox.setStyleSheet(
            "background:#161616;color:#e6e6e6;"
            "font-family: ui-monospace, Menlo, Monaco, Consolas, 'SF Mono';"
            "font-size: 12px;"
        )
        v.addWidget(self.logBox, 1)

        QTimer.singleShot(30, self.run_installer)

    def _append(self, s: str):
        self.logBox.append(s); log(s)

    def run_installer(self):
        try:
            if not INSTALLER.exists():
                raise FileNotFoundError(f"installer.py missing at {INSTALLER}")
            self._append(f"[bootstrap] Running: {INSTALLER}")
            stream = os.popen(f'"{sys.executable}" "{INSTALLER}" --from-gui 2>&1')
            out = stream.read() if stream else ""
            if out:
                for line in out.splitlines():
                    self._append(line)
            self._append("[bootstrap] Completed.]")
            self.term = TerminalWindow(); self.term.show(); self.close()
        except Exception as e:
            err = f"Setup failed: {e}\n\n{traceback.format_exc()}"
            self._append(err)
            QMessageBox.critical(self, "AgentF Error", err)
            sys.exit(1)

# ------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon_path = RES_DIR / "LLM.icns"
    app.setWindowIcon(QIcon(str(icon_path)) if icon_path.exists() else QIcon())
    apply_dark(app)
    w = BootstrapWindow(); w.show()
    rc = app.exec()
    try:
        _log_fh.flush(); _log_fh.close()
    except Exception:
        pass
    sys.exit(rc)

if __name__ == "__main__":
    main()

