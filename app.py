from flask import Flask, render_template, Response
import re
import pymysql
import csv
import os
import sys

app = Flask(__name__)

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


if __name__ == '__main__':
    app.run(host="127.0.0.1", port=5000)

