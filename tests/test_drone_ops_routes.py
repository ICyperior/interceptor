"""Tests for Drone Ops API routes."""

from __future__ import annotations

import pytest

import utils.database as db_mod
from utils.drone import get_drone_ops_service


def _set_identity(client, role: str, username: str = 'tester') -> None:
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['role'] = role
        sess['username'] = username


def _clear_drone_tables() -> None:
    with db_mod.get_db() as conn:
        conn.execute('DELETE FROM action_audit_log')
        conn.execute('DELETE FROM action_approvals')
        conn.execute('DELETE FROM action_requests')
        conn.execute('DELETE FROM evidence_manifests')
        conn.execute('DELETE FROM drone_incident_artifacts')
        conn.execute('DELETE FROM drone_tracks')
        conn.execute('DELETE FROM drone_correlations')
        conn.execute('DELETE FROM drone_detections')
        conn.execute('DELETE FROM drone_incidents')
        conn.execute('DELETE FROM drone_sessions')


@pytest.fixture(scope='module', autouse=True)
def isolated_drone_db(tmp_path_factory):
    original_db_dir = db_mod.DB_DIR
    original_db_path = db_mod.DB_PATH

    tmp_dir = tmp_path_factory.mktemp('drone_ops_db')
    db_mod.DB_DIR = tmp_dir
    db_mod.DB_PATH = tmp_dir / 'test_intercept.db'

    if hasattr(db_mod._local, 'connection') and db_mod._local.connection is not None:
        db_mod._local.connection.close()
        db_mod._local.connection = None

    db_mod.init_db()
    yield

    db_mod.close_db()
    db_mod.DB_DIR = original_db_dir
    db_mod.DB_PATH = original_db_path
    db_mod._local.connection = None


@pytest.fixture(autouse=True)
def clean_drone_state():
    db_mod.init_db()
    _clear_drone_tables()
    get_drone_ops_service().disarm_actions(actor='test-reset', reason='test setup')
    yield
    _clear_drone_tables()
    get_drone_ops_service().disarm_actions(actor='test-reset', reason='test teardown')


def test_start_session_requires_operator_role(client):
    _set_identity(client, role='viewer')
    response = client.post('/drone-ops/session/start', json={'mode': 'passive'})
    assert response.status_code == 403
    data = response.get_json()
    assert data['required_role'] == 'operator'


def test_session_lifecycle_and_status(client):
    _set_identity(client, role='operator', username='op1')

    started = client.post('/drone-ops/session/start', json={'mode': 'passive'})
    assert started.status_code == 200
    start_data = started.get_json()
    assert start_data['status'] == 'success'
    assert start_data['session']['mode'] == 'passive'
    assert start_data['session']['active'] is True

    status = client.get('/drone-ops/status')
    assert status.status_code == 200
    status_data = status.get_json()
    assert status_data['status'] == 'success'
    assert status_data['active_session'] is not None
    assert status_data['active_session']['id'] == start_data['session']['id']

    stopped = client.post('/drone-ops/session/stop', json={'id': start_data['session']['id']})
    assert stopped.status_code == 200
    stop_data = stopped.get_json()
    assert stop_data['status'] == 'success'
    assert stop_data['session']['active'] is False


def test_detection_ingest_visible_via_endpoint(client):
    _set_identity(client, role='operator', username='op1')
    start_resp = client.post('/drone-ops/session/start', json={'mode': 'passive'})
    assert start_resp.status_code == 200

    service = get_drone_ops_service()
    service.ingest_event(
        mode='wifi',
        event={
            'bssid': '60:60:1F:AA:BB:CC',
            'ssid': 'DJI-OPS-TEST',
        },
        event_type='network_update',
    )

    _set_identity(client, role='viewer', username='viewer1')
    response = client.get('/drone-ops/detections?source=wifi&min_confidence=0.5')
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'success'
    assert data['count'] >= 1
    detection = data['detections'][0]
    assert detection['source'] == 'wifi'
    assert detection['confidence'] >= 0.5


