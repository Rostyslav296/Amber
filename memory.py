#!/usr/bin/env python3
"""
memory.py — Agentic Memory & Training System for Amber

Three-tier persistent memory:
  - Procedural: Step-by-step task procedures (from -train mode)
  - Episodic:   Auto-recorded task summaries (outcomes & patterns)
  - Semantic:   Facts, preferences, site-specific knowledge

Retrieval: TF-IDF keyword matching (no embeddings — fits 16GB M4)
Storage: JSON files in memory/ directory

Training mode: Agent records every step during execution → saves as
reusable procedure. Next time a similar task is requested, the procedure
is recalled and injected into the prompt for faster, more reliable execution.

Inspired by:
  - Anthropic's tool-use memory (CLAUDE.md style persistent context)
  - Google Gemini's structured memory (facts + preferences + procedures)
  - OpenAI's memory system (key-value pairs with retrieval)
  - MemGPT/Letta tiered memory (core, recall, archival)
"""

import os
import json
import hashlib
import time
import re
import math
import datetime
from typing import Dict, List, Optional, Tuple
from collections import Counter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_DIR = os.path.join(BASE_DIR, "memory")

# ═══════════════════════════════════════════════════════════════════
#  KEYWORD EXTRACTION & TF-IDF
# ═══════════════════════════════════════════════════════════════════

STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "to", "of",
    "in", "for", "on", "with", "at", "by", "from", "as", "into", "about",
    "it", "its", "this", "that", "these", "those", "i", "me", "my", "we",
    "our", "you", "your", "he", "she", "they", "them", "and", "or", "but",
    "if", "then", "so", "not", "no", "just", "also", "very", "too",
    "up", "out", "all", "each", "some", "any", "how", "what", "which",
    "who", "when", "where", "why", "go", "get", "make", "use", "try",
    "using", "used", "one", "two", "first", "new", "now", "way", "like",
    "tool", "args", "action", "value", "text", "result", "status",
})


def extract_keywords(text: str) -> List[str]:
    """Extract meaningful keywords from text. Preserves URLs/domains."""
    if not text:
        return []

    text_lower = text.lower()

    # Extract URLs and domains as single tokens
    urls = re.findall(r'https?://[^\s,\'"]+', text_lower)
    domains = re.findall(r'\b\w+\.(?:com|org|net|io|gov|edu)\b', text_lower)

    # Remove URLs from text before word extraction
    clean = re.sub(r'https?://[^\s,\'"]+', ' ', text_lower)

    # Split on non-alphanumeric
    words = re.findall(r'[a-z0-9]+', clean)

    # Filter stop words, keep length >= 2
    keywords = [w for w in words if w not in STOP_WORDS and len(w) >= 2]

    # Add domains/URLs (cleaned)
    for url in urls:
        # Extract domain from URL
        domain_match = re.search(r'://(?:www\.)?([^/]+)', url)
        if domain_match:
            keywords.append(domain_match.group(1))
    for domain in domains:
        if domain not in keywords:
            keywords.append(domain)

    # Deduplicate preserving order
    seen = set()
    result = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)

    return result


def _compute_score(query_kw: List[str], doc_kw: List[str],
                   idf: Dict[str, float]) -> float:
    """BM25-inspired similarity score between query and document keywords.

    v4.1: Upgraded from binary TF-IDF to BM25 with k1=1.2, b=0.75.
    Accounts for term frequency and document length normalization.
    """
    if not query_kw or not doc_kw:
        return 0.0

    k1 = 1.2
    b = 0.75
    avg_dl = 15.0  # approximate average document keyword count

    doc_counter = Counter(doc_kw)
    dl = len(doc_kw)

    score = 0.0
    for kw in query_kw:
        if kw in doc_counter:
            tf = doc_counter[kw]
            idf_val = idf.get(kw, 1.0)
            # BM25 term score
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * (dl / avg_dl))
            score += idf_val * (numerator / denominator)

    # Normalize by query length
    return score / len(query_kw) if query_kw else 0.0


# ═══════════════════════════════════════════════════════════════════
#  MEMORY STORE
# ═══════════════════════════════════════════════════════════════════

