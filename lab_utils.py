"""Shared utilities for the lab availability site.

Consolidates duplicated logic across app.py, check_notifications.py, and update_db.py:
- Calendar event parsing (with caching)
- Station groupings
- CSV/file path constants
- Manual overrides reader
- Advisory file locking for CSV safety
"""
import os
import csv
import time
import fcntl
import re
from datetime import datetime, timedelta
from contextlib import contextmanager

from icalendar import Calendar
from dateutil.rrule import rrulestr
from dateutil import tz

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


# ---------------------------------------------------------------------------
# Manual overrides reader
# ---------------------------------------------------------------------------
def get_manual_overrides():
    """Return {station_num: bool} from the manual overrides CSV."""
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
