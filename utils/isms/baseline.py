"""
ISMS baseline management.

Provides functions for recording, storing, and comparing
spectrum and cellular baselines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean, stdev
from typing import Any

from utils.database import (
    create_isms_baseline,
    get_isms_baseline,
    update_isms_baseline,
    get_active_isms_baseline,
)

from .spectrum import BandMetrics, SpectrumBin, compute_band_metrics
from .towers import CellTower

logger = logging.getLogger('intercept.isms.baseline')


@dataclass
class SpectrumBaseline:
    """Baseline spectrum profile for a band."""
    band_name: str
    freq_start_mhz: float
    freq_end_mhz: float
    noise_floor_db: float
    avg_power_db: float
    activity_score: float
    peak_frequencies: list[float]  # MHz
    recorded_at: datetime = field(default_factory=datetime.now)
    sample_count: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            'band_name': self.band_name,
            'freq_start_mhz': self.freq_start_mhz,
            'freq_end_mhz': self.freq_end_mhz,
            'noise_floor_db': self.noise_floor_db,
            'avg_power_db': self.avg_power_db,
            'activity_score': self.activity_score,
            'peak_frequencies': self.peak_frequencies,
            'recorded_at': self.recorded_at.isoformat(),
            'sample_count': self.sample_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SpectrumBaseline':
        """Create from dictionary."""
        recorded_at = data.get('recorded_at')
        if isinstance(recorded_at, str):
            recorded_at = datetime.fromisoformat(recorded_at)
        elif recorded_at is None:
            recorded_at = datetime.now()

        return cls(
            band_name=data.get('band_name', 'Unknown'),
            freq_start_mhz=data.get('freq_start_mhz', 0),
            freq_end_mhz=data.get('freq_end_mhz', 0),
            noise_floor_db=data.get('noise_floor_db', -100),
            avg_power_db=data.get('avg_power_db', -100),
            activity_score=data.get('activity_score', 0),
            peak_frequencies=data.get('peak_frequencies', []),
            recorded_at=recorded_at,
            sample_count=data.get('sample_count', 0),
        )


@dataclass
class CellularBaseline:
    """Baseline cellular environment."""
    cells: list[dict]  # List of cell info dicts
    operators: list[str]  # List of PLMNs seen
    recorded_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            'cells': self.cells,
            'operators': self.operators,
            'recorded_at': self.recorded_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CellularBaseline':
        """Create from dictionary."""
        recorded_at = data.get('recorded_at')
        if isinstance(recorded_at, str):
            recorded_at = datetime.fromisoformat(recorded_at)
        elif recorded_at is None:
            recorded_at = datetime.now()

        return cls(
            cells=data.get('cells', []),
            operators=data.get('operators', []),
            recorded_at=recorded_at,
        )


@dataclass
class TowerBaseline:
    """Baseline tower environment."""
    towers: list[dict]  # List of tower info dicts
    recorded_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            'towers': self.towers,
            'recorded_at': self.recorded_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TowerBaseline':
        """Create from dictionary."""
        recorded_at = data.get('recorded_at')
        if isinstance(recorded_at, str):
            recorded_at = datetime.fromisoformat(recorded_at)
        elif recorded_at is None:
            recorded_at = datetime.now()

        return cls(
            towers=data.get('towers', []),
            recorded_at=recorded_at,
        )


class BaselineRecorder:
    """Records spectrum and cellular data for baseline creation."""

    def __init__(self):
        self._spectrum_samples: dict[str, list[BandMetrics]] = {}
        self._cellular_samples: list[dict] = []
        self._tower_samples: list[dict] = []
        self._gsm_cells: list[dict] = []
        self._recording = False
        self._started_at: datetime | None = None

    @property
    def is_recording(self) -> bool:
        """Check if recording is active."""
        return self._recording

    def start_recording(self) -> None:
        """Start baseline recording."""
        self._spectrum_samples.clear()
        self._cellular_samples.clear()
        self._tower_samples.clear()
        self._gsm_cells.clear()
        self._recording = True
        self._started_at = datetime.now()
        logger.info("Started baseline recording")

    def stop_recording(self) -> dict:
        """
        Stop recording and return compiled baseline data.

        Returns:
            Dictionary with spectrum_profile, cellular_environment, known_towers
        """
        self._recording = False

        # Compile spectrum baselines
        spectrum_profile = {}
        for band_name, samples in self._spectrum_samples.items():
            if samples:
                spectrum_profile[band_name] = self._compile_spectrum_baseline(
                    band_name, samples
                ).to_dict()

        # Compile cellular baseline
        cellular_env = self._compile_cellular_baseline().to_dict()

        # Compile tower baseline
        tower_data = self._compile_tower_baseline().to_dict()

        logger.info(
            f"Stopped baseline recording: {len(spectrum_profile)} bands, "
            f"{len(cellular_env.get('cells', []))} cells, "
            f"{len(tower_data.get('towers', []))} towers, "
            f"{len(self._gsm_cells)} GSM cells"
        )

        return {
            'spectrum_profile': spectrum_profile,
            'cellular_environment': cellular_env.get('cells', []),
            'known_towers': tower_data.get('towers', []),
            'gsm_cells': self._gsm_cells.copy(),
        }

    def add_spectrum_sample(self, band_name: str, metrics: BandMetrics) -> None:
        """Add a spectrum sample during recording."""
        if not self._recording:
            return

        if band_name not in self._spectrum_samples:
            self._spectrum_samples[band_name] = []

        self._spectrum_samples[band_name].append(metrics)

    def add_cellular_sample(self, cell_info: dict) -> None:
        """Add a cellular sample during recording."""
        if not self._recording:
            return

        # Deduplicate by cell_id + plmn
        key = (cell_info.get('cell_id'), cell_info.get('plmn'))
        existing = next(
            (c for c in self._cellular_samples
             if (c.get('cell_id'), c.get('plmn')) == key),
            None
        )

        if existing:
            # Update signal strength if stronger
            if cell_info.get('rsrp', -200) > existing.get('rsrp', -200):
                existing.update(cell_info)
        else:
            self._cellular_samples.append(cell_info)

    def add_tower_sample(self, tower: CellTower | dict) -> None:
        """Add a tower sample during recording."""
        if not self._recording:
            return

        if isinstance(tower, CellTower):
            tower_dict = tower.to_dict()
        else:
            tower_dict = tower

        # Deduplicate by cellid
        cell_id = tower_dict.get('cellid')
        if not any(t.get('cellid') == cell_id for t in self._tower_samples):
            self._tower_samples.append(tower_dict)

    def add_gsm_cell(self, cell: Any) -> None:
        """
        Add a GSM cell sample during recording.

        Args:
            cell: GsmCell object or dict with GSM cell info
        """
        if not self._recording:
            return

        # Convert to dict if needed
        if hasattr(cell, 'arfcn'):
            # It's a GsmCell object
            cell_dict = {
                'arfcn': cell.arfcn,
                'freq_mhz': cell.freq_mhz,
                'power_dbm': cell.power_dbm,
                'mcc': cell.mcc,
                'mnc': cell.mnc,
                'lac': cell.lac,
                'cell_id': cell.cell_id,
                'bsic': cell.bsic,
                'plmn': cell.plmn,
                'cell_global_id': cell.cell_global_id,
            }
        else:
            cell_dict = cell

        # Deduplicate by ARFCN (keep strongest signal)
        arfcn = cell_dict.get('arfcn')
        existing = next(
            (c for c in self._gsm_cells if c.get('arfcn') == arfcn),
            None
        )

        if existing:
            # Update if stronger signal
            if cell_dict.get('power_dbm', -200) > existing.get('power_dbm', -200):
                existing.update(cell_dict)
        else:
            self._gsm_cells.append(cell_dict)

    def _compile_spectrum_baseline(
        self,
        band_name: str,
        samples: list[BandMetrics]
    ) -> SpectrumBaseline:
        """Compile spectrum samples into a baseline."""
        if not samples:
            return SpectrumBaseline(
                band_name=band_name,
                freq_start_mhz=0,
                freq_end_mhz=0,
                noise_floor_db=-100,
                avg_power_db=-100,
                activity_score=0,
                peak_frequencies=[],
            )

        # Average the noise floors
        noise_floors = [s.noise_floor_db for s in samples]
        avg_noise = mean(noise_floors)

        # Average the power levels
        avg_powers = [s.avg_power_db for s in samples]
        avg_power = mean(avg_powers)

        # Average activity scores
        activity_scores = [s.activity_score for s in samples]
        avg_activity = mean(activity_scores)

        # Collect peak frequencies that appear consistently
        all_peaks = [s.peak_frequency_mhz for s in samples]
        # Group peaks within 0.1 MHz
        peak_groups: dict[float, int] = {}
        for peak in all_peaks:
            # Find existing group
            found = False
            for group_freq in list(peak_groups.keys()):
                if abs(peak - group_freq) < 0.1:
                    peak_groups[group_freq] += 1
                    found = True
                    break
            if not found:
                peak_groups[peak] = 1

        # Keep peaks that appear in >50% of samples
        threshold = len(samples) * 0.5
        consistent_peaks = [
            freq for freq, count in peak_groups.items()
            if count >= threshold
        ]

        return SpectrumBaseline(
            band_name=band_name,
            freq_start_mhz=samples[0].freq_start_mhz,
            freq_end_mhz=samples[0].freq_end_mhz,
            noise_floor_db=avg_noise,
            avg_power_db=avg_power,
            activity_score=avg_activity,
            peak_frequencies=consistent_peaks,
            sample_count=len(samples),
        )

    def _compile_cellular_baseline(self) -> CellularBaseline:
        """Compile cellular samples into a baseline."""
        operators = list(set(
            c.get('plmn') for c in self._cellular_samples
            if c.get('plmn')
        ))

        return CellularBaseline(
            cells=self._cellular_samples.copy(),
            operators=operators,
        )

    def _compile_tower_baseline(self) -> TowerBaseline:
        """Compile tower samples into a baseline."""
        return TowerBaseline(
            towers=self._tower_samples.copy(),
        )


def compare_spectrum_baseline(
    current: BandMetrics,
    baseline: SpectrumBaseline | dict,
) -> dict:
    """
    Compare current spectrum metrics to baseline.

    Args:
        current: Current band metrics
        baseline: Baseline to compare against (SpectrumBaseline or dict)

    Returns:
        Dictionary with comparison results
    """
    if isinstance(baseline, dict):
        baseline = SpectrumBaseline.from_dict(baseline)

    noise_delta = current.noise_floor_db - baseline.noise_floor_db
    activity_delta = current.activity_score - baseline.activity_score

    # Check if current peak is new
    is_new_peak = all(
        abs(current.peak_frequency_mhz - bp) > 0.1
        for bp in baseline.peak_frequencies
    ) if baseline.peak_frequencies else False

    return {
        'band_name': current.band_name,
        'noise_floor_current': current.noise_floor_db,
        'noise_floor_baseline': baseline.noise_floor_db,
        'noise_delta': noise_delta,
        'activity_current': current.activity_score,
        'activity_baseline': baseline.activity_score,
        'activity_delta': activity_delta,
        'peak_current': current.peak_frequency_mhz,
        'is_new_peak': is_new_peak,
        'baseline_peaks': baseline.peak_frequencies,
        'anomaly_detected': (
            abs(noise_delta) > 6 or
            abs(activity_delta) > 30 or
            is_new_peak
        ),
    }


def compare_tower_baseline(
    current_towers: list[CellTower | dict],
    baseline: TowerBaseline | dict,
) -> dict:
    """
    Compare current towers to baseline.

    Args:
        current_towers: List of current towers
        baseline: Baseline to compare against

    Returns:
        Dictionary with comparison results
    """
    if isinstance(baseline, dict):
        baseline = TowerBaseline.from_dict(baseline)

    # Get cell IDs from baseline
    baseline_ids = set(t.get('cellid') for t in baseline.towers)

    # Get current cell IDs
    current_ids = set()
    for tower in current_towers:
        if isinstance(tower, CellTower):
            current_ids.add(tower.cellid)
        else:
            current_ids.add(tower.get('cellid'))

    new_towers = current_ids - baseline_ids
    missing_towers = baseline_ids - current_ids
    unchanged = current_ids & baseline_ids

    return {
        'total_current': len(current_ids),
        'total_baseline': len(baseline_ids),
        'new_tower_ids': list(new_towers),
        'missing_tower_ids': list(missing_towers),
        'unchanged_count': len(unchanged),
        'new_count': len(new_towers),
        'missing_count': len(missing_towers),
        'anomaly_detected': len(new_towers) > 0,
    }


def compare_cellular_baseline(
    current_cells: list[dict],
    baseline: CellularBaseline | dict,
) -> dict:
    """
    Compare current cellular environment to baseline.

    Args:
        current_cells: List of current cell info dicts
        baseline: Baseline to compare against

    Returns:
        Dictionary with comparison results
    """
    if isinstance(baseline, dict):
        baseline = CellularBaseline.from_dict(baseline)

    # Get cell identifiers from baseline
    baseline_cell_keys = set(
        (c.get('cell_id'), c.get('plmn'))
        for c in baseline.cells
    )

    # Get current cell identifiers
    current_cell_keys = set(
        (c.get('cell_id'), c.get('plmn'))
        for c in current_cells
    )

    new_cells = current_cell_keys - baseline_cell_keys
    missing_cells = baseline_cell_keys - current_cell_keys

    # Check for new operators
    current_operators = set(c.get('plmn') for c in current_cells if c.get('plmn'))
    new_operators = current_operators - set(baseline.operators)

    return {
        'total_current': len(current_cell_keys),
        'total_baseline': len(baseline_cell_keys),
        'new_cells': list(new_cells),
        'missing_cells': list(missing_cells),
        'new_cell_count': len(new_cells),
        'missing_cell_count': len(missing_cells),
        'new_operators': list(new_operators),
        'anomaly_detected': len(new_cells) > 0 or len(new_operators) > 0,
    }


def save_baseline_to_db(
    name: str,
    location_name: str | None,
    latitude: float | None,
    longitude: float | None,
    baseline_data: dict,
) -> int:
    """
    Save baseline data to database.

    Args:
        name: Baseline name
        location_name: Location description
        latitude: GPS latitude
        longitude: GPS longitude
        baseline_data: Dict from BaselineRecorder.stop_recording()

    Returns:
        Database ID of created baseline
    """
    return create_isms_baseline(
        name=name,
        location_name=location_name,
        latitude=latitude,
        longitude=longitude,
        spectrum_profile=baseline_data.get('spectrum_profile'),
        cellular_environment=baseline_data.get('cellular_environment'),
        known_towers=baseline_data.get('known_towers'),
    )
