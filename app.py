from flask import Flask, render_template, Response, request, jsonify, session, redirect, url_for
import re
import pymysql
import csv
import os
import sys
from google.oauth2 import id_token
from google.auth.transport import requests
import secrets
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Google OAuth configuration
GOOGLE_CLIENT_ID = "22576242210-5dqoo2haju5f7t0qf5cnuq2hpbhstjpe.apps.googleusercontent.com"
ALLOWED_DOMAIN = "berkeley.edu"

# Lab states
STATE_OPEN = 'Open'
STATE_FULL = 'Full'
STATE_LAB_SECTION = 'Lab Section'
STATE_LAB_OH = 'Lab OH'

# State colors
STATE_COLORS = {
    STATE_OPEN: '#00A676',        # Green
    STATE_FULL: '#EB9486',        # Red
    STATE_LAB_SECTION: '#EB9486', # Red
    STATE_LAB_OH: '#F3DE8A'       # Yellow
}

# Desk colors (for SVG)
GREEN = '#00A676'
RED = '#EB9486'

# Station groupings
TURTLEBOT_STATIONS = {1, 2, 3, 4, 5, 11}
UR7E_STATIONS = {6, 7, 8, 9, 10}

# Data source configuration (csv or database)
DATA_SOURCE = os.environ.get('DATA_SOURCE', 'csv').lower()
QUEUE_UR7E_CSV_PATH = 'csv/queue_ur7e.csv'
QUEUE_TURTLEBOT_CSV_PATH = 'csv/queue_turtlebot.csv'
MANUAL_OVERRIDES_CSV_PATH = 'csv/manual_overrides.csv'
ADMIN_USERS_FILE = 'admin_users.txt'
UPLOAD_FOLDER = 'uploads'
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

# Database connection info
DB_CONFIG = {
    "host": "instapphost.eecs.berkeley.edu",
    "user": "ee106a",
    "password": "REDACTED",
    "database": "ee106a"
}


