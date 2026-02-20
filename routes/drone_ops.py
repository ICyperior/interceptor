"""Drone Ops routes: professional workflow for detection, incidents, actions, and evidence."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from utils.authz import current_username, require_armed, require_role
from utils.database import (
    get_action_request,
    get_drone_incident,
    get_evidence_manifest,
    list_action_audit_logs,
    list_action_requests,
    list_drone_correlations,
    list_drone_sessions,
    list_drone_incidents,
    list_evidence_manifests,
)
from utils.drone import get_drone_ops_service
from utils.sse import format_sse


drone_ops_bp = Blueprint('drone_ops', __name__, url_prefix='/drone-ops')


def _json_body() -> dict:
    return request.get_json(silent=True) or {}


@drone_ops_bp.route('/status', methods=['GET'])
@require_role('viewer')
def status() -> Response:
    service = get_drone_ops_service()
    return jsonify(service.get_status())


@drone_ops_bp.route('/sessions', methods=['GET'])
@require_role('viewer')
def list_sessions() -> Response:
    limit = max(1, min(500, request.args.get('limit', default=50, type=int)))
    active_only = request.args.get('active_only', 'false').lower() == 'true'
    return jsonify({
        'status': 'success',
        'sessions': list_drone_sessions(limit=limit, active_only=active_only),
    })


@drone_ops_bp.route('/session/start', methods=['POST'])
@require_role('operator')
def start_session() -> Response:
    data = _json_body()
    mode = str(data.get('mode') or 'passive').strip().lower()
    if mode not in {'passive', 'active'}:
        return jsonify({'status': 'error', 'message': 'mode must be passive or active'}), 400

    label = data.get('label')
    metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}

    service = get_drone_ops_service()
    session = service.start_session(
        mode=mode,
        label=label,
        operator=current_username(),
        metadata=metadata,
    )
    return jsonify({'status': 'success', 'session': session})


@drone_ops_bp.route('/session/stop', methods=['POST'])
@require_role('operator')
def stop_session() -> Response:
    data = _json_body()
    session_id = data.get('id')
    try:
        session_id_int = int(session_id) if session_id is not None else None
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'id must be an integer'}), 400

    summary = data.get('summary') if isinstance(data.get('summary'), dict) else None
    service = get_drone_ops_service()
    session = service.stop_session(
        operator=current_username(),
        session_id=session_id_int,
        summary=summary,
    )
    if not session:
        return jsonify({'status': 'error', 'message': 'No active session found'}), 404
    return jsonify({'status': 'success', 'session': session})


@drone_ops_bp.route('/detections', methods=['GET'])
@require_role('viewer')
def detections() -> Response:
    service = get_drone_ops_service()
    source = request.args.get('source')
    min_confidence = request.args.get('min_confidence', default=0.0, type=float)
    limit = max(1, min(5000, request.args.get('limit', default=200, type=int)))
    session_id = request.args.get('session_id', default=None, type=int)

    rows = service.get_detections(
        session_id=session_id,
        source=source,
        min_confidence=min_confidence,
        limit=limit,
    )
    return jsonify({'status': 'success', 'count': len(rows), 'detections': rows})


@drone_ops_bp.route('/stream', methods=['GET'])
@require_role('viewer')
def stream() -> Response:
    service = get_drone_ops_service()

    def _generate():
        for event in service.stream_events(timeout=1.0):
            evt_name = event.get('type') if isinstance(event, dict) else None
            payload = event
            yield format_sse(payload, event=evt_name)

    response = Response(_generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Connection'] = 'keep-alive'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@drone_ops_bp.route('/remote-id/decode', methods=['POST'])
@require_role('analyst')
def decode_remote_id() -> Response:
    data = _json_body()
    payload = data.get('payload')
    if payload is None:
        return jsonify({'status': 'error', 'message': 'payload is required'}), 400

    service = get_drone_ops_service()
    decoded = service.decode_remote_id(payload)
    return jsonify({'status': 'success', 'decoded': decoded})


@drone_ops_bp.route('/tracks', methods=['GET'])
@require_role('viewer')
def tracks() -> Response:
    service = get_drone_ops_service()
    detection_id = request.args.get('detection_id', default=None, type=int)
    identifier = request.args.get('identifier')
    limit = max(1, min(5000, request.args.get('limit', default=1000, type=int)))

    rows = service.get_tracks(detection_id=detection_id, identifier=identifier, limit=limit)
    return jsonify({'status': 'success', 'count': len(rows), 'tracks': rows})


@drone_ops_bp.route('/geolocate/estimate', methods=['POST'])
@require_role('analyst')
def geolocate_estimate() -> Response:
    data = _json_body()
    observations = data.get('observations')
    environment = str(data.get('environment') or 'outdoor')

    if not isinstance(observations, list) or len(observations) < 3:
        return jsonify({'status': 'error', 'message': 'at least 3 observations are required'}), 400

    service = get_drone_ops_service()
    try:
        location = service.estimate_geolocation(observations=observations, environment=environment)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    return jsonify({'status': 'success', 'location': location})


@drone_ops_bp.route('/correlations', methods=['GET'])
@require_role('analyst')
def correlations() -> Response:
    min_confidence = request.args.get('min_confidence', default=0.6, type=float)
    refresh = request.args.get('refresh', 'true').lower() == 'true'
    service = get_drone_ops_service()

    if refresh:
        rows = service.refresh_correlations(min_confidence=min_confidence)
    else:
        rows = list_drone_correlations(min_confidence=min_confidence, limit=200)

    return jsonify({'status': 'success', 'count': len(rows), 'correlations': rows})


@drone_ops_bp.route('/incidents', methods=['GET'])
@require_role('viewer')
def incidents_list() -> Response:
    status = request.args.get('status')
    limit = max(1, min(1000, request.args.get('limit', default=100, type=int)))
    rows = list_drone_incidents(status=status, limit=limit)
    return jsonify({'status': 'success', 'count': len(rows), 'incidents': rows})


@drone_ops_bp.route('/incidents', methods=['POST'])
@require_role('operator')
def incidents_create() -> Response:
    data = _json_body()
    title = str(data.get('title') or '').strip()
    if not title:
        return jsonify({'status': 'error', 'message': 'title is required'}), 400

    severity = str(data.get('severity') or 'medium').strip().lower()
    summary = data.get('summary')
    metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}

    service = get_drone_ops_service()
    incident = service.create_incident(
        title=title,
        severity=severity,
        opened_by=current_username(),
        summary=summary,
        metadata=metadata,
    )
    return jsonify({'status': 'success', 'incident': incident}), 201


@drone_ops_bp.route('/incidents/<int:incident_id>', methods=['GET'])
@require_role('viewer')
def incidents_get(incident_id: int) -> Response:
    incident = get_drone_incident(incident_id)
    if not incident:
        return jsonify({'status': 'error', 'message': 'Incident not found'}), 404
    return jsonify({'status': 'success', 'incident': incident})


@drone_ops_bp.route('/incidents/<int:incident_id>', methods=['PUT'])
@require_role('operator')
def incidents_update(incident_id: int) -> Response:
    data = _json_body()
    service = get_drone_ops_service()
    incident = service.update_incident(
        incident_id=incident_id,
        status=data.get('status'),
        severity=data.get('severity'),
        summary=data.get('summary'),
        metadata=data.get('metadata') if isinstance(data.get('metadata'), dict) else None,
    )
    if not incident:
        return jsonify({'status': 'error', 'message': 'Incident not found'}), 404
    return jsonify({'status': 'success', 'incident': incident})


@drone_ops_bp.route('/incidents/<int:incident_id>/artifacts', methods=['POST'])
@require_role('operator')
def incidents_add_artifact(incident_id: int) -> Response:
    if not get_drone_incident(incident_id):
        return jsonify({'status': 'error', 'message': 'Incident not found'}), 404

    data = _json_body()
    artifact_type = str(data.get('artifact_type') or '').strip()
    artifact_ref = str(data.get('artifact_ref') or '').strip()
    metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}

    if not artifact_type or not artifact_ref:
        return jsonify({'status': 'error', 'message': 'artifact_type and artifact_ref are required'}), 400

    service = get_drone_ops_service()
    artifact = service.add_incident_artifact(
        incident_id=incident_id,
        artifact_type=artifact_type,
        artifact_ref=artifact_ref,
        added_by=current_username(),
        metadata=metadata,
    )
    return jsonify({'status': 'success', 'artifact': artifact}), 201


@drone_ops_bp.route('/actions/arm', methods=['POST'])
@require_role('operator')
def actions_arm() -> Response:
    data = _json_body()
    reason = str(data.get('reason') or '').strip()
    incident_id = data.get('incident_id')
    duration_seconds = data.get('duration_seconds', 900)

    if not reason:
        return jsonify({'status': 'error', 'message': 'reason is required'}), 400
    try:
        incident_id_int = int(incident_id)
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'incident_id is required and must be an integer'}), 400

    if not get_drone_incident(incident_id_int):
        return jsonify({'status': 'error', 'message': 'Incident not found'}), 404

    service = get_drone_ops_service()
    state = service.arm_actions(
        actor=current_username(),
        reason=reason,
        incident_id=incident_id_int,
        duration_seconds=duration_seconds,
    )
    return jsonify({'status': 'success', 'policy': state})


@drone_ops_bp.route('/actions/disarm', methods=['POST'])
@require_role('operator')
def actions_disarm() -> Response:
    data = _json_body()
    reason = str(data.get('reason') or '').strip() or None
    service = get_drone_ops_service()
    state = service.disarm_actions(actor=current_username(), reason=reason)
    return jsonify({'status': 'success', 'policy': state})


@drone_ops_bp.route('/actions/request', methods=['POST'])
@require_role('operator')
def actions_request() -> Response:
    data = _json_body()
    try:
        incident_id = int(data.get('incident_id'))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'incident_id is required'}), 400

    if not get_drone_incident(incident_id):
        return jsonify({'status': 'error', 'message': 'Incident not found'}), 404

    action_type = str(data.get('action_type') or '').strip()
    if not action_type:
        return jsonify({'status': 'error', 'message': 'action_type is required'}), 400

    payload = data.get('payload') if isinstance(data.get('payload'), dict) else {}

    service = get_drone_ops_service()
    action_request = service.request_action(
        incident_id=incident_id,
        action_type=action_type,
        requested_by=current_username(),
        payload=payload,
    )
    return jsonify({'status': 'success', 'request': action_request}), 201


@drone_ops_bp.route('/actions/approve/<int:request_id>', methods=['POST'])
@require_role('supervisor')
def actions_approve(request_id: int) -> Response:
    data = _json_body()
    decision = str(data.get('decision') or 'approved').strip().lower()
    notes = data.get('notes')

    if decision not in {'approved', 'rejected'}:
        return jsonify({'status': 'error', 'message': 'decision must be approved or rejected'}), 400

    service = get_drone_ops_service()
    req = service.approve_action(
        request_id=request_id,
        approver=current_username(),
        decision=decision,
        notes=notes,
    )
    if not req:
        return jsonify({'status': 'error', 'message': 'Action request not found'}), 404
    return jsonify({'status': 'success', 'request': req})


@drone_ops_bp.route('/actions/execute/<int:request_id>', methods=['POST'])
@require_role('operator')
@require_armed
def actions_execute(request_id: int) -> Response:
    service = get_drone_ops_service()
    req, error = service.execute_action(request_id=request_id, actor=current_username())
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'request': req})


@drone_ops_bp.route('/actions/requests', methods=['GET'])
@require_role('viewer')
def actions_list() -> Response:
    incident_id = request.args.get('incident_id', default=None, type=int)
    status = request.args.get('status')
    limit = max(1, min(1000, request.args.get('limit', default=100, type=int)))

    rows = list_action_requests(incident_id=incident_id, status=status, limit=limit)
    return jsonify({'status': 'success', 'count': len(rows), 'requests': rows})


@drone_ops_bp.route('/actions/requests/<int:request_id>', methods=['GET'])
@require_role('viewer')
def actions_get(request_id: int) -> Response:
    row = get_action_request(request_id)
    if not row:
        return jsonify({'status': 'error', 'message': 'Action request not found'}), 404
    return jsonify({'status': 'success', 'request': row})


@drone_ops_bp.route('/actions/audit', methods=['GET'])
@require_role('viewer')
def actions_audit() -> Response:
    request_id = request.args.get('request_id', default=None, type=int)
    limit = max(1, min(2000, request.args.get('limit', default=200, type=int)))
    rows = list_action_audit_logs(request_id=request_id, limit=limit)
    return jsonify({'status': 'success', 'count': len(rows), 'events': rows})


@drone_ops_bp.route('/evidence/<int:incident_id>/manifest', methods=['POST'])
@require_role('analyst')
def evidence_manifest_create(incident_id: int) -> Response:
    if not get_drone_incident(incident_id):
        return jsonify({'status': 'error', 'message': 'Incident not found'}), 404

    data = _json_body()
    signature = data.get('signature')

    service = get_drone_ops_service()
    manifest = service.generate_evidence_manifest(
        incident_id=incident_id,
        created_by=current_username(),
        signature=signature,
    )
    if not manifest:
        return jsonify({'status': 'error', 'message': 'Failed to generate manifest'}), 500
    return jsonify({'status': 'success', 'manifest': manifest}), 201


@drone_ops_bp.route('/evidence/manifests/<int:manifest_id>', methods=['GET'])
@require_role('viewer')
def evidence_manifest_get(manifest_id: int) -> Response:
    row = get_evidence_manifest(manifest_id)
    if not row:
        return jsonify({'status': 'error', 'message': 'Manifest not found'}), 404
    return jsonify({'status': 'success', 'manifest': row})


@drone_ops_bp.route('/evidence/<int:incident_id>/manifests', methods=['GET'])
@require_role('viewer')
def evidence_manifest_list(incident_id: int) -> Response:
    limit = max(1, min(500, request.args.get('limit', default=50, type=int)))
    rows = list_evidence_manifests(incident_id=incident_id, limit=limit)
    return jsonify({'status': 'success', 'count': len(rows), 'manifests': rows})
