"""RF Fingerprinting CRUD + compare API."""

from __future__ import annotations

import os
import threading
from flask import Blueprint, jsonify, request

fingerprint_bp = Blueprint("fingerprint", __name__, url_prefix="/fingerprint")

_fingerprinter = None
_fingerprinter_lock = threading.Lock()

_active_session_id: int | None = None
_session_lock = threading.Lock()


def _get_fingerprinter():
    global _fingerprinter
    if _fingerprinter is None:
        with _fingerprinter_lock:
            if _fingerprinter is None:
                from utils.rf_fingerprint import RFFingerprinter
                db_path = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "instance", "rf_fingerprints.db"
                )
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                _fingerprinter = RFFingerprinter(db_path)
    return _fingerprinter


@fingerprint_bp.route("/start", methods=["POST"])
def start_session():
    global _active_session_id
    data = request.get_json(force=True) or {}
    name = data.get("name", "Unnamed Session")
    location = data.get("location")
    fp = _get_fingerprinter()
    with _session_lock:
        if _active_session_id is not None:
            return jsonify({"error": "Session already active", "session_id": _active_session_id}), 409
        session_id = fp.start_session(name, location)
        _active_session_id = session_id
    return jsonify({"session_id": session_id, "name": name})


@fingerprint_bp.route("/stop", methods=["POST"])
def stop_session():
    global _active_session_id
    fp = _get_fingerprinter()
    with _session_lock:
        if _active_session_id is None:
            return jsonify({"error": "No active session"}), 400
        session_id = _active_session_id
        result = fp.finalize(session_id)
        _active_session_id = None
    return jsonify(result)


@fingerprint_bp.route("/observation", methods=["POST"])
def add_observation():
    global _active_session_id
    fp = _get_fingerprinter()
    data = request.get_json(force=True) or {}
    observations = data.get("observations", [])
    with _session_lock:
        session_id = _active_session_id
    if session_id is None:
        return jsonify({"error": "No active session"}), 400
    if not observations:
        return jsonify({"added": 0})
    fp.add_observations_batch(session_id, observations)
    return jsonify({"added": len(observations)})


@fingerprint_bp.route("/list", methods=["GET"])
def list_sessions():
    fp = _get_fingerprinter()
    sessions = fp.list_sessions()
    with _session_lock:
        active_id = _active_session_id
    return jsonify({"sessions": sessions, "active_session_id": active_id})


@fingerprint_bp.route("/compare", methods=["POST"])
def compare():
    fp = _get_fingerprinter()
    data = request.get_json(force=True) or {}
    baseline_id = data.get("baseline_id")
    observations = data.get("observations", [])
    if not baseline_id:
        return jsonify({"error": "baseline_id required"}), 400
    anomalies = fp.compare(int(baseline_id), observations)
    bands = fp.get_baseline_bands(int(baseline_id))
    return jsonify({"anomalies": anomalies, "baseline_bands": bands})


@fingerprint_bp.route("/<int:session_id>", methods=["DELETE"])
def delete_session(session_id: int):
    global _active_session_id
    fp = _get_fingerprinter()
    with _session_lock:
        if _active_session_id == session_id:
            _active_session_id = None
    fp.delete_session(session_id)
    return jsonify({"deleted": session_id})


@fingerprint_bp.route("/status", methods=["GET"])
def session_status():
    with _session_lock:
        active_id = _active_session_id
    return jsonify({"active_session_id": active_id})
