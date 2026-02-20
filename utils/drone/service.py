"""Stateful Drone Ops service: ingestion, policy, incidents, actions, and evidence."""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Generator

import app as app_module

from utils.correlation import get_correlations as get_wifi_bt_correlations
from utils.database import (
    add_action_approval,
    add_action_audit_log,
    add_drone_correlation,
    add_drone_incident_artifact,
    add_drone_track,
    create_action_request,
    create_drone_incident,
    create_drone_session,
    create_evidence_manifest,
    get_action_request,
    get_active_drone_session,
    get_drone_detection,
    get_drone_incident,
    get_drone_session,
    get_evidence_manifest,
    list_action_audit_logs,
    list_action_requests,
    list_drone_correlations,
    list_drone_detections,
    list_drone_incidents,
    list_drone_sessions,
    list_drone_tracks,
    list_evidence_manifests,
    stop_drone_session,
    update_action_request_status,
    update_drone_incident,
    upsert_drone_detection,
)
from utils.drone.detector import detect_from_event
from utils.drone.remote_id import decode_remote_id_payload
from utils.trilateration import estimate_location_from_observations


class DroneOpsService:
    """Orchestrates Drone Ops data and policy controls."""

    def __init__(self) -> None:
        self._subscribers: set[queue.Queue] = set()
        self._subs_lock = threading.Lock()

        self._policy_lock = threading.Lock()
        self._armed_until_ts: float | None = None
        self._armed_by: str | None = None
        self._arm_reason: str | None = None
        self._arm_incident_id: int | None = None

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _emit(self, event_type: str, payload: dict) -> None:
        envelope = {
            'type': event_type,
            'timestamp': self._utc_now_iso(),
            'payload': payload,
        }
        with self._subs_lock:
            subscribers = tuple(self._subscribers)

        for sub in subscribers:
            try:
                sub.put_nowait(envelope)
            except queue.Full:
                try:
                    sub.get_nowait()
                    sub.put_nowait(envelope)
                except (queue.Empty, queue.Full):
                    continue

    def stream_events(self, timeout: float = 1.0) -> Generator[dict, None, None]:
        """Yield Drone Ops events for SSE streaming."""
        client_queue: queue.Queue = queue.Queue(maxsize=500)

        with self._subs_lock:
            self._subscribers.add(client_queue)

        try:
            while True:
                try:
                    yield client_queue.get(timeout=timeout)
                except queue.Empty:
                    yield {'type': 'keepalive', 'timestamp': self._utc_now_iso(), 'payload': {}}
        finally:
            with self._subs_lock:
                self._subscribers.discard(client_queue)

    # ------------------------------------------------------------------
    # Policy / arming
    # ------------------------------------------------------------------

    def _policy_state_locked(self) -> dict:
        armed = self._armed_until_ts is not None and time.time() < self._armed_until_ts
        if not armed:
            self._armed_until_ts = None
            self._armed_by = None
            self._arm_reason = None
            self._arm_incident_id = None

        return {
            'armed': armed,
            'armed_by': self._armed_by,
            'arm_reason': self._arm_reason,
            'arm_incident_id': self._arm_incident_id,
            'armed_until': datetime.fromtimestamp(self._armed_until_ts, tz=timezone.utc).isoformat() if self._armed_until_ts else None,
            'required_approvals_default': 2,
        }

    def get_policy_state(self) -> dict:
        """Get current policy and arming state."""
        with self._policy_lock:
            return self._policy_state_locked()

    def arm_actions(
        self,
        actor: str,
        reason: str,
        incident_id: int,
        duration_seconds: int = 900,
    ) -> dict:
        """Arm action plane for a bounded duration."""
        duration_seconds = max(60, min(7200, int(duration_seconds or 900)))
        with self._policy_lock:
            self._armed_until_ts = time.time() + duration_seconds
            self._armed_by = actor
            self._arm_reason = reason
            self._arm_incident_id = incident_id
            state = self._policy_state_locked()

        self._emit('policy_armed', {'actor': actor, 'reason': reason, 'incident_id': incident_id, 'state': state})
        return state

    def disarm_actions(self, actor: str, reason: str | None = None) -> dict:
        """Disarm action plane."""
        with self._policy_lock:
            self._armed_until_ts = None
            self._armed_by = None
            self._arm_reason = None
            self._arm_incident_id = None
            state = self._policy_state_locked()

        self._emit('policy_disarmed', {'actor': actor, 'reason': reason, 'state': state})
        return state

    @staticmethod
    def required_approvals(action_type: str) -> int:
        """Compute required approvals for an action type."""
        action = (action_type or '').strip().lower()
        if action.startswith('passive_'):
            return 1
        return 2

    # ------------------------------------------------------------------
    # Sessions and detections
    # ------------------------------------------------------------------

    def start_session(
        self,
        mode: str,
        label: str | None,
        operator: str,
        metadata: dict | None = None,
    ) -> dict:
        """Start a Drone Ops session."""
        active = get_active_drone_session()
        if active:
            return active

        session_id = create_drone_session(
            mode=mode or 'passive',
            label=label,
            operator=operator,
            metadata=metadata,
        )
        session = get_drone_session(session_id)
        if session:
            self._emit('session_started', {'session': session})
        return session or {}

    def stop_session(
        self,
        operator: str,
        session_id: int | None = None,
        summary: dict | None = None,
    ) -> dict | None:
        """Stop a Drone Ops session."""
        active = get_active_drone_session()
        target_id = session_id or (active['id'] if active else None)
        if not target_id:
            return None

        if summary is None:
            summary = {
                'operator': operator,
                'stopped_at': self._utc_now_iso(),
                'detections': len(list_drone_detections(session_id=target_id, limit=1000)),
            }

        stop_drone_session(target_id, summary=summary)
        session = get_drone_session(target_id)
        if session:
            self._emit('session_stopped', {'session': session})
        return session

    def get_status(self) -> dict:
        """Get full Drone Ops status payload."""
        return {
            'status': 'success',
            'active_session': get_active_drone_session(),
            'policy': self.get_policy_state(),
            'counts': {
                'detections': len(list_drone_detections(limit=1000)),
                'incidents_open': len(list_drone_incidents(status='open', limit=1000)),
                'actions_pending': len(list_action_requests(status='pending', limit=1000)),
            },
        }

    def ingest_event(self, mode: str, event: dict, event_type: str | None = None) -> None:
        """Ingest cross-mode event and produce Drone Ops detections."""
        try:
            detections = detect_from_event(mode, event, event_type)
        except Exception:
            return

        if not detections:
            return

        active = get_active_drone_session()
        session_id = active['id'] if active else None

        for detection in detections:
            try:
                detection_id = upsert_drone_detection(
                    session_id=session_id,
                    source=detection['source'],
                    identifier=detection['identifier'],
                    classification=detection.get('classification'),
                    confidence=float(detection.get('confidence') or 0.0),
                    payload=detection.get('payload') or {},
                    remote_id=detection.get('remote_id') or None,
                )
                row = get_drone_detection(detection_id)

                track = detection.get('track') or {}
                if row and track and track.get('lat') is not None and track.get('lon') is not None:
                    add_drone_track(
                        detection_id=row['id'],
                        lat=track.get('lat'),
                        lon=track.get('lon'),
                        altitude_m=track.get('altitude_m'),
                        speed_mps=track.get('speed_mps'),
                        heading_deg=track.get('heading_deg'),
                        quality=track.get('quality'),
                        source=track.get('source') or detection.get('source'),
                    )

                remote_id = detection.get('remote_id') or {}
                uas_id = remote_id.get('uas_id')
                operator_id = remote_id.get('operator_id')
                if uas_id and operator_id:
                    add_drone_correlation(
                        drone_identifier=str(uas_id),
                        operator_identifier=str(operator_id),
                        method='remote_id_binding',
                        confidence=float(remote_id.get('confidence') or 0.8),
                        evidence={
                            'source': detection.get('source'),
                            'event_type': event_type,
                            'detection_id': row['id'] if row else None,
                        },
                    )

                if row:
                    self._emit('detection', {
                        'mode': mode,
                        'event_type': event_type,
                        'detection': row,
                    })
            except Exception:
                continue

    def decode_remote_id(self, payload: Any) -> dict:
        """Decode an explicit Remote ID payload."""
        decoded = decode_remote_id_payload(payload)
        self._emit('remote_id_decoded', {'decoded': decoded})
        return decoded

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_detections(
        self,
        session_id: int | None = None,
        source: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 200,
    ) -> list[dict]:
        return list_drone_detections(
            session_id=session_id,
            source=source,
            min_confidence=min_confidence,
            limit=limit,
        )

    def get_tracks(
        self,
        detection_id: int | None = None,
        identifier: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        return list_drone_tracks(
            detection_id=detection_id,
            identifier=identifier,
            limit=limit,
        )

    def estimate_geolocation(self, observations: list[dict], environment: str = 'outdoor') -> dict | None:
        """Estimate location from observations."""
        return estimate_location_from_observations(observations, environment=environment)

    def refresh_correlations(self, min_confidence: float = 0.6) -> list[dict]:
        """Refresh and persist likely drone/operator correlations from WiFi<->BT pairs."""
        wifi_devices = dict(app_module.wifi_networks)
        wifi_devices.update(dict(app_module.wifi_clients))
        bt_devices = dict(app_module.bt_devices)

        pairs = get_wifi_bt_correlations(
            wifi_devices=wifi_devices,
            bt_devices=bt_devices,
            min_confidence=min_confidence,
            include_historical=True,
        )

        detections = list_drone_detections(min_confidence=0.5, limit=1000)
        known_ids = {d['identifier'].upper() for d in detections}

        for pair in pairs:
            wifi_mac = str(pair.get('wifi_mac') or '').upper()
            bt_mac = str(pair.get('bt_mac') or '').upper()
            if wifi_mac in known_ids or bt_mac in known_ids:
                add_drone_correlation(
                    drone_identifier=wifi_mac if wifi_mac in known_ids else bt_mac,
                    operator_identifier=bt_mac if wifi_mac in known_ids else wifi_mac,
                    method='wifi_bt_correlation',
                    confidence=float(pair.get('confidence') or 0.0),
                    evidence=pair,
                )

        return list_drone_correlations(min_confidence=min_confidence, limit=200)

    # ------------------------------------------------------------------
    # Incidents and artifacts
    # ------------------------------------------------------------------

    def create_incident(
        self,
        title: str,
        severity: str,
        opened_by: str,
        summary: str | None,
        metadata: dict | None,
    ) -> dict:
        incident_id = create_drone_incident(
            title=title,
            severity=severity,
            opened_by=opened_by,
            summary=summary,
            metadata=metadata,
        )
        incident = get_drone_incident(incident_id) or {'id': incident_id}
        self._emit('incident_created', {'incident': incident})
        return incident

    def update_incident(
        self,
        incident_id: int,
        status: str | None = None,
        severity: str | None = None,
        summary: str | None = None,
        metadata: dict | None = None,
    ) -> dict | None:
        update_drone_incident(
            incident_id=incident_id,
            status=status,
            severity=severity,
            summary=summary,
            metadata=metadata,
        )
        incident = get_drone_incident(incident_id)
        if incident:
            self._emit('incident_updated', {'incident': incident})
        return incident

    def add_incident_artifact(
        self,
        incident_id: int,
        artifact_type: str,
        artifact_ref: str,
        added_by: str,
        metadata: dict | None = None,
    ) -> dict:
        artifact_id = add_drone_incident_artifact(
            incident_id=incident_id,
            artifact_type=artifact_type,
            artifact_ref=artifact_ref,
            added_by=added_by,
            metadata=metadata,
        )
        artifact = {
            'id': artifact_id,
            'incident_id': incident_id,
            'artifact_type': artifact_type,
            'artifact_ref': artifact_ref,
            'added_by': added_by,
            'metadata': metadata or {},
        }
        self._emit('incident_artifact_added', {'artifact': artifact})
        return artifact

    # ------------------------------------------------------------------
    # Actions and approvals
    # ------------------------------------------------------------------

    def request_action(
        self,
        incident_id: int,
        action_type: str,
        requested_by: str,
        payload: dict | None,
    ) -> dict | None:
        request_id = create_action_request(
            incident_id=incident_id,
            action_type=action_type,
            requested_by=requested_by,
            payload=payload,
        )
        add_action_audit_log(
            request_id=request_id,
            event_type='requested',
            actor=requested_by,
            details={'payload': payload or {}},
        )
        req = get_action_request(request_id)
        if req:
            req['required_approvals'] = self.required_approvals(req['action_type'])
            self._emit('action_requested', {'request': req})
        return req

    def approve_action(
        self,
        request_id: int,
        approver: str,
        decision: str = 'approved',
        notes: str | None = None,
    ) -> dict | None:
        req = get_action_request(request_id)
        if not req:
            return None

        approvals = req.get('approvals', [])
        if any((a.get('approved_by') or '').lower() == approver.lower() for a in approvals):
            return req

        add_action_approval(request_id=request_id, approved_by=approver, decision=decision, notes=notes)
        add_action_audit_log(
            request_id=request_id,
            event_type='approval',
            actor=approver,
            details={'decision': decision, 'notes': notes},
        )

        req = get_action_request(request_id)
        if not req:
            return None

        approvals = req.get('approvals', [])
        approved_count = len([a for a in approvals if str(a.get('decision')).lower() == 'approved'])
        required = self.required_approvals(req['action_type'])

        if decision.lower() == 'rejected':
            update_action_request_status(request_id, status='rejected')
        elif approved_count >= required and req.get('status') not in {'executed', 'rejected'}:
            update_action_request_status(request_id, status='approved')

        req = get_action_request(request_id)
        if req:
            req['required_approvals'] = required
            req['approved_count'] = approved_count
            self._emit('action_approved', {'request': req})
        return req

    def execute_action(self, request_id: int, actor: str) -> tuple[dict | None, str | None]:
        """Execute an approved action request (policy-gated)."""
        req = get_action_request(request_id)
        if not req:
            return None, 'Action request not found'

        policy = self.get_policy_state()
        if not policy.get('armed'):
            return None, 'Action plane is not armed'

        approvals = req.get('approvals', [])
        approved_count = len([a for a in approvals if str(a.get('decision')).lower() == 'approved'])
        required = self.required_approvals(req['action_type'])

        if approved_count < required:
            return None, f'Insufficient approvals ({approved_count}/{required})'

        update_action_request_status(request_id, status='executed', executed_by=actor)
        add_action_audit_log(
            request_id=request_id,
            event_type='executed',
            actor=actor,
            details={
                'dispatch': 'framework',
                'note': 'Execution recorded. Attach route-specific handlers per action_type.',
            },
        )

        req = get_action_request(request_id)
        if req:
            req['required_approvals'] = required
            req['approved_count'] = approved_count
            self._emit('action_executed', {'request': req})
        return req, None

    # ------------------------------------------------------------------
    # Evidence and manifests
    # ------------------------------------------------------------------

    def generate_evidence_manifest(
        self,
        incident_id: int,
        created_by: str,
        signature: str | None = None,
    ) -> dict | None:
        """Build and persist an evidence manifest for an incident."""
        incident = get_drone_incident(incident_id)
        if not incident:
            return None

        action_requests = list_action_requests(incident_id=incident_id, limit=1000)
        request_ids = [r['id'] for r in action_requests]
        action_audit: list[dict] = []
        for request_id in request_ids:
            action_audit.extend(list_action_audit_logs(request_id=request_id, limit=500))

        manifest_core = {
            'generated_at': self._utc_now_iso(),
            'incident': {
                'id': incident['id'],
                'title': incident['title'],
                'status': incident['status'],
                'severity': incident['severity'],
                'opened_at': incident['opened_at'],
                'closed_at': incident['closed_at'],
            },
            'artifact_count': len(incident.get('artifacts', [])),
            'action_request_count': len(action_requests),
            'audit_event_count': len(action_audit),
            'artifacts': incident.get('artifacts', []),
            'action_requests': action_requests,
            'action_audit': action_audit,
        }

        canonical = json.dumps(manifest_core, sort_keys=True, separators=(',', ':'))
        sha256_hex = hashlib.sha256(canonical.encode('utf-8')).hexdigest()

        manifest = {
            **manifest_core,
            'integrity': {
                'algorithm': 'sha256',
                'digest': sha256_hex,
            },
        }

        manifest_id = create_evidence_manifest(
            incident_id=incident_id,
            manifest=manifest,
            hash_algo='sha256',
            signature=signature,
            created_by=created_by,
        )

        stored = get_evidence_manifest(manifest_id)
        if stored:
            self._emit('evidence_manifest_created', {'manifest': stored})
        return stored


_drone_service: DroneOpsService | None = None
_drone_service_lock = threading.Lock()


def get_drone_ops_service() -> DroneOpsService:
    """Get global Drone Ops service singleton."""
    global _drone_service
    with _drone_service_lock:
        if _drone_service is None:
            _drone_service = DroneOpsService()
        return _drone_service
