#!/usr/bin/env python3
import sys, os, json, glob, ast, subprocess, re, time

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FUNCTIONS_DIR = os.path.join(BASE_DIR, "agent-functions")
LAUNCH_CHECK_TIMEOUT = 4.0  # Seconds to monitor a process before assuming success

# --- 1. HYBRID REGISTRY LOADER (Robust Discovery) ---
def load_tools():
    """Scans for both new JSON-metadata tools and old Regex tools."""
    registry = {}
    system_prompt_lines = []
    
    if not os.path.exists(FUNCTIONS_DIR):
        return {}, "Error: agent-functions folder not found."

    scripts = glob.glob(os.path.join(FUNCTIONS_DIR, "*.py"))
    
    for script_path in scripts:
        fname = os.path.basename(script_path)
        if fname.startswith("_") or fname == "ai.py": continue

        metadata = None
        try:
            with open(script_path, "r", encoding="utf-8") as f: content = f.read()

            # Strategy A: New Style (TOOL_METADATA variable)
            try:
                tree = ast.parse(content)
                for node in tree.body:
                    if isinstance(node, ast.Assign) and len(node.targets) == 1:
                        target = node.targets[0]
                        if isinstance(target, ast.Name) and target.id == "TOOL_METADATA":
                            metadata = ast.literal_eval(node.value)
                            break
            except: pass

            # Strategy B: Old Style (Regex Header)
            if not metadata:
                match = re.search(r"#\s*AGENTCMD:(.*)", content)
                if match:
                    props = {k.strip().lower(): v.strip() for k, v in [x.split("=", 1) for x in match.group(1).split(";") if "=" in x]}
                    if "name" in props:
                        metadata = {
                            "name": props["name"],
                            "description": props.get("description", "Legacy Tool"),
                            "parameters": {"type": "object", "properties": {}}
                        }

            # Register
            if metadata:
                name = metadata.get("name")
                registry[name] = {"path": script_path, "meta": metadata}
                
                # Prompt Formatting
                desc = metadata.get("description", "")
                args = ", ".join(metadata.get("parameters", {}).get("properties", {}).keys())
                system_prompt_lines.append(f'- "{name}": {desc} [Args: {args}]')

        except Exception: pass

    header = (
        "\n\n[AVAILABLE TOOLS]\n"
        "To use a tool, output a JSON object. You can \"think\" before acting.\n"
        "Format: {\"tool\": \"name\", \"args\": {\"key\": \"val\"}}\n"
        "Available Tools:\n"
    )
    return registry, header + "\n".join(system_prompt_lines)

# --- INIT ---
registry, sys_prompt_addendum = load_tools()

# --- 2. PUBLIC API ---
def get_system_prompt_addendum():
    return sys_prompt_addendum

def route_intent(llm_response):
    """Aggressive parser that hunts for the first valid JSON object."""
    if not llm_response: return None
    
    # A. Strip <think> blocks (cleaner input)
    text = re.sub(r'<think>.*?</think>', '', llm_response, flags=re.DOTALL).strip()
    
    # B. Extraction Strategy: Look for the widest {...} block
    # We use a stack-based approach or greedy regex to find the JSON blob
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if not match: return None
    
    candidate = match.group(1)
    
    # C. Robust Parsing (Fix Single Quotes, etc.)
    data = None
    try:
        data = json.loads(candidate)
    except:
        try: data = ast.literal_eval(candidate)
        except: pass

    # D. Execution Decision
    if isinstance(data, dict):
        tool = data.get("tool")
        args = data.get("args", {})
        if tool in registry:
            return _launch_sequence(tool, args, registry)
            
    return None

# --- 3. AGGRESSIVE LAUNCH SEQUENCE ---
def _launch_sequence(tool_name, args, registry):
    tool_def = registry[tool_name]
    path = tool_def["path"]
    
    # Always pass args as JSON for consistency
    cmd = [sys.executable, path, "--json", json.dumps(args)]
    
    print(f"🚀  Firing {tool_name}...", flush=True)

    # Heuristic: Is this a GUI/Background tool?
    # We look at the name OR if the description contains 'launch'/'open'
    is_gui = any(x in tool_name.lower() for x in ["launch", "open", "browser", "safari", "calc", "terminal", "fterminal"])
    
    try:
        # STEP 1: Start the process (PIPE stderr so we can see errors)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # STEP 2: The "Smart Monitor"
        # We wait briefly to see if it crashes immediately (e.g. ImportError)
        try:
            stdout, stderr = proc.communicate(timeout=LAUNCH_CHECK_TIMEOUT)
            
            # If we are here, the process finished within 2 seconds.
            if proc.returncode != 0:
                # IT CRASHED. Report the error.
                err_msg = stderr.strip() or stdout.strip() or "Unknown Error"
                return f"❌ Launch Failed: {err_msg}"
            else:
                # It finished successfully (e.g. a quick command like 'ls')
                return stdout.strip() or "✅ executed."

        except subprocess.TimeoutExpired:
            # STEP 3: It's still running after 2 seconds.
            # This means it's a healthy GUI/Background app. Detach and move on.
            if is_gui:
                return f"✅ {tool_name} launched successfully."
            else:
                # If it's NOT a GUI app but taking long, we wait for it to finish.
                stdout, stderr = proc.communicate()
                if proc.returncode != 0:
                    return f"❌ Error: {stderr.strip()}"
                return stdout.strip()

    except Exception as e:
        return f"❌ System Error: {e}"
