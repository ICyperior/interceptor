"""Tests for the System Health monitoring blueprint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _login(client):
    """Mark the Flask test session as authenticated."""
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'test'
        sess['role'] = 'admin'


def test_metrics_returns_expected_keys(client):
    """GET /system/metrics returns top-level metric keys."""
    _login(client)
    resp = client.get('/system/metrics')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'system' in data
    assert 'processes' in data
    assert 'cpu' in data
    assert 'memory' in data
    assert 'disk' in data
    assert data['system']['hostname']
    assert 'version' in data['system']
    assert 'uptime_seconds' in data['system']
    assert 'uptime_human' in data['system']


def test_metrics_without_psutil(client):
    """Metrics degrade gracefully when psutil is unavailable."""
    _login(client)
    import routes.system as mod

    orig = mod._HAS_PSUTIL
    mod._HAS_PSUTIL = False
    try:
        resp = client.get('/system/metrics')
        assert resp.status_code == 200
        data = resp.get_json()
        # These fields should be None without psutil
        assert data['cpu'] is None
        assert data['memory'] is None
        assert data['disk'] is None
    finally:
        mod._HAS_PSUTIL = orig


def test_sdr_devices_returns_list(client):
    """GET /system/sdr_devices returns a devices list."""
    _login(client)
    mock_device = MagicMock()
    mock_device.sdr_type = MagicMock()
    mock_device.sdr_type.value = 'rtlsdr'
    mock_device.index = 0
    mock_device.name = 'Generic RTL2832U'
    mock_device.serial = '00000001'
    mock_device.driver = 'rtlsdr'

    with patch('utils.sdr.detection.detect_all_devices', return_value=[mock_device]):
        resp = client.get('/system/sdr_devices')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'devices' in data
    assert len(data['devices']) == 1
    assert data['devices'][0]['type'] == 'rtlsdr'
    assert data['devices'][0]['name'] == 'Generic RTL2832U'


def test_sdr_devices_handles_detection_failure(client):
    """SDR detection failure returns empty list with error."""
    _login(client)
    with patch('utils.sdr.detection.detect_all_devices', side_effect=RuntimeError('no devices')):
        resp = client.get('/system/sdr_devices')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['devices'] == []
    assert 'error' in data


def test_stream_returns_sse_content_type(client):
    """GET /system/stream returns text/event-stream."""
    _login(client)
    resp = client.get('/system/stream')
    assert resp.status_code == 200
    assert 'text/event-stream' in resp.content_type
