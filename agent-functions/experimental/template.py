#!/usr/bin/env python3
import sys, subprocess, json, argparse, os

# --- METADATA ---
TOOL_METADATA = {
    "name": "pdf_reader",
    "description": "Reads the text content of a PDF file.",
    "parameters": {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Path to the PDF file."
            }
        },
        "required": ["filepath"]
    }
}

# --- LOGIC ---
def read_pdf(filepath):
    path = os.path.expanduser(filepath)
    if not os.path.exists(path):
        print(f"❌ Error: File '{path}' not found.")
        return

    print(f"📄 Extracting text from '{os.path.basename(path)}'...")
    
    # Method 1: macOS Native (textutil)
    # textutil converts to txt, prints to stdout (-stdout)
    try:
        cmd = ["textutil", "-convert", "txt", path, "-stdout"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        if res.stdout:
            text = res.stdout.strip()
            print("\n--- PDF CONTENT START ---")
            print(text[:3000]) # Limit to 3000 chars for context window
            if len(text) > 3000: print("\n... (truncated) ...")
            print("--- PDF CONTENT END ---")
            return
    except: pass

    # Method 2: pdftotext (Poppler) - Fallback
    try:
        cmd = ["pdftotext", path, "-"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.stdout:
            print(res.stdout[:3000])
            return
    except: pass

    print("❌ Error: Could not read PDF. Ensure it contains text, not just images.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            read_pdf(data.get("filepath"))
        except: pass