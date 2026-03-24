#ai.py for Amber agent
#!/usr/bin/env python3
import argparse
import sys
import os
import json
import numpy as np
import re
import io
import logging
import warnings
import select as _select_mod
import termios
import time as _time_mod
import threading                  # ← REQUIRED for Telegram sidecar
import importlib.util             # ← REQUIRED for hyphen filename import

# --- ANSI COLORS (Red Theme) ---
R   = "\033[31m"
BR  = "\033[91m"
DIM = "\033[2m"
B   = "\033[1m"
X   = "\033[0m"
GR  = "\033[90m"

# --- IMPORTS ---
try:
    import mlx.core as mx
    from mlx_lm import load, stream_generate
except ImportError:
    print(f"{BR}Error:{X} MLX not installed. Run: pip install mlx mlx-lm")
    sys.exit(1)

try:
    import agent
except ImportError:
    print(f"{BR}Error:{X} Could not import 'agent.py'. Ensure it is in the same folder.")
    sys.exit(1)

try:
    from memory import MemoryStore, Learnings
except ImportError:
    MemoryStore = None
    Learnings = None

# --- NPZ LOADING LOGIC ---
def _load_meta(weights_npz: str):
    try:
        manifest = np.load(weights_npz, allow_pickle=True)
        if "meta_json" not in manifest:
            return {"repo": weights_npz}
        raw_meta = manifest["meta_json"][()]
        if isinstance(raw_meta, bytes): raw_meta = raw_meta.decode("utf-8")
        return json.loads(raw_meta)
    except Exception as e:
        print(f"Error reading NPZ: {e}")
        sys.exit(1)

def load_from_npz(weights: str):
    meta = _load_meta(weights)
    repo = meta.get("repo")
    if not repo:
        print("Error: Could not find 'repo' in weights metadata.")
        sys.exit(1)

    print(f"  {GR}Loading {repo}{X}", flush=True)

    # Suppress noisy mlx-lm / transformers warnings during load
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    prev_log_level = logging.root.manager.disable
    logging.disable(logging.WARNING)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model, tokenizer = load(repo)
        finally:
            sys.stderr = old_stderr
            logging.disable(prev_log_level)

    return model, tokenizer

def _strip_markdown_for_display(text):
    """Strip markdown formatting (* / **) from text for clean terminal display."""
    return re.sub(r'\*+', '', text)

