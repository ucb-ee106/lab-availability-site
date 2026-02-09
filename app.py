from flask import Flask, render_template, Response, request, jsonify, session, redirect, url_for
import csv
import os
import sys
import time
import hashlib
from datetime import datetime
from google.oauth2 import id_token
from google.auth.transport import requests
import secrets
from werkzeug.utils import secure_filename

import lab_utils
from lab_utils import (
    TURTLEBOT_STATIONS, UR7E_STATIONS,
    ADMIN_USERS_FILE, UPLOAD_FOLDER, CALENDAR_PATH,
    DESK_REGEX,
    DATA_SOURCE, get_db_connection,
    get_current_lab_event, get_manual_overrides,
    # Data access layer
    get_queue, add_to_queue, remove_from_queue, reorder_queue, reposition_queue,
    get_claimed_stations, get_pending_claim, mark_claim_confirmed,
    set_manual_override,
)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Google OAuth configuration
GOOGLE_CLIENT_ID = "22576242210-5dqoo2haju5f7t0qf5cnuq2hpbhstjpe.apps.googleusercontent.com"
ALLOWED_DOMAIN = "berkeley.edu"

# Lab states
STATE_OPEN = 'Open'
STATE_FULL = 'Full'
STATE_LAB_SECTION = 'Lab Section'
STATE_LAB_SECTION_106A = '106A Lab Section'
STATE_LAB_SECTION_106B = '106B Lab Section'
STATE_LAB_OH = 'Lab OH'
STATE_LAB_OH_106A = '106A Lab OH'
STATE_LAB_OH_106B = '106B Lab OH'

# State colors
STATE_COLORS = {
    STATE_OPEN: '#00A676',        # Green
    STATE_FULL: '#EB9486',        # Red
    STATE_LAB_SECTION: '#EB9486', # Red
    STATE_LAB_SECTION_106A: '#EB9486', # Red
    STATE_LAB_SECTION_106B: '#EB9486', # Red
    STATE_LAB_OH: '#F3DE8A',       # Yellow
    STATE_LAB_OH_106A: '#F3DE8A',  # Yellow
    STATE_LAB_OH_106B: '#F3DE8A'   # Yellow
}

# Desk colors (for SVG)
GREEN = '#00A676'
RED = '#EB9486'
YELLOW = '#F3DE8A'  # Claimed/pending

ALLOWED_EXTENSIONS = {'ics'}

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Parse command line arguments to select appropriate CSV file and state
STATE_OVERRIDE = None

if len(sys.argv) > 1:
    arg = sys.argv[1].lower()
    valid_states = ['lab_full', 'lab_full_oh', 'lab_oh', 'lab_section', 'lab_regular']

    if arg in valid_states:
        CSV_PATH = f'csv/station_status_{arg}.csv'

        # Map command line arg to state
        if arg == 'lab_full':
            STATE_OVERRIDE = STATE_FULL
        elif arg in ['lab_full_oh', 'lab_oh']:
            STATE_OVERRIDE = STATE_LAB_OH
        elif arg == 'lab_section':
            STATE_OVERRIDE = STATE_LAB_SECTION
        elif arg == 'lab_regular':
            STATE_OVERRIDE = STATE_OPEN
    else:
        print(f"Warning: Unknown state '{arg}'. Valid options: {', '.join(valid_states)}")
        CSV_PATH = 'csv/station_status.csv'
else:
    CSV_PATH = 'csv/station_status.csv'

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------
# SVG cache: avoid re-reading + regex-replacing on every request
_svg_cache = {'content': None, 'hash': None, 'time': 0}
_SVG_CACHE_TTL = 5  # seconds

# About page content (rarely changes)
_about_content = None

# Admin users cache
_admin_cache = {'emails': None, 'time': 0, 'mtime': 0}
_ADMIN_CACHE_TTL = 60  # seconds