class MemoryStore:
    """Three-tier memory store with keyword-based retrieval.

    Procedural: Recorded step sequences from training mode
    Episodic:   Auto-recorded task outcomes
    Semantic:   Facts, preferences, learned knowledge
    """

    def __init__(self, memory_dir: str = MEMORY_DIR):
        self.memory_dir = memory_dir
        self.proc_dir = os.path.join(memory_dir, "procedural")
        self.ep_dir = os.path.join(memory_dir, "episodic")
        self.sem_dir = os.path.join(memory_dir, "semantic")
        self.index_path = os.path.join(memory_dir, "index.json")
        self._ensure_dirs()
        self._index = self._load_index()

    def _ensure_dirs(self):
        for d in (self.memory_dir, self.proc_dir, self.ep_dir, self.sem_dir):
            os.makedirs(d, exist_ok=True)

    def _load_index(self) -> Dict:
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"keywords": {}, "idf": {}, "doc_count": 0}

    def _save_index(self):
        try:
            with open(self.index_path, "w") as f:
                json.dump(self._index, f, indent=1)
        except Exception:
            pass

    def _update_index(self, mem_id: str, keywords: List[str]):
        """Add a document's keywords to the inverted index."""
        idx = self._index.setdefault("keywords", {})
        for kw in keywords:
            if kw not in idx:
                idx[kw] = []
            if mem_id not in idx[kw]:
                idx[kw].append(mem_id)

        # Update doc count and IDF
        self._index["doc_count"] = self._index.get("doc_count", 0) + 1
        self._recompute_idf()
        self._save_index()

    def _remove_from_index(self, mem_id: str):
        """Remove a document from the inverted index."""
        idx = self._index.get("keywords", {})
        empty_keys = []
        for kw, ids in idx.items():
            if mem_id in ids:
                ids.remove(mem_id)
                if not ids:
                    empty_keys.append(kw)
        for kw in empty_keys:
            del idx[kw]
        self._index["doc_count"] = max(0, self._index.get("doc_count", 1) - 1)
        self._recompute_idf()
        self._save_index()

    def _recompute_idf(self):
        """Recompute IDF values for all keywords."""
        idx = self._index.get("keywords", {})
        doc_count = max(self._index.get("doc_count", 1), 1)
        idf = {}
        for kw, ids in idx.items():
            df = len(ids)
            idf[kw] = math.log(doc_count / (df + 1)) + 1.0
        self._index["idf"] = idf

    def _rebuild_index(self):
        """Full rebuild from all memory files."""
        self._index = {"keywords": {}, "idf": {}, "doc_count": 0}
        for d, prefix in [(self.proc_dir, "proc_"),
                          (self.ep_dir, "ep_"),
                          (self.sem_dir, "sem_")]:
            if not os.path.exists(d):
                continue
            for fname in os.listdir(d):
                if not fname.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(d, fname), "r") as f:
                        doc = json.load(f)
                    mem_id = doc.get("id", fname.replace(".json", ""))
                    keywords = doc.get("keywords", [])
                    for kw in keywords:
                        idx = self._index.setdefault("keywords", {})
                        if kw not in idx:
                            idx[kw] = []
                        if mem_id not in idx[kw]:
                            idx[kw].append(mem_id)
                    self._index["doc_count"] += 1
                except Exception:
                    pass
        self._recompute_idf()
        self._save_index()

    # ── PROCEDURAL MEMORY ───────────────────────────────────────

    def save_procedure(self, task_description: str, steps: List[Dict],
                       outcome: str = "success",
                       metadata: Optional[Dict] = None) -> str:
        """Save a recorded procedure from training mode."""
        task_hash = hashlib.md5(task_description.encode()).hexdigest()[:8]
        proc_id = f"proc_{task_hash}"

        keywords = extract_keywords(task_description)
        # Also extract keywords from tool names and key args
        for step in steps[:20]:
            tool = step.get("tool", "")
            if tool:
                keywords.append(tool)
            args = step.get("args", {})
            url = args.get("url", "")
            if url:
                keywords.extend(extract_keywords(url))

        keywords = list(dict.fromkeys(keywords))  # dedupe

        doc = {
            "id": proc_id,
            "type": "procedural",
            "task": task_description,
            "keywords": keywords,
            "steps": steps,
            "outcome": outcome,
            "created": time.time(),
            "used_count": 0,
            "last_used": None,
            "metadata": metadata or {},
        }

        path = os.path.join(self.proc_dir, f"{proc_id}.json")

        # If procedure already exists, merge (update steps, increment version)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    existing = json.load(f)
                doc["used_count"] = existing.get("used_count", 0)
                doc["metadata"]["version"] = existing.get("metadata", {}).get("version", 1) + 1
                doc["metadata"]["previous_outcome"] = existing.get("outcome", "unknown")
            except Exception:
                pass

        with open(path, "w") as f:
            json.dump(doc, f, indent=2)

        self._update_index(proc_id, keywords)
        return proc_id

    def get_procedure(self, proc_id: str) -> Optional[Dict]:
        path = os.path.join(self.proc_dir, f"{proc_id}.json")
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return None

    def record_procedure_use(self, proc_id: str):
        """Increment usage counter and update last_used."""
        doc = self.get_procedure(proc_id)
        if doc:
            doc["used_count"] = doc.get("used_count", 0) + 1
            doc["last_used"] = time.time()
            path = os.path.join(self.proc_dir, f"{proc_id}.json")
            with open(path, "w") as f:
                json.dump(doc, f, indent=2)

    # ── EPISODIC MEMORY ─────────────────────────────────────────

    def save_episode(self, task_description: str, outcome: str,
                     what_worked: List[str], what_failed: List[str],
                     tools_used: List[str], duration_seconds: float,
                     steps_count: int,
                     success_score: float = 0.0) -> str:
        """Auto-save a task episode after agent run.

        v4.1: Added success_score (0-10) for ranking episode quality.
        """
        ts = int(time.time())
        ep_id = f"ep_{ts}"

        keywords = extract_keywords(task_description)
        keywords.extend(tools_used)
        keywords = list(dict.fromkeys(keywords))

        # Auto-compute success score if not provided
        if success_score == 0.0:
            if outcome == "success":
                success_score = 7.0
                # Bonus for efficiency
                if steps_count < 10:
                    success_score += 1.5
                elif steps_count < 20:
                    success_score += 0.5
                # Penalty for failures
                success_score -= min(len(what_failed) * 0.5, 3.0)
            elif outcome == "paused":
                success_score = 4.0
            else:
                success_score = 2.0

        doc = {
            "id": ep_id,
            "type": "episodic",
            "task": task_description,
            "keywords": keywords,
            "outcome": outcome,
            "success_score": round(max(0, min(10, success_score)), 1),
            "what_worked": what_worked,
            "what_failed": what_failed,
            "tools_used": tools_used,
            "duration": round(duration_seconds, 1),
            "steps": steps_count,
            "created": time.time(),
        }

        path = os.path.join(self.ep_dir, f"{ep_id}.json")
        with open(path, "w") as f:
            json.dump(doc, f, indent=2)

        self._update_index(ep_id, keywords)

        # Auto-extract patterns from high-success episodes
        if success_score >= 8.0 and what_worked:
            self._extract_patterns(task_description, what_worked, tools_used)

        return ep_id

    def _extract_patterns(self, task: str, what_worked: List[str],
                          tools_used: List[str]):
        """Auto-extract successful patterns and save as semantic memory."""
        if not what_worked:
            return
        # Only save if we see repeated success patterns (3+ similar successes)
        task_kw = extract_keywords(task)
        similar = self.recall(task, memory_types=["episodic"], top_k=5, min_score=0.3)
        high_success = [e for e in similar if e.get("success_score", 0) >= 7.0]
        if len(high_success) >= 2:
            # Extract common tools/actions from successful episodes
            common_tools = set(tools_used)
            for ep in high_success:
                common_tools &= set(ep.get("tools_used", []))
            if common_tools:
                pattern = f"For tasks like '{task[:60]}': tools {', '.join(common_tools)} work well. " \
                          f"Successful actions: {', '.join(what_worked[:5])}"
                self.save_fact(pattern, source="auto_pattern", category="pattern")

    # ── SEMANTIC MEMORY ─────────────────────────────────────────

    def save_fact(self, content: str, source: str = "user",
                  category: str = "general") -> str:
        """Store a fact, preference, or piece of knowledge."""
        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        sem_id = f"sem_{content_hash}"

        keywords = extract_keywords(content)

        doc = {
            "id": sem_id,
            "type": "semantic",
            "content": content,
            "keywords": keywords,
            "source": source,
            "category": category,
            "created": time.time(),
            "used_count": 0,
        }

        path = os.path.join(self.sem_dir, f"{sem_id}.json")
        with open(path, "w") as f:
            json.dump(doc, f, indent=2)

        self._update_index(sem_id, keywords)
        return sem_id

    # ── RETRIEVAL ENGINE ────────────────────────────────────────

    def recall(self, query: str, memory_types: Optional[List[str]] = None,
               top_k: int = 3, min_score: float = 0.1) -> List[Dict]:
        """Retrieve relevant memories using TF-IDF keyword matching."""
        query_kw = extract_keywords(query)
        if not query_kw:
            return []

        # Rebuild index if empty
        if not self._index.get("keywords"):
            self._rebuild_index()

        idx = self._index.get("keywords", {})
        idf = self._index.get("idf", {})

        # Collect candidate IDs from index
        candidates: Dict[str, float] = {}
        for kw in query_kw:
            if kw in idx:
                for mem_id in idx[kw]:
                    # Filter by type if specified
                    if memory_types:
                        mem_type = mem_id.split("_")[0]
                        type_map = {"proc": "procedural", "ep": "episodic", "sem": "semantic"}
                        if type_map.get(mem_type) not in memory_types:
                            continue
                    candidates[mem_id] = candidates.get(mem_id, 0) + idf.get(kw, 1.0)

        if not candidates:
            return []

        # Load top candidates and compute full scores
        scored = []
        for mem_id, raw_score in sorted(candidates.items(), key=lambda x: -x[1])[:top_k * 3]:
            doc = self._load_memory(mem_id)
            if not doc:
                continue

            doc_kw = doc.get("keywords", [])
            score = _compute_score(query_kw, doc_kw, idf)

            # Boost procedural memories (most actionable)
            if doc.get("type") == "procedural":
                score *= 1.5
                # Extra boost for successful procedures
                if doc.get("outcome") == "success":
                    score *= 1.2

            # Boost high-success episodes (v4.1)
            if doc.get("type") == "episodic":
                ss = doc.get("success_score", 5.0)
                if ss >= 8.0:
                    score *= 1.4  # highly successful episodes are very relevant
                elif ss >= 6.0:
                    score *= 1.1
                elif ss < 3.0:
                    score *= 0.7  # de-rank failed episodes

            # Boost auto-patterns (v4.1)
            if doc.get("type") == "semantic" and doc.get("source") == "auto_pattern":
                score *= 1.3

            # Recency boost (memories from last 7 days get 1.2x)
            age_days = (time.time() - doc.get("created", 0)) / 86400
            if age_days < 7:
                score *= 1.2
            elif age_days < 30:
                score *= 1.1

            # Usage boost (frequently used = proven useful)
            used = doc.get("used_count", 0)
            if used > 0:
                score *= 1.0 + min(used * 0.1, 0.5)

            doc["relevance_score"] = round(score, 3)
            scored.append(doc)

        # Sort by score, filter, return top_k
        scored.sort(key=lambda x: -x["relevance_score"])
        return [m for m in scored if m["relevance_score"] >= min_score][:top_k]

    def recall_procedures(self, query: str, top_k: int = 2) -> List[Dict]:
        return self.recall(query, memory_types=["procedural"], top_k=top_k)

    def recall_episodes(self, query: str, top_k: int = 3) -> List[Dict]:
        return self.recall(query, memory_types=["episodic"], top_k=top_k)

    def _load_memory(self, mem_id: str) -> Optional[Dict]:
        """Load a memory document by ID."""
        prefix = mem_id.split("_")[0]
        dir_map = {"proc": self.proc_dir, "ep": self.ep_dir, "sem": self.sem_dir}
        d = dir_map.get(prefix)
        if not d:
            return None
        path = os.path.join(d, f"{mem_id}.json")
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    # ── PROMPT FORMATTING ───────────────────────────────────────

    def format_procedure_for_prompt(self, proc: Dict) -> str:
        """Format a procedure for injection into the system prompt."""
        task = proc.get("task", "unknown task")
        outcome = proc.get("outcome", "unknown")
        used = proc.get("used_count", 0)
        steps = proc.get("steps", [])

        lines = [f'[PROCEDURE] "{task}" ({outcome}, used {used}x):']

        for i, step in enumerate(steps[:15]):  # cap at 15 steps
            tool = step.get("tool", "?")
            args = step.get("args", {})
            action = args.get("action", step.get("action", ""))
            # Build concise step summary
            arg_summary = ""
            if "url" in args:
                arg_summary = args["url"][:80]
            elif "text" in args:
                arg_summary = args["text"][:40]
            elif "value" in args:
                arg_summary = args["value"][:40]
            elif "command" in args:
                arg_summary = args["command"][:60]

            result_brief = step.get("result_summary", "")[:80]
            step_line = f"  {i+1}. [{tool}] {action}"
            if arg_summary:
                step_line += f" {arg_summary}"
            if result_brief:
                step_line += f" → {result_brief}"
            lines.append(step_line)

        if len(steps) > 15:
            lines.append(f"  ... ({len(steps) - 15} more steps)")

        # Add tips from metadata
        tips = proc.get("metadata", {}).get("tips", [])
        for tip in tips[:3]:
            lines.append(f"  TIP: {tip}")

        return "\n".join(lines)

    def format_episode_for_prompt(self, ep: Dict) -> str:
        """Format an episode summary for prompt injection."""
        task = ep.get("task", "unknown")[:60]
        outcome = ep.get("outcome", "?")
        steps = ep.get("steps", 0)
        worked = ep.get("what_worked", [])[:3]
        failed = ep.get("what_failed", [])[:3]

        age_days = (time.time() - ep.get("created", 0)) / 86400
        age_str = f"{int(age_days)}d ago" if age_days >= 1 else "today"

        parts = [f'[EPISODE] {age_str}: "{task}" — {outcome} ({steps} steps)']
        if worked:
            parts.append(f"  Worked: {', '.join(worked)}")
        if failed:
            parts.append(f"  Failed: {', '.join(failed)}")
        return "\n".join(parts)

    def format_recall_for_prompt(self, memories: List[Dict]) -> str:
        """Format all recalled memories into a single prompt block."""
        if not memories:
            return ""

        parts = ["=== AGENT MEMORY (recalled for this task) ==="]

        for mem in memories:
            mem_type = mem.get("type", "unknown")
            if mem_type == "procedural":
                parts.append(self.format_procedure_for_prompt(mem))
            elif mem_type == "episodic":
                parts.append(self.format_episode_for_prompt(mem))
            elif mem_type == "semantic":
                content = mem.get("content", "")
                parts.append(f"[FACT] {content}")
            parts.append("")  # blank line between memories

        parts.append("=== END MEMORY ===")
        return "\n".join(parts)

    # ── MANAGEMENT ──────────────────────────────────────────────

    def list_memories(self, memory_type: Optional[str] = None,
                      limit: int = 20) -> List[Dict]:
        """List stored memories."""
        results = []
        dirs = []
        if memory_type in (None, "procedural"):
            dirs.append(self.proc_dir)
        if memory_type in (None, "episodic"):
            dirs.append(self.ep_dir)
        if memory_type in (None, "semantic"):
            dirs.append(self.sem_dir)

        for d in dirs:
            if not os.path.exists(d):
                continue
            for fname in sorted(os.listdir(d), reverse=True):
                if not fname.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(d, fname), "r") as f:
                        doc = json.load(f)
                    results.append(doc)
                except Exception:
                    pass
                if len(results) >= limit:
                    break

        # Sort by creation time (newest first)
        results.sort(key=lambda x: x.get("created", 0), reverse=True)
        return results[:limit]

    def delete_memory(self, mem_id: str) -> bool:
        """Delete a memory by ID."""
        prefix = mem_id.split("_")[0]
        dir_map = {"proc": self.proc_dir, "ep": self.ep_dir, "sem": self.sem_dir}
        d = dir_map.get(prefix)
        if not d:
            return False
        path = os.path.join(d, f"{mem_id}.json")
        if os.path.exists(path):
            os.remove(path)
            self._remove_from_index(mem_id)
            return True
        return False

    def stats(self) -> Dict:
        """Return memory statistics."""
        counts = {}
        for name, d in [("procedural", self.proc_dir),
                         ("episodic", self.ep_dir),
                         ("semantic", self.sem_dir)]:
            if os.path.exists(d):
                counts[name] = len([f for f in os.listdir(d) if f.endswith(".json")])
            else:
                counts[name] = 0
        counts["total"] = sum(counts.values())
        counts["index_keywords"] = len(self._index.get("keywords", {}))
        return counts

    def prune(self, max_age_days: int = 90, max_episodes: int = 500):
        """Prune old episodic memories."""
        if not os.path.exists(self.ep_dir):
            return

        episodes = []
        for fname in os.listdir(self.ep_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.ep_dir, fname)
            try:
                with open(path, "r") as f:
                    doc = json.load(f)
                episodes.append((path, doc))
            except Exception:
                pass

        # Sort by age (oldest first)
        episodes.sort(key=lambda x: x[1].get("created", 0))

        cutoff = time.time() - (max_age_days * 86400)
        removed = 0

        # Remove episodes older than max_age_days
        for path, doc in episodes:
            if doc.get("created", 0) < cutoff:
                mem_id = doc.get("id", "")
                os.remove(path)
                if mem_id:
                    self._remove_from_index(mem_id)
                removed += 1

        # If still over max_episodes, remove oldest
        remaining = len(episodes) - removed
        if remaining > max_episodes:
            for path, doc in episodes[:remaining - max_episodes]:
                if os.path.exists(path):
                    mem_id = doc.get("id", "")
                    os.remove(path)
                    if mem_id:
                        self._remove_from_index(mem_id)
                    removed += 1

        return removed

    # ── AUTO-PROMOTE (episodes → procedures) ─────────────────

    def auto_promote(self, episode_id: str):
        """Auto-promote high-success episodes to procedures.

        When an episode scores >=8.0 and 2+ similar high-success episodes exist,
        converts the pattern into a reusable procedure.
        """
        ep = self._load_memory(episode_id)
        if not ep or ep.get("success_score", 0) < 8.0:
            return None

        task = ep.get("task", "")
        if not task:
            return None

        # Find similar high-success episodes
        similar = self.recall(task, memory_types=["episodic"], top_k=5, min_score=0.3)
        high_success = [e for e in similar
                        if e.get("success_score", 0) >= 7.0
                        and e.get("id") != episode_id]

        if len(high_success) < 1:
            return None

        # Check if procedure already exists for this task pattern
        existing_procs = self.recall(task, memory_types=["procedural"], top_k=1, min_score=0.5)
        if existing_procs:
            # Procedure exists — try to refine it instead
            return self.refine_procedure(existing_procs[0]["id"], ep)

        # Build procedure from the successful episode's actions
        steps = []
        for i, action in enumerate(ep.get("what_worked", [])[:15]):
            parts = action.split(":", 1)
            tool = parts[0] if parts else "edge"
            act = parts[1] if len(parts) > 1 else action
            steps.append({
                "step": i + 1,
                "tool": tool,
                "action": act,
                "args": {},
                "quality": "critical",
            })

        if not steps:
            return None

        proc_id = self.save_procedure(
            task_description=task,
            steps=steps,
            outcome="success",
            metadata={
                "source": "auto_promote",
                "source_episodes": [episode_id] + [e["id"] for e in high_success[:2]],
                "avg_score": round(
                    (ep["success_score"] + sum(e.get("success_score", 0) for e in high_success[:2]))
                    / (1 + len(high_success[:2])), 1
                ),
                "version": 1,
            }
        )

        # Mark source episodes as promoted
        for e in [ep] + high_success[:2]:
            eid = e.get("id", "")
            doc = self._load_memory(eid)
            if doc:
                doc["promoted_to"] = proc_id
                path = os.path.join(self.ep_dir, f"{eid}.json")
                try:
                    with open(path, "w") as f:
                        json.dump(doc, f, indent=2)
                except Exception:
                    pass

        return proc_id

    def refine_procedure(self, proc_id: str, new_episode: Dict) -> Optional[str]:
        """Refine an existing procedure if a new episode scores higher.

        Updates the procedure's steps when a better execution is found.
        """
        proc = self.get_procedure(proc_id)
        if not proc:
            return None

        # Only refine if new episode is better
        old_score = proc.get("metadata", {}).get("avg_score", 5.0)
        new_score = new_episode.get("success_score", 0)
        if new_score <= old_score:
            return None

        # Build improved steps from new episode
        new_steps = []
        for i, action in enumerate(new_episode.get("what_worked", [])[:15]):
            parts = action.split(":", 1)
            tool = parts[0] if parts else "edge"
            act = parts[1] if len(parts) > 1 else action
            new_steps.append({
                "step": i + 1,
                "tool": tool,
                "action": act,
                "args": {},
                "quality": "critical",
            })

        if not new_steps:
            return None

        # Update procedure
        refinement_count = proc.get("metadata", {}).get("refinement_count", 0) + 1
        proc["steps"] = new_steps
        proc["metadata"]["refinement_count"] = refinement_count
        proc["metadata"]["avg_score"] = round(new_score, 1)
        proc["metadata"]["last_refined"] = time.time()
        proc["outcome"] = "success"

        path = os.path.join(self.proc_dir, f"{proc_id}.json")
        try:
            with open(path, "w") as f:
                json.dump(proc, f, indent=2)
        except Exception:
            pass

        return proc_id

    # ── FAILURE PATTERN DETECTION ────────────────────────────

    def detect_failure_patterns(self, episode_id: str, learnings=None):
        """Detect recurring failure patterns across episodes.

        If 2+ similar failures exist, saves a semantic memory and a learning.
        """
        ep = self._load_memory(episode_id)
        if not ep:
            return
        if ep.get("outcome") == "success" or ep.get("success_score", 5) >= 3.0:
            return

        task = ep.get("task", "")
        failures = ep.get("what_failed", [])
        if not task or not failures:
            return

        # Find similar past failures
        similar = self.recall(task, memory_types=["episodic"], top_k=5, min_score=0.2)
        past_failures = [e for e in similar
                         if e.get("success_score", 5) < 3.0
                         and e.get("id") != episode_id]

        if len(past_failures) < 1:
            return

        # Extract common failure causes
        all_fails = failures[:]
        for pf in past_failures:
            all_fails.extend(pf.get("what_failed", []))

        fail_counter = Counter(all_fails)
        common = fail_counter.most_common(3)
        if not common:
            return

        pattern_text = (
            f"Recurring failure for tasks like '{task[:60]}': "
            f"{', '.join(f[0] for f in common)}. "
            f"Seen {len(past_failures) + 1} times."
        )
        self.save_fact(pattern_text, source="failure_pattern", category="debugging")

        if learnings:
            try:
                learnings.add("mistake", pattern_text, source_task=task[:60])
            except Exception:
                pass

    # ── SELF-REVIEW (statistics, no LLM) ─────────────────────

    def self_review(self, last_n: int = 20) -> Dict:
        """Compute agent performance statistics. Pure math, no LLM calls."""
        episodes = self.list_memories("episodic", limit=last_n)

        if not episodes:
            return {"message": "No episodes recorded yet."}

        # Success rate
        outcomes = Counter(ep.get("outcome", "unknown") for ep in episodes)
        total = len(episodes)
        success_rate = outcomes.get("success", 0) / total * 100 if total else 0

        # Score statistics
        scores = [ep.get("success_score", 0) for ep in episodes]
        avg_score = sum(scores) / len(scores) if scores else 0
        max_score = max(scores) if scores else 0
        min_score = min(scores) if scores else 0

        # Step count / efficiency
        step_counts = [ep.get("steps", 0) for ep in episodes]
        avg_steps = sum(step_counts) / len(step_counts) if step_counts else 0

        # Most common tools
        all_tools = []
        for ep in episodes:
            all_tools.extend(ep.get("tools_used", []))
        tool_counts = Counter(all_tools).most_common(5)

        # Failure categories
        all_failures = []
        for ep in episodes:
            all_failures.extend(ep.get("what_failed", []))
        failure_counts = Counter(all_failures).most_common(5)

        # Procedure stats
        procedures = self.list_memories("procedural", limit=100)
        most_used = sorted(procedures, key=lambda p: p.get("used_count", 0), reverse=True)[:3]
        least_used = sorted(procedures, key=lambda p: p.get("used_count", 0))[:3]

        return {
            "episodes_reviewed": total,
            "success_rate": round(success_rate, 1),
            "outcomes": dict(outcomes),
            "avg_score": round(avg_score, 1),
            "max_score": round(max_score, 1),
            "min_score": round(min_score, 1),
            "avg_steps": round(avg_steps, 1),
            "top_tools": tool_counts,
            "top_failures": failure_counts,
            "procedures_total": len(procedures),
            "most_used_procedures": [
                {"task": p.get("task", "?")[:50], "used": p.get("used_count", 0)}
                for p in most_used
            ],
            "least_used_procedures": [
                {"task": p.get("task", "?")[:50], "used": p.get("used_count", 0)}
                for p in least_used
            ],
        }