def get_manual_overrides():
    """Get manual station overrides from CSV file."""
    overrides = {}
    if os.path.exists(MANUAL_OVERRIDES_CSV_PATH):
        try:
            with open(MANUAL_OVERRIDES_CSV_PATH, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    station = int(row['station'])
                    override_occupied = row['override_occupied'].lower()
                    if override_occupied in ['true', 'false']:
                        overrides[station] = (override_occupied == 'true')
        except Exception as e:
            print(f"Error reading manual overrides: {e}")
    return overrides

def get_station_data():
    """Get station data from configured source (CSV or database), applying manual overrides."""
    if DATA_SOURCE == 'database':
        conn = pymysql.connect(**DB_CONFIG)
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
    """Determine the current lab state based on time and availability.

    Priority:
    1. Command line state override (for testing/demo)
    2. Lab Section (if it's section time) - TODO: Add time-based logic
    3. Lab OH (if it's OH time) - TODO: Add time-based logic
    4. Full (if no stations available)
    5. Open (default)
    """
    # Use state override if set via command line
    if STATE_OVERRIDE is not None:
        return STATE_OVERRIDE

    # TODO: Add time-based logic here
    # Example: if is_section_time(): return STATE_LAB_SECTION
    # Example: if is_oh_time(): return STATE_LAB_OH

    # For now, just check if lab is full
    if total_available == 0:
        return STATE_FULL

    return STATE_OPEN

def get_queue_data():
    """Get queue data from CSV files for both robot types."""
    ur7e_queue = []
    turtlebot_queue = []

    # Read UR7e queue
    try:
        with open(QUEUE_UR7E_CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ur7e_queue.append({
                    'name': row['name'],
                    'email': row['email']
                })
    except FileNotFoundError:
        pass

    # Read Turtlebot queue
    try:
        with open(QUEUE_TURTLEBOT_CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                turtlebot_queue.append({
                    'name': row['name'],
                    'email': row['email']
                })
    except FileNotFoundError:
        pass

    return {
        'ur7e': ur7e_queue,
        'turtlebot': turtlebot_queue
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
    """Check if the given email is in the admin users list."""
    if not os.path.exists(ADMIN_USERS_FILE):
        return False

    try:
        with open(ADMIN_USERS_FILE, 'r') as f:
            admin_emails = set()
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith('#'):
                    admin_emails.add(line.lower())
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

    # Show queues only when it's Lab OH and the specific robot type is full
    show_ur7e_queue = (state == STATE_LAB_OH and ur7es_available == 0)
    show_turtlebot_queue = (state == STATE_LAB_OH and turtlebots_available == 0)

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
    """Display website_about.md content on the about page."""
    with open('website_about.md', 'r') as f:
        readme_content = f.read()
    return render_template('about.html', readme_content=readme_content)


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
    """Serve the SVG with dynamically updated desk colors based on configured data source."""
    svg_path = 'static/lab_room.svg'

    # Get station status
    station_colors = {}
    for station_num, is_occupied in get_station_data():
        color = RED if is_occupied else GREEN
        station_colors[str(station_num)] = color

    # Read the SVG file
    with open(svg_path, 'r') as f:
        svg_content = f.read()

    # Update each desk color
    for station_num, color in station_colors.items():
        pattern = rf'(<path id="desk-{station_num}"[^>]*fill=")[^"]*(")'
        svg_content = re.sub(pattern, rf'\1{color}\2', svg_content)

    return Response(svg_content, mimetype='image/svg+xml')


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
def add_to_queue():
    """Add authenticated user to specified queue."""
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    queue_type = data.get('queue_type')  # 'turtlebot' or 'ur7e'

    if queue_type not in ['turtlebot', 'ur7e']:
        return jsonify({'error': 'Invalid queue type'}), 400

    user = session['user']
    csv_path = QUEUE_TURTLEBOT_CSV_PATH if queue_type == 'turtlebot' else QUEUE_UR7E_CSV_PATH

    # Check if user is already in queue
    existing_entries = []
    file_exists = os.path.exists(csv_path)

    if file_exists:
        try:
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                existing_entries = list(reader)

            # Check for duplicate
            for entry in existing_entries:
                if entry['email'] == user['email']:
                    return jsonify({'error': 'You are already in this queue'}), 400

        except Exception as e:
            return jsonify({'error': 'Error reading queue'}), 500
    else:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    # Add user to queue
    mode = 'a' if file_exists else 'w'
    with open(csv_path, mode, newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['name', 'email'])

        # Write header if file is new or empty
        if not file_exists or len(existing_entries) == 0:
            writer.writeheader()

        writer.writerow({
            'name': user['name'],
            'email': user['email']
        })

    return jsonify({
        'success': True,
        'message': f'Added to {queue_type} queue'
    })


@app.route('/api/queue/remove', methods=['POST'])
def remove_from_queue():
    """Remove a user from specified queue (admin only)."""
    # Check authentication
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    # Check admin privileges
    user_email = session['user']['email']
    if not is_admin_user(user_email):
        return jsonify({'error': 'Admin access required'}), 403

    data = request.json
    queue_type = data.get('queue_type')  # 'turtlebot' or 'ur7e'
    email_to_remove = data.get('email')

    if queue_type not in ['turtlebot', 'ur7e']:
        return jsonify({'error': 'Invalid queue type'}), 400

    if not email_to_remove:
        return jsonify({'error': 'Email is required'}), 400

    csv_path = QUEUE_TURTLEBOT_CSV_PATH if queue_type == 'turtlebot' else QUEUE_UR7E_CSV_PATH

    # Read current queue
    if not os.path.exists(csv_path):
        return jsonify({'error': 'Queue does not exist'}), 404

    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            entries = list(reader)

        # Filter out the entry to remove
        original_count = len(entries)
        entries = [e for e in entries if e['email'] != email_to_remove]

        if len(entries) == original_count:
            return jsonify({'error': 'User not found in queue'}), 404

        # Write back to file
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['name', 'email'])
            writer.writeheader()
            writer.writerows(entries)

        return jsonify({
            'success': True,
            'message': f'Removed from {queue_type} queue'
        })

    except Exception as e:
        return jsonify({'error': f'Error updating queue: {str(e)}'}), 500


@app.route('/api/queue/reorder', methods=['POST'])
def reorder_queue():
    """Move a queue entry up or down (admin only)."""
    # Check authentication
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    # Check admin privileges
    user_email = session['user']['email']
    if not is_admin_user(user_email):
        return jsonify({'error': 'Admin access required'}), 403

    data = request.json
    queue_type = data.get('queue_type')  # 'turtlebot' or 'ur7e'
    email = data.get('email')
    direction = data.get('direction')  # 'up' or 'down'

    if queue_type not in ['turtlebot', 'ur7e']:
        return jsonify({'error': 'Invalid queue type'}), 400

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    if direction not in ['up', 'down']:
        return jsonify({'error': 'Invalid direction'}), 400

    csv_path = QUEUE_TURTLEBOT_CSV_PATH if queue_type == 'turtlebot' else QUEUE_UR7E_CSV_PATH

    # Read current queue
    if not os.path.exists(csv_path):
        return jsonify({'error': 'Queue does not exist'}), 404

    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            entries = list(reader)

        # Find the index of the entry to move
        index = None
        for i, entry in enumerate(entries):
            if entry['email'] == email:
                index = i
                break

        if index is None:
            return jsonify({'error': 'User not found in queue'}), 404

        # Perform the swap
        if direction == 'up':
            if index == 0:
                return jsonify({'error': 'Already at the top of the queue'}), 400
            # Swap with previous entry
            entries[index], entries[index - 1] = entries[index - 1], entries[index]
        else:  # down
            if index == len(entries) - 1:
                return jsonify({'error': 'Already at the bottom of the queue'}), 400
            # Swap with next entry
            entries[index], entries[index + 1] = entries[index + 1], entries[index]

        # Write back to file
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['name', 'email'])
            writer.writeheader()
            writer.writerows(entries)

        return jsonify({
            'success': True,
            'message': f'Queue order updated'
        })

    except Exception as e:
        return jsonify({'error': f'Error updating queue: {str(e)}'}), 500


@app.route('/api/queue/reposition', methods=['POST'])
def reposition_in_queue():
    """Move a queue entry to a specific position (admin only)."""
    # Check authentication
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    # Check admin privileges
    user_email = session['user']['email']
    if not is_admin_user(user_email):
        return jsonify({'error': 'Admin access required'}), 403

    data = request.json
    queue_type = data.get('queue_type')  # 'turtlebot' or 'ur7e'
    email = data.get('email')
    new_index = data.get('new_index')  # 0-based index

    if queue_type not in ['turtlebot', 'ur7e']:
        return jsonify({'error': 'Invalid queue type'}), 400

    if not email:
        return jsonify({'error': 'Email is required'}), 400

    if new_index is None or not isinstance(new_index, int) or new_index < 0:
        return jsonify({'error': 'Valid new_index is required'}), 400

    csv_path = QUEUE_TURTLEBOT_CSV_PATH if queue_type == 'turtlebot' else QUEUE_UR7E_CSV_PATH

    # Read current queue
    if not os.path.exists(csv_path):
        return jsonify({'error': 'Queue does not exist'}), 404

    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            entries = list(reader)

        # Find the entry to move
        old_index = None
        entry_to_move = None
        for i, entry in enumerate(entries):
            if entry['email'] == email:
                old_index = i
                entry_to_move = entry
                break

        if old_index is None:
            return jsonify({'error': 'User not found in queue'}), 404

        # Validate new_index
        if new_index >= len(entries):
            new_index = len(entries) - 1

        # Remove from old position and insert at new position
        entries.pop(old_index)
        entries.insert(new_index, entry_to_move)

        # Write back to file
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['name', 'email'])
            writer.writeheader()
            writer.writerows(entries)

        return jsonify({
            'success': True,
            'message': f'Queue order updated'
        })

    except Exception as e:
        return jsonify({'error': f'Error updating queue: {str(e)}'}), 500


@app.route('/api/station/override', methods=['POST'])
def set_station_override():
    """Set or clear a manual override for a station (admin only)."""
    # Check authentication
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    # Check admin privileges
    user_email = session['user']['email']
    if not is_admin_user(user_email):
        return jsonify({'error': 'Admin access required'}), 403

    data = request.json
    station = data.get('station')
    override_occupied = data.get('override_occupied')  # True, False, or None to clear

    if station is None or not isinstance(station, int):
        return jsonify({'error': 'Valid station number is required'}), 400

    if station not in TURTLEBOT_STATIONS and station not in UR7E_STATIONS:
        return jsonify({'error': 'Invalid station number'}), 400

    try:
        # Read current overrides
        overrides = {}
        if os.path.exists(MANUAL_OVERRIDES_CSV_PATH):
            with open(MANUAL_OVERRIDES_CSV_PATH, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    overrides[int(row['station'])] = row['override_occupied']

        # Update or remove override
        if override_occupied is None:
            # Clear override
            if station in overrides:
                del overrides[station]
                message = f'Cleared override for station {station}'
            else:
                return jsonify({'error': 'No override exists for this station'}), 404
        else:
            # Set override
            if not isinstance(override_occupied, bool):
                return jsonify({'error': 'override_occupied must be true, false, or null'}), 400
            overrides[station] = 'true' if override_occupied else 'false'
            message = f'Set station {station} override to {"occupied" if override_occupied else "available"}'

        # Write back to file
        with open(MANUAL_OVERRIDES_CSV_PATH, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['station', 'override_occupied'])
            writer.writeheader()
            for s, occupied in sorted(overrides.items()):
                writer.writerow({'station': s, 'override_occupied': occupied})

        return jsonify({
            'success': True,
            'message': message
        })

    except Exception as e:
        return jsonify({'error': f'Error setting override: {str(e)}'}), 500


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


if __name__ == '__main__':
    app.run(host="127.0.0.1", port=5000)

