#!/usr/bin/env python3
import socket, json, os, sys

# --- CONFIGURATION ---
# This must match the 'SOCKET_PATH' in agentf-datasette.py exactly.
SOCKET_PATH = "/tmp/fdatasette_sock.ipc"

def open_in_datasette(db_path):
    """
    Universal Trigger: Tells the running FDatasette GUI to load this database.
    """
    # Resolve absolute path so Datasette knows exactly where the file is
    # regardless of where FTerminal is running from.
    abs_path = os.path.abspath(db_path)
    
    if not os.path.exists(SOCKET_PATH):
        print(f"⚠️  FDatasette GUI is closed (Socket not found at {SOCKET_PATH})")
        print(f"   To launch it, run: open_datasette_app")
        return False

    print(f"🔗 Sending '{os.path.basename(db_path)}' to Dashboard...")
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(SOCKET_PATH)
        
        # Payload
        payload = json.dumps({"db_path": abs_path})
        client.sendall(payload.encode('utf-8'))
        client.close()
        print("✅ Dashboard Triggered.")
        return True
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: datasette_fterminal_link.py <database.db>")
        sys.exit(1)
    open_in_datasette(sys.argv[1])
