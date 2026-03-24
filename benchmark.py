#!/usr/bin/env python3
"""Benchmark Amber's token generation speed on M4 16GB."""
import time
import sys
import mlx.core as mx
from mlx_lm import load, stream_generate

import warnings, logging, io
warnings.filterwarnings("ignore")

R = "\033[31m"; BR = "\033[91m"; B = "\033[1m"; X = "\033[0m"; GR = "\033[90m"; G = "\033[32m"

def load_model():
    print(f"{GR}Loading model...{X}", flush=True)
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    logging.disable(logging.WARNING)
    try:
        model, tokenizer = load('mlx-community/Qwen3.5-9B-MLX-4bit')
    finally:
        sys.stderr = old_stderr
        logging.disable(logging.NOTSET)
    return model, tokenizer

def benchmark(model, tokenizer, prompt_text, label, max_tokens=256, system="You are a helpful assistant.", **gen_kwargs):
    """Run a single benchmark and return stats."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt_text}]
    prompt = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    tokens = tokenizer.encode(prompt)
    prompt_len = len(tokens)

    gen_tokens = 0; gen_tps = 0.0; prompt_tps = 0.0; peak_mem = 0.0; text = ""

    t0 = time.perf_counter()
    for resp in stream_generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, **gen_kwargs):
        gen_tokens = resp.generation_tokens
        gen_tps = resp.generation_tps
        prompt_tps = resp.prompt_tps
        peak_mem = resp.peak_memory
        text += resp.text
    elapsed = time.perf_counter() - t0

    color = G if gen_tps >= 40 else (GR if gen_tps >= 30 else BR)
    print(f"  {B}{label}{X}")
    print(f"    Prompt: {prompt_len} tok @ {prompt_tps:.1f} t/s | Gen: {gen_tokens} tok @ {color}{gen_tps:.1f} t/s{X} | Peak: {peak_mem:.2f} GB | {elapsed:.2f}s")
    return {"label": label, "prompt_len": prompt_len, "gen_tokens": gen_tokens,
            "gen_tps": gen_tps, "prompt_tps": prompt_tps, "peak_mem": peak_mem, "elapsed": elapsed}

def main():
    model, tokenizer = load_model()
    info = mx.metal.device_info()
    print(f"\n{B}{BR}=== AMBER BENCHMARK ==={X}")
    print(f"  {GR}Device: {info['device_name']} ({info['memory_size'] / (1024**3):.0f}GB){X}")
    print()

    results = []

    # --- Baseline tests ---
    print(f"{B}--- BASELINE (no optimization) ---{X}")
    results.append(benchmark(model, tokenizer,
        "What is 2+2? Answer in one word.", "Baseline: short", max_tokens=100))

    results.append(benchmark(model, tokenizer,
        "List the top 5 programming languages in 2026.", "Baseline: medium", max_tokens=256))

    # Agent-style prompt
    agent_prompt = (
        "You are browsing a webpage. The page snapshot shows:\n"
        "#1 [link] Home\n#2 [link] Products\n#3 [link] Sign Up\n"
        "#4 [button] Login\n#5 [input] Search\n\n"
        "The user wants you to sign up. Output a JSON tool call."
    )
    results.append(benchmark(model, tokenizer, agent_prompt, "Baseline: agent", max_tokens=256))

    # Long context
    long_ctx = "Here is a conversation history:\n" + "\n".join(
        [f"Step {i}: Agent action {i}, result was item_{i}. " * 2 for i in range(40)]
    ) + "\nWhat should I do next?"
    results.append(benchmark(model, tokenizer, long_ctx, "Baseline: long ctx", max_tokens=256))

    # --- KV cache quantization ---
    print(f"\n{B}--- KV CACHE QUANTIZATION ---{X}")
    results.append(benchmark(model, tokenizer,
        "List the top 5 programming languages in 2026.", "KV 4-bit", max_tokens=256,
        kv_bits=4, kv_group_size=64))

    results.append(benchmark(model, tokenizer,
        "List the top 5 programming languages in 2026.", "KV 8-bit", max_tokens=256,
        kv_bits=8, kv_group_size=64))

    results.append(benchmark(model, tokenizer, long_ctx, "Long + KV 4-bit", max_tokens=256,
        kv_bits=4, kv_group_size=64))

    # --- max_kv_size ---
    print(f"\n{B}--- MAX KV SIZE ---{X}")
    results.append(benchmark(model, tokenizer, long_ctx, "Long + max_kv=4096", max_tokens=256,
        max_kv_size=4096))

    results.append(benchmark(model, tokenizer, long_ctx, "Long + max_kv=2048", max_tokens=256,
        max_kv_size=2048))

    # --- Metal cache limits ---
    print(f"\n{B}--- METAL CACHE LIMITS ---{X}")
    mx.metal.set_cache_limit(4 * 1024**3)  # 4GB
    results.append(benchmark(model, tokenizer,
        "List the top 5 programming languages in 2026.", "Cache 4GB", max_tokens=256))

    mx.metal.set_cache_limit(8 * 1024**3)  # 8GB
    results.append(benchmark(model, tokenizer,
        "List the top 5 programming languages in 2026.", "Cache 8GB", max_tokens=256))

    mx.metal.set_cache_limit(0)  # unlimited (default)

    # --- Thinking disabled ---
    print(f"\n{B}--- THINKING DISABLED ---{X}")
    no_think_prompt = tokenizer.apply_chat_template(
        [{"role": "system", "content": "You are a helpful assistant."},
         {"role": "user", "content": "List the top 5 programming languages in 2026."}],
        tokenize=False, add_generation_prompt=True
    )
    # Manually disable thinking by closing the think block
    no_think_prompt_disabled = no_think_prompt.replace("<think>\n", "<think>\n\n</think>\n\n")
    text = ""; gen_tps = 0; prompt_tps = 0; gen_tokens = 0; peak_mem = 0
    t0 = time.perf_counter()
    for resp in stream_generate(model, tokenizer, prompt=no_think_prompt_disabled, max_tokens=256):
        text += resp.text; gen_tps = resp.generation_tps; prompt_tps = resp.prompt_tps
        gen_tokens = resp.generation_tokens; peak_mem = resp.peak_memory
    elapsed = time.perf_counter() - t0
    color = G if gen_tps >= 40 else (GR if gen_tps >= 30 else BR)
    print(f"  {B}No-think: medium{X}")
    print(f"    Prompt: ? tok @ {prompt_tps:.1f} t/s | Gen: {gen_tokens} tok @ {color}{gen_tps:.1f} t/s{X} | Peak: {peak_mem:.2f} GB | {elapsed:.2f}s")
    results.append({"label": "No-think: medium", "gen_tps": gen_tps, "prompt_tps": prompt_tps,
                     "gen_tokens": gen_tokens, "peak_mem": peak_mem, "prompt_len": 0, "elapsed": elapsed})

    # --- Combined optimizations ---
    print(f"\n{B}--- COMBINED OPTIMIZATIONS ---{X}")
    mx.metal.set_cache_limit(4 * 1024**3)
    results.append(benchmark(model, tokenizer,
        "List the top 5 programming languages in 2026.", "KV4+Cache4GB", max_tokens=256,
        kv_bits=4, kv_group_size=64))

    results.append(benchmark(model, tokenizer, long_ctx, "Long+KV4+Cache4GB", max_tokens=256,
        kv_bits=4, kv_group_size=64))

    results.append(benchmark(model, tokenizer, long_ctx, "Long+KV4+maxKV4096", max_tokens=256,
        kv_bits=4, kv_group_size=64, max_kv_size=4096))

    mx.metal.set_cache_limit(0)

    # --- Summary ---
    print(f"\n{B}{BR}=== SUMMARY ==={X}")
    print(f"  {'Test':<25} {'Gen t/s':>8} {'Prompt t/s':>10} {'Peak GB':>8}")
    print(f"  {'-'*53}")
    for r in results:
        color = G if r['gen_tps'] >= 40 else (GR if r['gen_tps'] >= 30 else BR)
        print(f"  {r['label']:<25} {color}{r['gen_tps']:>8.1f}{X} {r['prompt_tps']:>10.1f} {r['peak_mem']:>8.2f}")

    avg_tps = sum(r['gen_tps'] for r in results) / len(results)
    max_tps = max(r['gen_tps'] for r in results)
    color = G if avg_tps >= 40 else (GR if avg_tps >= 30 else BR)
    print(f"\n  {B}Average: {color}{avg_tps:.1f} t/s{X}  |  {B}Peak: {G}{max_tps:.1f} t/s{X}")
    print()

if __name__ == "__main__":
    main()
