"""RF Fingerprinting engine using Welford online algorithm for statistics."""

from __future__ import annotations

import sqlite3
import threading
import math
from typing import Optional


class RFFingerprinter:
    BAND_RESOLUTION_MHZ = 0.1  # 100 kHz buckets

    def __init__(self, db_path: str):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS rf_fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    location TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    finalized_at TEXT
                );
                CREATE TABLE IF NOT EXISTS rf_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fp_id INTEGER NOT NULL REFERENCES rf_fingerprints(id) ON DELETE CASCADE,
                    band_center_mhz REAL NOT NULL,
                    power_dbm REAL NOT NULL,
                    recorded_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS rf_baselines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fp_id INTEGER NOT NULL REFERENCES rf_fingerprints(id) ON DELETE CASCADE,
                    band_center_mhz REAL NOT NULL,
                    mean_dbm REAL NOT NULL,
                    std_dbm REAL NOT NULL,
                    sample_count INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_obs_fp_id ON rf_observations(fp_id);
                CREATE INDEX IF NOT EXISTS idx_baseline_fp_id ON rf_baselines(fp_id);
            """)
            self.db.commit()

    def _snap_to_band(self, freq_mhz: float) -> float:
        """Snap frequency to nearest band center (100 kHz resolution)."""
        return round(round(freq_mhz / self.BAND_RESOLUTION_MHZ) * self.BAND_RESOLUTION_MHZ, 3)

    def start_session(self, name: str, location: Optional[str] = None) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO rf_fingerprints (name, location) VALUES (?, ?)",
                (name, location),
            )
            self.db.commit()
            return cur.lastrowid

    def add_observation(self, session_id: int, freq_mhz: float, power_dbm: float):
        band = self._snap_to_band(freq_mhz)
        with self._lock:
            self.db.execute(
                "INSERT INTO rf_observations (fp_id, band_center_mhz, power_dbm) VALUES (?, ?, ?)",
                (session_id, band, power_dbm),
            )
            self.db.commit()

    def add_observations_batch(self, session_id: int, observations: list[dict]):
        rows = [
            (session_id, self._snap_to_band(o["freq_mhz"]), o["power_dbm"])
            for o in observations
        ]
        with self._lock:
            self.db.executemany(
                "INSERT INTO rf_observations (fp_id, band_center_mhz, power_dbm) VALUES (?, ?, ?)",
                rows,
            )
            self.db.commit()

    def finalize(self, session_id: int) -> dict:
        """Compute statistics per band and store baselines."""
        with self._lock:
            rows = self.db.execute(
                "SELECT band_center_mhz, power_dbm FROM rf_observations WHERE fp_id = ? ORDER BY band_center_mhz",
                (session_id,),
            ).fetchall()

        # Group by band
        bands: dict[float, list[float]] = {}
        for row in rows:
            b = row["band_center_mhz"]
            bands.setdefault(b, []).append(row["power_dbm"])

        baselines = []
        for band_mhz, powers in bands.items():
            n = len(powers)
            mean = sum(powers) / n
            if n > 1:
                variance = sum((p - mean) ** 2 for p in powers) / (n - 1)
                std = math.sqrt(variance)
            else:
                std = 0.0
            baselines.append((session_id, band_mhz, mean, std, n))

        with self._lock:
            self.db.executemany(
                "INSERT INTO rf_baselines (fp_id, band_center_mhz, mean_dbm, std_dbm, sample_count) VALUES (?, ?, ?, ?, ?)",
                baselines,
            )
            self.db.execute(
                "UPDATE rf_fingerprints SET finalized_at = datetime('now') WHERE id = ?",
                (session_id,),
            )
            self.db.commit()

        return {"session_id": session_id, "bands_recorded": len(baselines)}

    def compare(self, baseline_id: int, observations: list[dict]) -> list[dict]:
        """Compare observations against a stored baseline. Returns anomaly list."""
        with self._lock:
            baseline_rows = self.db.execute(
                "SELECT band_center_mhz, mean_dbm, std_dbm, sample_count FROM rf_baselines WHERE fp_id = ?",
                (baseline_id,),
            ).fetchall()

        baseline_map: dict[float, dict] = {
            row["band_center_mhz"]: dict(row) for row in baseline_rows
        }

        # Build current band map (average power per band)
        current_bands: dict[float, list[float]] = {}
        for obs in observations:
            b = self._snap_to_band(obs["freq_mhz"])
            current_bands.setdefault(b, []).append(obs["power_dbm"])
        current_map = {b: sum(ps) / len(ps) for b, ps in current_bands.items()}

        anomalies = []

        # Check each baseline band
        for band_mhz, bl in baseline_map.items():
            if band_mhz in current_map:
                current_power = current_map[band_mhz]
                delta = current_power - bl["mean_dbm"]
                std = bl["std_dbm"] if bl["std_dbm"] > 0 else 1.0
                z_score = delta / std
                if abs(z_score) >= 2.0:
                    anomalies.append({
                        "band_center_mhz": band_mhz,
                        "band_label": f"{band_mhz:.1f} MHz",
                        "baseline_mean": bl["mean_dbm"],
                        "baseline_std": bl["std_dbm"],
                        "current_power": current_power,
                        "z_score": z_score,
                        "anomaly_type": "power",
                    })
            else:
                anomalies.append({
                    "band_center_mhz": band_mhz,
                    "band_label": f"{band_mhz:.1f} MHz",
                    "baseline_mean": bl["mean_dbm"],
                    "baseline_std": bl["std_dbm"],
                    "current_power": None,
                    "z_score": None,
                    "anomaly_type": "missing",
                })

        # Check for new bands not in baseline
        for band_mhz, current_power in current_map.items():
            if band_mhz not in baseline_map:
                anomalies.append({
                    "band_center_mhz": band_mhz,
                    "band_label": f"{band_mhz:.1f} MHz",
                    "baseline_mean": None,
                    "baseline_std": None,
                    "current_power": current_power,
                    "z_score": None,
                    "anomaly_type": "new",
                })

        anomalies.sort(
            key=lambda a: abs(a["z_score"]) if a["z_score"] is not None else 0,
            reverse=True,
        )
        return anomalies

    def list_sessions(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                """SELECT id, name, location, created_at, finalized_at,
                   (SELECT COUNT(*) FROM rf_baselines WHERE fp_id = rf_fingerprints.id) AS band_count
                   FROM rf_fingerprints ORDER BY created_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: int):
        with self._lock:
            self.db.execute("DELETE FROM rf_fingerprints WHERE id = ?", (session_id,))
            self.db.commit()

    def get_baseline_bands(self, baseline_id: int) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT band_center_mhz, mean_dbm, std_dbm, sample_count FROM rf_baselines WHERE fp_id = ? ORDER BY band_center_mhz",
                (baseline_id,),
            ).fetchall()
        return [dict(row) for row in rows]
