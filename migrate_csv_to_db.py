#!/usr/bin/env python3
"""One-time migration: create new MariaDB tables and import existing CSV data.

Run on prod:
    DATA_SOURCE=database python3 migrate_csv_to_db.py

This script is idempotent — safe to re-run. It uses CREATE TABLE IF NOT EXISTS
and INSERT ... ON DUPLICATE KEY UPDATE so existing rows are overwritten, not
duplicated.
"""
import csv
import os
import sys

from lab_utils import (
    QUEUE_TURTLEBOT_CSV_PATH, QUEUE_UR7E_CSV_PATH,
    MANUAL_OVERRIDES_CSV_PATH, PENDING_CLAIMS_CSV_PATH,
    get_db_connection,
)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

TABLES_DDL = [
    # Add previous_occupied column to stations (if not already present)
    """
    ALTER TABLE stations ADD COLUMN previous_occupied BOOLEAN DEFAULT NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS queues (
        id INT AUTO_INCREMENT PRIMARY KEY,
        queue_type ENUM('turtlebot', 'ur7e') NOT NULL,
        position INT NOT NULL,
        name VARCHAR(255) NOT NULL,
        email VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_queue_email (queue_type, email),
        INDEX idx_queue_position (queue_type, position)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_claims (
        id INT AUTO_INCREMENT PRIMARY KEY,
        email VARCHAR(255) NOT NULL,
        name VARCHAR(255) NOT NULL,
        station_type ENUM('turtlebot', 'ur7e') NOT NULL,
        station INT NOT NULL,
        claim_token VARCHAR(64) NOT NULL UNIQUE,
        expires_at DATETIME NOT NULL,
        confirmed BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_overrides (
        station INT PRIMARY KEY,
        override_occupied BOOLEAN NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
]


def run_ddl(conn):
    """Create tables. Ignores errors for ALTER TABLE if column already exists."""
    cursor = conn.cursor()
    for ddl in TABLES_DDL:
        try:
            cursor.execute(ddl)
            conn.commit()
            first_line = ddl.strip().split('\n')[0].strip()
            print(f"  OK: {first_line}")
        except Exception as e:
            if 'Duplicate column' in str(e):
                print(f"  SKIP (already exists): ALTER TABLE stations ADD COLUMN previous_occupied")
            else:
                print(f"  ERROR: {e}")
    cursor.close()


def migrate_queues(conn):
    """Import queue CSVs into the queues table."""
    cursor = conn.cursor()
    for queue_type, csv_path in [('turtlebot', QUEUE_TURTLEBOT_CSV_PATH),
                                  ('ur7e', QUEUE_UR7E_CSV_PATH)]:
        if not os.path.exists(csv_path):
            print(f"  {queue_type}: no CSV file, skipping")
            continue

        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            print(f"  {queue_type}: empty queue, skipping")
            continue

        for pos, row in enumerate(rows):
            cursor.execute("""
                INSERT INTO queues (queue_type, position, name, email)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE position = %s, name = %s
            """, (queue_type, pos, row['name'], row['email'],
                  pos, row['name']))

        conn.commit()
        print(f"  {queue_type}: migrated {len(rows)} entries")
    cursor.close()


def migrate_overrides(conn):
    """Import manual_overrides.csv into the manual_overrides table."""
    cursor = conn.cursor()
    if not os.path.exists(MANUAL_OVERRIDES_CSV_PATH):
        print("  No overrides CSV, skipping")
        cursor.close()
        return

    with open(MANUAL_OVERRIDES_CSV_PATH, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("  No overrides to migrate")
        cursor.close()
        return

    for row in rows:
        station = int(row['station'])
        override_occupied = row['override_occupied'].lower() == 'true'
        cursor.execute("""
            INSERT INTO manual_overrides (station, override_occupied)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE override_occupied = %s
        """, (station, override_occupied, override_occupied))

    conn.commit()
    print(f"  Migrated {len(rows)} overrides")
    cursor.close()


def migrate_claims(conn):
    """Import pending_claims.csv into the pending_claims table."""
    cursor = conn.cursor()
    if not os.path.exists(PENDING_CLAIMS_CSV_PATH):
        print("  No claims CSV, skipping")
        cursor.close()
        return

    with open(PENDING_CLAIMS_CSV_PATH, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("  No claims to migrate")
        cursor.close()
        return

    for row in rows:
        confirmed = row.get('confirmed', 'false').lower() == 'true'
        cursor.execute("""
            INSERT INTO pending_claims (email, name, station_type, station, claim_token, expires_at, confirmed)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE expires_at = %s, confirmed = %s
        """, (row['email'], row['name'], row['station_type'],
              int(row['station']), row['claim_token'], row['expires_at'], confirmed,
              row['expires_at'], confirmed))

    conn.commit()
    print(f"  Migrated {len(rows)} claims")
    cursor.close()


def main():
    print("=== Lab Availability: CSV → MariaDB Migration ===\n")

    print("Connecting to database...")
    conn = get_db_connection()
    print(f"  Connected to {conn.host_info}\n")

    print("1. Creating tables...")
    run_ddl(conn)

    print("\n2. Migrating queues...")
    migrate_queues(conn)

    print("\n3. Migrating manual overrides...")
    migrate_overrides(conn)

    print("\n4. Migrating pending claims...")
    migrate_claims(conn)

    conn.close()
    print("\n=== Migration complete ===")


if __name__ == '__main__':
    main()