# ═══════════════════════════════════════════════════════════════════
#  TRAINING RECORDER
# ═══════════════════════════════════════════════════════════════════

class TrainingRecorder:
    """Records agent steps during -train mode for procedure extraction.

    v4.1: Tracks step quality, identifies critical path steps,
    and compresses redundant/failed steps in saved procedures.
    """

    def __init__(self, memory_store: MemoryStore, task_description: str):
        self.store = memory_store
        self.task = task_description
        self.steps: List[Dict] = []
        self.start_time = time.time()
        self._active = True
        self._stall_count = 0
        self._critical_steps: List[int] = []  # indices of steps that made real progress

    def record_step(self, tool_name: str, tool_args: Dict,
                    result_summary: str, reasoning: str = ""):
        """Record a single agent step with quality assessment."""
        if not self._active:
            return

        # Assess step quality
        is_failure = any(ind in result_summary for ind in
                        ("NOT_FOUND", "STALE_REF", "404", "error", "CAPTCHA"))
        is_progress = any(ind in result_summary for ind in
                         ("success", "navigated", "filled", "clicked", "checked"))

        if is_failure:
            self._stall_count += 1
        elif is_progress:
            self._stall_count = 0
            self._critical_steps.append(len(self.steps))

        self.steps.append({
            "step": len(self.steps) + 1,
            "tool": tool_name,
            "args": tool_args,
            "action": tool_args.get("action", ""),
            "result_summary": result_summary[:300],
            "reasoning": reasoning[:500] if reasoning else "",
            "timestamp": time.time(),
            "quality": "critical" if is_progress else ("failure" if is_failure else "neutral"),
        })

    def finalize(self, outcome: str = "success") -> str:
        """Save the recorded procedure with quality-compressed steps."""
        self._active = False
        duration = time.time() - self.start_time

        # Extract tips from failures
        tips = []
        for step in self.steps:
            rs = step.get("result_summary", "")
            if "NOT_FOUND" in rs or "STALE_REF" in rs:
                tips.append(f"Step {step['step']} had stale ref — snapshot first")
            elif "404" in rs.lower():
                url = step.get("args", {}).get("url", "")
                if url:
                    tips.append(f"URL {url[:60]} returned 404")
            elif "browser has been closed" in rs.lower():
                tips.append("Browser died mid-task — edge server auto-reconnects now")

        # Compress: keep critical steps + first/last 3 non-critical
        if len(self.steps) > 25:
            compressed = []
            for i, step in enumerate(self.steps):
                if step.get("quality") == "critical" or i < 3 or i >= len(self.steps) - 3:
                    compressed.append(step)
                elif step.get("quality") == "failure" and len([s for s in compressed if s.get("quality") == "failure"]) < 3:
                    compressed.append(step)  # keep a few failures for learning
            save_steps = compressed
            tips.append(f"Compressed from {len(self.steps)} to {len(compressed)} steps")
        else:
            save_steps = self.steps

        proc_id = self.store.save_procedure(
            task_description=self.task,
            steps=save_steps,
            outcome=outcome,
            metadata={
                "duration": round(duration, 1),
                "steps_count": len(self.steps),
                "critical_steps": len(self._critical_steps),
                "stall_count": self._stall_count,
                "tips": tips[:8],
                "version": "4.1",
            }
        )
        return proc_id

    @property
    def is_active(self) -> bool:
        return self._active


