from flask import Flask, render_template, Response
import re
import pymysql
import csv
import os

app = Flask(__name__)

# Colors
GREEN = '#C8F9BE'
RED = '#FE9193'

# Station groupings
TURTLEBOT_STATIONS = {1, 2, 3, 4, 5, 11}
UR7E_STATIONS = {6, 7, 8, 9, 10}

# Data source configuration (csv or database)
DATA_SOURCE = os.environ.get('DATA_SOURCE', 'csv').lower()
CSV_PATH = 'station_status.csv'

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
    else:  # csv
        data = []
        with open(CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                station_num = int(row['station'])
                is_occupied = row['occupied'].lower() == 'true'
                data.append((station_num, is_occupied))
        return data

def get_lab_status():
    """Calculate lab status from configured data source."""
    turtlebots_available = 0
    ur7es_available = 0

    for station_num, is_occupied in get_station_data():
        if not is_occupied:
            if station_num in TURTLEBOT_STATIONS:
                turtlebots_available += 1
            elif station_num in UR7E_STATIONS:
                ur7es_available += 1

    total_available = turtlebots_available + ur7es_available
    is_open = total_available > 0

    return {
        'is_open': is_open,
        'turtlebots_available': turtlebots_available,
        'ur7es_available': ur7es_available
    }

@app.route('/')
def index():
    lab_status = get_lab_status()
    return render_template('index.html', lab_status=lab_status)

@app.route('/lab_room.svg')
def get_svg():
    """Serve the SVG with dynamically updated desk colors based on configured data source."""
    svg_path = 'static/lab_room.svg'

    # Get station status from configured data source
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
