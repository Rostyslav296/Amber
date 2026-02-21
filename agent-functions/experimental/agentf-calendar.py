#!/usr/bin/env python3
import sys, subprocess, json, argparse, datetime

# --- METADATA ---
TOOL_METADATA = {
    "name": "calendar",
    "description": "Checks your macOS Calendar for events.",
    "parameters": {
        "type": "object",
        "properties": {
            "day": {
                "type": "string",
                "enum": ["today", "tomorrow"],
                "description": "Which day to check (default: today)."
            }
        },
        "required": []
    }
}

# --- LOGIC ---
def get_events(day="today"):
    # AppleScript to fetch events efficiently
    # We calculate the date range in Python to keep AppleScript simple
    now = datetime.datetime.now()
    if day == "tomorrow":
        start_date = now + datetime.timedelta(days=1)
    else:
        start_date = now
    
    # Format for AppleScript
    date_str = start_date.strftime("%B %d, %Y")
    
    script = f'''
    set checkDate to date "{date_str}"
    tell application "Calendar"
        set output to ""
        -- Check all calendars
        repeat with aCal in calendars
            tell aCal
                -- Find events in the 24 hour window of that date
                set foundEvents to (every event whose start date is greater than or equal to checkDate and start date is less than (checkDate + (1 * days)))
                repeat with anEvent in foundEvents
                    set evtSubject to summary of anEvent
                    set evtStart to start date of anEvent
                    
                    -- Simple time formatting
                    set timeStr to time string of evtStart
                    set output to output & "• " & timeStr & ": " & evtSubject & " (" & name of aCal & ")" & return
                end repeat
            end tell
        end repeat
        return output
    end tell
    '''
    
    print(f"📅 Checking calendar for {day} ({date_str})...")
    try:
        cmd = ["osascript", "-e", script]
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        output = res.stdout.strip()
        if output:
            print(output)
        else:
            print("✅ No events found.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="JSON args")
    args = parser.parse_args()

    day = "today"
    if args.json:
        try:
            data = json.loads(args.json)
            day = data.get("day", "today")
        except: pass
        
    get_events(day)