# ═══════════════════════════════════════════════════════════════════
#  EPISODE RECORDER (automatic, lightweight)
# ═══════════════════════════════════════════════════════════════════

class EpisodeRecorder:
    """Lightweight auto-recorder for every agent execution.

    v4.1: Tracks success_score, stall recoveries, and tool effectiveness.
    """

    def __init__(self, memory_store: MemoryStore, task_description: str):
        self.store = memory_store
        self.task = task_description
        self.start_time = time.time()
        self.tools_used: set = set()
        self.successes: List[str] = []
        self.failures: List[str] = []
        self.step_count = 0
        self._stall_recoveries = 0
        self._browser_reconnects = 0

    def record_tool_call(self, tool_name: str, action: str,
                         result_str: str):
        """Record a tool call outcome at high level."""
        self.step_count += 1
        self.tools_used.add(tool_name)

        # Detect failures
        fail_indicators = ("NOT_FOUND", "STALE_REF", "CAPTCHA_DETECTED",
                           "404", "error", "CHECKBOX_NOT_FOUND")
        if any(ind in result_str for ind in fail_indicators):
            brief = f"{tool_name}:{action}"
            if "NOT_FOUND" in result_str:
                brief += " (not found)"
            elif "404" in result_str:
                brief += " (404)"
            elif "CAPTCHA" in result_str:
                brief += " (captcha)"
            elif "browser has been closed" in result_str.lower():
                brief += " (browser died)"
                self._browser_reconnects += 1
            self.failures.append(brief)
        else:
            # Record notable successes
            if action in ("new_tab", "browse", "fill", "click", "check",
                          "submit", "search", "run", "send"):
                self.successes.append(f"{tool_name}:{action}")

    def record_stall_recovery(self):
        """Called when stall detection fires and agent recovers."""
        self._stall_recoveries += 1

    def finalize(self, outcome: str = "success") -> Optional[str]:
        """Save episodic memory with auto-computed success score."""
        if self.step_count < 2:
            return None
        duration = time.time() - self.start_time

        # Compute success score (0-10)
        score = 5.0  # baseline
        if outcome == "success":
            score = 7.0
        elif outcome == "paused":
            score = 4.0
        else:
            score = 2.0

        # Efficiency bonus
        if self.step_count < 10 and outcome == "success":
            score += 1.5
        elif self.step_count < 20 and outcome == "success":
            score += 0.5

        # Penalty for failures and stalls
        score -= min(len(self.failures) * 0.3, 3.0)
        score -= min(self._stall_recoveries * 0.5, 2.0)
        score -= min(self._browser_reconnects * 1.0, 2.0)

        # Bonus for successful tool diversity (agent used multiple tools well)
        if len(self.tools_used) >= 3 and outcome == "success":
            score += 0.5

        return self.store.save_episode(
            task_description=self.task,
            outcome=outcome,
            what_worked=self.successes[-10:],
            what_failed=self.failures[-10:],
            tools_used=list(self.tools_used),
            duration_seconds=duration,
            steps_count=self.step_count,
            success_score=score,
        )


