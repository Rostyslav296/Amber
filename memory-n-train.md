# Memory & Training Architecture (v4.2)

Closed-loop agentic memory system for Amber. Inspired by Karpathy's autoresearch
(self-improving agent) and the viral LEARNINGS.md shared brain file pattern.

## Status

- **Memory:** Always ON (auto-records episodes after every task)
- **Learnings:** Always ON (reads before work, writes after work)
- **Training (-train):** Manual activation per-task
- **Auto-learn:** ON by default (toggle: `auto-train on/off`)
- **Model:** Qwen 3.5-9B (text-only); all memory is structured text

---

## Architecture Overview

```
User: "go to kayak.com and find flights to NYC"
                    |
                    v
            [ai.py] Chat loop
            Initializes MemoryStore + Learnings
                    |
                    v
            [agent.py] SwarmAgentLoop.run()
            ┌───────────────────────────────┐
            │  READ BEFORE WORK             │
            │  1. Store base system prompt   │
            │  2. Recall memories (BM25)     │
            │  3. Load learnings (relevant)  │
            │  4. Rebuild system message     │
            │     (bounded, not cumulative)  │
            └───────────┬───────────────────┘
                        |
                        v
            [agent.py] _loop() — execute task
            EpisodeRecorder tracks every tool call
            TrainingRecorder active if -train mode
                        |
                        v
            [agent.py] _finalize_memory()
            ┌───────────────────────────────┐
            │  WRITE AFTER WORK             │
            │  1. Save episode (auto)        │
            │  2. Extract learnings:         │
            │     - Stalls → mistake         │
            │     - Efficient → optimization │
            │     - Failed → mistake         │
            │  3. Auto-promote if score ≥8   │
            │  4. Detect failure patterns    │
            │  5. Refine existing procedures │
            └───────────────────────────────┘
```

---

## Three-Tier Memory

### 1. Procedural Memory (`memory/procedural/`)

Step-by-step task procedures, either recorded via `-train` mode or auto-promoted
from high-success episodes.

```json
{
  "id": "proc_a1b2c3d4",
  "type": "procedural",
  "task": "book flights on kayak.com",
  "steps": [
    {"step": 1, "tool": "edge", "action": "new_tab", "args": {"url": "..."}},
    {"step": 2, "tool": "edge", "action": "dismiss"},
    {"step": 3, "tool": "edge", "action": "fill", "args": {"text": "#3", "value": "NYC"}}
  ],
  "outcome": "success",
  "used_count": 5,
  "metadata": {
    "source": "auto_promote",      // or "training"
    "avg_score": 8.5,
    "refinement_count": 2,
    "tips": ["Always dismiss popups after navigating to kayak"]
  }
}
```

**Retrieval boost:** 1.5x base, +1.2x if outcome=success

### 2. Episodic Memory (`memory/episodic/`)

Auto-recorded summaries of every task execution (≥2 steps).

```json
{
  "id": "ep_1709913600",
  "type": "episodic",
  "task": "search amazon for headphones",
  "outcome": "success",
  "success_score": 8.5,
  "what_worked": ["edge:new_tab", "edge:search", "edge:click"],
  "what_failed": ["edge:click (not found)"],
  "tools_used": ["edge"],
  "duration": 45.2,
  "steps": 12
}
```

**Success score** (0-10) auto-computed from:
- Base: success=7.0, paused=4.0, failed=2.0
- Efficiency bonus: <10 steps +1.5, <20 steps +0.5
- Failure penalty: -0.3 per failure (max -3.0)
- Stall penalty: -0.5 per stall recovery (max -2.0)
- Browser reconnect penalty: -1.0 each (max -2.0)
- Tool diversity bonus: 3+ tools +0.5

**Retrieval boost:** ≥8.0 score → 1.4x, ≥6.0 → 1.1x, <3.0 → 0.7x

### 3. Semantic Memory (`memory/semantic/`)

Facts, patterns, preferences, and debugging knowledge.

```json
{
  "id": "sem_e5f6g7h8",
  "type": "semantic",
  "content": "For tasks like 'search amazon': tools edge work well. Successful: new_tab, search, click",
  "source": "auto_pattern",
  "category": "pattern"
}
```

**Sources:** `user` (manual teach), `auto_pattern` (extracted), `stall_recovery`, `failure_pattern`

**Retrieval boost:** auto_pattern → 1.3x

---

## LEARNINGS.md — Shared Brain File

The core innovation. A persistent markdown file that the agent reads before every task
and writes to after every task. Located at `memory/LEARNINGS.md`.

### Format

```markdown
# Agent Learnings

Auto-maintained shared brain file. Read before work, written after work.

## Patterns
- [2026-03-08] When navigating sites with popups, always dismiss overlays first (from: "book flights on kayak")

## Mistakes to Avoid
- [2026-03-08] Don't click cookie banners by coordinate — use dismiss action (from: "search amazon")
- [2026-03-08] Stalls from edge:click (not found) — snapshot before clicking refs (from: "fill form on xyz.com")

## Optimizations
- [2026-03-08] Efficient path: new_tab → search → click → fill → submit (from: "sign up for service")

## Facts
- [2026-03-08] kayak.com requires dismissing 2 overlay layers before interaction

## User Preferences
- [2026-03-08] User prefers parallel swarm for multi-site research tasks
```

