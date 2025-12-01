from flask import Flask, render_template, Response
import re
import pymysql

app = Flask(__name__)

# Colors
GREEN = '#C8F9BE'
RED = '#FE9193'

# Station groupings
TURTLEBOT_STATIONS = {1, 2, 3, 4, 5, 11}
UR7E_STATIONS = {6, 7, 8, 9, 10}

# Database connection info
DB_CONFIG = {
    "host": "instapphost.eecs.berkeley.edu",
    "user": "ee106a",
    "password": "REDACTED",
    "database": "ee106a"
}

def get_lab_status():
    """Calculate lab status from database."""
    turtlebots_available = 0
    ur7es_available = 0

    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT station, occupied FROM stations")
    for station_num, is_occupied in cursor.fetchall():
        if not is_occupied:
            if station_num in TURTLEBOT_STATIONS:
                turtlebots_available += 1
            elif station_num in UR7E_STATIONS:
                ur7es_available += 1
    cursor.close()
    conn.close()

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
    """Serve the SVG with dynamically updated desk colors based on database."""
    svg_path = 'static/lab_room.svg'

    # Query database for station status
    station_colors = {}
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT station, occupied FROM stations")
    for station_num, is_occupied in cursor.fetchall():
        color = RED if is_occupied else GREEN
        station_colors[str(station_num)] = color
    cursor.close()
    conn.close()

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