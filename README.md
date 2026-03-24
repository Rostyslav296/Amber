# Amber AI Agent (Agent F)

> Fully autonomous, local-first macOS AI agent powered by Qwen 3.5-9B on Apple Silicon via MLX.

Amber operates through a ReAct-style multi-step reasoning engine with an always-on swarm coordinator, chaining tool calls autonomously until tasks are complete. She controls Microsoft Edge, sends emails, manages files, runs terminal commands, interacts with Apple apps (Messages, Mail, Notes), and orchestrates complex multi-step workflows — all running locally without cloud dependencies.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [The Five Layers](#2-the-five-layers)
3. [Agentic Execution Engine](#3-agentic-execution-engine)
4. [AetherEdge Browser Automation](#4-aetheredge-browser-automation)
5. [Tool Registry & Agent Functions](#5-tool-registry--agent-functions)
6. [Memory & Training System](#6-memory--training-system)
7. [Swarm Coordination](#7-swarm-coordination)
8. [Model & Inference](#8-model--inference)
9. [CLI Usage](#9-cli-usage)
10. [Project Structure](#10-project-structure)
11. [Setup & Prerequisites](#11-setup--prerequisites)

---

## 1. Architecture Overview

Amber is a cybernetic OODA loop: **Observe → Orient → Decide → Act**.

The user speaks in natural language. Amber's brain (Qwen 3.5-9B via MLX) reasons about the request, then autonomously calls tools (Python scripts) to execute actions. Each tool returns observations that feed back into the next reasoning step. This continues until the task is fully complete or the user says `stop`.

```
┌─────────────────────────────────────────────────────────┐
│  USER (Natural Language)                                │
└───────────────────────┬─────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────┐
│  BRAIN — ai.py (541 lines)                              │
│  Qwen 3.5-9B 4-bit · MLX · stream_generate             │
│  Real-time token streaming with <think> tag filtering   │
│  TPS tracker · RAM monitor · Memory integration         │
├─────────────────────────────────────────────────────────┤
│  ROUTER — agent.py (2357 lines)                         │
│  ReAct Loop · SwarmCoordinator v4.1 · Stall Detection   │
│  Browser auto-reconnect · Plan validation               │
│  Context pruning (2-phase) · RAM-aware workers          │
├─────────────────────────────────────────────────────────┤
│  TOOLS — agent-functions/*.py (15 tools)                │
│  edge.py · email.py · i.py · nano.py · macOs-terminal   │
│  fterminal · app-opener · safari · weather · more        │
├─────────────────────────────────────────────────────────┤
│  MEMORY — memory.py (899 lines)                         │
│  Procedural + Episodic + Semantic · BM25 retrieval      │
│  Training recorder · Auto-pattern extraction            │
├─────────────────────────────────────────────────────────┤
│  REALITY — macOS System                                 │
│  Edge browser · Terminal · Mail · Messages · Finder      │
└─────────────────────────────────────────────────────────┘
```

---

## 2. The Five Layers

### Layer 1: The Brain (`ai.py` — 541 lines)

The inference engine. Loads model weights from `qwen.npz` manifest, which points to `mlx-community/Qwen3.5-9B-MLX-4bit` on HuggingFace. Runs entirely on Apple Silicon via MLX — no cloud, no API keys, no internet required after initial download.

**Key features:**
- **Real-time streaming** via `mlx_lm.stream_generate()` (token-by-token output)
- **TPS tracker** — after each response: `142 tok · 21.3 t/s · prefill 168 t/s · 5.2GB`
- **`<think>` tag filtering** — model reasons internally in grey, responds in red
- Thinking always visible (grey text) by default
- **GPU RAM monitoring** at startup with headroom display
- System prompt construction with tool instructions from `agent.py`
- Multi-line paste support with adaptive timeout
- **Swarm mode commands** — `swarm on/off/parallel/sequential/auto`
- **Memory commands** — `memory list/search/delete/teach/prune`
- **Training mode** — `-train <prompt>` records every step as a reusable procedure

### Layer 2: The Router (`agent.py` — 2357 lines)

The agentic orchestration engine. Implements ReAct + Swarm Coordinator v4.1:

1. LLM generates response (may contain a JSON tool call)
2. Parser extracts tool call: `{"tool": "name", "args": {...}}`
3. Tool is executed (one-shot subprocess or persistent server)
4. Result is fed back as `[TOOL RESULT]` observation
5. Repeat until LLM responds conversationally (task complete)

**Key features:**
- Hybrid tool registry via AST parse or `# AGENTCMD:` headers
- **Server-mode processes** — tools like `edge.py` run as persistent stdin/stdout JSON servers
- **Browser auto-reconnect** — detects dead browser contexts, auto-restarts servers
- **Smart observation truncation** — preserves ELEMENTS section, truncates TEXT first
- **2-phase context pruning** at 64K chars (compress old results → remove old messages)
- **Stall detection v4.1** — identical repeats, A→B→A→B cycling, browser-dead loops
- **Plan validation** — anti-hallucination URL checking before worker dispatch
- **RAM-aware workers** — queries `mx.metal.get_active_memory()` to limit concurrency
- **Swarm checkpoint** — pause/resume swarm plans via `.swarm_checkpoint.json`

### Layer 3: The Tools (`agent-functions/` — 15 Python scripts)

Each tool is a standalone Python script with a `TOOL_METADATA` dictionary that declares its name, description, parameters, and capabilities. The `agent.py` registry scanner discovers them automatically.

**Two execution modes:**
- **One-shot** — `agent.py` runs the script with `--json '{"args": ...}'`
- **Server mode** — `agent.py` starts with `--server`, keeps alive, sends/receives JSON over stdin/stdout

### Layer 4: The Memory (`memory.py` — 899 lines)

Three-tier persistent memory with BM25 retrieval:
- **Procedural** — Saved task procedures for replay (training mode)
- **Episodic** — Auto-recorded agent runs with success scoring
- **Semantic** — User-taught facts and auto-learned patterns

### Layer 5: The Body (`gui.py`)

PySide6 (Qt) macOS application wrapper with dark theme. Provides `BootstrapWindow` (venv setup via `installer.py`) and `TerminalWindow` (live zsh shell). Primarily for the `.app` bundle distribution.

---

## 3. Agentic Execution Engine

### Tool Call Format

The LLM outputs JSON on its own line to invoke a tool:

```json
{"tool": "edge", "args": {"action": "snapshot"}}
```

The parser uses balanced-brace extraction to find JSON candidates, tries `json.loads()` then `ast.literal_eval()`, and validates the tool name against the registry.

### Execution Modes

| Mode | How it works | Timeout |
|------|-------------|---------|
| **Server** | Persistent subprocess, JSON over stdin/stdout. Auto-restarts on crash. | 55s |
| **One-shot** | `subprocess.Popen` with `--json` flag, waits for completion. | 30s |

### Browser Auto-Reconnect (v4.1)

When the Edge browser context dies mid-task:

1. **`edge.py`** catches browser-closed exceptions in `process()` → calls `_reinit_browser()` → retries action
2. **`edge.py` server loop** catches same errors → reconnect + retry at server level
3. **`agent.py`** `execute_tool()` detects dead-browser responses via `_is_browser_dead()` → calls `_restart_server()` → retries
4. **Stall detection** counts dead-browser results — 2+ triggers `BROWSER DEAD` termination

Max 3 reconnection attempts per session before giving up.

### Smart Truncation

For JSON results with `"page"` key:
- **Preserves** high-priority sections: `PAGE`, `ELEMENTS`, `FORM STATUS`, `DIALOG`
- **Truncates** low-priority sections: `TEXT`, `IFRAMES`
- Standard limit: **14,000 chars** · Content-heavy actions (read, search): **24,000 chars**

### Context Window Management

`_prune_context()` — 2-phase sliding window at 64K chars:

1. **Phase 1**: Compress old tool results (replace with summaries)
2. **Phase 2**: Remove entire old messages if still over threshold

Always keeps: system message (index 0) + last 6 messages.

### Stall Detection & Recovery (v4.1)

| Pattern | Detection | Recovery |
|---------|-----------|----------|
| 3 identical actions | Direct match | Context-specific guidance |
| A→B→A→B cycling | 2-step cycle match | "You are alternating between X and Y" |
| A→B→C→A→B→C cycling | 3-step cycle match | "You are cycling through X, Y, Z" |
| 2+ browser-dead results | Dead count threshold | "BROWSER DEAD" termination |
| Click-no-navigate | Same URL after 2+ clicks | "CLICK NOT NAVIGATING" escalation |
| Wrong-page stuck | URL pattern detection | "WRONG PAGE" browse guidance |

Stall recoveries are auto-saved as semantic memory for future avoidance.

### Context-Sensitive Observation Hints

After each tool execution, hints are injected based on result content:

| Condition | Hint |
|-----------|------|
| Empty snapshot | "Content probably in iframe. Try `iframe list → enter 0`." |
| `NOT_FOUND` / `STALE_REF` | "Page changed. Use updated #N refs from snapshot." |
| Search results | "Open relevant results in new tabs." |
| Redirect warning | "Do NOT retry URL. Use navigation menu elements." |
| CAPTCHA detected | "Cannot solve CAPTCHA. Go back, try different link." |
| ≤5 steps remaining | "Wrap up or summarize progress." |

---

## 4. AetherEdge Browser Automation

**5284 lines · 84+ methods · 2 classes · 40+ dispatchable actions**

See [edge-py.md](edge-py.md) for the exhaustive architecture reference.

AetherEdge is a production-grade browser automation engine using Playwright + CDP that rivals Browser-Use, Stagehand, and Manus Browser Operator while running entirely locally.

### Quick Highlights

- **5-tier snapshot engine** — NEVER returns empty. JS DOM → Simple JS → A11Y Tree → Native PW → Emergency HTML
- **5-tier page reading engine** — optimized for article content extraction
- **Visual feedback system** — red cursor dot, click ripple, action overlay, element highlight
- **8-strategy checkbox handler** — handles hidden inputs, custom widgets, ARIA roles
- **3-tier browser launch** — CDP attach → Fresh Edge → Chromium fallback
- **Auto-reconnect** — `_reinit_browser()` on browser crash/close (max 3 attempts)
- **Anti-detection stealth** — `navigator.webdriver`, WebGL, canvas, plugins, permissions
- **60+ overlay dismiss patterns** — cookie consent, notifications, newsletters, modals
- **Stagehand v3 semantic primitives** — `observe()`, `act()`, `extract()`
- **Action cache** — deterministic replay in <100ms for repeated actions

---

## 5. Tool Registry & Agent Functions

### Production Tools

| Tool | File | Description | Mode |
|------|------|-------------|------|
| `edge` | `edge.py` | Microsoft Edge browser automation (5284 lines, 40+ actions) | Server |
| `email` | `email.py` | macOS Mail.app controller: send, search, read. HTML link extraction. | Server |
| `i` | `i.py` | Messages app: read, search, send SMS/iMessage. 3-tier fallback (SQLite → AppleScript → Accessibility). Verification code/link extraction. | Server |
| `imessage` | `imessage.py` | Simple iMessage sender (legacy, `i.py` supersedes) | One-shot |
| `nano` | `nano.py` | File editor: Python direct write + nano visual feedback | One-shot |
| `macOs-terminal` | `macOs-terminal.py` | Terminal.app control: run, read, state, keystroke, write_file | One-shot |
| `fterminal` | `fterminal.py` | Shell command execution in sandboxed environment | One-shot |
| `app-opener` | `app-opener.py` | Launch macOS apps via `open -a` | One-shot |
| `safari` | `safari.py` | Safari browser automation (AppleScript + JS) | Server |
| `weather` | `agentf-weather.py` | Weather from wttr.in with auto-geolocation | One-shot |
| `file-finder` | `file-finder.py` | Spotlight-powered file search (mdfind) | One-shot |
| `notes` | `agentf-notes.py` | Apple Notes.app control | One-shot |
| `calc` | `calc.py` | Calculator utility | One-shot |
| `datasette` | `datasette.py` | SQL database visualization dashboard | One-shot |
| `a` | `a.py` | Agent orchestrator: boot, clone, nano_edit | One-shot |

### Verification Flow (Email + SMS)

The agent has a unified account verification workflow:
1. Search email/messages for verification codes → `email search` or `i search`
2. Auto-extract verification codes (4-8 digit) and URLs from results
3. Open verification link in Edge or fill code into form
4. Resume task in the original Edge tab

---

## 6. Memory & Training System

### Three-Tier Memory (`memory.py` — 899 lines)

```
memory/
├── procedural/     # Saved task procedures (training mode)
│   └── proc_<hash>.json
├── episodic/       # Auto-recorded agent runs
│   └── ep_<timestamp>.json
├── semantic/       # Facts and auto-learned patterns
│   └── sem_<hash>.json
└── index.json      # Master index with metadata
```

### BM25 Retrieval (upgraded from binary TF-IDF)

- Scoring: k1=1.2, b=0.75, avg_dl=15.0
- **Boosts**: procedural 1.5x, auto-patterns 1.3x, recent 1.2x, high-success 1.4x
- **Penalties**: failed episodes (<3.0 score) de-ranked 0.7x
- Retrieved memories injected as `=== AGENT MEMORY ===` block in system prompt

### Training Mode

Activate with `-train <prompt>`:

1. `TrainingRecorder` captures every step: tool, args, result_summary, reasoning
2. Step quality tracked: critical / failure / neutral
3. On completion: `save_procedure()` → `proc_<hash>.json`
4. Compression: procedures >25 steps → keep critical + first/last 3
5. Next time similar task → procedure recalled + injected into system prompt

### Episode Recording

Always active. After each agent run:
- Auto-computes `success_score` (0-10) from outcome, efficiency, failure count
- Tracks browser reconnects, stall recoveries
- When 3+ similar high-success episodes → `_extract_patterns()` saves as semantic memory

### Commands

| Command | Description |
|---------|-------------|
| `memory` | Show stats (procedures, episodes, facts) |
| `memory list [type]` | List memories, optionally filtered |
| `memory search <query>` | BM25 search with relevance scores |
| `memory teach <fact>` | Save a semantic fact |
| `memory delete <id>` | Delete a specific memory |
| `memory prune` | Remove old low-value episodes |

---

## 7. Swarm Coordination

### Architecture: AetherSwarm v4.1

Always-on swarm with 4-phase pipeline. Default mode: **parallel**.

```
User Request
    │
    ▼
Phase 0 — SCOUT
    LLM makes first tool call (e.g., new_tab to target URL)
    Auto-dismiss overlays + fresh snapshot
    Scout data truncated at 18K chars (preserves link URLs)
    │
    ▼
Phase 1 — PLAN
    _extract_links() parses real links from scout snapshot
    build_plan_prompt() injects CLICKABLE LINKS section
    _validate_plan() checks URLs against scout data (anti-hallucination)
    LLM outputs swarm_plan JSON or regular tool call
    │
    ▼
Phase 2 — DISPATCH
    execute_swarm() auto-detects MODE A vs MODE B
    RAM-aware worker count (get_safe_workers())
    404 detection on all navigation steps
    Round-robin tab interleaving (MODE A) or sequential clicks (MODE B)
    │
    ▼
Phase 3 — SYNTHESIZE
    format_results_for_llm() condenses worker results
    LLM synthesizes unified response
    If follow-up tool call needed → switches to sequential loop
```

### Dual-Mode Execution

| | MODE A — Parallel Tabs | MODE B — Sequential Clicks |
|--|------------------------|---------------------------|
| **When** | Real URLs found in CLICKABLE LINKS | Only #N refs available |
| **Worker start** | `new_tab(EXACT_URL)` | `click(#N)` on scout tab |
| **Execution** | Round-robin interleaving across tabs | Sequential per-worker on shared tab |
| **Speed** | ~15-25s for 3 workers × 4 steps | ~30-45s for 3 workers × 5 steps |
| **Reliability** | Needs valid URLs | Works when URLs are JS-based |

### User Mode Control

| Command | Behavior |
|---------|----------|
| `swarm parallel` | (Default) Force swarm_plan for every task |
| `swarm sequential` | Skip planning, go straight to sequential loop |
| `swarm auto` | Old Rule 5: surveys → sequential, everything else → coordinator decides |
| `swarm on/off` | Enable/disable swarm entirely |

Inline detection: saying "use swarm in parallel mode" in a chat message auto-activates parallel mode.

### Fallback Guarantees

- Scout returns empty → retry once
- Plan parsing fails → seamless fallback to sequential `_loop()`
- All workers 404 → recovery message + sequential fallback
- Partial worker failures → partial results synthesized
- Synthesis requests follow-up → switches to standard loop

---

## 8. Model & Inference

### Hardware

| Spec | Value |
|------|-------|
| Chip | Apple M4 (base) |
| RAM | 16 GB unified memory |
| Memory bandwidth | ~120 GB/s |
| Model | Qwen 3.5-9B (4-bit, MLX) |
| Model footprint | ~5 GB |
| Architecture | Hybrid: linear attention (GatedDeltaNet) + full attention |
| Generation speed | ~21 tok/s (79% of theoretical max) |
| Prompt processing | ~100-170 tok/s |
| Max generation | 4096 tokens per response |
| Context window | 32,768 tokens native (262,144 with YaRN) |

### Model Loading

```python
# qwen.npz is a manifest pointing to HuggingFace
from mlx_lm import load, stream_generate
model, tokenizer = load('mlx-community/Qwen3.5-9B-MLX-4bit')
```

The `.npz` file is an indirection layer — it doesn't contain weights, just a pointer to the HF repo. On first run, `mlx_lm` downloads the model (~5 GB). Subsequent runs load from cache.

**Note:** Qwen3.5-9B uses a hybrid architecture (`qwen3_5` model type) not natively supported by mlx-lm 0.29.1. Custom model files (`qwen3_5.py` and `qwen3_5_text.py`) were added to `mlx_lm/models/` to enable loading. The tokenizer config was also patched (`TokenizersBackend` → `PreTrainedTokenizerFast`).

### Think-Tag Streaming

Qwen 3.5 uses `<think>...</think>` for chain-of-thought reasoning:

- Thinking text streams in **grey** (always visible by default)
- Response text streams in **red**
- Partial tag buffering handles tags split across token chunks
- `show_thoughts=True` by default — user sees the full reasoning trace

### TPS Status Line

After each generation, a status line appears:

```
  142 tok · 21.3 t/s · prefill 168 t/s · 5.2GB
```

Token count, generation speed, prompt processing speed, and peak GPU memory.

---

## 9. CLI Usage

### Primary

```bash
# Launch Amber
python ai.py --weights qwen.npz

# Or via chat.sh launcher
./chat.sh
```

### Chat Interface

```
  > Go to swagbucks.com and complete the survey
  Amber  <thinks in grey, then calls tools autonomously>
  142 tok · 21.3 t/s · prefill 168 t/s · 5.2GB

  > stop                    # Pause agent
  > exit                    # Quit

  > swarm parallel          # Set parallel swarm mode (default)
  > swarm sequential        # Set sequential mode
  > swarm                   # Show swarm status

  > memory                  # Show memory stats
  > memory search surveys   # Search memories
  > memory teach always use dismiss before reading surveys

  > -train sign up on example.com    # Training mode: records procedure
```

---

## 10. Project Structure

```
~/Developer/llm/
├── ai.py                       Brain: MLX streaming engine (541 lines)
├── agent.py                    Router: ReAct + Swarm v4.1 (2357 lines)
├── memory.py                   Memory: BM25 retrieval + training (899 lines)
├── qwen.npz                    Model manifest (HF repo pointer)
├── benchmark.py                Token speed benchmarking tool
├── edge-py.md                  AetherEdge architecture reference
├── README.md                   This file
│
├── agent-functions/            Tool scripts (auto-discovered)
│   ├── edge.py                 AetherEdge v22.1+ (5284 lines)
│   ├── email.py                Mail.app controller
│   ├── i.py                    Messages controller (3-tier fallback)
│   ├── imessage.py             Simple iMessage sender
│   ├── nano.py                 File editor
│   ├── macOs-terminal.py       Terminal.app control
│   ├── fterminal.py            Shell executor
│   ├── app-opener.py           macOS app launcher
│   ├── safari.py               Safari automation
│   ├── agentf-weather.py       Weather lookup
│   ├── file-finder.py          Spotlight search
│   ├── agentf-notes.py         Apple Notes
│   ├── calc.py                 Calculator
│   ├── datasette.py            SQL visualization
│   └── a.py                    Agent orchestrator
│
├── memory/                     Persistent memory store
│   ├── procedural/             Saved procedures
│   ├── episodic/               Agent run records
│   ├── semantic/               Facts and patterns
│   └── index.json              Master index
│
├── gui.py                      PySide6 macOS app wrapper
├── installer.py                Venv bootstrap manager
├── setup.py                    py2app bundling recipe
├── chat.sh                     CLI launcher
├── build.sh                    Build script
└── supplementary-files/        Utilities, backups
```

---

## 11. Setup & Prerequisites

### Requirements

- macOS 14.0+ (Apple Silicon required for MLX)
- Python 3.9+ with `pip`
- Microsoft Edge browser (for `edge.py`)
- `mlx`, `mlx-lm`, `playwright` packages

### Quick Start

```bash
# 1. Install dependencies
pip install mlx mlx-lm playwright
python -m playwright install chromium

# 2. Launch Amber
cd ~/Developer/llm
python ai.py --weights qwen.npz

# First run downloads Qwen 3.5-9B 4-bit from HuggingFace (~5GB).
# Subsequent runs load from cache instantly.
```

### Edge Browser Setup

For `edge.py` CDP attachment (recommended — preserves existing logins):

```bash
# Launch Edge with remote debugging
/Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge \
  --remote-debugging-port=9222
```

### Configuration Constants

| Constant | File | Value | Description |
|----------|------|-------|-------------|
| `MAX_TOKENS` | `ai.py` | 4096 | Max generation tokens per response |
| `OBSERVATION_CHARS` | `agent.py` | 14000 | Standard tool result truncation |
| `OBSERVATION_CHARS_CONTENT` | `agent.py` | 24000 | Content-heavy action truncation |
| `SERVER_READ_TIMEOUT` | `agent.py` | 55s | Server mode response timeout |
| `CONTEXT_PRUNE_LIMIT` | `agent.py` | 64000 | Chars before context pruning |
| `STEP_TIME_WARN` | `agent.py` | 25s | Slow step warning threshold |

---

*Agent F (Amber) — AetherEdge v22.1 + AetherSwarm v4.1 — March 2026*
