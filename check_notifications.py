#!/usr/bin/env python3
"""
Notification checker - runs every 10 seconds via systemd.
Only does work during Lab OH times.
"""
import os
import json
import csv
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets

from lab_utils import (
    TURTLEBOT_STATIONS, UR7E_STATIONS,
    QUEUE_TURTLEBOT_CSV_PATH, QUEUE_UR7E_CSV_PATH,
    PENDING_CLAIMS_CSV_PATH, STATION_STATUS_CSV_PATH,
    PREVIOUS_STATES_PATH, MANUAL_OVERRIDES_CSV_PATH,
    is_lab_oh_time, get_manual_overrides, file_lock,
)

# Configuration from environment
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
GMAIL_SENDER = os.environ.get('GMAIL_SENDER', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

# Claim expiry time in minutes
CLAIM_EXPIRY_MINUTES = 5


def get_current_states():
    """Get current station occupied states from CSV, applying manual overrides."""
    states = {}
    if not os.path.exists(STATION_STATUS_CSV_PATH):
        return states

    try:
        with open(STATION_STATUS_CSV_PATH, 'r') as f:
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
    if not os.path.exists(PREVIOUS_STATES_PATH):
        return {}

    try:
        with open(PREVIOUS_STATES_PATH, 'r') as f:
            data = json.load(f)
            # Convert string keys back to int
            return {int(k): v for k, v in data.items()}
    except Exception as e:
        print(f"Error reading previous states: {e}")
        return {}


def save_states(states):
    """Save current states for next comparison."""
    try:
        with open(PREVIOUS_STATES_PATH, 'w') as f:
            json.dump(states, f)
    except Exception as e:
        print(f"Error saving states: {e}")


def get_first_in_queue(station_type):
    """Get first person in queue for station type."""
    csv_path = QUEUE_TURTLEBOT_CSV_PATH if station_type == 'turtlebot' else QUEUE_UR7E_CSV_PATH

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


def save_pending_claims(claims):
    """Save pending claims to CSV file."""
    try:
        with file_lock(PENDING_CLAIMS_CSV_PATH):
            with open(PENDING_CLAIMS_CSV_PATH, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['email', 'name', 'station_type', 'station', 'claim_token', 'expires_at', 'confirmed'])
                writer.writeheader()
                writer.writerows(claims)
    except Exception as e:
        print(f"Error saving pending claims: {e}")


def has_pending_claim(station_type, claims):
    """Check if there's already a pending claim for this station type."""
    now = datetime.now()

    for claim in claims:
        if claim['station_type'] == station_type:
            is_confirmed = claim.get('confirmed', '').lower() == 'true'
            expires_at = datetime.fromisoformat(claim['expires_at'])
            if is_confirmed or expires_at > now:
                return True

    return False


def person_has_active_claim(email, claims):
    """Check if a person already has any active claim (to avoid spam)."""
    now = datetime.now()

    for claim in claims:
        if claim['email'] == email:
            is_confirmed = claim.get('confirmed', '').lower() == 'true'
            expires_at = datetime.fromisoformat(claim['expires_at'])
            if is_confirmed or expires_at > now:
                return True

    return False


def create_pending_claim(email, name, station_type, station, claims):
    """Create claim with 5-min expiry, return token. Appends to claims list in-place."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(minutes=CLAIM_EXPIRY_MINUTES)

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
    csv_path = QUEUE_TURTLEBOT_CSV_PATH if station_type == 'turtlebot' else QUEUE_UR7E_CSV_PATH

    if not os.path.exists(csv_path):
        return

    try:
        with file_lock(csv_path):
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


def check_expired_claims(claims, current_states):
    """Find and handle expired claims - notify next person in queue.

    Mutates *claims* in-place (removes expired, keeps active).
    Returns the list of active claims.
    """
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
                    if person_has_active_claim(person['email'], active_claims):
                        print(f"Person {person['email']} already has an active claim, skipping")
                    else:
                        token = create_pending_claim(person['email'], person['name'], station_type, available_station, active_claims)
                        send_notification_email(person['email'], person['name'], station_type, available_station, token)

    return active_claims


def main():
    """Main notification check loop."""
    # Quick exit if not Lab OH
    if not is_lab_oh_time():
        return

    print(f"Running notification check at {datetime.now()}")

    # Read all shared state once at the start
    current = get_current_states()
    previous = get_previous_states()
    claims = get_pending_claims()

    # Check for expired claims first (mutates claims list)
    active_claims = check_expired_claims(claims, current)

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

            # Check no pending claim for this station type
            if not has_pending_claim(station_type, active_claims):
                person = get_first_in_queue(station_type)
                if person:
                    if person_has_active_claim(person['email'], active_claims):
                        print(f"Person {person['email']} already has an active claim, skipping")
                    else:
                        token = create_pending_claim(person['email'], person['name'], station_type, station, active_claims)
                        send_notification_email(person['email'], person['name'], station_type, station, token)
            else:
                print(f"Already have pending claim for {station_type}, skipping notification")

    # Save states for next run
    save_states(current)


if __name__ == '__main__':
    main()
