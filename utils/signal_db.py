"""Signal database loader and match engine.

Loads data/signals.json once at startup (lazy, cached). Provides a pure
match_signals() function that scores candidates by frequency, bandwidth,
modulation, and region.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.logging import get_logger

logger = get_logger('intercept.signal_db')

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "signals.json"
_cache: list[dict[str, Any]] | None = None


def load_signals() -> list[dict[str, Any]]:
    """Return cached signal list, loading from JSON on first call."""
    global _cache
    if _cache is not None:
        return _cache

    if not _DB_PATH.exists():
        logger.warning("signals.json not found at %s — signal matching will return no results", _DB_PATH)
        _cache = []
        return _cache

    try:
        with open(_DB_PATH) as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("signals.json must be a JSON array")
        _cache = data
        logger.info("Loaded %d signals from %s", len(_cache), _DB_PATH)
    except Exception as exc:
        logger.error("Failed to load signals.json: %s", exc)
        _cache = []

    return _cache


def match_signals(
    *,
    frequency_mhz: float,
    bandwidth_hz: int | None = None,
    modulation: str | None = None,
    region: str = "GLOBAL",
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return signals ranked by how well they match the given parameters.

    Args:
        frequency_mhz: Centre frequency to match (required).
        bandwidth_hz: Observed signal bandwidth in Hz (optional — improves scoring).
        modulation: Observed modulation token e.g. 'WFM', 'AM' (optional).
        region: User's region code e.g. 'EU', 'US', 'GLOBAL'.
        limit: Maximum number of results to return (clamped to 1–20).

    Returns:
        List of signal dicts (copies) sorted by score descending, each with
        added fields: score (int 0–100), match_reasons (list[str]).
    """
    limit = max(1, min(limit, 20))
    target_hz = frequency_mhz * 1_000_000
    mod_upper = modulation.strip().upper() if modulation else None
    region_upper = region.strip().upper() if region else "GLOBAL"

    candidates: list[dict[str, Any]] = []
    for sig in load_signals():
        ranges = sig.get("frequency_ranges", [])
        if not any(r["min_hz"] <= target_hz <= r["max_hz"] for r in ranges):
            continue
        candidates.append(sig)

    scored: list[dict[str, Any]] = []
    for sig in candidates:
        result = dict(sig)  # shallow copy — do not mutate original
        score = 0
        reasons: list[str] = []

        # --- Frequency centrality (10–40 pts) ---
        ranges = sig.get("frequency_ranges", [])
        best = min(
            ranges,
            key=lambda r: abs(target_hz - (r["min_hz"] + r["max_hz"]) / 2),
        )
        centre = (best["min_hz"] + best["max_hz"]) / 2
        half_span = (best["max_hz"] - best["min_hz"]) / 2 or 1
        centrality = 1.0 - min(abs(target_hz - centre) / half_span, 1.0)
        freq_pts = int(10 + 30 * centrality)
        score += freq_pts
        if centrality >= 0.8:
            reasons.append("frequency: centre of range")
        elif centrality >= 0.4:
            reasons.append("frequency: within range")
        else:
            reasons.append("frequency: edge of range")

        # --- Bandwidth match (0–30 pts) ---
        bw_range = sig.get("bandwidth_range")
        if bandwidth_hz is not None:
            if bw_range is None:
                score += 10
            elif bw_range["min_hz"] <= bandwidth_hz <= bw_range["max_hz"]:
                score += 30
                reasons.append("bandwidth: within typical")
            elif (bandwidth_hz <= bw_range["max_hz"] * 2
                  and bandwidth_hz >= bw_range["min_hz"] // 2):
                score += 15
                reasons.append("bandwidth: near typical")
            # else: 0 pts, no reason added
        else:
            score += 15  # neutral — no bandwidth provided

        # --- Modulation match (0–20 pts) ---
        sig_mods = [m.upper() for m in sig.get("modulations", [])]
        if mod_upper:
            if mod_upper in sig_mods:
                score += 20
                reasons.append("modulation: exact match")
            # else: 0 pts
        else:
            score += 10  # neutral — no modulation provided

        # --- Region match (5–10 pts) ---
        sig_regions = [r.upper() for r in sig.get("regions", [])]
        if "GLOBAL" in sig_regions or region_upper in sig_regions:
            score += 10
        else:
            score += 5

        result["score"] = min(score, 100)
        result["match_reasons"] = reasons
        scored.append(result)

    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:limit]
