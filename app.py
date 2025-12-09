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


def get_station_data():
    """Get station data from configured source (CSV or database)."""
    if DATA_SOURCE == 'database':
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT station, occupied FROM stations")
        data = cursor.fetchall()
        cursor.close()
        conn.close()
        return data

    else:  # CSV mode
        data = []
        with open(CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                station_num = int(row['station'])
                is_occupied = row['occupied'].lower() == 'true'
                data.append((station_num, is_occupied))
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


if __name__ == '__main__':
    app.run(host="127.0.0.1", port=5000)

