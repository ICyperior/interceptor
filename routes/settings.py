"""Settings management routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request, Response

from utils.database import (
    get_setting,
    set_setting,
    delete_setting,
    get_all_settings,
    get_signal_history,
    add_signal_reading,
    get_correlations,
)
from utils.logging import get_logger

logger = get_logger('intercept.settings')

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')


@settings_bp.route('', methods=['GET'])
def get_settings() -> Response:
    """Get all settings."""
    try:
        settings = get_all_settings()
        return jsonify({
            'status': 'success',
            'settings': settings
        })
    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@settings_bp.route('', methods=['POST'])
def save_settings() -> Response:
    """Save one or more settings."""
    data = request.json or {}

    if not data:
        return jsonify({
            'status': 'error',
            'message': 'No settings provided'
        }), 400

    try:
        saved = []
        for key, value in data.items():
            # Validate key (alphanumeric, underscores, dots, hyphens)
            if not key or not all(c.isalnum() or c in '_.-' for c in key):
                continue

            set_setting(key, value)
            saved.append(key)

        return jsonify({
            'status': 'success',
            'saved': saved
        })
    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@settings_bp.route('/<key>', methods=['GET'])
def get_single_setting(key: str) -> Response:
    """Get a single setting by key."""
    try:
        value = get_setting(key)
        if value is None:
            return jsonify({
                'status': 'not_found',
                'key': key
            }), 404

        return jsonify({
            'status': 'success',
            'key': key,
            'value': value
        })
    except Exception as e:
        logger.error(f"Error getting setting {key}: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@settings_bp.route('/<key>', methods=['PUT'])
def update_single_setting(key: str) -> Response:
    """Update a single setting."""
    data = request.json or {}
    value = data.get('value')

    if value is None and 'value' not in data:
        return jsonify({
            'status': 'error',
            'message': 'Value is required'
        }), 400

    try:
        set_setting(key, value)
        return jsonify({
            'status': 'success',
            'key': key,
            'value': value
        })
    except Exception as e:
        logger.error(f"Error updating setting {key}: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@settings_bp.route('/<key>', methods=['DELETE'])
def delete_single_setting(key: str) -> Response:
    """Delete a setting."""
    try:
        deleted = delete_setting(key)
        if deleted:
            return jsonify({
                'status': 'success',
                'key': key,
                'deleted': True
            })
        else:
            return jsonify({
                'status': 'not_found',
                'key': key
            }), 404
    except Exception as e:
        logger.error(f"Error deleting setting {key}: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# =============================================================================
# Signal History Endpoints
# =============================================================================

@settings_bp.route('/signal-history/<mode>/<device_id>', methods=['GET'])
def get_device_signal_history(mode: str, device_id: str) -> Response:
    """Get signal strength history for a device."""
    limit = request.args.get('limit', 100, type=int)
    since_minutes = request.args.get('since', 60, type=int)

    # Validate mode
    valid_modes = ['wifi', 'bluetooth', 'adsb', 'pager', 'sensor']
    if mode not in valid_modes:
        return jsonify({
            'status': 'error',
            'message': f'Invalid mode. Valid modes: {valid_modes}'
        }), 400

    try:
        history = get_signal_history(mode, device_id, limit, since_minutes)
        return jsonify({
            'status': 'success',
            'mode': mode,
            'device_id': device_id,
            'history': history
        })
    except Exception as e:
        logger.error(f"Error getting signal history: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@settings_bp.route('/signal-history', methods=['POST'])
def add_signal_history() -> Response:
    """Add a signal strength reading (for internal use)."""
    data = request.json or {}

    mode = data.get('mode')
    device_id = data.get('device_id')
    signal_strength = data.get('signal_strength')

    if not all([mode, device_id, signal_strength is not None]):
        return jsonify({
            'status': 'error',
            'message': 'mode, device_id, and signal_strength are required'
        }), 400

    try:
        add_signal_reading(mode, device_id, signal_strength, data.get('metadata'))
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error adding signal reading: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# =============================================================================
# Device Correlation Endpoints
# =============================================================================

@settings_bp.route('/correlations', methods=['GET'])
def get_device_correlations() -> Response:
    """Get device correlations between WiFi and Bluetooth."""
    min_confidence = request.args.get('min_confidence', 0.5, type=float)

    try:
        correlations = get_correlations(min_confidence)
        return jsonify({
            'status': 'success',
            'correlations': correlations
        })
    except Exception as e:
        logger.error(f"Error getting correlations: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
