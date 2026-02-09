# Lab Availability Site Docs

Hello! This site was made by Daniel Municio (Fall 2025) to show which computers are being occupied in the lab, and eventually also contain the OH computer queue and robot booking information.

This section is an explanation of how it was made, for future TAs who want to maintain/improve the site, and any interested students.

## Getting the Apphost

To make a UCB site, you first have to get permission and registration from the Instructional Support Group. They have nice instructions [here](https://inst.eecs.berkeley.edu/cgi-bin/pub.cgi?file=apphost.help) that include both how to make the request and how to actually set things up, I heavily recommend reading through all of it before trying to do anything. 

## Getting Computer Availability

The way the apphost gets computer availability is by logging into every computer in the lab (via SSH) and running the `who` command to check if anybody else is using that computer.

It does this based on a service which is triggered by a timer that goes off every 10 seconds. The timer file, `update_lab_db.timer` looks like this:

```bash
[Unit]
Description=Run update_lab_db every 10 seconds (throttled to 1 min outside OH)

[Timer]
OnBootSec=10s
OnUnitActiveSec=10s
Unit=update_lab_db.service

[Install]
WantedBy=timers.target
```

Outside of OH, the script throttles itself to only actually check once per minute to avoid unnecessary SSH traffic.

While the service it's calling, `update_lab_db.service` looks like this:

```bash
[Unit]
Description=Update Lab Availability Database

[Service]
Type=oneshot
WorkingDirectory=/home/ff/ee106a/lab-availability-site
Environment="DATA_SOURCE=database"
ExecStart=/home/ff/ee106a/venvs/testing/bin/python3 /home/ff/ee106a/lab-availability-site/update_db.py
```

Where you can see how it's running the `update_db.py` file, everytime the service is called. The DB credentials are also passed as environment variables in the service file (omitted here for brevity).

While it's checking the availability of all the computers, it updates a MariaDB database that comes with the apphost. All app data lives in this database—station occupancy, queues, claims, and manual overrides.

## Creating the GUI

The picture of the lab room with each station is an SVG that I created with Figma, which has an ID for each workstation. To make a station red or green, you just use some regex over the SVG to find that station and manually override the color of the station block. There's definitely probably a better way to do this, but I study robotics, not web development—go easy on me please :)

## OH Queue & Notifications

During Lab OH, if all stations of a robot type (Turtlebot or UR7e) are occupied, a queue button shows up on the main page. Students sign in with their Berkeley Google account and join the queue for whichever robot type they need.

When a station frees up, the site emails the first person in queue with a claim link. They get 5 minutes to confirm the claim—if they don't, it goes to the next person. Claimed stations show up yellow on the map until the student actually logs into the machine.

All of this is handled by `check_notifications.py`, which runs every 10 seconds via another systemd timer (`lab_notify.timer`). It compares the current station states to the previous ones (stored in a `previous_occupied` column in the `stations` table), and when it detects a station opened up, it creates a claim token and sends the email. Queues, claims, and manual overrides all live in MariaDB alongside the station data. For local development, you can set `DATA_SOURCE=csv` (the default) and everything falls back to flat CSV files in the `csv/` directory instead.

Admins (listed in `admin_users.txt`) can manage queues and manually override station statuses from the `/admin` page.

## Deploying the Web Application

The GUI and all other buttons are added via a Flask application. According to the ISG instructions: "To expose a web application to the internet, you need to listen to HTTP requests on a Unix domain socket locked at `/srv/appsockets/YOURUSERNAME/main/app.sock`"

To properly connect our Flask application to the socket, I used gunicorn, called via the service `lab_availability.service`. You can manually start it with the command:

```bash
systemctl --user start lab_availability.service
```

You can also restart it by swapping `start` with `restart`, or have it start automatically on login with `enable`.

The lab availability service looks like this:

```bash
[Unit]
Description=Lab Availability Flask App
ConditionPathExists=/etc/is-instapphost

[Service]
WorkingDirectory=/home/ff/ee106a/lab-availability-site
Environment="DATA_SOURCE=database"
ExecStart=/home/ff/ee106a/venvs/testing/bin/gunicorn -w 4 -b unix:/srv/appsockets/ee106a/main/app.sock app:app
Restart=always

[Install]
WantedBy=default.target
```

Right now, it should be configured to run while the apphost is running, and if the server ever restarts, it should start up with the apphost. 

If you make any changes to the Flask application, you'll want to restart the service, with 
```bash
systemctl --user restart lab_availability.service
```
### Notes

- I should definitely rename the virtual environment to something that's not "testing"
- The `ConditionPathExists` portion ensures that only the apphost tries to start this service, not just any user (thank you Steven Luo for catching this!)
