"""Settings management routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request, Response

from utils.database import (
    get_setting,
    set_setting,
    delete_setting,
    get_all_settings,
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
