"""Shared utilities for the lab availability site.

Consolidates duplicated logic across app.py, check_notifications.py, and update_db.py:
- Calendar event parsing (with caching)
- Station groupings
- CSV/file path constants
- Manual overrides reader
- Advisory file locking for CSV safety
- Database connection management
- Data access layer (CSV / DB dispatch via DATA_SOURCE)
"""
import os
import csv
import time
import fcntl
import re
import secrets
from datetime import datetime, timedelta
from contextlib import contextmanager

import pymysql
from icalendar import Calendar
from dateutil.rrule import rrulestr
from dateutil import tz

# ---------------------------------------------------------------------------
# Data source configuration
# ---------------------------------------------------------------------------
DATA_SOURCE = os.environ.get('DATA_SOURCE', 'csv').lower()

# ---------------------------------------------------------------------------
# Database configuration
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "instapphost.eecs.berkeley.edu"),
    "user": os.environ.get("DB_USER", "ee106a"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "ee106a"),
}


def get_db_connection():
    """Return a new pymysql connection using shared DB_CONFIG."""
    return pymysql.connect(**DB_CONFIG)

# ---------------------------------------------------------------------------
# Station groupings
# ---------------------------------------------------------------------------
TURTLEBOT_STATIONS = {1, 2, 3, 4, 5, 11}
UR7E_STATIONS = {6, 7, 8, 9, 10}

# ---------------------------------------------------------------------------
# Paths (absolute, based on this file's location)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

QUEUE_UR7E_CSV_PATH = os.path.join(BASE_DIR, 'csv', 'queue_ur7e.csv')
QUEUE_TURTLEBOT_CSV_PATH = os.path.join(BASE_DIR, 'csv', 'queue_turtlebot.csv')
MANUAL_OVERRIDES_CSV_PATH = os.path.join(BASE_DIR, 'csv', 'manual_overrides.csv')
PENDING_CLAIMS_CSV_PATH = os.path.join(BASE_DIR, 'csv', 'pending_claims.csv')
STATION_STATUS_CSV_PATH = os.path.join(BASE_DIR, 'csv', 'station_status.csv')
PREVIOUS_STATES_PATH = os.path.join(BASE_DIR, 'csv', 'previous_states.json')
LAST_UPDATE_FILE = os.path.join(BASE_DIR, 'csv', 'last_update.txt')
CALENDAR_PATH = os.path.join(BASE_DIR, 'uploads', 'course_calendar.ics')
ADMIN_USERS_FILE = os.path.join(BASE_DIR, 'admin_users.txt')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns for SVG desk color replacement
# ---------------------------------------------------------------------------
DESK_REGEX = {
    str(n): re.compile(rf'(<path id="desk-{n}"[^>]*fill=")[^"]*(")')
    for n in list(TURTLEBOT_STATIONS | UR7E_STATIONS)
}

