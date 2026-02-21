#!/usr/bin/env python3
# setup.py — AgentF (macOS, py2app)

from pathlib import Path
from setuptools import setup

APP_NAME  = "AgentF"
ENTRY     = "gui.py"
ICON_FILE = "LLM.icns"
BUNDLE_ID = "com.yourname.agentf"

root = Path(__file__).parent.resolve()

# Ensure chat.sh is executable so it stays executable in Resources
chat = root / "chat.sh"
if chat.exists():
    chat.chmod(chat.stat().st_mode | 0o111)

# ---- Resource candidates (only the ones that exist will be copied) ----
# NOTE: agentf-app-launch.py and agentf-use-calc-app.py live under agent-functions/
RESOURCE_CANDIDATES = [
    "gui.py",
    "installer.py",
    "ai.py",
    "agent.py",
    "agent-functions",        # contains agentf-app-launch.py + agentf-use-calc-app.py
    "apps",                   # ships apps/calculator/*
    "qwen.npz",
    "chat.sh",
    "LLM.icns",
    "qt.conf",                # optional; include if present
    "openssl.ca",             # optional; include if present
]

RESOURCES = [str(p) for p in (root / c for c in RESOURCE_CANDIDATES) if p.exists()]

OPTIONS = {
    # py2app options
    "argv_emulation": False,
    "optimize": 1,
    "strip": True,

    # Make sure WebEngine pieces are collected
    "includes": [
        # Core Qt
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        # WebEngine + Channel
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        # Helpful extras
        "shiboken6",
        "packaging",
    ],
    "packages": [
        "PySide6",
        "shiboken6",
    ],
    "excludes": [
        "pip", "wheel", "setuptools", "pkg_resources", "distutils",
        "unittest", "test", "tests", "tkinter",
        "numpy", "pandas", "matplotlib", "scipy", "cv2",
    ],

    # Everything here is copied into Contents/Resources (dirs are recursive)
    "resources": RESOURCES,

    "iconfile": ICON_FILE,
    "plist": {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": APP_NAME,
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
        "LSEnvironment": {
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "TERM": "xterm-256color",
        },
    },
}

setup(
    app=[ENTRY],
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
