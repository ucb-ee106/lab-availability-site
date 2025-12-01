# Lab Availability Site

Flask app that displays lab station availability.

## Data Source

The app supports two data sources:

- **CSV** (default): Reads from `station_status.csv`
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

## Requirements

Install dependencies:
```bash
pip install -r requirements.txt
```
