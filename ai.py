#!/usr/bin/env python3
import argparse
import sys
import os
import json
import numpy as np
import re

# --- IMPORTS ---
try:
    import mlx.core as mx
    from mlx_lm import load, generate
except ImportError:
    print("❌ Error: MLX not installed. Run: pip install mlx mlx-lm")
    sys.exit(1)

try:
    import agent
except ImportError:
    print("❌ Error: Could not import 'agent.py'. Ensure it is in the same folder.")
    sys.exit(1)

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
    print(f"🔹 Reading manifest: {weights}")
    meta = _load_meta(weights)
    repo = meta.get("repo")
    if not repo:
        print("Error: Could not find 'repo' in weights metadata.")
        sys.exit(1)
    
    print(f"🔹 Loading Model from Repo: {repo}")
    model, tokenizer = load(repo)
    return model, tokenizer

# --- CHAT LOOP ---
def chat_main(args):
    # 1. Load Model
    if not os.path.exists(args.weights):
        print(f"❌ Error: Weights file not found: {args.weights}")
        return

    try:
        model, tokenizer = load_from_npz(args.weights)
    except Exception as e:
        print(f"❌ Load Failed: {e}")
        return

    # 2. Get Tools from Agent
    try:
        if hasattr(agent, "get_system_prompt_addendum"):
            tool_instructions = agent.get_system_prompt_addendum()
        else:
            tool_instructions = "Tools: browser, calculator."
    except Exception:
        tool_instructions = ""

    # 3. Construct System Prompt
    system_prompt = (
        "You are Amber, a helpful AI assistant residing on a Mac. "
        "You can control the computer using the tools below. "
        "To use a tool, reply ONLY with a JSON object describing the action. "
        "Do not wrap the JSON in markdown code blocks.\n\n"
        f"{tool_instructions}\n\n"
        "Example:\n"
        'User: "Open the calculator"\n'
        'Amber: {"tool": "calclaunch", "args": {"mode": "standard"}}\n'
    )

    messages = [{"role": "system", "content": system_prompt}]
    print("\n✅ Amber Ready. (Type 'exit' to quit)\n")

    # 4. Main Loop
    while True:
        try:
            try:
                raw_input = input("User: ").strip()
            except EOFError:
                break

            if not raw_input: continue
            if raw_input.lower() in ["exit", "quit"]: break

            # --- "SHOW THINK" TOGGLE LOGIC ---
            show_thoughts = False
            user_content = raw_input
            
            if raw_input.lower().startswith("show think"):
                show_thoughts = True
                user_content = re.sub(r"^show think\s*", "", raw_input, flags=re.IGNORECASE).strip()

            messages.append({"role": "user", "content": user_content})

            # --- GENERATION ---
            print("Amber: ", end="", flush=True)
            prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            
            full_response = ""
            
            # Variables for suppressing thought output
            output_buffer = ""
            is_thinking = False
            
            for chunk in generate(model, tokenizer, prompt=prompt, verbose=False, max_tokens=1024):
                full_response += chunk
                
                if show_thoughts:
                    # MODE A: Print everything (Raw)
                    print(chunk, end="", flush=True)
                else:
                    # MODE B: Suppress <think> blocks
                    output_buffer += chunk
                    
                    if not is_thinking:
                        if "<think>" in output_buffer:
                            pre, post = output_buffer.split("<think>", 1)
                            print(pre, end="", flush=True)
                            print("☁️ ", end="", flush=True) # Visual indicator
                            output_buffer = post
                            is_thinking = True
                        else:
                            if not any(output_buffer.endswith(x) for x in ["<", "<t", "<th", "<thi", "<thin", "<think"]):
                                print(output_buffer, end="", flush=True)
                                output_buffer = ""
                    else:
                        if "</think>" in output_buffer:
                            _, post = output_buffer.split("</think>", 1)
                            print("\r" + " " * 4 + "\r", end="", flush=True) # Clear indicator
                            output_buffer = post
                            is_thinking = False
                            print(output_buffer, end="", flush=True)
                            output_buffer = ""
                            
            print() # Final newline

            messages.append({"role": "assistant", "content": full_response})

            # --- ACTION LAYER ---
            if hasattr(agent, "route_intent"):
                tool_output = agent.route_intent(full_response)
                if tool_output:
                    print(f"⚙️  {tool_output}")
            
        except KeyboardInterrupt:
            print("\nGoodbye.")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")

def main():
    parser = argparse.ArgumentParser(description="Agent F (Amber)")
    parser.add_argument("--weights", type=str, default="qwen.npz", help="Path to qwen.npz")
    # ADDED THIS LINE TO FIX THE ERROR:
    parser.add_argument("--agent", type=str, default="agent.py", help="Path to agent script (legacy argument)")
    parser.add_argument("cmd", nargs="?", default="chat", help="Command (default: chat)")
    
    args = parser.parse_args()
    chat_main(args)

if __name__ == "__main__":
    main()