# ---------------------------------------------------------------------------
# Advisory file locking
# ---------------------------------------------------------------------------
@contextmanager
def file_lock(path):
    """Advisory file lock using fcntl.

    Acquires an exclusive lock on ``path.lock`` before yielding, and releases
    it on exit.  fcntl locks are automatically released if the process crashes,
    so stale-lock cleanup is not needed.
    """
    lock_path = path + '.lock'
    os.makedirs(os.path.dirname(lock_path) or '.', exist_ok=True)
    lock_fd = open(lock_path, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


# ---------------------------------------------------------------------------
# Calendar event parsing – cached
# ---------------------------------------------------------------------------
_calendar_cache = {'result': None, 'time': 0, 'mtime': 0}
_CALENDAR_CACHE_TTL = 30  # seconds


def get_current_lab_event():
    """Return the current calendar event type and class.

    Returns a dict::

        {'type': 'lab_oh' | 'lab_section' | 'maintenance' | None,
         'class': '106A' | '106B' | None}

    Results are cached for up to 30 s *and* invalidated when the ICS file's
    mtime changes (e.g. after an admin uploads a new calendar).
    """
    global _calendar_cache

    now_ts = time.time()

    # Fast-path: return cached result if still valid
    if _calendar_cache['result'] is not None:
        if now_ts - _calendar_cache['time'] < _CALENDAR_CACHE_TTL:
            try:
                mtime = os.path.getmtime(CALENDAR_PATH) if os.path.exists(CALENDAR_PATH) else 0
            except OSError:
                mtime = 0
            if mtime == _calendar_cache['mtime']:
                return _calendar_cache['result']

    result = _parse_calendar()

    try:
        mtime = os.path.getmtime(CALENDAR_PATH) if os.path.exists(CALENDAR_PATH) else 0
    except OSError:
        mtime = 0

    _calendar_cache = {'result': result, 'time': now_ts, 'mtime': mtime}
    return result


def _parse_calendar():
    """Heavy-lift: open, parse, and walk the ICS calendar."""
    if not os.path.exists(CALENDAR_PATH):
        return {'type': None, 'class': None}

    try:
        with open(CALENDAR_PATH, 'rb') as f:
            cal = Calendar.from_ical(f.read())
    except Exception as e:
        print(f"Error reading calendar: {e}")
        return {'type': None, 'class': None}

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
        elif ('106a' in summary_lower or '106b' in summary_lower
              or 'c106a' in summary_lower or 'c106b' in summary_lower) and 'lab' in summary_lower:
            event_type = 'lab_section'
        elif 'eecs' in summary_lower and ('106a' in summary_lower or '106b' in summary_lower):
            event_type = 'lab_section'
        else:
            continue

        # Determine which class
        if '106b' in summary_lower or 'c106b' in summary_lower:
            event_class = '106B'
        elif '106a' in summary_lower or 'c106a' in summary_lower:
            event_class = '106A'
        else:
            event_class = None

        dtstart = component.get('dtstart')
        dtend = component.get('dtend')

        if dtstart is None or dtend is None:
            continue

        start = dtstart.dt
        end = dtend.dt

        # Handle all-day events
        if not isinstance(start, datetime):
            start = datetime.combine(start, datetime.min.time()).replace(tzinfo=tz.tzlocal())
        if not isinstance(end, datetime):
            end = datetime.combine(end, datetime.max.time()).replace(tzinfo=tz.tzlocal())

        if start.tzinfo is None:
            start = start.replace(tzinfo=tz.tzlocal())
        if end.tzinfo is None:
            end = end.replace(tzinfo=tz.tzlocal())

        # Check for recurring events
        rrule_prop = component.get('rrule')
        if rrule_prop:
            try:
                rule = rrulestr(rrule_prop.to_ical().decode('utf-8'), dtstart=start)
                duration = end - start
                window_start = now - timedelta(days=1)
                window_end = now + timedelta(days=1)

                for occurrence in rule.between(window_start, window_end, inc=True):
                    if occurrence <= now <= occurrence + duration:
                        return {'type': event_type, 'class': event_class}
            except Exception as e:
                print(f"Error parsing rrule: {e}")

        if start <= now <= end:
            return {'type': event_type, 'class': event_class}

    return {'type': None, 'class': None}


# ---------------------------------------------------------------------------
# Convenience helpers (used by check_notifications.py and update_db.py)
# ---------------------------------------------------------------------------
def is_lab_oh_time():
    """True if we are currently in a Lab OH window."""
    return get_current_lab_event()['type'] == 'lab_oh'


def is_lab_section_time():
    """True if we are currently in a Lab Section."""
    return get_current_lab_event()['type'] == 'lab_section'


def is_maintenance_time():
    """True if we are currently in a maintenance window."""
    return get_current_lab_event()['type'] == 'maintenance'


def is_lab_active_time():
    """True during Lab OH *or* Lab Section (need frequent updates)."""
    return get_current_lab_event()['type'] in ('lab_oh', 'lab_section')


def is_queue_active_time():
    """True during 106A Lab OH or 106B Lab Section (queue-eligible times).

    Queues are only available during these specific sessions.
    Generic Lab OH (no class specified) is treated as 106A.
    """
    event = get_current_lab_event()
    etype = event['type']
    eclass = event['class']
    # 106A OH (or generic Lab OH, which is assumed to be 106A)
    if etype == 'lab_oh' and eclass != '106B':
        return True
    # 106B Lab Section
    if etype == 'lab_section' and eclass == '106B':
        return True
    return False


# ===========================================================================
# Data Access Layer
# ===========================================================================
# Each public function dispatches to a _csv or _db variant based on
# DATA_SOURCE.  The CSV variants preserve backward-compatible behaviour;
# the DB variants use MariaDB tables.
# ===========================================================================

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _queue_csv_path(queue_type):
    """Return the CSV path for the given queue type."""
    return QUEUE_TURTLEBOT_CSV_PATH if queue_type == 'turtlebot' else QUEUE_UR7E_CSV_PATH

PENDING_CLAIMS_FIELDS = ['email', 'name', 'station_type', 'station',
                         'claim_token', 'expires_at', 'confirmed']


# ---------------------------------------------------------------------------
# Manual overrides
# ---------------------------------------------------------------------------
def get_manual_overrides():
    """Return {station_num: bool} from manual overrides."""
    if DATA_SOURCE == 'database':
        return _get_manual_overrides_db()
    return _get_manual_overrides_csv()


def _get_manual_overrides_csv():
    overrides = {}
    if os.path.exists(MANUAL_OVERRIDES_CSV_PATH):
        try:
            with open(MANUAL_OVERRIDES_CSV_PATH, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    station = int(row['station'])
                    val = row['override_occupied'].lower()
                    if val in ('true', 'false'):
                        overrides[station] = (val == 'true')
        except Exception as e:
            print(f"Error reading manual overrides: {e}")
    return overrides


def _get_manual_overrides_db():
    overrides = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT station, override_occupied FROM manual_overrides")
        for station, override_occupied in cursor.fetchall():
            overrides[int(station)] = bool(override_occupied)
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error reading manual overrides from DB: {e}")
    return overrides


def set_manual_override(station, override_occupied):
    """Set or clear a manual override for a station.

    Args:
        station: station number
        override_occupied: True/False to set, None to clear

    Returns:
        (success: bool, message: str)
    """
    if DATA_SOURCE == 'database':
        return _set_manual_override_db(station, override_occupied)
    return _set_manual_override_csv(station, override_occupied)


def _set_manual_override_csv(station, override_occupied):
    try:
        with file_lock(MANUAL_OVERRIDES_CSV_PATH):
            overrides = {}
            if os.path.exists(MANUAL_OVERRIDES_CSV_PATH):
                with open(MANUAL_OVERRIDES_CSV_PATH, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        overrides[int(row['station'])] = row['override_occupied']

            if override_occupied is None:
                if station in overrides:
                    del overrides[station]
                    message = f'Cleared override for station {station}'
                else:
                    return False, 'No override exists for this station'
            else:
                overrides[station] = 'true' if override_occupied else 'false'
                message = f'Set station {station} override to {"occupied" if override_occupied else "available"}'

            with open(MANUAL_OVERRIDES_CSV_PATH, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['station', 'override_occupied'])
                writer.writeheader()
                for s, occupied in sorted(overrides.items()):
                    writer.writerow({'station': s, 'override_occupied': occupied})

        return True, message
    except Exception as e:
        return False, f'Error setting override: {e}'


def _set_manual_override_db(station, override_occupied):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if override_occupied is None:
            cursor.execute("DELETE FROM manual_overrides WHERE station = %s", (station,))
            if cursor.rowcount == 0:
                cursor.close()
                conn.close()
                return False, 'No override exists for this station'
            message = f'Cleared override for station {station}'
        else:
            cursor.execute("""
                INSERT INTO manual_overrides (station, override_occupied)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE override_occupied = %s
            """, (station, override_occupied, override_occupied))
            message = f'Set station {station} override to {"occupied" if override_occupied else "available"}'
        conn.commit()
        cursor.close()
        conn.close()
        return True, message
    except Exception as e:
        return False, f'Error setting override: {e}'


# ---------------------------------------------------------------------------
# Queue operations
# ---------------------------------------------------------------------------
def get_queue(queue_type):
    """Return ordered list of {name, email} for queue_type."""
    if DATA_SOURCE == 'database':
        return _get_queue_db(queue_type)
    return _get_queue_csv(queue_type)


def _get_queue_csv(queue_type):
    csv_path = _queue_csv_path(queue_type)
    entries = []
    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                entries.append({'name': row['name'], 'email': row['email']})
    except FileNotFoundError:
        pass
    return entries


def _get_queue_db(queue_type):
    entries = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, email FROM queues WHERE queue_type = %s ORDER BY position",
            (queue_type,))
        for name, email in cursor.fetchall():
            entries.append({'name': name, 'email': email})
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error reading queue from DB: {e}")
    return entries


def get_first_in_queue(queue_type):
    """Return first person in queue or None."""
    if DATA_SOURCE == 'database':
        return _get_first_in_queue_db(queue_type)
    return _get_first_in_queue_csv(queue_type)


def _get_first_in_queue_csv(queue_type):
    csv_path = _queue_csv_path(queue_type)
    if not os.path.exists(csv_path):
        return None
    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                return {'name': row['name'], 'email': row['email']}
    except Exception as e:
        print(f"Error reading queue: {e}")
    return None


def _get_first_in_queue_db(queue_type):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, email FROM queues WHERE queue_type = %s ORDER BY position LIMIT 1",
            (queue_type,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            return {'name': row[0], 'email': row[1]}
    except Exception as e:
        print(f"Error reading queue from DB: {e}")
    return None


def add_to_queue(queue_type, name, email):
    """Add a person to the end of the queue.

    Returns:
        (success: bool, error_msg: str | None)
    """
    if DATA_SOURCE == 'database':
        return _add_to_queue_db(queue_type, name, email)
    return _add_to_queue_csv(queue_type, name, email)


def _add_to_queue_csv(queue_type, name, email):
    csv_path = _queue_csv_path(queue_type)
    try:
        with file_lock(csv_path):
            existing = []
            if os.path.exists(csv_path):
                with open(csv_path, 'r') as f:
                    reader = csv.DictReader(f)
                    existing = list(reader)
                for entry in existing:
                    if entry['email'] == email:
                        return False, 'You are already in this queue'
            else:
                os.makedirs(os.path.dirname(csv_path), exist_ok=True)

            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['name', 'email'])
                writer.writeheader()
                writer.writerows(existing)
                writer.writerow({'name': name, 'email': email})
        return True, None
    except Exception as e:
        return False, f'Error adding to queue: {e}'


def _add_to_queue_db(queue_type, name, email):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Get next position
        cursor.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM queues WHERE queue_type = %s",
            (queue_type,))
        next_pos = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO queues (queue_type, position, name, email) VALUES (%s, %s, %s, %s)",
            (queue_type, next_pos, name, email))
        conn.commit()
        cursor.close()
        conn.close()
        return True, None
    except pymysql.IntegrityError:
        return False, 'You are already in this queue'
    except Exception as e:
        return False, f'Error adding to queue: {e}'


