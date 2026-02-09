# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Flask web app for UC Berkeley's EECS 106A course that shows real-time availability of 11 lab stations (computers) in Cory 105. Students can view which stations are open, join queues when all stations of a robot type are occupied, and receive email notifications when stations free up. Admins can manage queues, override station statuses, and upload course calendars.

## Running Locally

```bash
# Install dependencies (Python 3.10, virtualenv in ./venv)
pip install -r requirements.txt

# Run with CSV data (default, for local dev)
python3 app.py

# Run with a specific lab state for testing
python3 app.py lab_oh        # Simulate Lab OH state
python3 app.py lab_section   # Simulate Lab Section state
python3 app.py lab_full      # Simulate all stations full
python3 app.py lab_full_oh   # Full + OH
python3 app.py lab_regular   # Regular open state

# Run with database (production mode)
DATA_SOURCE=database python3 app.py
```

The app runs at `http://127.0.0.1:5000`.

## Production Deployment (Apphost)

Deployed on `instapphost.eecs.berkeley.edu` as user `ee106a`. Working directory: `/home/ff/ee106a/lab-availability-site`. Three systemd user services in `services/`:

- **lab_availability.service** - gunicorn serving Flask via Unix socket, `DATA_SOURCE=database`
- **update_lab_db.timer/service** - runs `update_db.py` every 10s (throttled to 60s outside OH) to SSH into each lab machine (c105-1 through c105-11), run `who`, and update MariaDB + CSV
- **lab_notify.timer/service** - runs `check_notifications.py` every 10s during Lab OH to detect freed stations and email the first person in queue

Restart after code changes: `systemctl --user restart lab_availability.service`

## Architecture

### Data Flow
1. `update_db.py` SSHs into c105-{1..11}.eecs.berkeley.edu, checks `who` for `ee106a`/`ee106b` users, writes to MariaDB `stations` table and `csv/station_status.csv`
2. `app.py` reads station data from DB (production) or CSV (local), applies manual overrides from `csv/manual_overrides.csv`, checks claimed stations from `csv/pending_claims.csv`
3. `check_notifications.py` compares current vs previous station states (`csv/previous_states.json`), emails first-in-queue when a station frees up, manages claim tokens with 5-min expiry

### Lab State Machine
Lab state is determined by `determine_lab_state()` in `app.py` with priority: CLI override > calendar event (maintenance/lab section/lab OH) > full (0 available) > open. Calendar events are parsed from `uploads/course_calendar.ics` (uploaded via admin page). States map to colors in `STATE_COLORS`.

### Station Types
- **Turtlebot stations**: {1, 2, 3, 4, 5, 11}
- **UR7e stations**: {6, 7, 8, 9, 10}

Each type has its own queue (`csv/queue_turtlebot.csv`, `csv/queue_ur7e.csv`). Queues only appear on the main page when all stations of that type are occupied.

### Authentication
Google OAuth (berkeley.edu domain only) via Google Identity Services. Admin access controlled by `admin_users.txt` (one email per line). Auth is required for: joining queues, all admin actions (queue management, station overrides, calendar upload).

### SVG Map
`static/lab_room.svg` is a Figma-created SVG with `<path id="desk-N">` elements. The `/lab_room.svg` route dynamically recolors desks using regex substitution: green (#00A676) = open, red (#EB9486) = occupied, yellow (#F3DE8A) = claimed/pending.

### Notification & Claim Flow
When a station frees up during Lab OH: `check_notifications.py` creates a claim (token in `csv/pending_claims.csv`), emails the first queued person a link to `/claim/<token>`. They have 5 minutes to confirm. Confirmed claims hold the station (yellow on map) until the user logs into the machine. Expired unclaimed claims notify the next person.

### Key CSV Files (in `csv/`)
- `station_status.csv` - current station occupancy (written by `update_db.py`)
- `queue_turtlebot.csv` / `queue_ur7e.csv` - queue entries (name, email)
- `pending_claims.csv` - active station claims with tokens and expiry
- `manual_overrides.csv` - admin overrides for station status
- `previous_states.json` - last-known states for change detection
- `station_status_*.csv` - preset test fixtures for different lab states

### API Endpoints
- `POST /api/auth/google` - Google OAuth login
- `POST /api/auth/logout` - logout
- `GET /api/auth/user` - current user
- `POST /api/queue/add` - join queue (authenticated)
- `POST /api/queue/remove` - remove from queue (admin)
- `POST /api/queue/reorder` - move up/down in queue (admin)
- `POST /api/queue/reposition` - drag-drop reorder (admin)
- `POST /api/station/override` - set/clear station override (admin)
- `GET /api/station/overrides` - list overrides
- `POST /api/claim/confirm` - confirm a station claim

## Important Patterns

- `get_current_lab_event()` is duplicated across `app.py`, `check_notifications.py`, and `update_db.py` with slight variations (app.py returns class info, others just return event type)
- All state is stored in flat CSV/JSON files, not the database (queues, claims, overrides). Only station occupancy uses MariaDB.
- The notification checker (`check_notifications.py`) is designed to be stateless between runs - it reads/writes CSVs each invocation and exits.
