#!/usr/bin/env python3
import sys, os, json, argparse
from pathlib import Path

# --- METADATA (Agent reads this) ---
TOOL_METADATA = {
    "name": "calclaunch",
    "description": "Launches the JavaScript Calculator mini-app in a native window.",
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "description": "Optional mode (e.g. 'scientific' or 'standard') if supported."
            }
        },
        "required": []
    }
}

# --- CONFIGURATION ---
# Assumes structure: ~/Developer/llm/agent-functions/agentf-calc-launch.py
# and calculator is at: ~/Developer/llm/apps/calculator/index.html
CURRENT_DIR = Path(__file__).resolve().parent
APPS_DIR = CURRENT_DIR.parent / "apps"
CALC_HTML = APPS_DIR / "calculator" / "index.html"

# --- LOGIC ---
def launch_js_app():
    try:
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtCore import QUrl
    except ImportError:
        print("❌ Error: PySide6 is missing. Please run: pip install PySide6")
        return

    if not CALC_HTML.exists():
        print(f"❌ Error: Calculator app not found at {CALC_HTML}")
        print("Please check that 'apps/calculator/index.html' exists.")
        return

    # Initialize App
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    
    app.setApplicationName("Amber Calculator")

    # Create Window
    window = QMainWindow()
    window.resize(400, 600) # Standard calculator size
    window.setWindowTitle("Calculator (JS)")

    # Load JS App
    view = QWebEngineView()
    window.setCentralWidget(view)
    view.load(QUrl.fromLocalFile(str(CALC_HTML)))

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    # Standard Agent Parsing
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args from Agent")
    args = parser.parse_args()

    # We ignore args.json for now since the JS app might not support deep linking yet,
    # but the infrastructure is there if you add it later.
    launch_js_app()
