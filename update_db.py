import paramiko
import pymysql
import csv

USERNAME = "ee106a"
BASE_HOST = "c105-{}.eecs.berkeley.edu"
DB_CONFIG = {
    "host": "instapphost.eecs.berkeley.edu",
    "user": "ee106a",
    "password": "REDACTED",  # your DB password
    "database": "ee106a"
}

def check_station(station_num):
    """SSH into the machine and check if the user is logged in."""
    hostname = BASE_HOST.format(station_num)
    command = "who"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=hostname,
            username=USERNAME,
            key_filename=None,  # uses ~/.ssh/
            timeout=2
        )
        _, stdout, _ = client.exec_command(command)
        output = stdout.read().decode()
        # occupied if your username appears on any line
        occupied = USERNAME in output
    except Exception:
        # Machine offline or SSH failed -> not occupied
        occupied = False
    finally:
        try:
            client.close()
        except:
            pass

    return occupied

def main():
    results = []

    # Check all stations
    for station in range(1, 12):
        print(f"Checking c105-{station}...")
        occ = check_station(station)
        results.append((station, occ))

    # Connect to MariaDB
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # Make sure the table exists
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stations (
        station INT PRIMARY KEY,
        occupied BOOLEAN NOT NULL,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """)

    # Update database
    for station, occupied in results:
        cursor.execute("""
        INSERT INTO stations (station, occupied)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE occupied=%s, last_updated=CURRENT_TIMESTAMP
        """, (station, occupied, occupied))

    conn.commit()

    # Optional: write CSV for debugging
    cursor.execute("SELECT station, occupied FROM stations ORDER BY station")
    rows = cursor.fetchall()
    with open("stations.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["station", "occupied"])
        for row in rows:
            writer.writerow(row)

    cursor.close()
    conn.close()
    print("Database updated and CSV written.")

if __name__ == "__main__":
    main()