# --- CHAT LOOP ---
def chat_main(args):
    if not os.path.exists(args.weights):
        print(f"{BR}Error:{X} Weights file not found: {args.weights}")
        return

    # Banner
    print()
    print(f"  {B}{BR}  A M B E R{X}")
    print(f"  {GR}  autonomous agent{X}")
    print()

    try:
        model, tokenizer = load_from_npz(args.weights)
    except Exception as e:
        print(f"  {BR}Load failed:{X} {e}")
        return

    # RAM monitoring — show available headroom
    try:
        mem_used_gb = mx.metal.get_active_memory() / (1024**3)
        mem_limit_gb = 16.0
        headroom = mem_limit_gb - mem_used_gb
        print(f"  {GR}GPU RAM: {mem_used_gb:.1f}GB used, {headroom:.1f}GB free{X}", flush=True)
    except Exception:
        pass

    # Get Tools from Agent
    try:
        if hasattr(agent, "get_system_prompt_addendum"):
            tool_instructions = agent.get_system_prompt_addendum()
        else:
            tool_instructions = "Tools: browser, calculator."
    except Exception:
        tool_instructions = ""

    # System Prompt — no "Amber:" prefixes to avoid model echoing it
    system_prompt = (
        "You are Amber, a helpful AI assistant and expert software engineer residing on a Mac. "
        "You can control the computer using the tools below — browser, terminal, file editor, and more. "
        "You are an autonomous agent — when given a task, break it into steps "
        "and execute them one at a time using your tools until the task is fully complete. "
        "Do not stop after a single tool call. Keep going until done. "
        "IMPORTANT: Never prefix your responses with your name.\n\n"
        "CRITICAL: 'Complete' means the FULL task is done. If asked to apply for jobs, "
        "searching is NOT done — you must actually SUBMIT applications. If asked to build an app, "
        "writing one file is NOT done — you must create ALL files, build, fix errors, and deliver "
        "a working project. If asked to sign up, you must complete registration. "
        "NEVER stop at an intermediate step and summarize.\n\n"
        "CODING CRITICAL: When building software, you are a CODING AGENT — like Claude Code or Cursor. "
        "You write real code, create real files, run real builds, and debug real errors. "
        "NEVER guess CLI flags — run 'tool --help' if unsure. Read build errors carefully and fix them. "
        "Use write_file or nano_editor to create source files (NOT echo/printf). "
        "Iterate: write → build → read errors → fix → rebuild until the project compiles and runs.\n\n"
        "SWARM MODE (always active, sequential by default): After your first tool call (scout), "
        "continue step by step. Execute one tool call at a time until the task is complete. "
        "If the user says 'swarm parallel', you may be asked to create a swarm_plan for "
        "parallel execution using REAL URLs from scout data — never guess URLs.\n\n"
        "To use a tool, reply with a JSON object describing the action. "
        "Do not wrap the JSON in markdown code blocks.\n\n"
        f"{tool_instructions}\n\n"
        "JOB APPLICATION WORKFLOW (when user asks to apply for jobs):\n"
        "1. Read user's resume first (preview tool) to understand their skills and experience\n"
        "2. Open browser → navigate to job site with search URL\n"
        "3. FOR EACH JOB in search results: click job → click Apply → fill form → submit → go back to results → NEXT JOB\n"
        "4. After page 1 exhausted: click Next page → repeat\n"
        "5. After first keyword exhausted: search with DIFFERENT keywords → repeat\n"
        "6. ONLY stop when you have submitted multiple applications (goal: 10+)\n"
        "7. Skip 'Apply on company site' (external links). Only use Indeed/Quick Apply.\n"
        "8. Use resume data for ALL form fields. You already read the resume — use that info.\n"
        "9. Keep a running count of applications submitted and report it.\n\n"
        "Example multi-step workflow:\n"
        'User: "Go to swagbucks.com and sign up"\n'
        '{"tool": "edge", "args": {"action": "new_tab", "url": "https://swagbucks.com"}}\n'
        '[TOOL RESULT: edge]\n... page snapshot ...\n'
        'I can see the Swagbucks homepage. Let me click the sign up button.\n'
        '{"tool": "edge", "args": {"action": "click", "text": "#5"}}\n'
        '[TOOL RESULT: edge]\n... page snapshot ...\n'
        '{"tool": "edge", "args": {"action": "fill", "text": "#3", "value": "user@email.com"}}\n'
        '...and so on until the task is complete.\n'
    )

    messages = [{"role": "system", "content": system_prompt}]

    # Agent loop
    agent_loop = agent.get_agent_loop()

    # Initialize memory system
    memory_store = None
    learnings_store = None
    if MemoryStore is not None:
        try:
            memory_store = MemoryStore()
            agent_loop.memory = memory_store
        except Exception:
            pass
    if Learnings is not None:
        try:
            learnings_store = Learnings()
            agent_loop.learnings = learnings_store
        except Exception:
            pass

    # Detect if chat template starts model in thinking mode
    _test_prompt = tokenizer.apply_chat_template(
        [{"role": "system", "content": "x"}, {"role": "user", "content": "x"}],
        tokenize=False, add_generation_prompt=True
    )
    template_starts_thinking = _test_prompt.rstrip().endswith("<think>")

    # Status line
    swarm_info = ""
    if hasattr(agent_loop, 'swarm_enabled'):
        state = "on" if agent_loop.swarm_enabled else "off"
        workers = agent_loop.coordinator.max_workers if hasattr(agent_loop, 'coordinator') else 3
        mode = getattr(agent_loop, 'swarm_mode', 'auto')
        swarm_info = f" {GR}|{X} {DIM}swarm {state} ({workers}w, {mode}){X}"

    mem_info = ""
    if memory_store:
        stats = memory_store.stats()
        total = stats.get("total", 0)
        if total > 0:
            mem_info = f" {GR}|{X} {DIM}memory {total}{X}"
        else:
            mem_info = f" {GR}|{X} {DIM}memory on{X}"

    learn_info = ""
    if learnings_store:
        lc = learnings_store.count()
        total = lc.get("total", 0)
        if total > 0:
            learn_info = f" {GR}|{X} {DIM}learnings {total}{X}"
        else:
            learn_info = f" {GR}|{X} {DIM}learnings on{X}"

    print(f"  {B}{R}Ready{X}{swarm_info}{mem_info}{learn_info}")
    print(f"  {GR}exit · stop · swarm · vision · memory · learnings · review · -train · telegram{X}")
    print()

    # Telegram sidecar (None until user types "telegram")
    telegram_sidecar = None

    # Optimal max_tokens for M4 16GB — high enough for large file writes,
    # low enough to avoid memory pressure. Qwen 3.5-9B 4-bit sweet spot.
    MAX_TOKENS = 8192

    # Generate callback — real streaming via stream_generate
    def make_generate_fn(show_thoughts=False):
        def generate_fn(msgs):
            prompt = tokenizer.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True
            )
            full_response = ""
            buf = ""

            # If template adds <think>, model starts in thinking mode
            in_think = template_starts_thinking
            prefix_done = in_think  # skip "Amber:" check if starting mid-think

            if in_think:
                sys.stdout.write(GR)  # gray for thinking
                sys.stdout.flush()
            else:
                sys.stdout.write(BR)  # red for response
                sys.stdout.flush()

            # TPS tracking
            _gen_tps = 0.0
            _prompt_tps = 0.0
            _gen_tokens = 0
            _peak_mem = 0.0

            for resp in stream_generate(model, tokenizer, prompt=prompt,
                                        max_tokens=MAX_TOKENS):
                chunk = resp.text
                _gen_tps = resp.generation_tps
                _prompt_tps = resp.prompt_tps
                _gen_tokens = resp.generation_tokens
                _peak_mem = resp.peak_memory

                full_response += chunk
                buf += chunk

                # Process buffer
                while buf:
                    if in_think:
                        # === THINKING MODE: gray text, looking for </think> ===
                        pos = buf.find("</think>")
                        if pos >= 0:
                            # Print remaining think content
                            think_part = buf[:pos].replace("<think>", "")
                            if show_thoughts:
                                sys.stdout.write(think_part)
                                sys.stdout.write(f"\n")  # visual break
                            # Switch to red for response
                            sys.stdout.write(f"{X}{BR}")
                            sys.stdout.flush()
                            buf = buf[pos + 8:].lstrip('\n')
                            in_think = False
                            prefix_done = True
                            continue

                        # Check for partial </think> at buffer end
                        held = 0
                        for i in range(min(7, len(buf)), 0, -1):
                            if buf.endswith("</think>"[:i]):
                                held = i
                                break
                        safe = buf[:len(buf) - held] if held else buf
                        if safe:
                            safe = safe.replace("<think>", "")
                            if show_thoughts:
                                sys.stdout.write(safe)
                                sys.stdout.flush()
                        buf = buf[len(buf) - held:] if held else ""
                        break

                    else:
                        # === RESPONSE MODE: red text ===
                        # Strip "Amber:" prefix from model output
                        if not prefix_done:
                            stripped = buf.lstrip()
                            sl = stripped.lower()
                            if sl.startswith("amber:"):
                                cut = buf.lower().find("amber:") + 6
                                while cut < len(buf) and buf[cut] == ' ':
                                    cut += 1
                                buf = buf[cut:]
                                prefix_done = True
                                continue
                            elif len(stripped) < 7 and stripped and "amber:".startswith(sl):
                                break  # Need more data for prefix check
                            elif stripped:
                                prefix_done = True

                        # Check for <think> (model re-entering thinking)
                        pos = buf.find("<think>")
                        if pos >= 0:
                            pre = buf[:pos]
                            if pre:
                                sys.stdout.write(pre)
                            sys.stdout.write(f"{X}{GR}")  # switch to gray
                            sys.stdout.flush()
                            buf = buf[pos + 7:]
                            in_think = True
                            continue

                        # Check for partial <think> at buffer end
                        held = 0
                        for i in range(min(6, len(buf)), 0, -1):
                            if buf.endswith("<think>"[:i]):
                                held = i
                                break
                        safe = buf[:len(buf) - held] if held else buf
                        if safe:
                            sys.stdout.write(_strip_markdown_for_display(safe))
                            sys.stdout.flush()
                        buf = buf[len(buf) - held:] if held else ""
                        break

            # Flush remaining buffer
            if buf:
                buf = buf.replace("<think>", "").replace("</think>", "")
                sys.stdout.write(_strip_markdown_for_display(buf))
            sys.stdout.write(f"{X}\n")  # reset color + newline

            # TPS status line
            sys.stdout.write(
                f"  {GR}{_gen_tokens} tok · {_gen_tps:.1f} t/s · "
                f"prefill {_prompt_tps:.0f} t/s · {_peak_mem:.1f}GB{X}\n"
            )
            sys.stdout.flush()

            # Signal truncation to agent loop (hit max_tokens)
            generate_fn.was_truncated = (_gen_tokens >= MAX_TOKENS)
            return full_response

        return generate_fn

    # Multi-line paste support
    def read_user_input():
        """Read input with multi-line paste support.

        Uses readline normally (arrow keys, history, backspace work).
        After input() returns, switches to non-canonical mode to read
        any remaining paste data from the PTY buffer.
        """
        fd = sys.stdin.fileno()

        try:
            original = termios.tcgetattr(fd)
        except termios.error:
            return input(f"  {B}{R}>{X} ").strip()

        # Let readline handle input normally (canonical mode, full editing)
        try:
            first_line = input(f"  {B}{R}>{X} ")
        except (EOFError, KeyboardInterrupt):
            raise

        # After input() returns, switch to non-canonical for paste reading
        nc = termios.tcgetattr(fd)
        nc[3] &= ~termios.ICANON
        nc[6][termios.VMIN] = 0
        nc[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, nc)

        try:
            if not _select_mod.select([fd], [], [], 0.1)[0]:
                return first_line.strip()

            # Paste detected — read all chunks with sleep between reads
            # so Terminal.app can flush flow-controlled PTY data
            remaining = b''
            timeout = 0.5
            while True:
                if not _select_mod.select([fd], [], [], timeout)[0]:
                    break
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                remaining += chunk
                _time_mod.sleep(0.1)
                timeout = 3.0 if len(remaining) > 500 else 0.5

            if remaining:
                text = remaining.decode('utf-8', errors='replace')
                text = text.replace('\r\n', '\n').replace('\r', '\n')
                return (first_line + '\n' + text).strip()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, original)

        return first_line.strip()

    # Main Loop
    while True:
        try:
            telegram_chat_id = None

            # ── When telegram sidecar is active, poll both sources ──
            if telegram_sidecar and telegram_sidecar.active:
                raw_input = None
                # Use a background thread to read terminal input without blocking
                # the Telegram queue check
                _term_result = [None]
                _term_done = threading.Event()

                def _read_terminal():
                    try:
                        _term_result[0] = read_user_input()
                    except EOFError:
                        _term_result[0] = "exit"
                    _term_done.set()

                _term_thread = threading.Thread(target=_read_terminal, daemon=True)
                _term_thread.start()

                while raw_input is None:
                    # Check Telegram queue
                    try:
                        tg_msg = telegram_sidecar.inject_queue.get_nowait()
                        telegram_chat_id = tg_msg['chat_id']
                        raw_input = tg_msg['text']
                        if raw_input == '/reset':
                            messages.clear()
                            messages.append({"role": "system", "content": system_prompt})
                            telegram_sidecar.api.send_message(telegram_chat_id, "Conversation cleared.")
                            print(f"  {GR}[Telegram] /reset{X}")
                            raw_input = None
                            continue
                        # print(f"  {GR}[Telegram] {raw_input[:80]}{X}")   # removed — sidecar already prints it
                        break
                    except Exception:
                        pass

                    # Check if terminal input arrived
                    if _term_done.wait(0.3):
                        raw_input = _term_result[0]
                        break
            else:
                # ── Normal mode: blocking terminal input ──
                try:
                    raw_input = read_user_input()
                except EOFError:
                    break

            if not raw_input:
                continue
            if raw_input.lower() in ("exit", "quit"):
                break

            # Swarm toggle and mode commands
            raw_lower = raw_input.lower().strip()
            if raw_lower in ("swarm on", "swarm enable"):
                if hasattr(agent_loop, 'swarm_enabled'):
                    agent_loop.swarm_enabled = True
                    print(f"  {GR}swarm: on ({agent_loop.swarm_mode}){X}")
                continue
            if raw_lower in ("swarm off", "swarm disable"):
                if hasattr(agent_loop, 'swarm_enabled'):
                    agent_loop.swarm_enabled = False
                    print(f"  {GR}swarm: off{X}")
                continue
            if raw_lower in ("swarm parallel", "swarm mode parallel"):
                if hasattr(agent_loop, 'swarm_mode'):
                    agent_loop.swarm_enabled = True
                    agent_loop.swarm_mode = "parallel"
                    print(f"  {GR}swarm: on (parallel mode — workers will run in parallel tabs){X}")
                continue
            if raw_lower in ("swarm sequential", "swarm mode sequential", "swarm seq"):
                if hasattr(agent_loop, 'swarm_mode'):
                    agent_loop.swarm_enabled = True
                    agent_loop.swarm_mode = "sequential"
                    print(f"  {GR}swarm: on (sequential mode — step-by-step execution){X}")
                continue
            if raw_lower in ("swarm auto", "swarm mode auto"):
                if hasattr(agent_loop, 'swarm_mode'):
                    agent_loop.swarm_enabled = True
                    agent_loop.swarm_mode = "auto"
                    print(f"  {GR}swarm: on (auto mode — coordinator decides){X}")
                continue
            if raw_lower == "swarm":
                if hasattr(agent_loop, 'swarm_enabled'):
                    state = "on" if agent_loop.swarm_enabled else "off"
                    mode = getattr(agent_loop, 'swarm_mode', 'auto')
                    print(f"  {GR}swarm: {state} ({mode}){X}")
                    print(f"  {GR}  commands: swarm on/off/parallel/sequential/auto{X}")
                continue

            # ── Memory management commands ──
            if memory_store:
                if raw_lower == "memory":
                    stats = memory_store.stats()
                    print(f"  {GR}Memory: {stats.get('procedural',0)} procedures, "
                          f"{stats.get('episodic',0)} episodes, {stats.get('semantic',0)} facts{X}")
                    print(f"  {GR}  commands: memory list/search/delete/prune/teach{X}")
                    continue
                if raw_lower.startswith("memory list"):
                    parts = raw_lower.split()
                    mem_type = parts[2] if len(parts) > 2 else None
                    mems = memory_store.list_memories(mem_type, limit=15)
                    if not mems:
                        print(f"  {GR}(no memories){X}")
                    for m in mems:
                        label = m.get("task", m.get("content", ""))[:60]
                        print(f"  {GR}{m.get('id','?')}: {label}{X}")
                    continue
                if raw_lower.startswith("memory search "):
                    query = raw_input[14:].strip()
                    results = memory_store.recall(query, top_k=5, min_score=0.05)
                    if not results:
                        print(f"  {GR}(no matches){X}")
                    for r in results:
                        score = r.get('relevance_score', 0)
                        label = r.get('task', r.get('content', ''))[:60]
                        print(f"  {GR}[{score:.2f}] {r.get('type','?')}: {label}{X}")
                    continue
                if raw_lower.startswith("memory delete "):
                    mem_id = raw_input[14:].strip()
                    if memory_store.delete_memory(mem_id):
                        print(f"  {GR}Deleted {mem_id}{X}")
                    else:
                        print(f"  {BR}Not found: {mem_id}{X}")
                    continue
                if raw_lower.startswith("memory teach ") or raw_lower.startswith("memory fact "):
                    prefix_len = 13 if raw_lower.startswith("memory teach") else 12
                    fact = raw_input[prefix_len:].strip()
                    if fact:
                        sem_id = memory_store.save_fact(fact, source="user")
                        print(f"  {GR}Saved: {sem_id}{X}")
                    continue
                if raw_lower == "memory prune":
                    removed = memory_store.prune()
                    print(f"  {GR}Pruned {removed} old episodes{X}")
                    continue

            # ── Learnings commands ──
            if learnings_store:
                if raw_lower == "learnings":
                    content = learnings_store.load()
                    if content:
                        print(f"  {GR}{content}{X}")
                    else:
                        print(f"  {GR}(no learnings yet — they accumulate automatically after tasks){X}")
                    continue
                if raw_lower.startswith("learnings search "):
                    query = raw_input[17:].strip()
                    results = learnings_store.search(query, top_k=10)
                    if not results:
                        print(f"  {GR}(no matches){X}")
                    for r in results:
                        print(f"  {GR}[{r.get('category','?')}] {r.get('text','')[:80]}{X}")
                    continue
                if raw_lower == "learnings prune":
                    removed = learnings_store.prune()
                    print(f"  {GR}Pruned {removed} old learnings{X}")
                    continue

            # ── Review command (self-review statistics) ──
            if raw_lower == "review" and memory_store:
                stats = memory_store.self_review()
                if "message" in stats:
                    print(f"  {GR}{stats['message']}{X}")
                else:
                    print(f"  {GR}─── Agent Self-Review ───{X}")
                    print(f"  {GR}Episodes reviewed: {stats['episodes_reviewed']}{X}")
                    print(f"  {GR}Success rate: {stats['success_rate']}%{X}")
                    print(f"  {GR}Outcomes: {stats['outcomes']}{X}")
                    print(f"  {GR}Avg score: {stats['avg_score']} (min {stats['min_score']}, max {stats['max_score']}){X}")
                    print(f"  {GR}Avg steps: {stats['avg_steps']}{X}")
                    if stats.get('top_tools'):
                        tools_str = ", ".join(f"{t}({c})" for t, c in stats['top_tools'])
                        print(f"  {GR}Top tools: {tools_str}{X}")
                    if stats.get('top_failures'):
                        fails_str = ", ".join(f"{t}({c})" for t, c in stats['top_failures'])
                        print(f"  {GR}Top failures: {fails_str}{X}")
                    print(f"  {GR}Procedures: {stats['procedures_total']}{X}")
                    if learnings_store:
                        lc = learnings_store.count()
                        print(f"  {GR}Learnings: {lc.get('total', 0)} "
                              f"(patterns:{lc.get('pattern',0)} mistakes:{lc.get('mistake',0)} "
                              f"optimizations:{lc.get('optimization',0)}){X}")
                    print(f"  {GR}─────────────────────────{X}")
                continue

            # ── Auto-train toggle ──
            if raw_lower in ("auto-train on", "auto-learn on", "autolearn on"):
                if hasattr(agent_loop, 'auto_learn'):
                    agent_loop.auto_learn = True
                    print(f"  {GR}auto-learn: on (write-after-work active){X}")
                continue
            if raw_lower in ("auto-train off", "auto-learn off", "autolearn off"):
                if hasattr(agent_loop, 'auto_learn'):
                    agent_loop.auto_learn = False
                    print(f"  {GR}auto-learn: off (no automatic learnings extraction){X}")
                continue

            # ── Telegram sidecar ──
            if raw_lower in ("telegram", "telegram on", "telegram start"):
                if telegram_sidecar and telegram_sidecar.active:
                    print(f"  {GR}telegram: already active (@{telegram_sidecar.bot_username}){X}")
                    print(f"  {GR}  https://t.me/{telegram_sidecar.bot_username}{X}")
                else:
                    try:
                        # FIXED: load by full path so hyphen filename works perfectly
                        tg_path = os.path.join(
                            os.path.dirname(os.path.abspath(__file__)),
                            'agent-functions',
                            'telegram-link.py'
                        )
                        spec = importlib.util.spec_from_file_location("telegram_link", tg_path)
                        tg_mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(tg_mod)

                        # Prompt for token if no config exists
                        config = tg_mod.load_config()
                        if not config or config.get('token', '').startswith('YOUR_'):
                            print(f"  {GR}telegram: first-time setup{X}")
                            try:
                                token_input = input(f"  {GR}paste bot token from @BotFather:{X} ").strip()
                            except (EOFError, KeyboardInterrupt):
                                print(f"\n  {GR}telegram: cancelled{X}")
                                continue
                            if not token_input or ':' not in token_input:
                                print(f"  {BR}invalid token (must contain ':'). Get one from @BotFather{X}")
                                continue
                            config = tg_mod.ensure_config(token_input)
                            if not config:
                                print(f"  {BR}telegram: config failed{X}")
                                continue
                            print(f"  {GR}token saved to ~/.amber_telegram.json{X}")
                        telegram_sidecar = tg_mod.TelegramSidecar(config['token'])
                        ok, info = telegram_sidecar.start()
                        if ok:
                            print(f"  {GR}telegram: connected to @{info}{X}")
                            print(f"  {GR}  https://t.me/{info}{X}")
                            print(f"  {GR}  send a message from your phone to register{X}")
                        else:
                            print(f"  {BR}telegram: {info}{X}")
                            telegram_sidecar = None
                    except Exception as e:
                        print(f"  {BR}telegram error: {e}{X}")
                        telegram_sidecar = None
                continue
            if raw_lower in ("telegram off", "telegram stop"):
                if telegram_sidecar:
                    telegram_sidecar.stop()
                    telegram_sidecar = None
                    print(f"  {GR}telegram: disconnected{X}")
                else:
                    print(f"  {GR}telegram: not active{X}")
                continue

            # ── Training mode: -train prefix ──
            if raw_lower.startswith("-train "):
                train_task = raw_input[7:].strip()
                if train_task and hasattr(agent_loop, 'training_mode'):
                    agent_loop.training_mode = True
                    print(f"  {GR}training mode: recording procedure{X}")
                    print(f"  {GR}task: {train_task[:80]}{X}")
                    # The training flag will be picked up by run()
                    # After task completes, it auto-saves the procedure
                    raw_input = train_task  # use the task as the user input
                    raw_lower = raw_input.lower().strip()
                    # Fall through to normal processing below

            # Detect inline swarm mode requests in user message
            # e.g., "use swarm in parallel mode, ai do something"
            if hasattr(agent_loop, 'swarm_mode'):
                if re.search(r'swarm\s+(?:in\s+)?parallel', raw_lower):
                    agent_loop.swarm_enabled = True
                    agent_loop.swarm_mode = "parallel"
                    print(f"  {GR}swarm: parallel mode activated{X}")
                elif re.search(r'swarm\s+(?:in\s+)?sequential', raw_lower):
                    agent_loop.swarm_enabled = True
                    agent_loop.swarm_mode = "sequential"
                    print(f"  {GR}swarm: sequential mode activated{X}")
                elif 'swarm on' in raw_lower:
                    agent_loop.swarm_enabled = True
                    print(f"  {GR}swarm: on ({agent_loop.swarm_mode}){X}")

            # ── Vision mode detection (off by default) ──
            if hasattr(agent_loop, 'vision_enabled'):
                if re.search(r'\busing?\s+vision\b|\bvision\s+mode\b', raw_lower):
                    agent_loop.vision_enabled = True
                    print(f"  {GR}vision: on (pixel-based navigation active){X}")
                elif raw_lower.strip() in ("vision on", "vision"):
                    agent_loop.vision_enabled = True
                    print(f"  {GR}vision: on (pixel-based navigation active){X}")
                    continue
                elif raw_lower.strip() in ("vision off", "no vision"):
                    agent_loop.vision_enabled = False
                    print(f"  {GR}vision: off (traditional #N ref navigation){X}")
                    continue

            # Thinking is always shown in grey text
            show_thoughts = True
            user_content = raw_input

            if not user_content:
                continue

            # ── Inject vision system prompt when vision mode is active ──
            if getattr(agent_loop, 'vision_enabled', False):
                vision_prompt = (
                    "\n\nVISION MODE (ACTIVE): You have pixel-based navigation. "
                    "Use vision_snapshot to capture the page with element coordinates. "
                    "Then use pixel_click/pixel_type/pixel_drag with normalized [x,y] coords (0-1000 scale). "
                    "[0,0]=top-left, [1000,1000]=bottom-right. "
                    "For desktop-wide vision, use screen_capture tool. "
                    "You can still use traditional snapshot + #N refs as fallback.\n"
                    "WORKFLOW: vision_snapshot → identify element positions → pixel_click/pixel_type → repeat"
                )
                if messages and messages[0]["role"] == "system":
                    if "VISION MODE (ACTIVE)" not in messages[0]["content"]:
                        messages[0]["content"] += vision_prompt

            messages.append({"role": "user", "content": user_content})

            # Execute — "Amber" prefix printed once, agent loop handles the rest
            print(f"  {B}{BR}Amber{X} ", end="", flush=True)

            # Telegram sidecar: begin streaming to Telegram
            if telegram_sidecar and telegram_sidecar.active:
                telegram_sidecar.begin_response(telegram_chat_id)

            gen_fn = make_generate_fn(show_thoughts)
            agent_loop.run(messages, gen_fn)

            # Telegram sidecar: finalize and send complete response
            if telegram_sidecar and telegram_sidecar.active:
                telegram_sidecar.end_response()

            # Reset training mode after each run
            if hasattr(agent_loop, 'training_mode') and agent_loop.training_mode:
                agent_loop.training_mode = False

        except KeyboardInterrupt:
            if telegram_sidecar:
                telegram_sidecar.stop()
            agent_loop.cleanup()
            print(f"\n  {GR}Goodbye.{X}")
            break
        except Exception as e:
            print(f"\n  {BR}Error:{X} {e}")

    if telegram_sidecar:
        telegram_sidecar.stop()
    agent_loop.cleanup()

def main():
    parser = argparse.ArgumentParser(description="Amber")
    parser.add_argument("--weights", type=str, default="qwen.npz", help="Path to qwen.npz")
    parser.add_argument("--agent", type=str, default="agent.py", help="Path to agent script")
    parser.add_argument("cmd", nargs="?", default="chat", help="Command (default: chat)")

    args = parser.parse_args()
    chat_main(args)

if __name__ == "__main__":
    main()
