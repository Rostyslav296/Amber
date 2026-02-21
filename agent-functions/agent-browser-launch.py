#!/usr/bin/env python3
import sys, os, json, argparse

# --- METADATA (Agent reads this) ---
TOOL_METADATA = {
    "name": "browser",
    "description": "Opens the custom Amber AI Browser. Use this to surf the web. If no URL is provided, it opens a homepage.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Optional: The URL to visit (e.g. 'https://cnn.com'). If omitted, opens Google."
            }
        },
        "required": []  # <--- CRITICAL FIX: No parameters are required now
    }
}

# --- LOGIC ---
def launch(url=None):
    # Set Env for Remote Debugging (Soft-scan support)
    os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = "9222"
    
    try:
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtCore import QUrl
    except ImportError:
        print("Error: PySide6 not installed.")
        return

    # Normalize URL if present
    if url:
        if not url.startswith("http"):
            url = "https://" + url

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Amber Browser")

    window = QMainWindow()
    window.resize(1200, 800)
    window.setWindowTitle("Amber AI Browser")
    
    browser = QWebEngineView()
    window.setCentralWidget(browser)
    
    # Logic: Load specific URL or Default to Google
    if url:
        browser.load(QUrl(url))
    else:
        browser.load(QUrl("https://www.google.com"))
        
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args from Agent")
    parser.add_argument("url", nargs="?", help="Legacy CLI arg")
    args = parser.parse_args()

    target_url = None
    
    # 1. Handle JSON Input (New Agent Standard)
    if args.json:
        try:
            data = json.loads(args.json)
            # Use .get() to safely handle missing 'url' key
            target_url = data.get("url")
        except:
            pass
        
    # 2. Handle Legacy Input (only if JSON didn't provide it)
    if target_url is None and args.url:
        target_url = args.url

    # Launch with whatever we found (None is handled inside launch)
    launch(target_url)
