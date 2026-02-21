#!/usr/bin/env python3
import sys, subprocess, json, argparse, urllib.parse, re

# --- METADATA (Optimized for Decisiveness) ---
TOOL_METADATA = {
    "name": "weather",
    "description": "Gets the weather. ARGS: 'location' (City/Zip). Omit 'location' to use Auto-IP (Current Location).",
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City (e.g. 'Paris'), Zip (e.g. '37725'). LEAVE EMPTY for current location."
            },
            "full": {
                "type": "boolean",
                "description": "Set true for 3-day forecast."
            }
        },
        "required": []
    }
}

# --- HELPERS ---
def sanitize_location(loc):
    if not loc: return None
    clean = loc.strip().replace("'", "").replace('"', "")
    
    # Keywords that mean "Auto-IP"
    if clean.lower() in ["current", "current location", "here", "me", "local", "today"]:
        return None

    # Fix US Zip Codes (37725 -> 37725,US)
    if re.match(r"^\d{5}$", clean):
        return f"{clean},US"

    # Fix Spacing (Dandridge, TN -> Dandridge,TN)
    if "," in clean:
        parts = clean.split(",")
        return f"{parts[0].strip()},{parts[1].strip()}"

    return clean

def get_ip_location():
    """Fallback: Finds city name via IP if wttr.in fails."""
    print("🌍 Resolving current location via IP-API...")
    try:
        # 2s timeout is enough for IP lookup
        cmd = ["curl", "-s", "--max-time", "2", "http://ip-api.com/json"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(res.stdout)
        
        if data.get("status") == "success":
            city = data.get("city")
            country = data.get("countryCode")
            print(f"📍 Detected: {city}, {country}")
            return f"{city},{country}"
    except Exception as e:
        print(f"⚠️ IP Lookup failed: {e}")
    return None

# --- SOURCE 1: wttr.in ---
def get_weather_wttr(location, full_report=False):
    url_params = "?T" if full_report else "?format=3"
    
    if location is None:
        url = f"wttr.in/{url_params}"
        display_name = "Current Location (Auto-IP)"
    else:
        safe_loc = urllib.parse.quote(location).replace("%20", "+")
        url = f"wttr.in/{safe_loc}{url_params}"
        display_name = location

    print(f"🌦️  Querying wttr.in for '{display_name}'...")
    
    try:
        cmd = ["curl", "-s", "--max-time", "4", url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        output = res.stdout.strip()
        
        if output and "Unknown location" not in output and "Sorry" not in output:
            return output
    except: pass
    
    return None

# --- SOURCE 2: Open-Meteo ---
def get_weather_openmeteo(query):
    if not query: return None

    print(f"⚠️  wttr.in failed. Trying Open-Meteo for '{query}'...")
    
    try:
        # 1. Geocode
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(query)}&count=1&language=en&format=json"
        geo_res = subprocess.run(["curl", "-s", geo_url], capture_output=True, text=True)
        geo_data = json.loads(geo_res.stdout)

        if not geo_data.get("results"):
            return None
        
        place = geo_data["results"][0]
        lat, lon = place["latitude"], place["longitude"]
        name = f"{place.get('name')}, {place.get('country_code')}"

        # 2. Forecast
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        weather_res = subprocess.run(["curl", "-s", weather_url], capture_output=True, text=True)
        w_data = json.loads(weather_res.stdout)

        curr = w_data.get("current_weather", {})
        
        return (f"Weather Report for {name}:\n"
                f"🌡️  Temp: {curr.get('temperature')}°C\n"
                f"💨 Wind: {curr.get('windspeed')} km/h\n"
                f"Conditions: Code {curr.get('weathercode')}")
    except Exception as e:
        print(f"❌ Open-Meteo Error: {e}")
        return None

# --- MAIN ---
def main(loc_arg, is_full):
    smart_loc = sanitize_location(loc_arg)
    
    # 1. Try Primary (wttr.in)
    report = get_weather_wttr(smart_loc, is_full)
    
    # 2. Fallback Logic
    if not report:
        # If we failed on "Current Location", we need to find the City Name first
        # so Open-Meteo has something to search for.
        if smart_loc is None:
            smart_loc = get_ip_location()
        
        # Now try Open-Meteo with the resolved name
        if smart_loc:
            report = get_weather_openmeteo(smart_loc)

    # 3. Output
    if report:
        print("--- WEATHER REPORT ---")
        print(report)
        print("----------------------")
        print("\nSYSTEM NOTE: Please summarize this weather report for the user.")
    else:
        print(f"❌ Weather unavailable. Networks down or location '{loc_arg}' not found.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args from Agent")
    parser.add_argument("raw", nargs="?", help="Legacy raw string argument")
    args = parser.parse_args()

    loc_arg = None
    is_full = False

    if args.json:
        try:
            data = json.loads(args.json)
            loc_arg = data.get("location")
            is_full = data.get("full", False)
        except: pass
    elif args.raw:
        loc_arg = args.raw

    main(loc_arg, is_full)
