#!/usr/bin/env python3
"""
Notification checker - runs every 10 seconds via systemd.
Only does work during Lab OH times.
"""
import os
import sys
import json
import csv
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets

from icalendar import Calendar
from dateutil.rrule import rrulestr
from dateutil import tz

# Paths (relative to script directory)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CALENDAR_PATH = os.path.join(SCRIPT_DIR, 'uploads/course_calendar.ics')
STATES_PATH = os.path.join(SCRIPT_DIR, 'csv/previous_states.json')
CLAIMS_PATH = os.path.join(SCRIPT_DIR, 'csv/pending_claims.csv')
QUEUE_TURTLEBOT_PATH = os.path.join(SCRIPT_DIR, 'csv/queue_turtlebot.csv')
QUEUE_UR7E_PATH = os.path.join(SCRIPT_DIR, 'csv/queue_ur7e.csv')
STATION_STATUS_PATH = os.path.join(SCRIPT_DIR, 'csv/station_status.csv')
MANUAL_OVERRIDES_PATH = os.path.join(SCRIPT_DIR, 'csv/manual_overrides.csv')

# Station groupings
TURTLEBOT_STATIONS = {1, 2, 3, 4, 5, 11}
UR7E_STATIONS = {6, 7, 8, 9, 10}

# Configuration from environment
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
GMAIL_SENDER = os.environ.get('GMAIL_SENDER', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

# Claim expiry time in minutes
CLAIM_EXPIRY_MINUTES = 5


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
            # Catch patterns like "[EECS C106A] - Satwik, Ishan" (lab without explicit "Lab" word)
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


def is_lab_section_time():
    """Check if current time is during a Lab Section (class in session)."""
    return get_current_lab_event() == 'lab_section'


def is_maintenance_time():
    """Check if current time is during Lab Maintenance."""
    return get_current_lab_event() == 'maintenance'


def get_manual_overrides():
    """Get manual station overrides from CSV file."""
    overrides = {}
    if os.path.exists(MANUAL_OVERRIDES_PATH):
        try:
            with open(MANUAL_OVERRIDES_PATH, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    station = int(row['station'])
                    override_occupied = row['override_occupied'].lower()
                    if override_occupied in ['true', 'false']:
                        overrides[station] = (override_occupied == 'true')
        except Exception as e:
            print(f"Error reading manual overrides: {e}")
    return overrides


def get_current_states():
    """Get current station occupied states from CSV, applying manual overrides."""
    states = {}
    if not os.path.exists(STATION_STATUS_PATH):
        return states

    try:
        with open(STATION_STATUS_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                station = int(row['station'])
                # Handle both 1/0 and true/false formats
                occ_val = row['occupied'].lower().strip()
                occupied = occ_val in ('true', '1', 'yes')
                states[station] = occupied
    except Exception as e:
        print(f"Error reading station status: {e}")

    # Apply manual overrides
    overrides = get_manual_overrides()
    for station, override_occupied in overrides.items():
        states[station] = override_occupied

    return states


def get_previous_states():
    """Load previous states from JSON file."""
    if not os.path.exists(STATES_PATH):
        return {}

    try:
        with open(STATES_PATH, 'r') as f:
            data = json.load(f)
            # Convert string keys back to int
            return {int(k): v for k, v in data.items()}
    except Exception as e:
        print(f"Error reading previous states: {e}")
        return {}


def save_states(states):
    """Save current states for next comparison."""
    try:
        with open(STATES_PATH, 'w') as f:
            json.dump(states, f)
    except Exception as e:
        print(f"Error saving states: {e}")


def get_first_in_queue(station_type):
    """Get first person in queue for station type."""
    csv_path = QUEUE_TURTLEBOT_PATH if station_type == 'turtlebot' else QUEUE_UR7E_PATH

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


def get_pending_claims():
    """Get all pending claims from CSV file."""
    claims = []
    if not os.path.exists(CLAIMS_PATH):
        return claims

    try:
        with open(CLAIMS_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                claims.append(row)
    except Exception as e:
        print(f"Error reading pending claims: {e}")

    return claims


def save_pending_claims(claims):
    """Save pending claims to CSV file."""
    try:
        with open(CLAIMS_PATH, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['email', 'name', 'station_type', 'station', 'claim_token', 'expires_at', 'confirmed'])
            writer.writeheader()
            writer.writerows(claims)
    except Exception as e:
        print(f"Error saving pending claims: {e}")


def has_pending_claim(station_type):
    """Check if there's already a pending claim for this station type."""
    claims = get_pending_claims()
    now = datetime.now()

    for claim in claims:
        if claim['station_type'] == station_type:
            expires_at = datetime.fromisoformat(claim['expires_at'])
            if expires_at > now:
                return True

    return False


def create_pending_claim(email, name, station_type, station):
    """Create claim with 5-min expiry, return token."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(minutes=CLAIM_EXPIRY_MINUTES)

    claims = get_pending_claims()
    claims.append({
        'email': email,
        'name': name,
        'station_type': station_type,
        'station': station,
        'claim_token': token,
        'expires_at': expires_at.isoformat(),
        'confirmed': 'false'
    })
    save_pending_claims(claims)

    return token


def remove_from_queue(station_type, email):
    """Remove a person from the queue by email."""
    csv_path = QUEUE_TURTLEBOT_PATH if station_type == 'turtlebot' else QUEUE_UR7E_PATH

    if not os.path.exists(csv_path):
        return

    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            entries = [row for row in reader if row['email'] != email]

        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['name', 'email'])
            writer.writeheader()
            writer.writerows(entries)
    except Exception as e:
        print(f"Error removing from queue: {e}")


def send_notification_email(to_email, to_name, station_type, station, claim_token):
    """Send email via Gmail SMTP."""
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        print(f"Email not configured. Would notify {to_email} about station {station}.")
        return False

    claim_url = f"{BASE_URL}/claim/{claim_token}"
    station_display = "Turtlebot" if station_type == 'turtlebot' else "UR7e"

    subject = f"[EE106A] Station {station} ({station_display}) Available - Claim Now!"

    html_body = f"""
    <html>
    <body>
        <h2>Station {station} ({station_display}) is now available!</h2>
        <p>Hi {to_name},</p>
        <p>You're first in the {station_display} queue, and <strong>Station {station}</strong> just became available.</p>
        <p><strong>You have {CLAIM_EXPIRY_MINUTES} minutes to claim it!</strong></p>
        <p><a href="{claim_url}" style="background-color: #4CAF50; color: white; padding: 14px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">Claim Station {station}</a></p>
        <p>Or copy this link: {claim_url}</p>
        <p>If you don't claim within {CLAIM_EXPIRY_MINUTES} minutes, the next person in the queue will be notified.</p>
        <p>Best,<br>EE106A Lab System</p>
        <hr style="margin-top: 30px; border: none; border-top: 1px solid #ccc;">
        <p style="color: #666; font-size: 0.9em;">Go Patriots, Go Celtics, Nobody is Illegal on Stolen Land, Love is Love, Black Lives Matter</p>
    </body>
    </html>
    """

    text_body = f"""
Station {station} ({station_display}) is now available!

Hi {to_name},

You're first in the {station_display} queue, and Station {station} just became available.

You have {CLAIM_EXPIRY_MINUTES} minutes to claim it!

Click here to claim: {claim_url}

If you don't claim within {CLAIM_EXPIRY_MINUTES} minutes, the next person in the queue will be notified.

Best,
EE106A Lab System

---
Go Patriots, Go Celtics, Nobody is Illegal on Stolen Land, Love is Love, Black Lives Matter
    """

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = GMAIL_SENDER
    msg['To'] = to_email

    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, to_email, msg.as_string())
        print(f"Sent notification email to {to_email} for {station_type}")
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False


def check_expired_claims():
    """Find and handle expired claims - notify next person in queue."""
    claims = get_pending_claims()
    current_states = get_current_states()
    now = datetime.now()
    active_claims = []
    expired_by_type = {}

    for claim in claims:
        is_confirmed = claim.get('confirmed', '').lower() == 'true'
        expires_at = datetime.fromisoformat(claim['expires_at'])
        station = int(claim.get('station', 0)) if claim.get('station') else None

        # Check if station is now occupied (someone logged in)
        station_occupied = station and current_states.get(station, False)

        if station_occupied and is_confirmed:
            # Confirmed claim + station occupied = they arrived, remove claim
            print(f"Confirmed claim cleared - {claim['email']} logged into station {station}")
            continue  # Don't keep this claim

        if is_confirmed:
            # Confirmed claims stay active until station is occupied
            active_claims.append(claim)
        elif expires_at > now:
            # Unconfirmed claim still has time
            active_claims.append(claim)
        else:
            # Unconfirmed claim expired - track by station type
            station_type = claim['station_type']
            if station_type not in expired_by_type:
                expired_by_type[station_type] = []
            expired_by_type[station_type].append(claim)
            print(f"Claim expired for {claim['email']} ({station_type})")

    # Save only active claims
    save_pending_claims(active_claims)

    # For each expired claim type, remove from queue and notify next person
    for station_type, expired_claims in expired_by_type.items():
        for claim in expired_claims:
            # Remove the person who didn't claim from the queue
            remove_from_queue(station_type, claim['email'])

        # Only notify next if there isn't already an active claim for this type
        has_active = any(c['station_type'] == station_type for c in active_claims)
        if not has_active:
            # Check if there are still stations available
            stations = TURTLEBOT_STATIONS if station_type == 'turtlebot' else UR7E_STATIONS
            # Find first available station of this type
            available_station = None
            for s in sorted(stations):
                if not current_states.get(s, True):
                    available_station = s
                    break

            if available_station:
                # Notify next person in queue
                person = get_first_in_queue(station_type)
                if person:
                    token = create_pending_claim(person['email'], person['name'], station_type, available_station)
                    send_notification_email(person['email'], person['name'], station_type, available_station, token)


def main():
    """Main notification check loop."""
    # Quick exit if not Lab OH
    if not is_lab_oh_time():
        return

    print(f"Running notification check at {datetime.now()}")

    # Check for expired claims first
    check_expired_claims()

    # Get states
    current = get_current_states()
    previous = get_previous_states()

    # On first run, just save states without notifications
    if not previous:
        print("First run - saving initial states")
        save_states(current)
        return

    # Detect freed stations (was occupied, now available)
    for station, occupied in current.items():
        prev_occupied = previous.get(station, occupied)
        if prev_occupied and not occupied:
            station_type = 'turtlebot' if station in TURTLEBOT_STATIONS else 'ur7e'
            print(f"Station {station} ({station_type}) became available")

            # Check no pending claim for this type
            if not has_pending_claim(station_type):
                person = get_first_in_queue(station_type)
                if person:
                    token = create_pending_claim(person['email'], person['name'], station_type, station)
                    send_notification_email(person['email'], person['name'], station_type, station, token)
            else:
                print(f"Already have pending claim for {station_type}, skipping notification")

    # Save states for next run
    save_states(current)


if __name__ == '__main__':
    main()
