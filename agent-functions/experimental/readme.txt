Here is the **Amber Agent Technical Encyclopedia**.

This document is structured as a technical wiki. Each entry provides a comprehensive breakdown of the function's architecture, logic, capabilities, and integration points.





**Legend:**

* 🟢 **STATUS: CORE / WORKING:** Validated file present in your system.
* 🔴 **STATUS: KNOWN ISSUE:** Validated file present, but functionality is limited (e.g., launches app but fails to perform action).
* 🟠 **STATUS: EXPERIMENTAL:** Script generated in our sessions; requires installation (`pip`) or configuration to run.

---

# 📚 Volume 1: Core System & Operating Environment

*The foundational tools that allow the agent to exist, see, and interact with the host OS.*

## 1. Weather Reporter (`agentf-weather.py`)

* **Status:** 🟢 **CORE / WORKING**
* **Version:** 1.2 (Optimized)
* **System Type:** Data Retrieval / API Bridge

### 📋 Executive Summary

The primary meteorological interface for the agent. Unlike simple API wrappers, this tool employs a **Dual-Redundancy Architecture**. It attempts to fetch weather data from `wttr.in` (a console-optimized weather service) first. If that fails or provides insufficient data, it falls back to `Open-Meteo`, a free, high-precision open API. It features "Smart Location" logic: if the user does not specify a city, it automatically triangulates the host's IP address to determine the current location.

### ⚙️ Technical Architecture

* **Language:** Python 3 (Standard Library).
* **Dependencies:** `subprocess` (for cURL), `json`, `urllib`.
* **Data Flow:**
1. **Sanitization:** Cleans user input (e.g., removes "current location" keywords).
2. **Primary Request:** cURL request to `wttr.in/{location}?format=j1`.
3. **Fallback Logic:** If Primary fails, queries `ip-api.com` for lat/lon, then queries `api.open-meteo.com`.
4. **Parsing:** Extracts Condition, Temp (C/F), Humidity, Moon Phase, and 3-Day Forecast.


* **Error Handling:** Silent failover between providers; explicit error message if network is down.

### 🧩 Input/Output Schema

```json
// Input (JSON)
{
  "location": "Paris, France", // Optional. If Omitted -> Auto-IP
  "full": true // Optional. Returns 3-day forecast if true.
}

```

### 🗣️ Prompt Examples

* "What is the weather right now?" (Triggers Auto-IP)
* "Check the forecast for Tokyo." (Specific Location)
* "Is it going to rain this week?" (Implies `full=true`)

---

## 2. Native Terminal (`agentf-terminal.py`)

* **Status:** 🟢 **CORE / WORKING**
* **Version:** 2.0 (GUI Persistent)
* **System Type:** Interactive Shell / GUI

### 📋 Executive Summary

A custom-built GUI terminal emulator designed specifically for Agent-User collaboration. Unlike a standard terminal (which is text-only and ephemeral), this tool launches a **Persistent PySide6 Window**. This allows the agent to execute shell commands in a visible environment where the user can watch the output in real-time. It supports session persistence, meaning variables set in one command remain available for the next (if implemented via the internal shell wrapper).

### ⚙️ Technical Architecture

* **Language:** Python 3 + PySide6 (Qt Framework).
* **Dependencies:** `PySide6`, `subprocess`, `os`.
* **Logic:**
1. **Instance Check:** Checks if a Terminal Window is already open via a local socket/lockfile.
2. **IPC Bridge:** If open, sends the command to the existing window via socket.
3. **Launch:** If closed, spawns a new process rendering the UI.
4. **Execution:** Runs commands via `subprocess.Popen` and streams `stdout` to the GUI text widget.



### 🧩 Input/Output Schema

```json
// Input (JSON)
{
  "command": "ls -la" // The shell command to execute.
}

```

### 🗣️ Prompt Examples

* "Open the terminal."
* "List the files in the current directory."
* "Run a ping test to https://www.google.com/search?q=google.com."

---

## 3. FDatasette Viewer (`agentf-datasette.py`)

* **Status:** 🟢 **CORE / WORKING**
* **Version:** 1.0
* **System Type:** Database Visualization / Local Server

### 📋 Executive Summary

A specialized visualization tool for SQLite databases. It wraps the open-source `datasette` ecosystem into a desktop app. When triggered, it spins up a local web server (on localhost:8001) pointing to a specific database file (like your iMessage `chat.db` or browser history), then renders that server inside a native Qt Web Engine window. This gives the agent "Eyes" on raw data, allowing it to show you tables, filter rows, and run SQL queries visually.

