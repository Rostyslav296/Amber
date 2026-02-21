Here is the updated, production-grade `README.md` for your Datasette application. It focuses strictly on the Core Architecture, the Bridge Protocol, and the Bluetooth Pilot module.

---

# AgentF Datasette: The Intelligence Dashboard

**Version:** 2.1 (Universal Bridge Architecture) | **Stack:** Python, PySide6, SQLite, Datasette

### 📖 Overview

**FDatasette** is a specialized "Headless-First" dashboard designed for AI Agents. Unlike a standard database viewer, it operates as a **Socket-Controlled Server** that waits for signals from your Agent's terminal.

When Amber runs a reconnaissance tool (like a Bluetooth scan), she doesn't just print text—she "beams" the structured data instantly to this dashboard, creating a real-time visualization loop.

---

### 🏗 High-Level Architecture

The system uses a **Client-Server-Bridge** model to separate the "doing" (Scanning) from the "seeing" (Dashboard).

```mermaid
graph LR
    subgraph FTerminal [Agent Terminal]
        A[Amber / Agent] -->|Runs Tool| B(scan-bluetooth-devices.py)
        B -->|1. Saves Data| C[(ble_recon.db)]
        B -->|2. Imports| D[datasette_fterminal_link.py]
    end

    subgraph IPC [Unix Socket]
        D -.->|3. Sends Signal| E{/tmp/fdatasette_sock.ipc}
    end

    subgraph FDatasette [Dashboard GUI]
        E -->|4. Receives Path| F[agentf-datasette.py]
        F -->|5. Spawns Server| G[Datasette Engine]
        G -->|6. Renders| H[Interactive Map/Table]
    end

```

### 🧩 Core Components

#### 1. The Viewer (`agentf-datasette.py`)

This is the main GUI application. It is a wrapper around the `datasette` library that adds **Socket Listening** capabilities.

* **Self-Healing:** If `datasette` or `PySide6` are missing, it auto-installs them on launch.
* **Socket Server:** Listens on `/tmp/fdatasette_sock.ipc` for incoming database paths.
* **Process Management:** Automatically kills and restarts the internal server when a new database is requested, ensuring you never have "port in use" errors.

#### 2. The Bridge (`datasette_fterminal_link.py`)

This is the **Universal Protocol** script. Any Python script in your ecosystem can import this file to control the dashboard.

* **Function:** `open_in_datasette(db_path)`
* **Logic:** It resolves the absolute path of the database and sends a JSON packet to the GUI socket.
* **Requirement:** This file must exist in **both** the `apps/datasette/tools/` folder AND the `apps/fterminal/tools/` folder (or be symlinked) so that terminal scripts can import it.

---

### 📡 The Pilot Module: Bluetooth Recon

**Script:** `scan-bluetooth-devices.py`

This is the reference implementation of a "Full Loop" tool. It demonstrates how to gather physical world data and visualize it instantly.

**Capabilities:**

1. **Dependency Guard:** Automatically bootstraps `pip` and installs `bleak` if missing.
2. **Permission Handler:** Detects macOS Bluetooth permission blocks and pauses execution ("Waiting for user to click Allow") instead of crashing.
3. **Dual Output:**
* **Terminal:** Prints a high-contrast ASCII summary table for the Agent.
* **GUI:** Auto-triggers FDatasette to load the `ble_recon.db` file.



**Usage:**

```bash
# In FTerminal
run scan-bluetooth-devices.py

```

---

### 📂 Directory Structure & Constraints

For the bridge to work, the "Link" script must be accessible to the tools running in FTerminal.

```text
~/Developer/llm/apps/
├── datasette/
│   ├── agentf-datasette.py           # The Main GUI
│   └── tools/
│       └── datasette_fterminal_link.py  # Master Copy
└── fterminal/
    └── tools/
        ├── scan-bluetooth-devices.py    # The Scanner
        └── datasette_fterminal_link.py  # Link Copy (or Symlink)

```

### ⚡ Quick Start

1. **Launch the Dashboard:**
```bash
# Command for Amber
open datasette

```


2. **Run a Scan:**
```bash
# Command for Amber
run scan-bluetooth-devices.py

```


3. **Result:** The dashboard will auto-refresh to show the "Device_Radar" view with signal strength heatmaps.




Further Synopsis: 

1. The Brain (Amber / FTerminal)

What it is: The command center.

What it does: This is where the work happens. When you run scan-bluetooth-devices.py, it acts like a worker bee. It goes out, talks to your hardware (Bluetooth), and gathers raw data.

The Key: It doesn't care about displaying the data. It just wants to capture it.

2. The Bucket (SQLite Database)

What it is: The .db file (e.g., ble_recon.db).

What it does: This is the shared memory. The "Brain" dumps the data here and walks away.

Why it's smart: Because it's a file, it persists. You can close the app, open it next week, and the data is still there. Both the Terminal and the Dashboard can read it at the same time without fighting.

3. The Nervous System (The IPC Bridge)

What it is: The datasette_fterminal_link.py script and the /tmp/fdatasette_sock.ipc socket.

What it does: This is the "Doorbell."

How it works: When the "Brain" finishes a job, it rings the doorbell (sends a signal to the socket) saying, "Hey, I just dropped new files in the bucket."

4. The Monitor (FDatasette)

What it is: The GUI window.

What it does: It sits there waiting for the doorbell to ring.

The Magic: It is "Reactive." It doesn't run the scan. It doesn't know how Bluetooth works. It just knows that when the doorbell rings, it should look in the "Bucket" and show you whatever is inside.

Why this is "Professional Grade"

Most hobbyist scripts are "spaghetti code"—the scanning logic, the saving logic, and the printing logic are all mixed together. If one breaks, everything breaks.

Your architecture is Decoupled:

You can rewrite the scanner completely, and as long as it saves to the same DB, the Dashboard doesn't care.

You can crash the Dashboard, and the scanner keeps running.

You can swap the Dashboard for a web page later, and you don't have to change a single line of your scanning code.

The Diagram

Here is the visual representation of your "One-Person Unicorn" stack:

Code snippet
graph TD
    subgraph "The Worker (FTerminal)"
        A[Amber/User] -->|Run Script| B(Bluetooth Scanner)
        B -->|1. Drop Data| C[(SQLite Bucket)]
        B -->|2. Ring Doorbell| D{Bridge Link}
    end

    subgraph "The Monitor (FDatasette)"
        E[Socket Listener] -->|3. Hears Ring| F[Web Server Engine]
        C -->|4. Reads Data| F
        F -->|5. Displays| G[GUI Window]
    end