def get_station_data():
    """Get station data from configured source (CSV or database), applying manual overrides."""
    if DATA_SOURCE == 'database':
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT station, occupied FROM stations")
        data = cursor.fetchall()
        cursor.close()
        conn.close()
    else:  # CSV mode
        data = []
        with open(CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                station_num = int(row['station'])
                is_occupied = row['occupied'].lower() == 'true'
                data.append((station_num, is_occupied))

    # Apply manual overrides
    overrides = get_manual_overrides()
    if overrides:
        data = [
            (station_num, overrides.get(station_num, is_occupied))
            for station_num, is_occupied in data
        ]

    return data


def determine_lab_state(total_available):
    """Determine the current lab state based on calendar and availability.

    Priority:
    1. Command line state override (for testing/demo)
    2. Maintenance (if calendar says maintenance)
    3. Lab Section (if calendar says lab section) - with class info
    4. Lab OH (if calendar says lab OH) - with class info
    5. Full (if no stations available)
    6. Open (default)
    """
    # Use state override if set via command line
    if STATE_OVERRIDE is not None:
        return STATE_OVERRIDE

    # Check calendar for current event (cached in lab_utils)
    event = get_current_lab_event()
    event_type = event['type']
    event_class = event['class']

    if event_type == 'maintenance':
        return STATE_FULL  # Treat maintenance as full/unavailable
    elif event_type == 'lab_section':
        if event_class == '106A':
            return STATE_LAB_SECTION_106A
        elif event_class == '106B':
            return STATE_LAB_SECTION_106B
        return STATE_LAB_SECTION
    elif event_type == 'lab_oh':
        if event_class == '106A':
            return STATE_LAB_OH_106A
        elif event_class == '106B':
            return STATE_LAB_OH_106B
        return STATE_LAB_OH

    # No calendar event - check if lab is full
    if total_available == 0:
        return STATE_FULL

    return STATE_OPEN

def get_queue_data():
    """Get queue data for both robot types."""
    return {
        'ur7e': get_queue('ur7e'),
        'turtlebot': get_queue('turtlebot'),
    }

def generate_lab_alt_text(station_data):
    """Generate descriptive alt text for screen readers."""
    turtlebot_open = []
    turtlebot_occupied = []
    ur7e_open = []
    ur7e_occupied = []

    for station_num, is_occupied in station_data:
        if station_num in TURTLEBOT_STATIONS:
            if is_occupied:
                turtlebot_occupied.append(station_num)
            else:
                turtlebot_open.append(station_num)
        elif station_num in UR7E_STATIONS:
            if is_occupied:
                ur7e_occupied.append(station_num)
            else:
                ur7e_open.append(station_num)

    # Build descriptive text
    parts = ["Cory 105 lab room layout showing station availability."]

    # Turtlebot stations
    if turtlebot_open:
        parts.append(f"Turtlebot stations open: {', '.join(map(str, sorted(turtlebot_open)))}.")
    if turtlebot_occupied:
        parts.append(f"Turtlebot stations occupied: {', '.join(map(str, sorted(turtlebot_occupied)))}.")

    # UR7e stations
    if ur7e_open:
        parts.append(f"UR7e stations open: {', '.join(map(str, sorted(ur7e_open)))}.")
    if ur7e_occupied:
        parts.append(f"UR7e stations occupied: {', '.join(map(str, sorted(ur7e_occupied)))}.")

    return " ".join(parts)

def allowed_file(filename):
    """Check if file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def is_admin_user(email):
    """Check if the given email is in the admin users list (cached)."""
    global _admin_cache

    now = time.time()

    try:
        mtime = os.path.getmtime(ADMIN_USERS_FILE) if os.path.exists(ADMIN_USERS_FILE) else 0
    except OSError:
        mtime = 0

    if (_admin_cache['emails'] is not None
            and now - _admin_cache['time'] < _ADMIN_CACHE_TTL
            and mtime == _admin_cache['mtime']):
        return email.lower() in _admin_cache['emails']

    if not os.path.exists(ADMIN_USERS_FILE):
        _admin_cache = {'emails': set(), 'time': now, 'mtime': mtime}
        return False

    try:
        with open(ADMIN_USERS_FILE, 'r') as f:
            admin_emails = set()
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    admin_emails.add(line.lower())
        _admin_cache = {'emails': admin_emails, 'time': now, 'mtime': mtime}
        return email.lower() in admin_emails
    except Exception as e:
        print(f"Error reading admin users file: {e}")
        return False


def get_lab_status():
    """Calculate lab status from configured data source."""
    turtlebots_available = 0
    ur7es_available = 0
    station_data = list(get_station_data())

    for station_num, is_occupied in station_data:
        if not is_occupied:
            if station_num in TURTLEBOT_STATIONS:
                turtlebots_available += 1
            elif station_num in UR7E_STATIONS:
                ur7es_available += 1

    total_available = turtlebots_available + ur7es_available
    state = determine_lab_state(total_available)

    # Show queues only when that robot type is full
    show_ur7e_queue = (ur7es_available == 0)
    show_turtlebot_queue = (turtlebots_available == 0)

    # Show book robot button only when lab is open
    show_book_robot = (state == STATE_OPEN)

    return {
        'state': state,
        'color': STATE_COLORS[state],
        'turtlebots_available': turtlebots_available,
        'ur7es_available': ur7es_available,
        'show_ur7e_queue': show_ur7e_queue,
        'show_turtlebot_queue': show_turtlebot_queue,
        'alt_text': generate_lab_alt_text(station_data),
        'show_book_robot': show_book_robot
    }


@app.route('/')
def index():
    lab_status = get_lab_status()
    queue = get_queue_data()
    return render_template('index.html', lab_status=lab_status, queue=queue)


@app.route('/about')
def about():
    """Display website_about.md content on the about page (cached)."""
    global _about_content
    if _about_content is None:
        with open('website_about.md', 'r') as f:
            _about_content = f.read()
    return render_template('about.html', readme_content=_about_content)


@app.route('/admin')
def admin():
    """Display admin page - requires authentication and admin privileges."""
    # Check if user is authenticated
    if 'user' not in session:
        return render_template('admin_unauthorized.html',
                             message='Please sign in to access the admin page.',
                             show_signin=True)

    # Check if user is an admin
    user_email = session['user']['email']
    if not is_admin_user(user_email):
        return render_template('admin_unauthorized.html',
                             message='Sorry! You do not have admin access. Please email Daniel Municio for admin access.',
                             show_signin=False)

    # User is authenticated and is an admin
    lab_status = get_lab_status()
    # Admin page always shows both queues for management
    lab_status['show_turtlebot_queue'] = True
    lab_status['show_ur7e_queue'] = True
    queue = get_queue_data()
    return render_template('admin.html', lab_status=lab_status, queue=queue)


@app.route('/admin/upload-calendar', methods=['GET', 'POST'])
def upload_calendar():
    """Upload course calendar ICS file - admin only."""
    # Check if user is authenticated
    if 'user' not in session:
        return redirect(url_for('admin'))

    # Check if user is an admin
    user_email = session['user']['email']
    if not is_admin_user(user_email):
        return redirect(url_for('admin'))

    if request.method == 'POST':
        # Check if file was uploaded
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']

        # Check if file was selected
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Check if file is allowed
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            # Save with a consistent name for the course calendar
            filepath = os.path.join(UPLOAD_FOLDER, 'course_calendar.ics')
            file.save(filepath)
            return jsonify({
                'success': True,
                'message': 'Course calendar uploaded successfully!',
                'filename': filename
            })
        else:
            return jsonify({'error': 'Invalid file type. Please upload an ICS file.'}), 400

    # GET request - show upload form
    return render_template('upload_calendar.html')


@app.route('/lab_room.svg')
def get_svg():
    """Serve the SVG with dynamically updated desk colors.

    Uses pre-compiled regex patterns and a short TTL cache so that rapid
    requests (HTML page + embedded <img>) don't duplicate work.
    """
    global _svg_cache

    now = time.time()

    # Build a fingerprint of current station state for cache invalidation
    claimed_stations = get_claimed_stations()
    station_colors = {}
    for station_num, is_occupied in get_station_data():
        if station_num in claimed_stations:
            color = YELLOW
        elif is_occupied:
            color = RED
        else:
            color = GREEN
        station_colors[str(station_num)] = color

    state_hash = hashlib.md5(str(sorted(station_colors.items())).encode()).hexdigest()

    # Return cached SVG if still valid
    if (_svg_cache['content'] is not None
            and _svg_cache['hash'] == state_hash
            and now - _svg_cache['time'] < _SVG_CACHE_TTL):
        return Response(
            _svg_cache['content'],
            mimetype='image/svg+xml',
            headers={'Cache-Control': 'public, max-age=5', 'ETag': state_hash},
        )

    # Read the SVG file and update desk colors using pre-compiled patterns
    with open('static/lab_room.svg', 'r') as f:
        svg_content = f.read()

    for station_num, color in station_colors.items():
        pattern = DESK_REGEX.get(station_num)
        if pattern:
            svg_content = pattern.sub(rf'\1{color}\2', svg_content)

    _svg_cache = {'content': svg_content, 'hash': state_hash, 'time': now}

    return Response(
        svg_content,
        mimetype='image/svg+xml',
        headers={'Cache-Control': 'public, max-age=5', 'ETag': state_hash},
    )


@app.route('/api/lab-data')
def api_lab_data():
    """JSON endpoint for auto-refresh polling.

    Returns lab status and queue data so the frontend can update without
    a full page reload.
    """
    lab_status = get_lab_status()
    queue = get_queue_data()
    return jsonify({
        'status': lab_status,
        'queue': queue,
    })


@app.route('/api/auth/google', methods=['POST'])
def google_auth():
    """Verify Google ID token and create session for berkeley.edu users."""
    try:
        token = request.json.get('credential')

        # Verify the token
        idinfo = id_token.verify_oauth2_token(
            token,
            requests.Request(),
            GOOGLE_CLIENT_ID
        )

        # Verify the domain
        email = idinfo.get('email')
        if not email.endswith(f'@{ALLOWED_DOMAIN}'):
            return jsonify({'error': 'Only berkeley.edu accounts are allowed'}), 403

        # Create session
        session['user'] = {
            'email': email,
            'name': idinfo.get('name'),
            'picture': idinfo.get('picture')
        }

        return jsonify({
            'success': True,
            'user': session['user']
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    """Clear user session."""
    session.pop('user', None)
    return jsonify({'success': True})


@app.route('/api/auth/user')
def get_current_user():
    """Get current authenticated user."""
    if 'user' in session:
        return jsonify(session['user'])
    return jsonify({'error': 'Not authenticated'}), 401


@app.route('/api/queue/add', methods=['POST'])
def api_add_to_queue():
    """Add authenticated user to specified queue."""
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    queue_type = data.get('queue_type')  # 'turtlebot' or 'ur7e'

    if queue_type not in ['turtlebot', 'ur7e']:
        return jsonify({'error': 'Invalid queue type'}), 400

    user = session['user']
    success, error = add_to_queue(queue_type, user['name'], user['email'])
    if not success:
        return jsonify({'error': error}), 400

    return jsonify({
        'success': True,
        'message': f'Added to {queue_type} queue'
    })


@app.route('/api/queue/remove', methods=['POST'])
def api_remove_from_queue():
    """Remove a user from specified queue (admin only)."""
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    user_email = session['user']['email']
    if not is_admin_user(user_email):
        return jsonify({'error': 'Admin access required'}), 403

    data = request.json
    queue_type = data.get('queue_type')
    email_to_remove = data.get('email')

    if queue_type not in ['turtlebot', 'ur7e']:
        return jsonify({'error': 'Invalid queue type'}), 400

    if not email_to_remove:
        return jsonify({'error': 'Email is required'}), 400

    if not remove_from_queue(queue_type, email_to_remove):
        return jsonify({'error': 'User not found in queue'}), 404

    return jsonify({
        'success': True,
        'message': f'Removed from {queue_type} queue'
    })


@app.route('/api/queue/reorder', methods=['POST'])
def api_reorder_queue():
    """Move a queue entry up or down (admin only)."""
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    user_email = session['user']['email']
    if not is_admin_user(user_email):
        return jsonify({'error': 'Admin access required'}), 403

    data = request.json
    queue_type = data.get('queue_type')
    email = data.get('email')
    direction = data.get('direction')

    if queue_type not in ['turtlebot', 'ur7e']:
        return jsonify({'error': 'Invalid queue type'}), 400

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    if direction not in ['up', 'down']:
        return jsonify({'error': 'Invalid direction'}), 400

    success, error = reorder_queue(queue_type, email, direction)
    if not success:
        code = 404 if 'not found' in (error or '').lower() else 400
        return jsonify({'error': error}), code

    return jsonify({
        'success': True,
        'message': 'Queue order updated'
    })


@app.route('/api/queue/reposition', methods=['POST'])
def reposition_in_queue():
    """Move a queue entry to a specific position (admin only)."""
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    user_email = session['user']['email']
    if not is_admin_user(user_email):
        return jsonify({'error': 'Admin access required'}), 403

    data = request.json
    queue_type = data.get('queue_type')
    email = data.get('email')
    new_index = data.get('new_index')

    if queue_type not in ['turtlebot', 'ur7e']:
        return jsonify({'error': 'Invalid queue type'}), 400

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    if new_index is None or not isinstance(new_index, int) or new_index < 0:
        return jsonify({'error': 'Valid new_index is required'}), 400

    success, error = reposition_queue(queue_type, email, new_index)
    if not success:
        code = 404 if 'not found' in (error or '').lower() else 400
        return jsonify({'error': error}), code

    return jsonify({
        'success': True,
        'message': 'Queue order updated'
    })


@app.route('/api/station/override', methods=['POST'])
def api_set_station_override():
    """Set or clear a manual override for a station (admin only)."""
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    user_email = session['user']['email']
    if not is_admin_user(user_email):
        return jsonify({'error': 'Admin access required'}), 403

    data = request.json
    station = data.get('station')
    override_occupied = data.get('override_occupied')

    if station is None or not isinstance(station, int):
        return jsonify({'error': 'Valid station number is required'}), 400

    if station not in TURTLEBOT_STATIONS and station not in UR7E_STATIONS:
        return jsonify({'error': 'Invalid station number'}), 400

    if override_occupied is not None and not isinstance(override_occupied, bool):
        return jsonify({'error': 'override_occupied must be true, false, or null'}), 400

    success, message = set_manual_override(station, override_occupied)
    if not success:
        code = 404 if 'No override exists' in message else 500
        return jsonify({'error': message}), code

    return jsonify({
        'success': True,
        'message': message
    })


@app.route('/api/station/overrides', methods=['GET'])
def get_station_overrides():
    """Get all current manual overrides."""
    try:
        overrides = get_manual_overrides()
        return jsonify({
            'success': True,
            'overrides': {str(k): v for k, v in overrides.items()}
        })
    except Exception as e:
        return jsonify({'error': f'Error getting overrides: {str(e)}'}), 500


@app.route('/claim/<token>')
def claim_page(token):
    """Show claim confirmation page."""
    claim = get_pending_claim(token)
    if not claim:
        return render_template('claim.html', error='Invalid or expired claim link.')

    # Calculate time remaining
    expires_at = datetime.fromisoformat(claim['expires_at'])
    time_remaining = (expires_at - datetime.now()).total_seconds()

    return render_template('claim.html',
                         claim=claim,
                         time_remaining=max(0, int(time_remaining)),
                         token=token)


@app.route('/api/claim/confirm', methods=['POST'])
def confirm_claim():
    """Confirm claim - remove from queue."""
    data = request.json
    token = data.get('token')

    if not token:
        return jsonify({'error': 'Token is required'}), 400

    claim = get_pending_claim(token)
    if not claim:
        return jsonify({'error': 'Invalid or expired claim'}), 404

    # Remove from BOTH queues (they got a station, no need to wait in either queue)
    remove_from_queue('turtlebot', claim['email'])
    remove_from_queue('ur7e', claim['email'])

    # Mark claim as confirmed (don't delete - keeps station yellow until they log in)
    mark_claim_confirmed(token)

    station_display = "Turtlebot" if claim['station_type'] == 'turtlebot' else "UR7e"
    station_num = claim.get('station', '')
    return jsonify({
        'success': True,
        'message': f'Station {station_num} ({station_display}) claimed successfully! Head to the lab now.'
    })


if __name__ == '__main__':
    app.run(host="127.0.0.1", port=5000)