def remove_from_queue(queue_type, email):
    """Remove a person from the queue by email. Returns True if removed."""
    if DATA_SOURCE == 'database':
        return _remove_from_queue_db(queue_type, email)
    return _remove_from_queue_csv(queue_type, email)


def _remove_from_queue_csv(queue_type, email):
    csv_path = _queue_csv_path(queue_type)
    if not os.path.exists(csv_path):
        return False
    try:
        with file_lock(csv_path):
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                entries = list(reader)
            original_count = len(entries)
            entries = [e for e in entries if e['email'] != email]
            if len(entries) == original_count:
                return False
            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['name', 'email'])
                writer.writeheader()
                writer.writerows(entries)
        return True
    except Exception as e:
        print(f"Error removing from queue: {e}")
        return False


def _remove_from_queue_db(queue_type, email):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Get position of the entry being removed
        cursor.execute(
            "SELECT position FROM queues WHERE queue_type = %s AND email = %s",
            (queue_type, email))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return False
        removed_pos = row[0]
        cursor.execute(
            "DELETE FROM queues WHERE queue_type = %s AND email = %s",
            (queue_type, email))
        # Shift positions down for entries after the removed one
        cursor.execute(
            "UPDATE queues SET position = position - 1 WHERE queue_type = %s AND position > %s",
            (queue_type, removed_pos))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error removing from queue: {e}")
        return False


