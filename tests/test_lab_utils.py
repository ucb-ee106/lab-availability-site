"""Tests for lab_utils shared module."""
import os
import csv
import json
import time
import tempfile
import threading
from unittest import mock
from datetime import datetime, timedelta

import pytest
from dateutil import tz

# Patch paths before importing lab_utils so tests use temp dirs
import lab_utils


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache():
    """Reset all caches between tests."""
    lab_utils._calendar_cache = {'result': None, 'time': 0, 'mtime': 0}
    yield


@pytest.fixture
def tmp_csv(tmp_path):
    """Create a temp CSV file for locking tests."""
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("name,email\nAlice,alice@b.edu\n")
    return str(csv_file)


@pytest.fixture
def overrides_csv(tmp_path):
    """Create a temporary manual overrides CSV."""
    csv_file = tmp_path / "manual_overrides.csv"
    csv_file.write_text("station,override_occupied\n3,true\n7,false\n")
    return str(csv_file)


# ---------------------------------------------------------------------------
# Station groupings
# ---------------------------------------------------------------------------

class TestStationGroupings:
    def test_turtlebot_stations_correct(self):
        assert lab_utils.TURTLEBOT_STATIONS == {1, 2, 3, 4, 5, 11}

    def test_ur7e_stations_correct(self):
        assert lab_utils.UR7E_STATIONS == {6, 7, 8, 9, 10}

    def test_no_overlap(self):
        assert lab_utils.TURTLEBOT_STATIONS & lab_utils.UR7E_STATIONS == set()

    def test_all_stations_covered(self):
        all_stations = lab_utils.TURTLEBOT_STATIONS | lab_utils.UR7E_STATIONS
        assert all_stations == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}


# ---------------------------------------------------------------------------
# DESK_REGEX
# ---------------------------------------------------------------------------

class TestDeskRegex:
    def test_all_stations_have_regex(self):
        all_stations = lab_utils.TURTLEBOT_STATIONS | lab_utils.UR7E_STATIONS
        for s in all_stations:
            assert str(s) in lab_utils.DESK_REGEX

    def test_regex_matches_svg_pattern(self):
        svg_snippet = '<path id="desk-3" d="M10 20" fill="#FF0000" />'
        pattern = lab_utils.DESK_REGEX['3']
        match = pattern.search(svg_snippet)
        assert match is not None

    def test_regex_substitution(self):
        svg_snippet = '<path id="desk-7" d="M10 20" fill="#FF0000" />'
        pattern = lab_utils.DESK_REGEX['7']
        result = pattern.sub(r'\g<1>#00FF00\2', svg_snippet)
        assert '#00FF00' in result
        assert '#FF0000' not in result


# ---------------------------------------------------------------------------
# file_lock
# ---------------------------------------------------------------------------

