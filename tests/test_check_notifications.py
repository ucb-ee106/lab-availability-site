"""Tests for check_notifications.py logic."""
import os
import csv
import json
from unittest import mock
from datetime import datetime, timedelta

import pytest

import lab_utils
from check_notifications import (
    get_current_states,
    get_previous_states,
    save_states,
    get_first_in_queue,
    has_pending_claim,
    person_has_active_claim,
    check_expired_claims,
)


@pytest.fixture(autouse=True)
def reset_cache():
    lab_utils._calendar_cache = {'result': None, 'time': 0, 'mtime': 0}
    yield


# ---------------------------------------------------------------------------
# get_current_states
# ---------------------------------------------------------------------------

class TestGetCurrentStates:
    def test_reads_station_status(self, tmp_path):
        csv_file = tmp_path / "station_status.csv"
        csv_file.write_text("station,occupied\n1,true\n2,false\n3,1\n4,0\n")
        with mock.patch('check_notifications.STATION_STATUS_CSV_PATH', str(csv_file)):
            with mock.patch('check_notifications.get_manual_overrides', return_value={}):
                states = get_current_states()
        assert states == {1: True, 2: False, 3: True, 4: False}

    def test_applies_overrides(self, tmp_path):
        csv_file = tmp_path / "station_status.csv"
        csv_file.write_text("station,occupied\n1,true\n2,false\n")
        with mock.patch('check_notifications.STATION_STATUS_CSV_PATH', str(csv_file)):
            with mock.patch('check_notifications.get_manual_overrides', return_value={2: True}):
                states = get_current_states()
        assert states[2] is True  # Overridden from False to True

    def test_missing_file_returns_empty(self):
        with mock.patch('check_notifications.STATION_STATUS_CSV_PATH', '/nonexistent'):
            states = get_current_states()
        assert states == {}


# ---------------------------------------------------------------------------
# get/save previous states
# ---------------------------------------------------------------------------

class TestPreviousStates:
    def test_roundtrip(self, tmp_path):
        states_file = tmp_path / "previous.json"
        with mock.patch('check_notifications.PREVIOUS_STATES_PATH', str(states_file)):
            save_states({1: True, 2: False})
            loaded = get_previous_states()
        assert loaded == {1: True, 2: False}

    def test_missing_file_returns_empty(self):
        with mock.patch('check_notifications.PREVIOUS_STATES_PATH', '/nonexistent'):
            assert get_previous_states() == {}


# ---------------------------------------------------------------------------
# get_first_in_queue
# ---------------------------------------------------------------------------

class TestGetFirstInQueue:
    def test_returns_first_person(self, tmp_path):
        csv_file = tmp_path / "queue.csv"
        csv_file.write_text("name,email\nAlice,a@b.edu\nBob,b@b.edu\n")
        with mock.patch('check_notifications.QUEUE_TURTLEBOT_CSV_PATH', str(csv_file)):
            person = get_first_in_queue('turtlebot')
        assert person == {'name': 'Alice', 'email': 'a@b.edu'}

    def test_empty_queue_returns_none(self, tmp_path):
        csv_file = tmp_path / "queue.csv"
        csv_file.write_text("name,email\n")
        with mock.patch('check_notifications.QUEUE_TURTLEBOT_CSV_PATH', str(csv_file)):
            person = get_first_in_queue('turtlebot')
        assert person is None

    def test_missing_file_returns_none(self):
        with mock.patch('check_notifications.QUEUE_TURTLEBOT_CSV_PATH', '/nonexistent'):
            person = get_first_in_queue('turtlebot')
        assert person is None


# ---------------------------------------------------------------------------
# has_pending_claim / person_has_active_claim
# ---------------------------------------------------------------------------

class TestClaimChecks:
    def _make_claim(self, station_type='turtlebot', email='a@b.edu',
                    expired=False, confirmed=False):
        expires = datetime.now() + (timedelta(minutes=-1) if expired else timedelta(minutes=5))
        return {
            'station_type': station_type,
            'email': email,
            'station': '1',
            'claim_token': 'tok123',
            'expires_at': expires.isoformat(),
            'confirmed': 'true' if confirmed else 'false',
        }

    def test_has_pending_claim_active(self):
        claims = [self._make_claim()]
        assert has_pending_claim('turtlebot', claims) is True

    def test_has_pending_claim_none(self):
        assert has_pending_claim('turtlebot', []) is False

    def test_has_pending_claim_expired(self):
        claims = [self._make_claim(expired=True)]
        assert has_pending_claim('turtlebot', claims) is False

    def test_has_pending_claim_confirmed_never_expires(self):
        claims = [self._make_claim(expired=True, confirmed=True)]
        assert has_pending_claim('turtlebot', claims) is True

    def test_person_has_active_claim(self):
        claims = [self._make_claim(email='test@b.edu')]
        assert person_has_active_claim('test@b.edu', claims) is True
        assert person_has_active_claim('other@b.edu', claims) is False


# ---------------------------------------------------------------------------
# check_expired_claims
# ---------------------------------------------------------------------------

class TestCheckExpiredClaims:
    def test_keeps_active_claims(self, tmp_path):
        claims_path = tmp_path / "claims.csv"
        claims_path.write_text("email,name,station_type,station,claim_token,expires_at,confirmed\n")

        active_claim = {
            'email': 'a@b.edu', 'name': 'Alice', 'station_type': 'turtlebot',
            'station': '1', 'claim_token': 'tok1',
            'expires_at': (datetime.now() + timedelta(minutes=3)).isoformat(),
            'confirmed': 'false',
        }

        with mock.patch('check_notifications.PENDING_CLAIMS_CSV_PATH', str(claims_path)):
            result = check_expired_claims([active_claim], {1: False})
        assert len(result) == 1

    def test_removes_expired_unconfirmed(self, tmp_path):
        claims_path = tmp_path / "claims.csv"
        claims_path.write_text("email,name,station_type,station,claim_token,expires_at,confirmed\n")
        queue_path = tmp_path / "queue_turtlebot.csv"
        queue_path.write_text("name,email\nAlice,a@b.edu\n")

        expired_claim = {
            'email': 'a@b.edu', 'name': 'Alice', 'station_type': 'turtlebot',
            'station': '1', 'claim_token': 'tok1',
            'expires_at': (datetime.now() - timedelta(minutes=1)).isoformat(),
            'confirmed': 'false',
        }

        with mock.patch('check_notifications.PENDING_CLAIMS_CSV_PATH', str(claims_path)), \
             mock.patch('check_notifications.QUEUE_TURTLEBOT_CSV_PATH', str(queue_path)), \
             mock.patch('check_notifications.send_notification_email'):
            result = check_expired_claims([expired_claim], {1: False})
        assert len(result) == 0

    def test_confirmed_claim_cleared_when_station_occupied(self, tmp_path):
        claims_path = tmp_path / "claims.csv"
        claims_path.write_text("email,name,station_type,station,claim_token,expires_at,confirmed\n")

        confirmed_claim = {
            'email': 'a@b.edu', 'name': 'Alice', 'station_type': 'turtlebot',
            'station': '1', 'claim_token': 'tok1',
            'expires_at': (datetime.now() + timedelta(minutes=3)).isoformat(),
            'confirmed': 'true',
        }

        # Station 1 is now occupied (user logged in)
        with mock.patch('check_notifications.PENDING_CLAIMS_CSV_PATH', str(claims_path)):
            result = check_expired_claims([confirmed_claim], {1: True})
        assert len(result) == 0  # Claim should be cleared