def reorder_queue(queue_type, email, direction):
    """Move a queue entry up or down.

    Returns:
        (success: bool, error_msg: str | None)
    """
    if DATA_SOURCE == 'database':
        return _reorder_queue_db(queue_type, email, direction)
    return _reorder_queue_csv(queue_type, email, direction)


def _reorder_queue_csv(queue_type, email, direction):
    csv_path = _queue_csv_path(queue_type)
    if not os.path.exists(csv_path):
        return False, 'Queue does not exist'
    try:
        with file_lock(csv_path):
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                entries = list(reader)

            index = None
            for i, entry in enumerate(entries):
                if entry['email'] == email:
                    index = i
                    break

            if index is None:
                return False, 'User not found in queue'

            if direction == 'up':
                if index == 0:
                    return False, 'Already at the top of the queue'
                entries[index], entries[index - 1] = entries[index - 1], entries[index]
            else:
                if index == len(entries) - 1:
                    return False, 'Already at the bottom of the queue'
                entries[index], entries[index + 1] = entries[index + 1], entries[index]

            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['name', 'email'])
                writer.writeheader()
                writer.writerows(entries)
        return True, None
    except Exception as e:
        return False, f'Error updating queue: {e}'


def _reorder_queue_db(queue_type, email, direction):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, position FROM queues WHERE queue_type = %s AND email = %s",
            (queue_type, email))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return False, 'User not found in queue'

        entry_id, pos = row

        if direction == 'up':
            cursor.execute(
                "SELECT id, position FROM queues WHERE queue_type = %s AND position < %s "
                "ORDER BY position DESC LIMIT 1",
                (queue_type, pos))
        else:
            cursor.execute(
                "SELECT id, position FROM queues WHERE queue_type = %s AND position > %s "
                "ORDER BY position ASC LIMIT 1",
                (queue_type, pos))

        neighbor = cursor.fetchone()
        if not neighbor:
            cursor.close()
            conn.close()
            msg = 'Already at the top of the queue' if direction == 'up' else 'Already at the bottom of the queue'
            return False, msg

        neighbor_id, neighbor_pos = neighbor
        # Swap positions
        cursor.execute("UPDATE queues SET position = %s WHERE id = %s", (neighbor_pos, entry_id))
        cursor.execute("UPDATE queues SET position = %s WHERE id = %s", (pos, neighbor_id))
        conn.commit()
        cursor.close()
        conn.close()
        return True, None
    except Exception as e:
        return False, f'Error updating queue: {e}'