class TestFileLock:
    def test_lock_creates_lock_file(self, tmp_csv):
        with lab_utils.file_lock(tmp_csv):
            assert os.path.exists(tmp_csv + '.lock')

    def test_lock_allows_read_inside(self, tmp_csv):
        with lab_utils.file_lock(tmp_csv):
            with open(tmp_csv, 'r') as f:
                content = f.read()
            assert 'Alice' in content

    def test_lock_allows_write_inside(self, tmp_csv):
        with lab_utils.file_lock(tmp_csv):
            with open(tmp_csv, 'a') as f:
                f.write("Bob,bob@b.edu\n")
        with open(tmp_csv, 'r') as f:
            content = f.read()
        assert 'Bob' in content

    def test_concurrent_locks_serialize(self, tmp_csv):
        """Two threads locking the same file should not corrupt data."""
        results = []

        def append_row(name):
            with lab_utils.file_lock(tmp_csv):
                with open(tmp_csv, 'r') as f:
                    reader = csv.DictReader(f)
                    entries = list(reader)
                entries.append({'name': name, 'email': f'{name}@b.edu'})
                with open(tmp_csv, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=['name', 'email'])
                    writer.writeheader()
                    writer.writerows(entries)
                results.append(name)

        threads = [threading.Thread(target=append_row, args=(f'User{i}',)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 5 should have been written (plus original Alice)
        with open(tmp_csv, 'r') as f:
            reader = csv.DictReader(f)
            entries = list(reader)
        assert len(entries) == 6  # Alice + 5 users


# ---------------------------------------------------------------------------
# get_manual_overrides
# ---------------------------------------------------------------------------

class TestGetManualOverrides:
    def test_reads_overrides(self, overrides_csv):
        with mock.patch.object(lab_utils, 'MANUAL_OVERRIDES_CSV_PATH', overrides_csv):
            result = lab_utils.get_manual_overrides()
        assert result == {3: True, 7: False}

    def test_missing_file_returns_empty(self):
        with mock.patch.object(lab_utils, 'MANUAL_OVERRIDES_CSV_PATH', '/nonexistent/path.csv'):
            result = lab_utils.get_manual_overrides()
        assert result == {}

    def test_malformed_file_returns_empty(self, tmp_path):
        bad_csv = tmp_path / "bad.csv"
        bad_csv.write_text("garbage data no headers")
        with mock.patch.object(lab_utils, 'MANUAL_OVERRIDES_CSV_PATH', str(bad_csv)):
            result = lab_utils.get_manual_overrides()
        assert result == {}


# ---------------------------------------------------------------------------
# Calendar event parsing & caching
# ---------------------------------------------------------------------------

class TestGetCurrentLabEvent:
    def test_no_calendar_file(self):
        with mock.patch.object(lab_utils, 'CALENDAR_PATH', '/nonexistent/cal.ics'):
            result = lab_utils.get_current_lab_event()
        assert result == {'type': None, 'class': None}

    def test_caching_returns_same_result(self, tmp_path):
        """Second call within TTL should not re-parse."""
        ics_file = tmp_path / "cal.ics"
        ics_file.write_bytes(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")

        with mock.patch.object(lab_utils, 'CALENDAR_PATH', str(ics_file)):
            result1 = lab_utils.get_current_lab_event()
            # Manually check that cache is populated
            assert lab_utils._calendar_cache['result'] is not None
            result2 = lab_utils.get_current_lab_event()

        assert result1 == result2
        assert result1 == {'type': None, 'class': None}

    def test_cache_invalidated_on_file_change(self, tmp_path):
        """Cache should be invalidated when file mtime changes."""
        ics_file = tmp_path / "cal.ics"
        ics_file.write_bytes(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")

        with mock.patch.object(lab_utils, 'CALENDAR_PATH', str(ics_file)):
            lab_utils.get_current_lab_event()
            old_cache_time = lab_utils._calendar_cache['time']

            # Touch the file to change mtime
            time.sleep(0.1)
            ics_file.write_bytes(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")

            lab_utils.get_current_lab_event()
            new_cache_time = lab_utils._calendar_cache['time']

        # Cache should have been refreshed
        assert new_cache_time > old_cache_time


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    def test_is_lab_oh_time(self):
        with mock.patch.object(lab_utils, 'get_current_lab_event', return_value={'type': 'lab_oh', 'class': '106A'}):
            assert lab_utils.is_lab_oh_time() is True

    def test_is_lab_oh_time_false(self):
        with mock.patch.object(lab_utils, 'get_current_lab_event', return_value={'type': None, 'class': None}):
            assert lab_utils.is_lab_oh_time() is False

    def test_is_lab_section_time(self):
        with mock.patch.object(lab_utils, 'get_current_lab_event', return_value={'type': 'lab_section', 'class': '106B'}):
            assert lab_utils.is_lab_section_time() is True

    def test_is_maintenance_time(self):
        with mock.patch.object(lab_utils, 'get_current_lab_event', return_value={'type': 'maintenance', 'class': None}):
            assert lab_utils.is_maintenance_time() is True

    def test_is_lab_active_time_oh(self):
        with mock.patch.object(lab_utils, 'get_current_lab_event', return_value={'type': 'lab_oh', 'class': None}):
            assert lab_utils.is_lab_active_time() is True

    def test_is_lab_active_time_section(self):
        with mock.patch.object(lab_utils, 'get_current_lab_event', return_value={'type': 'lab_section', 'class': None}):
            assert lab_utils.is_lab_active_time() is True

    def test_is_lab_active_time_idle(self):
        with mock.patch.object(lab_utils, 'get_current_lab_event', return_value={'type': None, 'class': None}):
            assert lab_utils.is_lab_active_time() is False
