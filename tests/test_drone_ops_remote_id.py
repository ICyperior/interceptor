"""Tests for Drone Ops Remote ID decoder helpers."""

from __future__ import annotations

import json

from utils.drone.remote_id import decode_remote_id_payload


def test_decode_remote_id_from_dict_payload():
    payload = {
        'remote_id': {
            'uas_id': 'UAS-001',
            'operator_id': 'OP-007',
            'lat': 37.7749,
            'lon': -122.4194,
            'altitude_m': 121.5,
            'speed_mps': 12.3,
            'heading_deg': 270.0,
        }
    }

    decoded = decode_remote_id_payload(payload)
    assert decoded['detected'] is True
    assert decoded['source_format'] == 'dict'
    assert decoded['uas_id'] == 'UAS-001'
    assert decoded['operator_id'] == 'OP-007'
    assert decoded['lat'] == 37.7749
    assert decoded['lon'] == -122.4194
    assert decoded['altitude_m'] == 121.5
    assert decoded['speed_mps'] == 12.3
    assert decoded['heading_deg'] == 270.0
    assert decoded['confidence'] > 0.0


def test_decode_remote_id_from_json_string():
    payload = json.dumps({
        'uas_id': 'RID-ABC',
        'lat': 35.0,
        'lon': -115.0,
        'altitude': 80,
    })

    decoded = decode_remote_id_payload(payload)
    assert decoded['detected'] is True
    assert decoded['source_format'] == 'json'
    assert decoded['uas_id'] == 'RID-ABC'
    assert decoded['lat'] == 35.0
    assert decoded['lon'] == -115.0
    assert decoded['altitude_m'] == 80.0


def test_decode_remote_id_from_raw_text_is_not_detected():
    decoded = decode_remote_id_payload('not-a-remote-id-payload')
    assert decoded['detected'] is False
    assert decoded['source_format'] == 'raw'
    assert decoded['uas_id'] is None
    assert decoded['operator_id'] is None
    assert isinstance(decoded['raw'], dict)
    assert decoded['raw']['raw'] == 'not-a-remote-id-payload'
