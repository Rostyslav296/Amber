#!/usr/bin/env python3
import sys, subprocess, json, argparse, urllib.parse

# --- METADATA ---
TOOL_METADATA = {
    "name": "places",
    "description": "Finds local places (restaurants, shops, banks) near a city.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to look for (e.g. 'Pizza', 'Pharmacy')."
            },
            "location": {
                "type": "string",
                "description": "The city or area (e.g. 'Knoxville, TN')."
            }
        },
        "required": ["query", "location"]
    }
}

# --- LOGIC ---
def search_places(query, location):
    # Using Nominatim (OpenStreetMap) free API
    # 1. First, geocode the location to get a "viewbox" (area)
    # For simplicity in this script, we'll use a structured search query
    
    full_query = f"{query} in {location}"
    encoded = urllib.parse.quote(full_query)
    
    url = f"https://nominatim.openstreetmap.org/search?q={encoded}&format=json&limit=5"
    
    print(f"📍 Searching for '{query}' in '{location}'...")
    
    try:
        # User-Agent is REQUIRED by Nominatim terms of service
        cmd = ["curl", "-s", "-H", "User-Agent: AmberAI/1.0", url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(res.stdout)
        
        if not data:
            print("❌ No results found.")
            return

        for place in data:
            name = place.get('display_name', 'Unknown').split(',')[0]
            # Try to get address bits
            addr = place.get('display_name')
            ptype = place.get('type', '')
            
            print(f"🏢 {name} ({ptype})")
            print(f"   ↳ {addr[:60]}...") # Shorten address
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    if args.json:
        try:
            data = json.loads(args.json)
            search_places(data.get("query"), data.get("location"))
        except: pass
