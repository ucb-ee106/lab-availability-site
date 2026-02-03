import paramiko
import pymysql
import csv
import os
import time
from datetime import datetime, timedelta

from icalendar import Calendar
from dateutil.rrule import rrulestr
from dateutil import tz

USERNAME = "ee106a"
USERNAMES_TO_CHECK = ["ee106a", "ee106b"]  # Check if any of these are logged in

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CALENDAR_PATH = os.path.join(SCRIPT_DIR, 'uploads/course_calendar.ics')
LAST_UPDATE_FILE = os.path.join(SCRIPT_DIR, 'csv/last_update.txt')
NON_OH_INTERVAL = 60  # seconds - only update once per minute outside OH


def get_current_lab_event():
    """
    Check calendar and return the current event type.
    Returns: 'lab_oh', 'lab_section', 'maintenance', or None

    Event patterns:
    - Lab OH: "[EECS C106A] Lab OH - ..." or "[EECS C106B] Lab OH - ..."
    - Lab Section: "[EECS C106A] Lab - ..." or "[EECS C106B] Lab - ..." or "[EECS C106A] - ..."
    - Maintenance: "Lab Maintenance - ..."
    """
    if not os.path.exists(CALENDAR_PATH):
        return None

    try:
        with open(CALENDAR_PATH, 'rb') as f:
            cal = Calendar.from_ical(f.read())
    except Exception as e:
        print(f"Error reading calendar: {e}")
        return None

    now = datetime.now(tz.tzlocal())

    for component in cal.walk():
        if component.name != 'VEVENT':
            continue

        summary = str(component.get('summary', ''))
        summary_lower = summary.lower()

        # Determine event type
        if 'lab oh' in summary_lower:
            event_type = 'lab_oh'
        elif 'lab maintenance' in summary_lower or 'maintenance' in summary_lower:
            event_type = 'maintenance'
        elif ('106a' in summary_lower or '106b' in summary_lower or 'c106a' in summary_lower or 'c106b' in summary_lower) and 'lab' in summary_lower:
            event_type = 'lab_section'
        elif ('eecs' in summary_lower and ('106a' in summary_lower or '106b' in summary_lower)):
            event_type = 'lab_section'
        else:
            continue

        dtstart = component.get('dtstart')
        dtend = component.get('dtend')

        if dtstart is None or dtend is None:
            continue

        start = dtstart.dt
        end = dtend.dt

        # Handle all-day events (date instead of datetime)
        if not isinstance(start, datetime):
            start = datetime.combine(start, datetime.min.time())
            start = start.replace(tzinfo=tz.tzlocal())
        if not isinstance(end, datetime):
            end = datetime.combine(end, datetime.max.time())
            end = end.replace(tzinfo=tz.tzlocal())

        # Ensure timezone awareness
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz.tzlocal())
        if end.tzinfo is None:
            end = end.replace(tzinfo=tz.tzlocal())

        # Check for recurring events
        rrule = component.get('rrule')
        if rrule:
            try:
                rule = rrulestr(rrule.to_ical().decode('utf-8'), dtstart=start)
                duration = end - start

                window_start = now - timedelta(days=1)
                window_end = now + timedelta(days=1)

                for occurrence in rule.between(window_start, window_end, inc=True):
                    occ_start = occurrence
                    occ_end = occurrence + duration

                    if occ_start <= now <= occ_end:
                        return event_type
            except Exception as e:
                print(f"Error parsing rrule: {e}")

        # Check if now is within event time (for non-recurring or as fallback)
        if start <= now <= end:
            return event_type

    return None


def is_lab_oh_time():
    """Check if current time is during Lab OH (for 106A or 106B)."""
    return get_current_lab_event() == 'lab_oh'


def is_lab_active_time():
    """Check if current time is during Lab OH or Lab Section (need frequent updates)."""
    event = get_current_lab_event()
    return event in ('lab_oh', 'lab_section')


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


BASE_HOST = "c105-{}.eecs.berkeley.edu"
DB_CONFIG = {
    "host": "instapphost.eecs.berkeley.edu",
    "user": "ee106a",
    "password": "REDACTED",  # your DB password
    "database": "ee106a"
}

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
    # Check if we should run based on OH status and throttling
    is_oh = is_lab_oh_time()
    print(f"Lab OH: {is_oh}")

    if not should_run_update():
        print("Outside OH and recent update exists, skipping.")
        return

    results = []

    # Check all stations
    for station in range(1, 12):
        print(f"Checking c105-{station}...")
        occ = check_station(station)
        results.append((station, occ))

    # Connect to MariaDB
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # Make sure the table exists
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stations (
        station INT PRIMARY KEY,
        occupied BOOLEAN NOT NULL,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """)

    # Update database
    for station, occupied in results:
        cursor.execute("""
        INSERT INTO stations (station, occupied)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE occupied=%s, last_updated=CURRENT_TIMESTAMP
        """, (station, occupied, occupied))

    conn.commit()

    # Write CSV for notification checker to use
    cursor.execute("SELECT station, occupied FROM stations ORDER BY station")
    rows = cursor.fetchall()
    csv_path = os.path.join(SCRIPT_DIR, 'csv/station_status.csv')
    with open(csv_path, "w", newline="") as f:
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

