import paramiko
import csv
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from lab_utils import (
    TURTLEBOT_STATIONS, UR7E_STATIONS,
    STATION_STATUS_CSV_PATH, LAST_UPDATE_FILE,
    DB_CONFIG, get_db_connection,
    is_lab_oh_time, is_lab_active_time,
)

USERNAME = "ee106a"
USERNAMES_TO_CHECK = ["ee106a", "ee106b"]  # Check if any of these are logged in

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NON_OH_INTERVAL = 60  # seconds - only update once per minute outside OH

ALL_STATIONS = sorted(TURTLEBOT_STATIONS | UR7E_STATIONS)

BASE_HOST = "c105-{}.eecs.berkeley.edu"

def should_run_update():
    """Check if we should run update based on lab status and last update time."""
    if is_lab_active_time():
        return True  # Always run during OH or Lab Section

    # Outside OH: only run if NON_OH_INTERVAL seconds have passed
    if os.path.exists(LAST_UPDATE_FILE):
        try:
            with open(LAST_UPDATE_FILE) as f:
                last_update = float(f.read().strip())
            if time.time() - last_update < NON_OH_INTERVAL:
                return False
        except Exception:
            pass  # If file is corrupted, run update
    return True


def save_update_time():
    """Save current timestamp for throttling logic."""
    os.makedirs(os.path.dirname(LAST_UPDATE_FILE), exist_ok=True)
    with open(LAST_UPDATE_FILE, 'w') as f:
        f.write(str(time.time()))


def check_station(station_num, retries=3):
    """SSH into the machine and check if the user is logged in."""
    hostname = BASE_HOST.format(station_num)
    command = "who"

    for attempt in range(retries):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            client.connect(
                hostname=hostname,
                username=USERNAME,
                key_filename=None,  # uses ~/.ssh/
                timeout=5
            )
            _, stdout, _ = client.exec_command(command)
            output = stdout.read().decode()
            # occupied if any class username appears on any line
            occupied = any(user in output for user in USERNAMES_TO_CHECK)
            client.close()
            return occupied
        except Exception as e:
            try:
                client.close()
            except:
                pass
            if attempt < retries - 1:
                print(f"  Retry {attempt + 1}/{retries - 1} for c105-{station_num}...")
                time.sleep(1)
            else:
                print(f"  SSH failed for c105-{station_num} after {retries} attempts: {e}")
                # If all retries fail, assume occupied (safer)
                return True

    return True  # Default to occupied if something weird happens

def main():
    # Check calendar event once (cached in lab_utils)
    is_oh = is_lab_oh_time()
    print(f"Lab OH: {is_oh}")

    if not should_run_update():
        print("Outside OH and recent update exists, skipping.")
        return

    # Check all stations in parallel
    results = {}
    print(f"Checking {len(ALL_STATIONS)} stations in parallel...")
    with ThreadPoolExecutor(max_workers=len(ALL_STATIONS)) as executor:
        futures = {
            executor.submit(check_station, station): station
            for station in ALL_STATIONS
        }
        for future in as_completed(futures):
            station = futures[future]
            try:
                results[station] = future.result()
            except Exception as e:
                print(f"  Error checking c105-{station}: {e}")
                results[station] = True  # Default to occupied on error

    # Sort results by station number
    sorted_results = [(s, results[s]) for s in sorted(results)]

    # Connect to MariaDB
    conn = get_db_connection()
    cursor = conn.cursor()

    # Make sure the table exists with previous_occupied column
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stations (
        station INT PRIMARY KEY,
        occupied BOOLEAN NOT NULL,
        previous_occupied BOOLEAN DEFAULT NULL,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """)

    # Add previous_occupied column if it doesn't exist (for existing deployments)
    try:
        cursor.execute("""
        ALTER TABLE stations ADD COLUMN previous_occupied BOOLEAN DEFAULT NULL
        """)
        conn.commit()
    except Exception:
        pass  # Column already exists

    # Snapshot current -> previous before updating
    cursor.execute("UPDATE stations SET previous_occupied = occupied")

    # Update database
    for station, occupied in sorted_results:
        cursor.execute("""
        INSERT INTO stations (station, occupied)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE occupied=%s, last_updated=CURRENT_TIMESTAMP
        """, (station, occupied, occupied))

    conn.commit()

    # Write CSV for notification checker to use
    cursor.execute("SELECT station, occupied FROM stations ORDER BY station")
    rows = cursor.fetchall()
    with open(STATION_STATUS_CSV_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["station", "occupied"])
        for row in rows:
            writer.writerow(row)

    cursor.close()
    conn.close()

    # Save update time for throttling
    save_update_time()
    print("Database updated and CSV written.")

if __name__ == "__main__":
    main()