### ⚙️ Technical Architecture

* **Language:** Python 3 + PySide6 + Datasette.
* **Dependencies:** `datasette` (must be installed via pip), `PySide6`.
* **Logic:**
1. **Server Spin-up:** subprocess call to `datasette serve <db_file> -p 8001`.
2. **GUI Launch:** Opens QWebEngineView pointing to `http://127.0.0.1:8001`.
3. **Lifecycle:** Manages the background server process to ensure it dies when the window closes.



### 🧩 Input/Output Schema

```json
// Input (JSON)
{
  "database": "/Users/name/Library/Messages/chat.db" // Path to DB
}

```

### 🗣️ Prompt Examples

* "Open the database viewer."
* "Show me my message history database."

---

## 4. Application Launcher (`agentf-open-app.py`)

* **Status:** 🟢 **CORE / WORKING**
* **Version:** 1.0
* **System Type:** OS Utility

### 📋 Executive Summary

The primary mechanism for the agent to affect the user's desktop environment. It utilizes the macOS Native `open` command to launch applications. It includes heuristic logic to handle fuzzy names (e.g., converting "chrome" to "Google Chrome.app") by checking the `/Applications` directory if a direct launch fails.

### ⚙️ Technical Architecture

* **Language:** Python 3.
* **Dependencies:** `subprocess` (calls `/usr/bin/open`).
* **Logic:**
1. Try `open -a "AppName"`.
2. If fail, append `.app` and retry.
3. If fail, scan standard app directories for partial matches.



### 🧩 Input/Output Schema

```json
// Input (JSON)
{
  "app_name": "Spotify"
}

```

### 🗣️ Prompt Examples

* "Open Spotify."
* "Launch Visual Studio Code."
* "Start Discord."

---

## 5. iMessage Sender (`agentf-imessage.py`)

* **Status:** 🟢 **CORE / WORKING**
* **Version:** 1.1 (AppleScript Bridge)
* **System Type:** Communication Bridge

### 📋 Executive Summary

Allows the agent to send blue-bubble iMessages or SMS text messages. Since Apple does not provide a public API for Messages on macOS, this tool uses the **AppleScript Bridge** (`osascript`). It instructs the Messages.app (in the background) to locate a "Buddy" matching the contact name or phone number and dispatch a text string.

### ⚙️ Technical Architecture

* **Language:** Python 3 + AppleScript.
* **Dependencies:** `osascript` (Native macOS tool).
* **Logic:**
1. Constructs an AppleScript payload: `tell application "Messages" to send "{msg}" to buddy "{contact}"`.
2. Executes via `subprocess`.
3. Parses exit code to confirm delivery hand-off (note: cannot confirm *read* receipt, only *send* success).



### 🧩 Input/Output Schema

```json
// Input (JSON)
{
  "contact": "Mom", // Name in Contacts.app OR Phone Number
  "message": "I'll be home in 10 mins."
}

```

### 🗣️ Prompt Examples

* "Text Mom that I'm on my way."
* "Send a message to 555-0199 saying hello."

---

## 6. File Finder (`agentf-file-finder.py`)

* **Status:** 🟢 **CORE / WORKING**
* **Version:** 1.0
* **System Type:** File System Search

### 📋 Executive Summary

A high-speed search utility that bypasses slow Python recursive crawling. It leverages the macOS **Spotlight Index** via the `mdfind` command line tool. This allows the agent to find files across the entire hard drive in milliseconds, returning the absolute paths of the top matches.

### ⚙️ Technical Architecture

* **Language:** Python 3.
* **Dependencies:** `mdfind` (Native macOS tool).
* **Logic:**
1. Executes `mdfind -name "{query}"`.
2. Filters out system files or hidden directories (optional config).
3. Returns the top 5-10 results to the Agent's context.



### 🧩 Input/Output Schema

```json
// Input (JSON)
{
  "filename": "quarterly_report"
}

```

### 🗣️ Prompt Examples

* "Find that PDF about the merger."
* "Where did I save the budget spreadsheet?"

---

## 7. Amber Browser (`agent-browser-launch.py`)

* **Status:** 🟢 **CORE / WORKING**
* **Version:** 1.0
* **System Type:** Web Navigation

### 📋 Executive Summary

A dedicated web browser for the agent. While the agent can use `open` to launch Safari/Chrome, this tool launches a **Controlled QtWebEngine** instance. This is significant because it allows for future expansion where the agent could inject JavaScript, take screenshots, or scrape content directly from the window DOM, which is difficult in Chrome/Safari due to security sandbox restrictions.

