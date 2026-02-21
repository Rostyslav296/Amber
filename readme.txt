Here is a comprehensive `README.md` for your project, documented to reflect the architecture, the "Amber" persona, and the dual-layer agentic structure you described.

---

# Agent F (Amber AI)

**Agent F** is a local-first macOS AI assistant powered by **Qwen 3 (8B)** running on Apple Silicon via **MLX**. It features a modular agentic architecture ("Amber") capable of routing natural language requests to specific Python scripts or JavaScript-based mini-apps.

The project is designed for rapid CLI iteration and deployment as a standalone macOS application via `py2app`.

## 🏗 Architecture

The system is composed of four distinct layers:

1. **The Brain (Inference Layer)** - `ai.py`
* Loads model weights from `qwen.npz`.
* Handles the chat loop and manages the "Amber" persona.
* Strips internal `<think>` tags to ensure clean output.


2. **The Router (Agent Layer)** - `agent.py`
* Intercepts user inputs before they reach the LLM.
* Scans `agent-functions/` for available tools.
* Decides whether to reply conversationally or execute a tool (e.g., "Open calculator"  `agentf-calc-launch.py`).


3. **The Tools (Dual Architecture)**
* **Python Scripts:** Located in `agent-functions/`. These handle OS-level tasks (File finding, App launching, Browser automation).
* **JS Mini-Apps:** Located in `apps/`. These are specialized tools (like the Calculator or Terminal) that launch in separate windows.


4. **The Body (GUI Layer)** - `gui.py`
* A PySide6 (Qt) wrapper that bootstraps the environment.
* Displays logs and manages the application lifecycle.



## 📂 Project Structure

```text
~/Developer/llm
├── agent.py                 # Main agent routing logic & tool registry
├── ai.py                    # LLM inference loop (MLX)
├── gui.py                   # PySide6 macOS GUI entry point
├── build.sh                 # Build script (cleans env, runs py2app)
├── setup.py                 # py2app configuration & resource bundling
├── qwen.npz                 # Model weights manifest
├── npz-weightmake-METAL.sh  # Script to generate qwen.npz from local model
├── agent-functions/         # 🛠 Python Tool Scripts
│   ├── agent-browser-launch.py
│   ├── agentf-file-finder.py
│   ├── agentf-open-app.py
│   ├── use-browser.py
│   └── ...
└── apps/                    # 🖥️ JavaScript Mini-Apps
    ├── calculator/          # JS Calculator (currently under maintenance)
    └── terminal/            # JS Terminal

```

## 🚀 Capabilities

Amber is designed to be "helpful and precise." The agent currently supports the following capabilities via the `agent-functions` directory:

| Command / Intent | Function | Description |
| --- | --- | --- |
| `calclaunch` | **Calculator** | Launches the calculator mini-app (PySide6 + QtWebEngine). |
| `filefind` | **File Finder** | Fuzzy searches for files on the local system. |
| `browserlaunch` | **Browser** | Launches the custom AI Browser. |
| `openapp` | **App Launcher** | Opens native macOS applications (e.g., "Open Anki"). |
| `maketxt` | **File I/O** | Creates UTF-8 text files from natural language. |
| `terminal` | **Terminal** | Executes system commands and custom tools. |

## 🛠 Setup & Installation

### Prerequisites

* macOS (Apple Silicon recommended for MLX).
* Python 3.12 (managed via `.venv`).

### 1. Environment Setup

The project relies on a virtual environment. The build script checks for Python 3.12.

```bash
# Initialize venv (if not done automatically by build.sh)
./v-env.sh
source .venv/bin/activate

```

### 2. Model Weights

Amber uses Qwen 3 (MLX optimized). You must generate the `npz` manifest:

```bash
# Generates qwen.npz pointing to your local Qwen model
./npz-weightmake-METAL.sh

```

### 3. Running in CLI (Dev Mode)

For rapid iteration without rebuilding the `.app`:

```bash
# Assumes you have an alias or run python directly
python ai.py --weights qwen.npz --agent agent.py

```

### 4. Building the App

To package Agent F as a standalone macOS application (`dist/AgentF.app`):

```bash
./build.sh

```

*Note: This script cleans the build artifacts, verifies the Python version, and executes `setup.py py2app`.*

## 🐛 Troubleshooting & Known Issues

* **Calculator / JS Apps:** Some JavaScript-based mini-apps (specifically in `apps/calculator`) are currently experiencing launch issues.
* **Permissions:** Ensure `agent.py` and helper scripts have execution permissions if running outside the python call.
* **Auto-Search:** If a terminal command fails, `agent.py` attempts an auto-search to find a fix.

## 📜 License

Personal Project. All rights reserved.