def test_incident_artifact_and_manifest_flow(client):
    _set_identity(client, role='operator', username='op1')
    created = client.post(
        '/drone-ops/incidents',
        json={'title': 'Unidentified UAS', 'severity': 'high'},
    )
    assert created.status_code == 201
    incident = created.get_json()['incident']
    incident_id = incident['id']

    artifact_resp = client.post(
        f'/drone-ops/incidents/{incident_id}/artifacts',
        json={'artifact_type': 'detection', 'artifact_ref': '12345'},
    )
    assert artifact_resp.status_code == 201

    _set_identity(client, role='analyst', username='analyst1')
    manifest_resp = client.post(f'/drone-ops/evidence/{incident_id}/manifest', json={})
    assert manifest_resp.status_code == 201
    manifest = manifest_resp.get_json()['manifest']
    assert manifest['manifest']['integrity']['algorithm'] == 'sha256'
    assert len(manifest['manifest']['integrity']['digest']) == 64

    _set_identity(client, role='viewer', username='viewer1')
    listed = client.get(f'/drone-ops/evidence/{incident_id}/manifests')
    assert listed.status_code == 200
    listed_data = listed.get_json()
    assert listed_data['count'] == 1
    assert listed_data['manifests'][0]['id'] == manifest['id']


def test_action_execution_requires_arming_and_two_approvals(client):
    _set_identity(client, role='operator', username='op1')
    incident_resp = client.post('/drone-ops/incidents', json={'title': 'Action Gate Test'})
    incident_id = incident_resp.get_json()['incident']['id']

    request_resp = client.post(
        '/drone-ops/actions/request',
        json={
            'incident_id': incident_id,
            'action_type': 'wifi_deauth_test',
            'payload': {'target': 'aa:bb:cc:dd:ee:ff'},
        },
    )
    assert request_resp.status_code == 201
    request_id = request_resp.get_json()['request']['id']

    not_armed_resp = client.post(f'/drone-ops/actions/execute/{request_id}', json={})
    assert not_armed_resp.status_code == 403
    assert 'not armed' in not_armed_resp.get_json()['message'].lower()

    arm_resp = client.post(
        '/drone-ops/actions/arm',
        json={'incident_id': incident_id, 'reason': 'controlled test'},
    )
    assert arm_resp.status_code == 200
    assert arm_resp.get_json()['policy']['armed'] is True

    insufficient_resp = client.post(f'/drone-ops/actions/execute/{request_id}', json={})
    assert insufficient_resp.status_code == 400
    assert 'insufficient approvals' in insufficient_resp.get_json()['message'].lower()

    _set_identity(client, role='supervisor', username='supervisor-a')
    approve_one = client.post(f'/drone-ops/actions/approve/{request_id}', json={'decision': 'approved'})
    assert approve_one.status_code == 200

    _set_identity(client, role='operator', username='op1')
    still_insufficient = client.post(f'/drone-ops/actions/execute/{request_id}', json={})
    assert still_insufficient.status_code == 400

    _set_identity(client, role='supervisor', username='supervisor-b')
    approve_two = client.post(f'/drone-ops/actions/approve/{request_id}', json={'decision': 'approved'})
    assert approve_two.status_code == 200
    assert approve_two.get_json()['request']['status'] == 'approved'

    _set_identity(client, role='operator', username='op1')
    executed = client.post(f'/drone-ops/actions/execute/{request_id}', json={})
    assert executed.status_code == 200
    assert executed.get_json()['request']['status'] == 'executed'


def test_passive_action_executes_after_single_approval(client):
    _set_identity(client, role='operator', username='op1')
    incident_resp = client.post('/drone-ops/incidents', json={'title': 'Passive Action Test'})
    incident_id = incident_resp.get_json()['incident']['id']

    request_resp = client.post(
        '/drone-ops/actions/request',
        json={'incident_id': incident_id, 'action_type': 'passive_spectrum_capture'},
    )
    request_id = request_resp.get_json()['request']['id']

    arm_resp = client.post(
        '/drone-ops/actions/arm',
        json={'incident_id': incident_id, 'reason': 'passive validation'},
    )
    assert arm_resp.status_code == 200

    _set_identity(client, role='supervisor', username='supervisor-a')
    approve_resp = client.post(f'/drone-ops/actions/approve/{request_id}', json={'decision': 'approved'})
    assert approve_resp.status_code == 200
    assert approve_resp.get_json()['request']['status'] == 'approved'

    _set_identity(client, role='operator', username='op1')
    execute_resp = client.post(f'/drone-ops/actions/execute/{request_id}', json={})
    assert execute_resp.status_code == 200
    assert execute_resp.get_json()['request']['status'] == 'executed'