### ⚙️ Technical Architecture

* **Language:** Python 3 + PySide6 (QtWebEngine).
* **Dependencies:** `PySide6`.
* **Logic:**
1. Initializes a QApplication.
2. Sets up a QWebEngineView.
3. Loads the URL provided in arguments (or defaults to Google).
4. Renders the window.



### 🧩 Input/Output Schema

```json
// Input (JSON)
{
  "url": "https://news.ycombinator.com"
}

```

### 🗣️ Prompt Examples

* "Open the browser to Hacker News."
* "Surf the web."

---

## 8. Calculator (`agentf-calc-launch.py`)

* **Status:** 🟢 **CORE / WORKING**
* **Version:** 1.0
* **System Type:** Utility Widget

### 📋 Executive Summary

Launches a lightweight, aesthetic calculator. Instead of opening the heavy macOS Calculator app, this spins up a minimal web-based calculator (HTML/CSS/JS) inside a stripped-down Python window. This serves as a proof-of-concept for the agent's ability to launch "Micro-Apps" stored in its own library.

### ⚙️ Technical Architecture

* **Language:** Python 3 + PySide6.
* **Dependencies:** Requires a local `index.html` file in the `apps/calculator` directory.
* **Logic:** Loads a local file URI (`file://.../index.html`) into a frameless or minimal WebEngine window.

### 🧩 Input/Output Schema

```json
// Input (JSON)
{
  "mode": "standard" // Reserved for future use
}

```

### 🗣️ Prompt Examples

* "Open the calculator."

---

## 9. Apple Notes (`agentf-notes.py`)

* **Status:** 🔴 **KNOWN ISSUE**
* **Issue:** The script successfully identifies the request and launches the Apple Notes application, but the AppleScript logic intended to *create a new note with specific text* fails to execute or is blocked by sandbox permissions, resulting in just the app opening blank.
* **System Type:** Productivity

### 📋 Executive Summary

Intended to allow the agent to dictate notes directly into the user's iCloud Notes. It uses AppleScript to target the "Notes" application, create a `new note`, and set its `body`.

### ⚙️ Technical Architecture

* **Language:** Python 3 + AppleScript.
* **Logic:**
* `tell application "Notes" to make new note with properties {name: "Title", body: "Content"}`.


* **Fix Required:** Needs updated AppleScript syntax for macOS Sonoma/Sequoia or Accessibility permissions granting.

### 🧩 Input/Output Schema

```json
// Input (JSON)
{
  "title": "Grocery List",
  "content": "Milk, Eggs, Bread"
}

```

### 🗣️ Prompt Examples

* "Create a note called Ideas."

---

# 📚 Volume 2: Productivity & Office (Experimental)

*These tools exist in the codebase but require `pip` installation of dependencies to function.*

## 10. Reminders (`agentf-reminders.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Interfaces with macOS Reminders to add to-do items.
* **Architecture:** Python wrapper for AppleScript.
* **Prompt:** "Remind me to call John at 5 PM."

## 11. Calendar (`agentf-calendar.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Reads the user's iCal/Calendar database to list upcoming events.
* **Architecture:** Python + `icalbuddy` (CLI tool) or AppleScript.
* **Prompt:** "What is my first meeting today?"

## 12. Email Client (`agentf-email.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** A headless email client capable of sending and reading emails via IMAP/SMTP protocols.
* **Architecture:** Wrapper for the `himalaya` CLI or Python `smtplib`/`imaplib`.
* **Prompt:** "Email the report to the team."

## 13. Google Sheets (`agentf-sheets.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Direct Read/Write access to Google Cloud Spreadsheets.
* **Architecture:** Uses `google-api-python-client`. Requires `credentials.json` service account.
* **Prompt:** "Add $50 to the budget spreadsheet."

## 14. PDF Reader (`agentf-pdf.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Extracts raw text from PDF files, allowing the agent to "read" documents.
* **Architecture:** Uses `pypdf` or macOS `textutil`.
* **Prompt:** "Summarize the contract.pdf file."

## 15. OCR Scanner (`agentf-ocr.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Optical Character Recognition. Reads text from images (screenshots, scanned docs).
* **Architecture:** Uses `pytesseract` (wrapper for Google Tesseract Engine).
* **Prompt:** "Extract the text from this screenshot."

