#!/usr/bin/env python3
import sys, subprocess, asyncio, sqlite3, importlib.util, time, os

# --- 1. DEPENDENCY CHECK ---
def install_dependency(package):
    print(f"📦 Checking dependency: {package}...")
    if importlib.util.find_spec(package) is None:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
            print(f"✅ {package} installed.")
        except:
            sys.exit(1)

if importlib.util.find_spec("bleak") is None:
    install_dependency("bleak")

from bleak import BleakScanner

# Import the Bridge (Fail gracefully if missing)
try:
    from datasette_fterminal_link import open_in_datasette
except ImportError:
    def open_in_datasette(path): pass

# --- 2. SPY ANALYSIS LOGIC ---
def generate_intel_report(conn):
    """
    Analyzes raw signal data and generates a plain-English 'Spy Report'
    for the Dashboard.
    """
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS INTEL_BRIEFING")
    c.execute("""CREATE TABLE INTEL_BRIEFING (
        Alert_Level TEXT,
        Device_Name TEXT,
        Assessment TEXT,
        Action_Item TEXT
    )""")
    
    # Analyze the raw data we just caught
    rows = c.execute("SELECT name, rssi, manufacturer_data FROM ble_devices ORDER BY rssi DESC").fetchall()
    
    intel = []
    for r in rows:
        name = r[0] or "Unknown Device"
        rssi = r[1]
        mfg = str(r[2])
        
        # A. IDENTITY ANALYSIS
        identity = "Unidentified Electronics"
        if "76" in mfg: identity = "Apple Ecosystem (iPhone/Mac/Tag)"
        elif "6" in mfg: identity = "Microsoft Device"
        elif "0" in mfg: identity = "Ericsson Infrastructure"
        
        # B. THREAT/DISTANCE ANALYSIS
        dist = "Unknown"
        action = "Monitor"
        alert = "GRAY"
        
        if rssi > -45:
            dist = "CONTACT (< 1ft)"
            action = "Check your pockets or immediate desk surface."
            alert = "🔴 CRITICAL"
        elif rssi > -60:
            dist = "CLOSE (Same Room)"
            action = "Visible range. Look for glowing LEDs."
            alert = "🟠 HIGH"
        elif rssi > -80:
            dist = "NEAR (Through Wall/Door)"
            action = "Likely neighbor or adjacent room."
            alert = "🟡 MEDIUM"
        else:
            dist = "DISTANT (Ambient)"
            action = "Ignore."
            alert = "🟢 LOW"

        # C. COMPILE BRIEFING
        assessment = f"Signal {rssi}dBm puts target in {dist}. Signature matches {identity}."
        intel.append((alert, name, assessment, action))

    c.executemany("INSERT INTO INTEL_BRIEFING VALUES (?,?,?,?)", intel)
    conn.commit()

# --- 3. SCAN LOGIC ---
async def scan_ble(duration=5.0, db_name="ble_recon.db"):
    print(f"📡 Initializing SIGINT Scan ({duration}s)...")
    
    devices = {}
    for attempt in range(1, 4):
        try:
            devices = await BleakScanner.discover(timeout=duration, return_adv=True)
            break
        except Exception as e:
            if "turned off" in str(e) or "Permission" in str(e):
                print(f"⚠️  Attempt {attempt}: Waiting for MacOS Permissions...")
                await asyncio.sleep(5)
            else:
                await asyncio.sleep(2)
    
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS ble_devices")
    c.execute("""CREATE TABLE ble_devices (address TEXT, name TEXT, rssi INTEGER, manufacturer_data TEXT, service_uuids TEXT, proximity TEXT)""")
    
    batch = []
    for d, adv in devices.values():
        name = d.name or "Unknown"
        prox = "Immediate" if adv.rssi > -60 else ("Near" if adv.rssi > -80 else "Far")
        batch.append((d.address, name, adv.rssi, str(adv.manufacturer_data), str(adv.service_uuids), prox))

    c.executemany("INSERT INTO ble_devices VALUES (?,?,?,?,?,?)", batch)
    
    # Create the Standard View
    c.execute("DROP VIEW IF EXISTS Device_Radar")
    c.execute("CREATE VIEW Device_Radar AS SELECT name, rssi, proximity, address FROM ble_devices ORDER BY rssi DESC")
    
    # Run the "Spy" Analysis
    generate_intel_report(conn)
    
    conn.commit()
    conn.close()
    
    print(f"✅ Target Acquisition Complete. {len(devices)} signals found.")
    open_in_datasette(db_name)

if __name__ == "__main__":
    try: asyncio.run(scan_ble())
    except KeyboardInterrupt: print("\n🛑 Scan aborted.")