### Categories

| Category | When Written | Priority (prune order) |
|----------|-------------|----------------------|
| `pattern` | 3+ similar high-success episodes | High (kept longest) |
| `mistake` | Task with stalls or failure | High (kept longest) |
| `preference` | User teaches via `memory teach` | High |
| `optimization` | Very efficient success (<10 steps, no failures) | Medium |
| `fact` | Auto-extracted from failure patterns | Low (pruned first) |

### Deduplication

New entries are checked against existing ones. An entry is rejected if:
- Exact duplicate text exists
- >80% word overlap with existing entry

### Pruning

When entries exceed 200, lowest-priority oldest entries are removed first.
Manual: `learnings prune` command.

---

## Closed-Loop Learning Flow

```
                    ┌──────────────────────────┐
                    │     LEARNINGS.md          │
                    │  (persistent shared brain)│
                    └─────┬──────────┬──────────┘
                          │          │
                    READ  │          │  WRITE
                    before│          │  after
                    work  │          │  work
                          │          │
                    ┌─────▼──────────▼──────────┐
                    │   Agent Task Execution     │
                    │                            │
                    │  System prompt includes:   │
                    │  - Base prompt              │
                    │  - Recalled memories        │
                    │  - Relevant learnings       │
                    │                            │
                    │  After completion:          │
                    │  - Episode saved            │
                    │  - Learnings extracted      │
                    │  - Auto-promote checked     │
                    │  - Failure patterns checked │
                    └────────────────────────────┘
```

### Read Before Work (agent.py `run()`)

1. Store base system prompt on first call (`_base_system_prompt`)
2. BM25 recall top-3 memories relevant to current task
3. Load learnings relevant to current task (capped ~1500 tokens)
4. **Rebuild** system message = base + memories + learnings
   - This is **bounded** — replaces previous injection instead of appending

### Write After Work (agent.py `_finalize_memory()`)

After each task completes, the agent auto-extracts learnings:

| Condition | Category | Example Learning |
|-----------|----------|------------------|
| Success + stalls | `mistake` | "Stalls from edge:click (not found) — snapshot before clicking refs" |
| Success + efficient (<10 steps, no failures) | `optimization` | "Efficient path: new_tab → search → click → fill" |
| Failure | `mistake` | "Task failed: edge:click (404), edge:fill (not found)" |

Then triggers:
- **Auto-promote:** If episode score ≥8.0 and 2+ similar successes → create procedure
- **Failure pattern detection:** If score <3.0 and 2+ similar failures → save semantic memory

---

## Auto-Promote Pipeline

```
Episode (score ≥ 8.0)
         │
         ▼
   BM25 search for similar episodes
         │
         ▼
   2+ high-success similar episodes found?
         │
    ┌────┴────┐
    │ YES     │ NO → stop
    ▼         │
Procedure     │
exists?       │
    │         │
  ┌─┴──┐     │
  YES  NO    │
  │    │     │
  ▼    ▼     │
Refine Create│
proc   proc  │
```

### Refinement

When a procedure exists but a new episode scores higher:
- Replace procedure steps with the better approach
- Increment `refinement_count`
- Update `avg_score`

---

## Failure Pattern Detection

```
Failed Episode (score < 3.0)
         │
         ▼
   BM25 search for similar failures
         │
         ▼
   2+ similar failures found?
         │
    ┌────┴────┐
    │ YES     │ NO → stop
    ▼         │
Extract common│
failure causes│
         │
         ▼
Save as semantic memory (category: debugging)
Add to LEARNINGS.md (category: mistake)
```

---

## Self-Review (Statistics Only)

Pure math computation, no LLM calls. Triggered via `review` command.

**Metrics computed:**
- Success rate (%) over last N episodes
- Score distribution (avg, min, max)
- Average step count (efficiency)
- Most common tools used
- Most common failure categories
- Procedure usage stats (most/least used)
- Learnings count by category

---

## Training Mode (-train)

Manual recording mode for creating procedures from agent execution.

### Usage

```
> -train book a flight on kayak from SF to NYC
  training mode: recording procedure
  task: book a flight on kayak from SF to NYC
```

### Step Quality Tracking

Each step is assessed as:
- **critical**: Made real progress (navigated, filled, clicked successfully)
- **failure**: Hit an error (NOT_FOUND, 404, CAPTCHA)
- **neutral**: Neither progress nor failure

### Compression

For procedures with >25 steps:
- Keep all `critical` steps
- Keep first 3 and last 3 steps
- Keep up to 3 `failure` steps (for learning)
- Discard remaining `neutral` steps

### Tip Extraction

Automatically extracts tips from failures:
- Stale refs → "Snapshot first"
- 404 errors → "URL returned 404"
- Browser crashes → "Edge server auto-reconnects now"

---

## BM25 Retrieval Engine

All memory retrieval uses BM25 scoring with 7 boost multipliers.