## 16. Notion (`agentf-notion.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Appends blocks (text, todos) to a specific Notion page.
* **Architecture:** Uses `notion-client` (Official API). Requires Integration Token.
* **Prompt:** "Add this to my Notion todo list."

## 17. Obsidian (`agentf-obsidian.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Manages local Markdown files in an Obsidian Vault. Can append to "Daily Notes".
* **Architecture:** Python File I/O (direct `.md` manipulation).
* **Prompt:** "Log this entry in my daily note."

## 18. Bear Notes (`agentf-bear.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Creates notes in the Bear App using its URL Scheme API.
* **Architecture:** Python `webbrowser` module to trigger `bear://x-callback-url/create`.
* **Prompt:** "Save this thought to Bear."

## 19. 1Password (`agentf-1pass.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Securely fetches API keys or passwords from the user's vault.
* **Architecture:** Wrapper for the `op` CLI tool.
* **Prompt:** "Get the OpenAI API key from 1Password."

---

# 📚 Volume 3: Communication & Web (Experimental)

## 20-24. Messengers

* **BlueBubbles (`agentf-bluebubbles.py`):** Android-to-iMessage relay API.
* **Telegram (`agentf-telegram.py`):** Bot API wrapper.
* **Discord (`agentf-discord.py`):** Webhook integration for channel posting.
* **Slack (`agentf-slack.py`):** Incoming Webhook integration.
* **Twilio (`agentf-twilio.py`):** Programmable SMS via Twilio Cloud.

## 25-30. Knowledge Engines

* **Deep Research (`agentf-tavily.py`):** Uses Tavily AI API to perform multi-step web research and synthesis.
* **Wikipedia (`agentf-wiki.py`):** Queries Wikipedia for summaries.
* **WolframAlpha (`agentf-wolfram.py`):** Computational intelligence for math/science.
* **YouTube Intel (`agentf-youtube.py`):** Downloads and parses video transcripts.
* **ArXiv (`agentf-arxiv.py`):** searches academic papers.
* **Browser CLI (`agentf-browser.py`):** Text-based DuckDuckGo searching.

---

# 📚 Volume 4: Bleeding Edge AI (Experimental)

*These tools represent the state-of-the-art in autonomous agency.*

## 47. AutoMode (`agentf-automode.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** A "Meta-Agent." This script acts as a recursive brain. It takes a complex goal, breaks it down, and calls *other* tools in this list to achieve it.
* **Architecture:** Python ReAct Loop using `litellm`.
* **Prompt:** "Research frogs, write a report, and email it to me."

## 48. Planner (`agentf-planner.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Generates a sequential list of steps (a plan) and executes them blindly. Faster than AutoMode but less adaptable.

## 49. Research Storm (`agentf-research-storm.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Based on Stanford's STORM. It performs recursive research—searching, reading, generating new questions, and searching again—before writing a final report.

## 50. Dev Loop (`agentf-dev-loop.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** A self-healing coding loop. It writes a script, runs it, captures any errors, feeds the errors back to the LLM to generate a fix, and retries.

## 51. Vector Memory (`agentf-memory.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** RAG (Retrieval Augmented Generation). Stores text snippets in a local ChromaDB vector database for long-term recall.

## 52. GPT Vision (`agentf-vision.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Uses OpenAI's GPT-4o Vision API to analyze image files and describe them.

## 53. Voice Clone (`agentf-eleven.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Uses ElevenLabs API to generate hyper-realistic speech, potentially cloning the user's voice.

---

# 📚 Volume 5: Cybersecurity & Automation (Experimental)

## 58. Stealth Browser (`agentf-ghost.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Uses `DrissionPage` to control a Chrome instance that is undetectable by most bot filters. Can click buttons and fill forms.

## 62. Autofill (`agentf-autofill.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** A heuristic script that navigates to a URL and attempts to identify "Email", "Username", and "Password" fields to automatically sign up for accounts.

## 63. Identity Faker (`agentf-identity.py`)

* **Status:** 🟠 **EXPERIMENTAL**
* **Description:** Generates consistent fake personas (Name, Address, Birthday) for use in account creation.

## 70-117. The Red Team Arsenal (Kali Linux Ports)

* **Recon:** Nmap, Shodan, Sublist3r, Photon, Sherlock.
* **Exploit:** SQLMap, Nuclei, Commix, Metasploit RPC.
* **Forensics:** Volatility, Binwalk, HashID.
* **Active:** Hydra, Medusa, Patator.
* *Note: These are Python wrappers that require the underlying Kali tools to be installed on the system.*
