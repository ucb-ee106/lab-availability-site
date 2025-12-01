# Lab Availability Site

Flask app that displays lab station availability.

## Data Source

The app supports two data sources:

- **CSV** (default): Reads from `csv/station_status.csv`
- **Database**: Connects to MySQL database

## Usage

Run locally with CSV:
```bash
python3 app.py
```

Run with database:
```bash
DATA_SOURCE=database python3 app.py
```

## Production Deployment (Apphost)

On the apphost, the application runs using systemd services located in the `services/` folder:

### Database Updates
- **update_lab_db.service**: Runs `update_db.py` to SSH into each lab machine (c105-1 through c105-11) and check if somebody else is logged into the computer using the `who` command
- **update_lab_db.timer**: Triggers the update service every 5 minutes to keep the database current
- The script updates a MySQL database on `instapphost.eecs.berkeley.edu` with the occupied status of each station

### Web Application
- **lab_availability.service**: Runs the Flask app using gunicorn with `Environment="DATA_SOURCE=database"` configured
- This ensures the web app queries the database (which is kept up-to-date by the timer) instead of static CSV files

## Requirements

Install dependencies:
```bash
pip install -r requirements.txt
```