def clear_queue(queue_type):
    """Clear all entries from a queue. Returns True on success."""
    if DATA_SOURCE == 'database':
        return _clear_queue_db(queue_type)
    return _clear_queue_csv(queue_type)


def _clear_queue_csv(queue_type):
    csv_path = _queue_csv_path(queue_type)
    try:
        with file_lock(csv_path):
            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['name', 'email'])
                writer.writeheader()
        return True
    except Exception as e:
        print(f"Error clearing queue: {e}")
        return False


def _clear_queue_db(queue_type):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM queues WHERE queue_type = %s", (queue_type,))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error clearing queue from DB: {e}")
        return False


def clear_all_queues():
    """Clear both turtlebot and ur7e queues."""
    clear_queue('turtlebot')
    clear_queue('ur7e')


def reposition_queue(queue_type, email, new_index):
    """Move a queue entry to a specific 0-based position.

    Returns:
        (success: bool, error_msg: str | None)
    """
    if DATA_SOURCE == 'database':
        return _reposition_queue_db(queue_type, email, new_index)
    return _reposition_queue_csv(queue_type, email, new_index)


def _reposition_queue_csv(queue_type, email, new_index):
    csv_path = _queue_csv_path(queue_type)
    if not os.path.exists(csv_path):
        return False, 'Queue does not exist'
    try:
        with file_lock(csv_path):
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                entries = list(reader)

            old_index = None
            entry_to_move = None
            for i, entry in enumerate(entries):
                if entry['email'] == email:
                    old_index = i
                    entry_to_move = entry
                    break

            if old_index is None:
                return False, 'User not found in queue'

            if new_index >= len(entries):
                new_index = len(entries) - 1

            entries.pop(old_index)
            entries.insert(new_index, entry_to_move)

            with open(csv_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['name', 'email'])
                writer.writeheader()
                writer.writerows(entries)
        return True, None
    except Exception as e:
        return False, f'Error updating queue: {e}'


