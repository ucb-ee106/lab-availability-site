"""Tests for the database code path in lab_utils data access layer.

All DB calls are mocked — no actual database connection is needed.
"""
import pymysql
from unittest import mock
from datetime import datetime, timedelta

import pytest

import lab_utils


@pytest.fixture(autouse=True)
def use_db_mode():
    """Force DATA_SOURCE='database' for all tests in this module."""
    original = lab_utils.DATA_SOURCE
    lab_utils.DATA_SOURCE = 'database'
    yield
    lab_utils.DATA_SOURCE = original


@pytest.fixture
def mock_conn():
    """Return a MagicMock pretending to be a pymysql connection."""
    conn = mock.MagicMock()
    cursor = mock.MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


# ---------------------------------------------------------------------------
# Manual overrides (DB)
# ---------------------------------------------------------------------------

class TestGetManualOverridesDB:
    def test_returns_overrides(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchall.return_value = [(3, 1), (7, 0)]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_manual_overrides()
        assert result == {3: True, 7: False}
        cursor.execute.assert_called_once_with("SELECT station, override_occupied FROM manual_overrides")

    def test_empty_table(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchall.return_value = []
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_manual_overrides()
        assert result == {}

    def test_db_error_returns_empty(self):
        with mock.patch.object(lab_utils, 'get_db_connection', side_effect=Exception("DB down")):
            result = lab_utils.get_manual_overrides()
        assert result == {}


class TestSetManualOverrideDB:
    def test_set_override(self, mock_conn):
        conn, cursor = mock_conn
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            success, msg = lab_utils.set_manual_override(3, True)
        assert success is True
        assert 'occupied' in msg
        conn.commit.assert_called_once()

    def test_clear_override(self, mock_conn):
        conn, cursor = mock_conn
        cursor.rowcount = 1
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            success, msg = lab_utils.set_manual_override(3, None)
        assert success is True
        assert 'Cleared' in msg

    def test_clear_nonexistent_returns_error(self, mock_conn):
        conn, cursor = mock_conn
        cursor.rowcount = 0
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            success, msg = lab_utils.set_manual_override(99, None)
        assert success is False
        assert 'No override' in msg


# ---------------------------------------------------------------------------
# Queue operations (DB)
# ---------------------------------------------------------------------------

class TestGetQueueDB:
    def test_returns_ordered_entries(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchall.return_value = [('Alice', 'a@b.edu'), ('Bob', 'b@b.edu')]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_queue('turtlebot')
        assert result == [
            {'name': 'Alice', 'email': 'a@b.edu'},
            {'name': 'Bob', 'email': 'b@b.edu'},
        ]

    def test_empty_queue(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchall.return_value = []
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_queue('ur7e')
        assert result == []


class TestGetFirstInQueueDB:
    def test_returns_first(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchone.return_value = ('Alice', 'a@b.edu')
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_first_in_queue('turtlebot')
        assert result == {'name': 'Alice', 'email': 'a@b.edu'}

    def test_empty_returns_none(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchone.return_value = None
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_first_in_queue('turtlebot')
        assert result is None


class TestAddToQueueDB:
    def test_success(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchone.return_value = (1,)  # next position
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            success, error = lab_utils.add_to_queue('turtlebot', 'Alice', 'a@b.edu')
        assert success is True
        assert error is None
        conn.commit.assert_called_once()

    def test_duplicate_rejected(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchone.return_value = (1,)
        cursor.execute.side_effect = [None, pymysql.IntegrityError()]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            success, error = lab_utils.add_to_queue('turtlebot', 'Alice', 'a@b.edu')
        assert success is False
        assert 'already' in error


class TestRemoveFromQueueDB:
    def test_remove_success(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchone.return_value = (2,)  # position
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.remove_from_queue('turtlebot', 'a@b.edu')
        assert result is True
        conn.commit.assert_called_once()

    def test_remove_not_found(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchone.return_value = None
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.remove_from_queue('turtlebot', 'nobody@b.edu')
        assert result is False


class TestReorderQueueDB:
    def test_move_up(self, mock_conn):
        conn, cursor = mock_conn
        # First call: find the entry
        cursor.fetchone.side_effect = [(1, 1), (2, 0)]  # (id, pos), then neighbor
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            success, error = lab_utils.reorder_queue('turtlebot', 'a@b.edu', 'up')
        assert success is True
        conn.commit.assert_called_once()

    def test_already_at_top(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchone.side_effect = [(1, 0), None]  # entry at pos 0, no neighbor above
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            success, error = lab_utils.reorder_queue('turtlebot', 'a@b.edu', 'up')
        assert success is False
        assert 'top' in error


class TestRepositionQueueDB:
    def test_reposition(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchall.return_value = [(1, 'a@b.edu', 0), (2, 'b@b.edu', 1), (3, 'c@b.edu', 2)]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            success, error = lab_utils.reposition_queue('turtlebot', 'a@b.edu', 2)
        assert success is True
        conn.commit.assert_called_once()

    def test_not_found(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchall.return_value = [(1, 'other@b.edu', 0)]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            success, error = lab_utils.reposition_queue('turtlebot', 'nobody@b.edu', 0)
        assert success is False
        assert 'not found' in error


# ---------------------------------------------------------------------------
# Claim operations (DB)
# ---------------------------------------------------------------------------

class TestGetClaimedStationsDB:
    def test_returns_active_claims(self, mock_conn):
        conn, cursor = mock_conn
        future = datetime.now() + timedelta(minutes=3)
        cursor.fetchall.return_value = [
            (5, 'Alice', future, False),
        ]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_claimed_stations()
        assert 5 in result
        assert result[5]['name'] == 'Alice'
        assert result[5]['confirmed'] is False

    def test_empty(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchall.return_value = []
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_claimed_stations()
        assert result == {}


class TestGetPendingClaimDB:
    def test_found(self, mock_conn):
        conn, cursor = mock_conn
        future = datetime.now() + timedelta(minutes=3)
        cursor.fetchone.return_value = (
            'a@b.edu', 'Alice', 'turtlebot', 5, 'tok123', future, False
        )
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_pending_claim('tok123')
        assert result is not None
        assert result['email'] == 'a@b.edu'
        assert result['station'] == '5'

    def test_not_found(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchone.return_value = None
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_pending_claim('nonexistent')
        assert result is None

    def test_expired(self, mock_conn):
        conn, cursor = mock_conn
        past = datetime.now() - timedelta(minutes=1)
        cursor.fetchone.return_value = (
            'a@b.edu', 'Alice', 'turtlebot', 5, 'tok123', past, False
        )
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.get_pending_claim('tok123')
        assert result is None


class TestCreatePendingClaimDB:
    def test_creates_claim(self, mock_conn):
        conn, cursor = mock_conn
        expires = datetime.now() + timedelta(minutes=5)
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.create_pending_claim(
                'a@b.edu', 'Alice', 'turtlebot', 5, 'tok123', expires)
        assert result is True
        conn.commit.assert_called_once()


class TestDeletePendingClaimDB:
    def test_deletes_existing(self, mock_conn):
        conn, cursor = mock_conn
        cursor.rowcount = 1
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.delete_pending_claim('tok123')
        assert result is True

    def test_not_found(self, mock_conn):
        conn, cursor = mock_conn
        cursor.rowcount = 0
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.delete_pending_claim('nonexistent')
        assert result is False


class TestMarkClaimConfirmedDB:
    def test_marks_confirmed(self, mock_conn):
        conn, cursor = mock_conn
        cursor.rowcount = 1
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            result = lab_utils.mark_claim_confirmed('tok123')
        assert result is True
        conn.commit.assert_called_once()


class TestSavePendingClaimsDB:
    def test_bulk_save(self, mock_conn):
        conn, cursor = mock_conn
        claims = [{
            'email': 'a@b.edu', 'name': 'Alice', 'station_type': 'turtlebot',
            'station': '5', 'claim_token': 'tok1',
            'expires_at': (datetime.now() + timedelta(minutes=3)).isoformat(),
            'confirmed': 'false',
        }]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            lab_utils.save_pending_claims(claims)
        conn.commit.assert_called_once()
        # Should DELETE all then INSERT each
        assert cursor.execute.call_count == 2  # DELETE + 1 INSERT


# ---------------------------------------------------------------------------
# Station state operations (DB)
# ---------------------------------------------------------------------------

class TestGetStationStatesDB:
    def test_returns_states_with_overrides(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchall.return_value = [(1, 1), (2, 0), (3, 1)]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            with mock.patch.object(lab_utils, 'get_manual_overrides', return_value={2: True}):
                states = lab_utils.get_station_states()
        assert states == {1: True, 2: True, 3: True}


class TestGetFreedStationsDB:
    def test_detects_freed(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchall.return_value = [(3,), (7,)]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            with mock.patch.object(lab_utils, 'get_manual_overrides', return_value={}):
                freed = lab_utils.get_freed_stations()
        assert freed == {3, 7}

    def test_override_excludes_station(self, mock_conn):
        conn, cursor = mock_conn
        cursor.fetchall.return_value = [(3,)]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            with mock.patch.object(lab_utils, 'get_manual_overrides', return_value={3: True}):
                freed = lab_utils.get_freed_stations()
        assert 3 not in freed


class TestGetAllPendingClaimsDB:
    def test_returns_all(self, mock_conn):
        conn, cursor = mock_conn
        future = datetime.now() + timedelta(minutes=3)
        cursor.fetchall.return_value = [
            ('a@b.edu', 'Alice', 'turtlebot', 5, 'tok1', future, False),
            ('b@b.edu', 'Bob', 'ur7e', 8, 'tok2', future, True),
        ]
        with mock.patch.object(lab_utils, 'get_db_connection', return_value=conn):
            claims = lab_utils.get_all_pending_claims()
        assert len(claims) == 2
        assert claims[0]['email'] == 'a@b.edu'
        assert claims[0]['confirmed'] == 'false'
        assert claims[1]['confirmed'] == 'true'
