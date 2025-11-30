import paramiko
import csv

USERNAME = "ee106a"
BASE_HOST = "c105-{}.eecs.berkeley.edu"

def check_station(station_num):
    hostname = BASE_HOST.format(station_num)
    command = "who"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=hostname,
            username=USERNAME,
            key_filename=None,      # use keys in ~/.ssh/
            timeout=2
        )

        _, stdout, _ = client.exec_command(command)
        output = stdout.read().decode()

        # occupied if your username appears on any line
        occupied = USERNAME in output

    except Exception:
        # If SSH fails (machine offline), mark as not occupied
        occupied = False

    finally:
        try:
            client.close()
        except:
            pass

    return occupied


def main():
    results = []

    for station in range(1, 12):
        print(f"Checking c105-{station}...")
        occ = check_station(station)
        results.append((station, occ))

    # Write CSV
    with open("station_status.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["station", "occupied"])
        for station, occ in results:
            writer.writerow([station, str(occ).lower()])

    print("\nDone!")


if __name__ == "__main__":
    main()

