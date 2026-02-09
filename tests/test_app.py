"""Tests for app.py Flask routes and API endpoints."""
import os
import csv
import json
import time
from unittest import mock
from datetime import datetime, timedelta

import pytest

# Must be imported after lab_utils
from app import app
import lab_utils


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Flask test client with testing config."""
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def reset_caches():
    """Reset all caches between tests."""
    import app as app_module
    app_module._svg_cache = {'content': None, 'hash': None, 'time': 0}
    app_module._about_content = None
    app_module._admin_cache = {'emails': None, 'time': 0, 'mtime': 0}
    lab_utils._calendar_cache = {'result': None, 'time': 0, 'mtime': 0}
    yield


@pytest.fixture
def tmp_queues(tmp_path):
    """Create temporary queue CSV files."""
    tb_path = tmp_path / "queue_turtlebot.csv"
    ur_path = tmp_path / "queue_ur7e.csv"
    tb_path.write_text("name,email\nAlice,alice@berkeley.edu\n")
    ur_path.write_text("name,email\nBob,bob@berkeley.edu\n")
    return str(tb_path), str(ur_path)


@pytest.fixture
def mock_session(client):
    """Helper to set a user session."""
    with client.session_transaction() as sess:
        sess['user'] = {
            'email': 'testuser@berkeley.edu',
            'name': 'Test User',
            'picture': 'https://example.com/pic.jpg'
        }


@pytest.fixture
def mock_admin_session(client):
    """Helper to set an admin user session."""
    with client.session_transaction() as sess:
        sess['user'] = {
            'email': 'danielmunicio@berkeley.edu',
            'name': 'Daniel',
            'picture': 'https://example.com/pic.jpg'
        }


# ---------------------------------------------------------------------------
# Index / Static routes
# ---------------------------------------------------------------------------

class TestIndexRoute:
    def test_index_returns_200(self, client):
        resp = client.get('/')
        assert resp.status_code == 200

    def test_index_contains_lab_status(self, client):
        resp = client.get('/')
        html = resp.data.decode()
        assert 'Lab Status:' in html

    def test_index_contains_svg(self, client):
        resp = client.get('/')
        html = resp.data.decode()
        assert 'lab_room.svg' in html


class TestAboutRoute:
    def test_about_returns_200(self, client):
        resp = client.get('/about')
        assert resp.status_code == 200

    def test_about_caches_content(self, client):
        import app as app_module
        assert app_module._about_content is None
        client.get('/about')
        assert app_module._about_content is not None
        # Second request uses cache
        client.get('/about')


# ---------------------------------------------------------------------------
# SVG endpoint
# ---------------------------------------------------------------------------

class TestSvgEndpoint:
    def test_svg_returns_svg_content_type(self, client):
        resp = client.get('/lab_room.svg')
        assert resp.status_code == 200
        assert 'image/svg+xml' in resp.content_type

    def test_svg_has_cache_headers(self, client):
        resp = client.get('/lab_room.svg')
        assert resp.headers.get('Cache-Control') == 'public, max-age=5'
        assert resp.headers.get('ETag') is not None

    def test_svg_contains_desk_paths(self, client):
        resp = client.get('/lab_room.svg')
        svg = resp.data.decode()
        assert 'desk-1' in svg

    def test_svg_caching_works(self, client):
        import app as app_module
        resp1 = client.get('/lab_room.svg')
        etag1 = resp1.headers.get('ETag')

        # Second request within TTL should return same ETag
        resp2 = client.get('/lab_room.svg')
        etag2 = resp2.headers.get('ETag')
        assert etag1 == etag2


# ---------------------------------------------------------------------------
# Lab Data API
# ---------------------------------------------------------------------------

class TestLabDataApi:
    def test_returns_json(self, client):
        resp = client.get('/api/lab-data')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'status' in data
        assert 'queue' in data

    def test_status_has_required_fields(self, client):
        resp = client.get('/api/lab-data')
        data = resp.get_json()
        status = data['status']
        assert 'state' in status
        assert 'color' in status
        assert 'turtlebots_available' in status
        assert 'ur7es_available' in status
        assert 'show_ur7e_queue' in status
        assert 'show_turtlebot_queue' in status

    def test_queue_has_both_types(self, client):
        resp = client.get('/api/lab-data')
        data = resp.get_json()
        assert 'turtlebot' in data['queue']
        assert 'ur7e' in data['queue']


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

class TestAuthEndpoints:
    def test_get_user_unauthenticated(self, client):
        resp = client.get('/api/auth/user')
        assert resp.status_code == 401

    def test_get_user_authenticated(self, client, mock_session):
        resp = client.get('/api/auth/user')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['email'] == 'testuser@berkeley.edu'

    def test_logout(self, client, mock_session):
        resp = client.post('/api/auth/logout')
        assert resp.status_code == 200

        # Should be logged out now
        resp = client.get('/api/auth/user')
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Queue API endpoints
# ---------------------------------------------------------------------------

class TestQueueAdd:
    def test_add_requires_auth(self, client):
        resp = client.post('/api/queue/add',
                          json={'queue_type': 'turtlebot'})
        assert resp.status_code == 401

    def test_add_invalid_type(self, client, mock_session):
        resp = client.post('/api/queue/add',
                          json={'queue_type': 'invalid'})
        assert resp.status_code == 400

    def test_add_to_queue_success(self, client, mock_session, tmp_path):
        csv_path = str(tmp_path / "queue_turtlebot.csv")
        with mock.patch.object(lab_utils, 'QUEUE_TURTLEBOT_CSV_PATH', csv_path):
            resp = client.post('/api/queue/add',
                              json={'queue_type': 'turtlebot'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True

    def test_add_duplicate_rejected(self, client, mock_session, tmp_path):
        csv_path = str(tmp_path / "queue_turtlebot.csv")
        with mock.patch.object(lab_utils, 'QUEUE_TURTLEBOT_CSV_PATH', csv_path):
            client.post('/api/queue/add', json={'queue_type': 'turtlebot'})
            resp = client.post('/api/queue/add', json={'queue_type': 'turtlebot'})
        assert resp.status_code == 400
        assert 'already in this queue' in resp.get_json()['error']


class TestQueueRemove:
    def test_remove_requires_admin(self, client, mock_session):
        resp = client.post('/api/queue/remove',
                          json={'queue_type': 'turtlebot', 'email': 'a@b.edu'})
        assert resp.status_code == 403

    def test_remove_success(self, client, mock_admin_session, tmp_queues):
        tb_path, _ = tmp_queues
        with mock.patch.object(lab_utils, 'QUEUE_TURTLEBOT_CSV_PATH', tb_path):
            resp = client.post('/api/queue/remove',
                              json={'queue_type': 'turtlebot', 'email': 'alice@berkeley.edu'})
        assert resp.status_code == 200

    def test_remove_nonexistent_user(self, client, mock_admin_session, tmp_queues):
        tb_path, _ = tmp_queues
        with mock.patch.object(lab_utils, 'QUEUE_TURTLEBOT_CSV_PATH', tb_path):
            resp = client.post('/api/queue/remove',
                              json={'queue_type': 'turtlebot', 'email': 'nobody@b.edu'})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Station override API
# ---------------------------------------------------------------------------

class TestStationOverride:
    def test_override_requires_admin(self, client, mock_session):
        resp = client.post('/api/station/override',
                          json={'station': 1, 'override_occupied': True})
        assert resp.status_code == 403

    def test_set_override(self, client, mock_admin_session, tmp_path):
        csv_path = str(tmp_path / "overrides.csv")
        with mock.patch.object(lab_utils, 'MANUAL_OVERRIDES_CSV_PATH', csv_path):
            resp = client.post('/api/station/override',
                              json={'station': 3, 'override_occupied': True})
        assert resp.status_code == 200
        assert 'occupied' in resp.get_json()['message']

    def test_invalid_station(self, client, mock_admin_session):
        resp = client.post('/api/station/override',
                          json={'station': 99, 'override_occupied': True})
        assert resp.status_code == 400

    def test_get_overrides(self, client):
        resp = client.get('/api/station/overrides')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'overrides' in data


# ---------------------------------------------------------------------------
# Admin page
# ---------------------------------------------------------------------------

class TestAdminPage:
    def test_admin_unauthenticated(self, client):
        resp = client.get('/admin')
        assert resp.status_code == 200
        assert b'sign in' in resp.data.lower()

    def test_admin_non_admin_user(self, client, mock_session):
        resp = client.get('/admin')
        assert resp.status_code == 200
        assert b'do not have admin access' in resp.data.lower()

    def test_admin_authorized(self, client, mock_admin_session):
        resp = client.get('/admin')
        assert resp.status_code == 200
        assert b'Admin Page' in resp.data


# ---------------------------------------------------------------------------
# Lab status logic
# ---------------------------------------------------------------------------

class TestLabStatus:
    def test_state_open_when_available(self):
        from app import determine_lab_state
        with mock.patch('app.get_current_lab_event',
                              return_value={'type': None, 'class': None}):
            state = determine_lab_state(5)
        assert state == 'Open'

    def test_state_full_when_zero_available(self):
        from app import determine_lab_state
        with mock.patch('app.get_current_lab_event',
                              return_value={'type': None, 'class': None}):
            state = determine_lab_state(0)
        assert state == 'Full'

    def test_state_lab_oh_from_calendar(self):
        from app import determine_lab_state
        with mock.patch('app.get_current_lab_event',
                              return_value={'type': 'lab_oh', 'class': '106A'}):
            state = determine_lab_state(5)
        assert state == '106A Lab OH'

    def test_state_lab_section_from_calendar(self):
        from app import determine_lab_state
        with mock.patch('app.get_current_lab_event',
                              return_value={'type': 'lab_section', 'class': '106B'}):
            state = determine_lab_state(5)
        assert state == '106B Lab Section'

    def test_maintenance_shows_full(self):
        from app import determine_lab_state
        with mock.patch('app.get_current_lab_event',
                              return_value={'type': 'maintenance', 'class': None}):
            state = determine_lab_state(5)
        assert state == 'Full'
