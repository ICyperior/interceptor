"""Meteor LRPT offline decode backend for ground-station observations."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from utils.logging import get_logger
from utils.weather_sat import WeatherSatDecoder

logger = get_logger('intercept.ground_station.meteor_backend')

OUTPUT_ROOT = Path('instance/ground_station/weather_outputs')
DECODE_TIMEOUT_SECONDS = 30 * 60

_NORAD_TO_SAT_KEY = {
    57166: 'METEOR-M2-3',
    59051: 'METEOR-M2-4',
}


def resolve_meteor_satellite_key(norad_id: int, satellite_name: str) -> str | None:
    if norad_id in _NORAD_TO_SAT_KEY:
        return _NORAD_TO_SAT_KEY[norad_id]

    upper = str(satellite_name or '').upper()
    if 'M2-4' in upper:
        return 'METEOR-M2-4'
    if 'M2-3' in upper or 'METEOR' in upper:
        return 'METEOR-M2-3'
    return None


def launch_meteor_decode(
    *,
    obs_db_id: int | None,
    norad_id: int,
    satellite_name: str,
    sample_rate: int,
    data_path: Path,
    emit_event,
    register_output,
) -> None:
    """Run Meteor LRPT offline decode in a background thread."""
    t = threading.Thread(
        target=_run_decode,
        kwargs={
            'obs_db_id': obs_db_id,
            'norad_id': norad_id,
            'satellite_name': satellite_name,
            'sample_rate': sample_rate,
            'data_path': data_path,
            'emit_event': emit_event,
            'register_output': register_output,
        },
        daemon=True,
        name=f'gs-meteor-decode-{norad_id}',
    )
    t.start()


def _run_decode(
    *,
    obs_db_id: int | None,
    norad_id: int,
    satellite_name: str,
    sample_rate: int,
    data_path: Path,
    emit_event,
    register_output,
) -> None:
    sat_key = resolve_meteor_satellite_key(norad_id, satellite_name)
    if not sat_key:
        emit_event({
            'type': 'weather_decode_failed',
            'norad_id': norad_id,
            'satellite': satellite_name,
            'backend': 'meteor_lrpt',
            'message': 'No Meteor satellite mapping is available for this observation.',
        })
        return

    output_dir = OUTPUT_ROOT / f'{norad_id}_{int(time.time())}'
    decoder = WeatherSatDecoder(output_dir=output_dir)
    if decoder.decoder_available is None:
        emit_event({
            'type': 'weather_decode_failed',
            'norad_id': norad_id,
            'satellite': satellite_name,
            'backend': 'meteor_lrpt',
            'message': 'SatDump backend is not available for Meteor LRPT decode.',
        })
        return

    def _progress_cb(progress):
        progress_event = progress.to_dict()
        progress_event.pop('type', None)
        emit_event({
            'type': 'weather_decode_progress',
            'norad_id': norad_id,
            'satellite': satellite_name,
            'backend': 'meteor_lrpt',
            **progress_event,
        })

    decoder.set_callback(_progress_cb)
    emit_event({
        'type': 'weather_decode_started',
        'norad_id': norad_id,
        'satellite': satellite_name,
        'backend': 'meteor_lrpt',
        'input_path': str(data_path),
    })

    ok, error = decoder.start_from_file(
        satellite=sat_key,
        input_file=data_path,
        sample_rate=sample_rate,
    )
    if not ok:
        emit_event({
            'type': 'weather_decode_failed',
            'norad_id': norad_id,
            'satellite': satellite_name,
            'backend': 'meteor_lrpt',
            'message': error or 'Meteor decode failed to start.',
        })
        return

    started = time.time()
    while decoder.is_running and (time.time() - started) < DECODE_TIMEOUT_SECONDS:
        time.sleep(1.0)

    if decoder.is_running:
        decoder.stop()
        emit_event({
            'type': 'weather_decode_failed',
            'norad_id': norad_id,
            'satellite': satellite_name,
            'backend': 'meteor_lrpt',
            'message': 'Meteor decode timed out.',
        })
        return

    images = decoder.get_images()
    if not images:
        emit_event({
            'type': 'weather_decode_failed',
            'norad_id': norad_id,
            'satellite': satellite_name,
            'backend': 'meteor_lrpt',
            'message': 'Decode completed but no image outputs were produced.',
        })
        return

    outputs = []
    for image in images:
        metadata = {
            'satellite': image.satellite,
            'mode': image.mode,
            'frequency': image.frequency,
            'product': image.product,
            'timestamp': image.timestamp.isoformat(),
            'size_bytes': image.size_bytes,
        }
        output_id = register_output(
            observation_id=obs_db_id,
            norad_id=norad_id,
            output_type='image',
            backend='meteor_lrpt',
            file_path=image.path,
            preview_path=image.path,
            metadata=metadata,
        )
        outputs.append({
            'id': output_id,
            'file_path': str(image.path),
            'filename': image.filename,
            'product': image.product,
        })

    emit_event({
        'type': 'weather_decode_complete',
        'norad_id': norad_id,
        'satellite': satellite_name,
        'backend': 'meteor_lrpt',
        'outputs': outputs,
    })
