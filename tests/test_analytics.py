"""Tests for analytics endpoints, export, and squawk detection."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope='session')
def app():
    """Create application for testing."""
    import app as app_module
    import utils.database as db_mod
    from routes import register_blueprints

    app_module.app.config['TESTING'] = True

    # Use temp directory for test database
    tmp_dir = Path(tempfile.mkdtemp())
    db_mod.DB_DIR = tmp_dir
    db_mod.DB_PATH = tmp_dir / 'test_intercept.db'
    # Reset thread-local connection so it picks up new path
    if hasattr(db_mod._local, 'connection') and db_mod._local.connection:
        db_mod._local.connection.close()
        db_mod._local.connection = None

    db_mod.init_db()

    if 'pager' not in app_module.app.blueprints:
        register_blueprints(app_module.app)

    return app_module.app


@pytest.fixture
def client(app):
    client = app.test_client()
    # Set session login to bypass require_login before_request hook
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


class TestAnalyticsSummary:
    """Tests for /analytics/summary endpoint."""

    def test_summary_returns_json(self, client):
        response = client.get('/analytics/summary')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'success'
        assert 'counts' in data
        assert 'health' in data
        assert 'squawks' in data

    def test_summary_counts_structure(self, client):
        response = client.get('/analytics/summary')
        data = json.loads(response.data)
        counts = data['counts']
        assert 'adsb' in counts
        assert 'ais' in counts
        assert 'wifi' in counts
        assert 'bluetooth' in counts
        assert 'dsc' in counts
        # All should be integers
        for val in counts.values():
            assert isinstance(val, int)

    def test_summary_health_structure(self, client):
        response = client.get('/analytics/summary')
        data = json.loads(response.data)
        health = data['health']
        # Should have process statuses
        assert 'pager' in health
        assert 'sensor' in health
        assert 'adsb' in health
        # Each should have a running flag
        for mode_info in health.values():
            if isinstance(mode_info, dict) and 'running' in mode_info:
                assert isinstance(mode_info['running'], bool)


class TestAnalyticsExport:
    """Tests for /analytics/export/<mode> endpoint."""

    def test_export_adsb_json(self, client):
        response = client.get('/analytics/export/adsb?format=json')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'success'
        assert data['mode'] == 'adsb'
        assert 'data' in data
        assert isinstance(data['data'], list)

    def test_export_adsb_csv(self, client):
        response = client.get('/analytics/export/adsb?format=csv')
        assert response.status_code == 200
        assert response.content_type.startswith('text/csv')
        assert 'Content-Disposition' in response.headers

    def test_export_invalid_mode(self, client):
        response = client.get('/analytics/export/invalid_mode')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['status'] == 'error'

    def test_export_wifi_json(self, client):
        response = client.get('/analytics/export/wifi?format=json')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'success'
        assert data['mode'] == 'wifi'


class TestAnalyticsSquawks:
    """Tests for squawk detection."""

    def test_squawks_endpoint(self, client):
        response = client.get('/analytics/squawks')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'success'
        assert isinstance(data['squawks'], list)

    def test_get_emergency_squawks_detects_7700(self):
        from utils.analytics import get_emergency_squawks

        # Mock the adsb_aircraft DataStore
        mock_store = MagicMock()
        mock_store.items.return_value = [
            ('ABC123', {'squawk': '7700', 'callsign': 'TEST01', 'altitude': 35000}),
            ('DEF456', {'squawk': '1200', 'callsign': 'TEST02'}),
        ]

        with patch('utils.analytics.app_module') as mock_app:
            mock_app.adsb_aircraft = mock_store
            squawks = get_emergency_squawks()

        assert len(squawks) == 1
        assert squawks[0]['squawk'] == '7700'
        assert squawks[0]['meaning'] == 'General Emergency'
        assert squawks[0]['icao'] == 'ABC123'


class TestGeofenceCRUD:
    """Tests for geofence CRUD endpoints."""

    def test_list_geofences(self, client):
        response = client.get('/analytics/geofences')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'success'
        assert isinstance(data['zones'], list)

    def test_create_geofence(self, client):
        response = client.post('/analytics/geofences',
                               data=json.dumps({
                                   'name': 'Test Zone',
                                   'lat': 51.5074,
                                   'lon': -0.1278,
                                   'radius_m': 500,
                               }),
                               content_type='application/json')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'success'
        assert 'zone_id' in data

    def test_create_geofence_missing_fields(self, client):
        response = client.post('/analytics/geofences',
                               data=json.dumps({'name': 'No coords'}),
                               content_type='application/json')
        assert response.status_code == 400

    def test_create_geofence_invalid_coords(self, client):
        response = client.post('/analytics/geofences',
                               data=json.dumps({
                                   'name': 'Bad',
                                   'lat': 100,
                                   'lon': 0,
                                   'radius_m': 100,
                               }),
                               content_type='application/json')
        assert response.status_code == 400

    def test_delete_geofence_not_found(self, client):
        response = client.delete('/analytics/geofences/99999')
        assert response.status_code == 404


class TestAnalyticsActivity:
    """Tests for /analytics/activity endpoint."""

    def test_activity_returns_sparklines(self, client):
        response = client.get('/analytics/activity')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'success'
        assert 'sparklines' in data
        assert isinstance(data['sparklines'], dict)