### BM25 Parameters
- k1 = 1.2 (term frequency saturation)
- b = 0.75 (document length normalization)
- avg_dl = 15.0 (approximate average keyword count)

### Boost Multipliers

| Boost | Factor | Condition |
|-------|--------|-----------|
| Procedural | 1.5x | Memory type is procedural |
| Successful proc | 1.2x | Procedure outcome = success |
| High-success episode | 1.4x | Episode score ≥ 8.0 |
| Good episode | 1.1x | Episode score ≥ 6.0 |
| Failed episode | 0.7x | Episode score < 3.0 |
| Auto-pattern | 1.3x | Semantic memory from auto_pattern |
| Recency (<7d) | 1.2x | Created within last 7 days |
| Recency (<30d) | 1.1x | Created within last 30 days |
| Usage | 1.0 + 0.1×uses | Frequently used (max +0.5) |

### Keyword Extraction

- Preserves URLs and domains as tokens
- Filters stop words (expanded set including tool-specific terms)
- Deduplicates preserving order

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `memory` | Show memory statistics |
| `memory list [type]` | List stored memories |
| `memory search <query>` | BM25 search across all memories |
| `memory delete <id>` | Delete a specific memory |
| `memory teach <fact>` | Manually add a fact/preference |
| `memory prune` | Remove old episodes |
| `learnings` | Display LEARNINGS.md contents |
| `learnings search <query>` | Search learnings by keyword |
| `learnings prune` | Remove low-priority old entries |
| `review` | Show agent performance statistics |
| `auto-train on` | Enable write-after-work (default) |
| `auto-train off` | Disable automatic learnings extraction |
| `-train <task>` | Record procedure for task |

---

## File Reference

| File | Component | Description |
|------|-----------|-------------|
| `memory.py` | `MemoryStore` | Three-tier memory with BM25 retrieval |
| `memory.py` | `MemoryStore.auto_promote()` | Episode → procedure promotion |
| `memory.py` | `MemoryStore.detect_failure_patterns()` | Recurring failure detection |
| `memory.py` | `MemoryStore.refine_procedure()` | Procedure improvement on better runs |
| `memory.py` | `MemoryStore.self_review()` | Performance statistics (no LLM) |
| `memory.py` | `Learnings` | LEARNINGS.md shared brain file |
| `memory.py` | `Learnings.to_prompt()` | Format learnings for system prompt |
| `memory.py` | `Learnings.add()` | Add entry with dedup check |
| `memory.py` | `TrainingRecorder` | -train mode step recording |
| `memory.py` | `EpisodeRecorder` | Auto episode recording |
| `agent.py` | `SwarmAgentLoop.run()` | Read-before-work injection |
| `agent.py` | `SwarmAgentLoop._finalize_memory()` | Write-after-work extraction |
| `agent.py` | `SwarmAgentLoop._base_system_prompt` | Bounded injection base |
| `agent.py` | `SwarmAgentLoop.auto_learn` | Toggle for write-after-work |
| `ai.py` | CLI commands | learnings, review, auto-train |

---

## Storage Layout

```
memory/
├── LEARNINGS.md              ← shared brain file (human-readable)
├── learnings_index.json      ← structured entries for LEARNINGS.md
├── index.json                ← inverted keyword index + IDF values
├── procedural/
│   ├── proc_a1b2c3d4.json   ← recorded/auto-promoted procedures
│   └── ...
├── episodic/
│   ├── ep_1709913600.json   ← auto-recorded task episodes
│   └── ...
└── semantic/
    ├── sem_e5f6g7h8.json    ← facts, patterns, debugging knowledge
    └── ...
```

---

## Performance Considerations

| Factor | Impact | Mitigation |
|--------|--------|------------|
| BM25 retrieval | ~5-20ms per query | Inverted index, IDF cached |
| Learnings load | ~1-5ms | JSON index, not markdown parse |
| System prompt rebuild | Negligible | String concatenation only |
| Episode save | ~2-5ms | Single JSON write |
| Auto-promote check | ~10-30ms | BM25 search + file writes |
| Failure detection | ~10-20ms | BM25 search + Counter |
| Self-review | ~20-50ms | File reads + Counter |
| Storage | ~1-10KB per memory | JSON files, prune at 500 episodes |

**RAM overhead:** <10MB for typical usage (500 episodes, 50 procedures, 200 learnings)

---

## Limitations

1. **No embeddings:** Uses keyword-based BM25, not semantic embeddings. Works well
   for tool/URL/action matching but may miss conceptual similarity.

2. **No LLM in learning loop:** Learnings extraction uses heuristics (step counts,
   failure indicators), not LLM analysis. Fast and deterministic but less nuanced.

3. **Dedup is approximate:** 80% word overlap threshold may miss paraphrased duplicates
   or catch unrelated entries with common words.

4. **Auto-promote requires patterns:** Only promotes when 2+ similar high-success
   episodes exist. Single exceptional performances don't auto-promote.

5. **Bounded injection caps:** System prompt injection capped at ~1500 tokens for
   learnings + ~2000 tokens for recalled memories. Very large memory stores may
   lose relevant context beyond these limits.