# ═══════════════════════════════════════════════════════════════════
#  LEARNINGS — Shared Brain File (Karpathy autoresearch-inspired)
# ═══════════════════════════════════════════════════════════════════

class Learnings:
    """Persistent LEARNINGS.md shared brain file for closed-loop learning.

    Inspired by Karpathy's autoresearch and the viral LEARNINGS.md pattern:
    - Agent reads before work (to_prompt)
    - Agent writes after work (add)
    - Entries are categorized, timestamped, and source-linked

    Categories: pattern, mistake, optimization, fact, preference
    """

    CATEGORIES = ("pattern", "mistake", "optimization", "fact", "preference")

    def __init__(self, memory_dir: str = MEMORY_DIR):
        self.memory_dir = memory_dir
        self.path = os.path.join(memory_dir, "LEARNINGS.md")
        self._index_path = os.path.join(memory_dir, "learnings_index.json")
        os.makedirs(memory_dir, exist_ok=True)

    def load(self) -> str:
        """Read the full LEARNINGS.md file."""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    return f.read()
            except Exception:
                pass
        return ""

    def _load_entries(self) -> List[Dict]:
        """Load structured entries from index."""
        if os.path.exists(self._index_path):
            try:
                with open(self._index_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_entries(self, entries: List[Dict]):
        """Save structured entries to index and rebuild LEARNINGS.md."""
        try:
            with open(self._index_path, "w") as f:
                json.dump(entries, f, indent=1)
        except Exception:
            pass
        self._rebuild_md(entries)

    def _rebuild_md(self, entries: List[Dict]):
        """Rebuild LEARNINGS.md from structured entries."""
        by_cat = {}
        for e in entries:
            cat = e.get("category", "pattern")
            by_cat.setdefault(cat, []).append(e)

        lines = ["# Agent Learnings", "",
                 "Auto-maintained shared brain file. Read before work, written after work.", ""]

        cat_titles = {
            "pattern": "Patterns",
            "mistake": "Mistakes to Avoid",
            "optimization": "Optimizations",
            "fact": "Facts",
            "preference": "User Preferences",
        }

        for cat in self.CATEGORIES:
            items = by_cat.get(cat, [])
            if not items:
                continue
            lines.append(f"## {cat_titles.get(cat, cat.title())}")
            for item in items:
                date = item.get("date", "?")
                text = item.get("text", "")
                source = item.get("source_task", "")
                source_part = f' (from: "{source}")' if source else ""
                lines.append(f"- [{date}] {text}{source_part}")
            lines.append("")

        try:
            with open(self.path, "w") as f:
                f.write("\n".join(lines))
        except Exception:
            pass

    def add(self, category: str, text: str, source_task: str = ""):
        """Add a learning entry with timestamp and source."""
        if category not in self.CATEGORIES:
            category = "pattern"

        entries = self._load_entries()

        # Dedup: don't add if very similar entry exists
        text_lower = text.lower()
        for e in entries:
            if e.get("text", "").lower() == text_lower:
                return  # exact duplicate
            # Simple similarity: >80% word overlap
            existing_words = set(e.get("text", "").lower().split())
            new_words = set(text_lower.split())
            if existing_words and new_words:
                overlap = len(existing_words & new_words) / max(len(existing_words), len(new_words))
                if overlap > 0.8:
                    return  # too similar

        entry = {
            "category": category,
            "text": text,
            "source_task": source_task[:80],
            "date": datetime.date.today().isoformat(),
            "timestamp": time.time(),
        }
        entries.append(entry)

        # Auto-prune if over limit
        if len(entries) > 200:
            entries = self._prune_entries(entries, 200)

        self._save_entries(entries)

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """BM25 search over learnings entries."""
        entries = self._load_entries()
        if not entries:
            return []

        query_kw = extract_keywords(query)
        if not query_kw:
            return entries[:top_k]

        scored = []
        for entry in entries:
            text = f"{entry.get('category', '')} {entry.get('text', '')} {entry.get('source_task', '')}"
            doc_kw = extract_keywords(text)
            if not doc_kw:
                continue
            # Simple keyword overlap scoring
            overlap = sum(1 for kw in query_kw if kw in doc_kw)
            if overlap > 0:
                score = overlap / len(query_kw)
                # Recency boost
                age_days = (time.time() - entry.get("timestamp", 0)) / 86400
                if age_days < 7:
                    score *= 1.3
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:top_k]]

    def to_prompt(self, query: str = "", max_tokens: int = 1500) -> str:
        """Format learnings for system prompt injection.

        If query is provided, returns most relevant entries first.
        Caps output at ~max_tokens (estimated from char count).
        """
        entries = self._load_entries()
        if not entries:
            return ""

        # If query provided, rank by relevance
        if query:
            relevant = self.search(query, top_k=15)
            # Add remaining entries sorted by recency
            relevant_ids = {id(e) for e in relevant}
            remaining = sorted(
                [e for e in entries if id(e) not in relevant_ids],
                key=lambda x: x.get("timestamp", 0), reverse=True
            )
            ordered = relevant + remaining[:10]
        else:
            ordered = sorted(entries, key=lambda x: x.get("timestamp", 0), reverse=True)[:20]

        if not ordered:
            return ""

        lines = ["=== AGENT LEARNINGS (read before work) ==="]
        char_count = len(lines[0])
        char_limit = max_tokens * 4  # rough token-to-char ratio

        for entry in ordered:
            cat = entry.get("category", "?")
            text = entry.get("text", "")
            line = f"[{cat.upper()}] {text}"
            if char_count + len(line) > char_limit:
                break
            lines.append(line)
            char_count += len(line)

        lines.append("=== END LEARNINGS ===")
        return "\n".join(lines)

    def _prune_entries(self, entries: List[Dict], max_entries: int) -> List[Dict]:
        """Remove oldest, lowest-value entries when over limit."""
        # Keep mistakes and patterns longer, prune facts/optimizations first
        priority = {"mistake": 3, "pattern": 2, "preference": 2,
                     "optimization": 1, "fact": 0}

        entries.sort(key=lambda e: (
            priority.get(e.get("category", ""), 0),
            e.get("timestamp", 0)
        ))

        # Remove lowest-priority oldest entries
        return entries[len(entries) - max_entries:]

    def prune(self, max_entries: int = 200):
        """Manually trigger pruning."""
        entries = self._load_entries()
        if len(entries) <= max_entries:
            return 0
        original = len(entries)
        entries = self._prune_entries(entries, max_entries)
        self._save_entries(entries)
        return original - len(entries)

    def count(self) -> Dict[str, int]:
        """Count entries by category."""
        entries = self._load_entries()
        counts = Counter(e.get("category", "unknown") for e in entries)
        counts["total"] = len(entries)
        return dict(counts)