def _reposition_queue_db(queue_type, email, new_index):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get all entries ordered by position
        cursor.execute(
            "SELECT id, email, position FROM queues WHERE queue_type = %s ORDER BY position",
            (queue_type,))
        rows = list(cursor.fetchall())

        old_index = None
        for i, (rid, remail, rpos) in enumerate(rows):
            if remail == email:
                old_index = i
                break

        if old_index is None:
            cursor.close()
            conn.close()
            return False, 'User not found in queue'

        if new_index >= len(rows):
            new_index = len(rows) - 1

        # Reorder in memory
        entry = rows.pop(old_index)
        rows.insert(new_index, entry)

        # Reassign positions
        for pos, (rid, remail, _) in enumerate(rows):
            cursor.execute("UPDATE queues SET position = %s WHERE id = %s", (pos, rid))

        conn.commit()
        cursor.close()
        conn.close()
        return True, None
    except Exception as e:
        return False, f'Error updating queue: {e}'


# ---------------------------------------------------------------------------
# Claim operations
# ---------------------------------------------------------------------------
def get_claimed_stations():
    """Return {station_num: claim_info} for active claims."""
    if DATA_SOURCE == 'database':
        return _get_claimed_stations_db()
    return _get_claimed_stations_csv()


def _get_claimed_stations_csv():
    claimed = {}
    if not os.path.exists(PENDING_CLAIMS_CSV_PATH):
        return claimed
    try:
        with open(PENDING_CLAIMS_CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'station' not in row or not row['station']:
                    continue
                is_confirmed = row.get('confirmed', '').lower() == 'true'
                expires_at = datetime.fromisoformat(row['expires_at'])
                if is_confirmed or expires_at > datetime.now():
                    station = int(row['station'])
                    time_remaining = int((expires_at - datetime.now()).total_seconds())
                    claimed[station] = {
                        'name': row['name'],
                        'expires_at': row['expires_at'],
                        'time_remaining': max(0, time_remaining),
                        'confirmed': is_confirmed
                    }
    except Exception as e:
        print(f"Error reading pending claims: {e}")
    return claimed


def _get_claimed_stations_db():
    claimed = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT station, name, expires_at, confirmed FROM pending_claims")
        now = datetime.now()
        for station, name, expires_at, confirmed in cursor.fetchall():
            is_confirmed = bool(confirmed)
            if is_confirmed or expires_at > now:
                time_remaining = int((expires_at - now).total_seconds())
                claimed[int(station)] = {
                    'name': name,
                    'expires_at': expires_at.isoformat(),
                    'time_remaining': max(0, time_remaining),
                    'confirmed': is_confirmed
                }
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error reading pending claims from DB: {e}")
    return claimed


def get_pending_claim(token):
    """Get a pending claim by token, or None if not found / expired."""
    if DATA_SOURCE == 'database':
        return _get_pending_claim_db(token)
    return _get_pending_claim_csv(token)


def _get_pending_claim_csv(token):
    if not os.path.exists(PENDING_CLAIMS_CSV_PATH):
        return None
    try:
        with open(PENDING_CLAIMS_CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['claim_token'] == token:
                    expires_at = datetime.fromisoformat(row['expires_at'])
                    if expires_at > datetime.now():
                        return row
                    return None
    except Exception as e:
        print(f"Error reading pending claims: {e}")
    return None


def _get_pending_claim_db(token):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT email, name, station_type, station, claim_token, expires_at, confirmed "
            "FROM pending_claims WHERE claim_token = %s", (token,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return None
        email, name, station_type, station, claim_token, expires_at, confirmed = row
        if expires_at <= datetime.now():
            return None
        return {
            'email': email,
            'name': name,
            'station_type': station_type,
            'station': str(station),
            'claim_token': claim_token,
            'expires_at': expires_at.isoformat(),
            'confirmed': 'true' if confirmed else 'false',
        }
    except Exception as e:
        print(f"Error reading pending claim from DB: {e}")
        return None


def get_all_pending_claims():
    """Return list of all pending claim dicts."""
    if DATA_SOURCE == 'database':
        return _get_all_pending_claims_db()
    return _get_all_pending_claims_csv()


def _get_all_pending_claims_csv():
    claims = []
    if not os.path.exists(PENDING_CLAIMS_CSV_PATH):
        return claims
    try:
        with open(PENDING_CLAIMS_CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                claims.append(row)
    except Exception as e:
        print(f"Error reading pending claims: {e}")
    return claims


def _get_all_pending_claims_db():
    claims = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT email, name, station_type, station, claim_token, expires_at, confirmed "
            "FROM pending_claims")
        for email, name, station_type, station, claim_token, expires_at, confirmed in cursor.fetchall():
            claims.append({
                'email': email,
                'name': name,
                'station_type': station_type,
                'station': str(station),
                'claim_token': claim_token,
                'expires_at': expires_at.isoformat(),
                'confirmed': 'true' if confirmed else 'false',
            })
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error reading pending claims from DB: {e}")
    return claims


def create_pending_claim(email, name, station_type, station, token, expires_at):
    """Save a new pending claim. Returns True on success."""
    if DATA_SOURCE == 'database':
        return _create_pending_claim_db(email, name, station_type, station, token, expires_at)
    return _create_pending_claim_csv(email, name, station_type, station, token, expires_at)


def _create_pending_claim_csv(email, name, station_type, station, token, expires_at):
    try:
        with file_lock(PENDING_CLAIMS_CSV_PATH):
            claims = []
            if os.path.exists(PENDING_CLAIMS_CSV_PATH):
                with open(PENDING_CLAIMS_CSV_PATH, 'r') as f:
                    reader = csv.DictReader(f)
                    claims = list(reader)
            claims.append({
                'email': email,
                'name': name,
                'station_type': station_type,
                'station': station,
                'claim_token': token,
                'expires_at': expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at,
                'confirmed': 'false',
            })
            with open(PENDING_CLAIMS_CSV_PATH, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=PENDING_CLAIMS_FIELDS)
                writer.writeheader()
                writer.writerows(claims)
        return True
    except Exception as e:
        print(f"Error creating pending claim: {e}")
        return False


def _create_pending_claim_db(email, name, station_type, station, token, expires_at):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pending_claims (email, name, station_type, station, claim_token, expires_at, confirmed)
            VALUES (%s, %s, %s, %s, %s, %s, FALSE)
        """, (email, name, station_type, station, token,
              expires_at if isinstance(expires_at, datetime) else datetime.fromisoformat(expires_at)))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Error creating pending claim in DB: {e}")
        return False


def delete_pending_claim(token):
    """Delete a pending claim by token. Returns True if deleted."""
    if DATA_SOURCE == 'database':
        return _delete_pending_claim_db(token)
    return _delete_pending_claim_csv(token)


def _delete_pending_claim_csv(token):
    if not os.path.exists(PENDING_CLAIMS_CSV_PATH):
        return False
    try:
        with file_lock(PENDING_CLAIMS_CSV_PATH):
            with open(PENDING_CLAIMS_CSV_PATH, 'r') as f:
                reader = csv.DictReader(f)
                claims = [row for row in reader if row['claim_token'] != token]
            with open(PENDING_CLAIMS_CSV_PATH, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=PENDING_CLAIMS_FIELDS)
                writer.writeheader()
                writer.writerows(claims)
        return True
    except Exception as e:
        print(f"Error deleting pending claim: {e}")
        return False


def _delete_pending_claim_db(token):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pending_claims WHERE claim_token = %s", (token,))
        deleted = cursor.rowcount > 0
        conn.commit()
        cursor.close()
        conn.close()
        return deleted
    except Exception as e:
        print(f"Error deleting pending claim from DB: {e}")
        return False


def mark_claim_confirmed(token):
    """Mark a claim as confirmed. Returns True on success."""
    if DATA_SOURCE == 'database':
        return _mark_claim_confirmed_db(token)
    return _mark_claim_confirmed_csv(token)


def _mark_claim_confirmed_csv(token):
    if not os.path.exists(PENDING_CLAIMS_CSV_PATH):
        return False
    try:
        with file_lock(PENDING_CLAIMS_CSV_PATH):
            with open(PENDING_CLAIMS_CSV_PATH, 'r') as f:
                reader = csv.DictReader(f)
                claims = list(reader)
            for claim in claims:
                if claim['claim_token'] == token:
                    claim['confirmed'] = 'true'
            with open(PENDING_CLAIMS_CSV_PATH, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=PENDING_CLAIMS_FIELDS)
                writer.writeheader()
                writer.writerows(claims)
        return True
    except Exception as e:
        print(f"Error marking claim confirmed: {e}")
        return False


def _mark_claim_confirmed_db(token):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE pending_claims SET confirmed = TRUE WHERE claim_token = %s",
            (token,))
        conn.commit()
        updated = cursor.rowcount > 0
        cursor.close()
        conn.close()
        return updated
    except Exception as e:
        print(f"Error marking claim confirmed in DB: {e}")
        return False


def save_pending_claims(claims):
    """Bulk-write all pending claims (used by check_notifications cleanup)."""
    if DATA_SOURCE == 'database':
        return _save_pending_claims_db(claims)
    return _save_pending_claims_csv(claims)


def _save_pending_claims_csv(claims):
    try:
        with file_lock(PENDING_CLAIMS_CSV_PATH):
            with open(PENDING_CLAIMS_CSV_PATH, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=PENDING_CLAIMS_FIELDS)
                writer.writeheader()
                writer.writerows(claims)
    except Exception as e:
        print(f"Error saving pending claims: {e}")


def _save_pending_claims_db(claims):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pending_claims")
        for claim in claims:
            confirmed = claim.get('confirmed', 'false')
            if isinstance(confirmed, str):
                confirmed = confirmed.lower() == 'true'
            expires_at = claim['expires_at']
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            cursor.execute("""
                INSERT INTO pending_claims (email, name, station_type, station, claim_token, expires_at, confirmed)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (claim['email'], claim['name'], claim['station_type'],
                  int(claim['station']), claim['claim_token'], expires_at, confirmed))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error saving pending claims to DB: {e}")


# ---------------------------------------------------------------------------
# Station state operations (used by check_notifications and app.py)
# ---------------------------------------------------------------------------
def get_station_states():
    """Return {station: occupied_bool} with manual overrides applied."""
    if DATA_SOURCE == 'database':
        return _get_station_states_db()
    return _get_station_states_csv()


def _get_station_states_csv():
    states = {}
    if not os.path.exists(STATION_STATUS_CSV_PATH):
        return states
    try:
        with open(STATION_STATUS_CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                station = int(row['station'])
                occ_val = row['occupied'].lower().strip()
                states[station] = occ_val in ('true', '1', 'yes')
    except Exception as e:
        print(f"Error reading station status: {e}")
    overrides = get_manual_overrides()
    for station, override_occupied in overrides.items():
        states[station] = override_occupied
    return states


def _get_station_states_db():
    states = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT station, occupied FROM stations")
        for station, occupied in cursor.fetchall():
            states[int(station)] = bool(occupied)
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error reading station states from DB: {e}")
    overrides = get_manual_overrides()
    for station, override_occupied in overrides.items():
        states[station] = override_occupied
    return states


def get_freed_stations():
    """Return set of station numbers that were occupied but are now free.

    In DB mode, uses previous_occupied column. In CSV mode, uses previous_states.json.
    """
    if DATA_SOURCE == 'database':
        return _get_freed_stations_db()
    return _get_freed_stations_csv()


def _get_freed_stations_csv():
    """CSV mode: compare current states with previous_states.json."""
    import json
    current = get_station_states()
    previous = {}
    if os.path.exists(PREVIOUS_STATES_PATH):
        try:
            with open(PREVIOUS_STATES_PATH, 'r') as f:
                data = json.load(f)
                previous = {int(k): v for k, v in data.items()}
        except Exception:
            pass

    freed = set()
    for station, occupied in current.items():
        if previous.get(station, occupied) and not occupied:
            freed.add(station)
    return freed


def _get_freed_stations_db():
    freed = set()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT station FROM stations "
            "WHERE previous_occupied = TRUE AND occupied = FALSE")
        for (station,) in cursor.fetchall():
            freed.add(int(station))
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error getting freed stations from DB: {e}")
    # Also apply overrides — if a station was freed but overridden to occupied, exclude it
    overrides = get_manual_overrides()
    for station, override_occupied in overrides.items():
        if override_occupied and station in freed:
            freed.discard(station)
        elif not override_occupied and station not in freed:
            # Override says available — but only if it was previously occupied
            pass  # Don't add to freed; override doesn't trigger notifications
    return freed


def get_previous_states():
    """Load previous states from JSON file (CSV mode only)."""
    import json
    if not os.path.exists(PREVIOUS_STATES_PATH):
        return {}
    try:
        with open(PREVIOUS_STATES_PATH, 'r') as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    except Exception as e:
        print(f"Error reading previous states: {e}")
        return {}


def save_states(states):
    """Save current states for next comparison (CSV mode only)."""
    import json
    try:
        with open(PREVIOUS_STATES_PATH, 'w') as f:
            json.dump(states, f)
    except Exception as e:
        print(f"Error saving states: {e}")
