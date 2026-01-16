"""
GSM cell detection using grgsm_scanner.

Provides passive GSM broadcast channel scanning to detect nearby
base stations and extract cell identity information.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Generator

logger = logging.getLogger('intercept.isms.gsm')


@dataclass
class GsmCell:
    """Detected GSM cell from broadcast channel."""
    arfcn: int  # Absolute Radio Frequency Channel Number
    freq_mhz: float
    power_dbm: float
    mcc: int | None = None  # Mobile Country Code
    mnc: int | None = None  # Mobile Network Code
    lac: int | None = None  # Location Area Code
    cell_id: int | None = None
    bsic: str | None = None  # Base Station Identity Code
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def plmn(self) -> str | None:
        """Public Land Mobile Network identifier (MCC-MNC)."""
        if self.mcc is not None and self.mnc is not None:
            return f'{self.mcc}-{self.mnc:02d}'
        return None

    @property
    def cell_global_id(self) -> str | None:
        """Cell Global Identity string."""
        if all(v is not None for v in [self.mcc, self.mnc, self.lac, self.cell_id]):
            return f'{self.mcc}-{self.mnc:02d}-{self.lac}-{self.cell_id}'
        return None


@dataclass
class GsmScanResult:
    """Result of a GSM scan."""
    cells: list[GsmCell]
    scan_duration_s: float
    device_index: int
    freq_start_mhz: float
    freq_end_mhz: float
    error: str | None = None


# GSM frequency bands (ARFCN to frequency mapping)
# GSM900: ARFCN 1-124 (Uplink: 890-915 MHz, Downlink: 935-960 MHz)
# GSM1800: ARFCN 512-885 (Uplink: 1710-1785 MHz, Downlink: 1805-1880 MHz)

def arfcn_to_freq(arfcn: int, band: str = 'auto') -> float:
    """
    Convert ARFCN to downlink frequency in MHz.

    Args:
        arfcn: Absolute Radio Frequency Channel Number
        band: Band hint ('gsm900', 'gsm1800', 'auto')

    Returns:
        Downlink frequency in MHz
    """
    if band == 'auto':
        if 1 <= arfcn <= 124:
            band = 'gsm900'
        elif 512 <= arfcn <= 885:
            band = 'gsm1800'
        elif 975 <= arfcn <= 1023:
            band = 'egsm900'  # Extended GSM900
        else:
            band = 'gsm900'  # Default

    if band in ('gsm900', 'p-gsm'):
        # P-GSM 900: ARFCN 1-124
        # Downlink = 935 + 0.2 * (ARFCN - 1)
        if 1 <= arfcn <= 124:
            return 935.0 + 0.2 * arfcn
        elif arfcn == 0:
            return 935.0

    elif band == 'egsm900':
        # E-GSM 900: ARFCN 975-1023, 0-124
        if 975 <= arfcn <= 1023:
            return 935.0 + 0.2 * (arfcn - 1024)
        elif 0 <= arfcn <= 124:
            return 935.0 + 0.2 * arfcn

    elif band in ('gsm1800', 'dcs1800'):
        # DCS 1800: ARFCN 512-885
        # Downlink = 1805.2 + 0.2 * (ARFCN - 512)
        if 512 <= arfcn <= 885:
            return 1805.2 + 0.2 * (arfcn - 512)

    elif band in ('gsm1900', 'pcs1900'):
        # PCS 1900: ARFCN 512-810
        # Downlink = 1930.2 + 0.2 * (ARFCN - 512)
        if 512 <= arfcn <= 810:
            return 1930.2 + 0.2 * (arfcn - 512)

    # Fallback for unknown
    return 935.0 + 0.2 * arfcn


def get_grgsm_scanner_path() -> str | None:
    """Get the path to grgsm_scanner executable."""
    return shutil.which('grgsm_scanner')


def _drain_stderr(process: subprocess.Popen, stop_event: threading.Event) -> list[str]:
    """Drain stderr and collect error messages."""
    errors = []
    try:
        while not stop_event.is_set() and process.poll() is None:
            if process.stderr:
                line = process.stderr.readline()
                if line:
                    errors.append(line.strip())
    except Exception:
        pass
    return errors


def parse_grgsm_output(line: str) -> GsmCell | None:
    """
    Parse a line of grgsm_scanner output.

    grgsm_scanner output format varies but typically includes:
    ARFCN: XXX, Freq: XXX.X MHz, Power: -XX.X dBm, CID: XXXX, LAC: XXXX, MCC: XXX, MNC: XX

    Args:
        line: A line of grgsm_scanner output

    Returns:
        GsmCell if parsed successfully, None otherwise
    """
    line = line.strip()
    if not line:
        return None

    # Skip header/info lines
    if line.startswith(('#', 'ARFCN', '---', 'gr-gsm', 'Using', 'Scanning')):
        return None

    # Pattern for typical grgsm_scanner output
    # Example: "ARFCN:   73, Freq:  949.6M, CID: 12345, LAC: 1234, MCC: 234, MNC: 10, Pwr: -65.2"
    arfcn_match = re.search(r'ARFCN[:\s]+(\d+)', line, re.IGNORECASE)
    freq_match = re.search(r'Freq[:\s]+([\d.]+)\s*M', line, re.IGNORECASE)
    power_match = re.search(r'(?:Pwr|Power)[:\s]+([-\d.]+)', line, re.IGNORECASE)
    cid_match = re.search(r'CID[:\s]+(\d+)', line, re.IGNORECASE)
    lac_match = re.search(r'LAC[:\s]+(\d+)', line, re.IGNORECASE)
    mcc_match = re.search(r'MCC[:\s]+(\d+)', line, re.IGNORECASE)
    mnc_match = re.search(r'MNC[:\s]+(\d+)', line, re.IGNORECASE)
    bsic_match = re.search(r'BSIC[:\s]+([0-9,]+)', line, re.IGNORECASE)

    # Alternative format: tab/comma separated values
    # ARFCN  Freq   Pwr    CID    LAC    MCC  MNC  BSIC
    if not arfcn_match:
        parts = re.split(r'[,\t]+', line)
        if len(parts) >= 3:
            try:
                # Try to parse as numeric fields
                arfcn = int(parts[0].strip())
                freq = float(parts[1].strip().replace('M', ''))
                power = float(parts[2].strip())
                cell = GsmCell(
                    arfcn=arfcn,
                    freq_mhz=freq,
                    power_dbm=power,
                )
                if len(parts) > 3:
                    cell.cell_id = int(parts[3].strip()) if parts[3].strip().isdigit() else None
                if len(parts) > 4:
                    cell.lac = int(parts[4].strip()) if parts[4].strip().isdigit() else None
                if len(parts) > 5:
                    cell.mcc = int(parts[5].strip()) if parts[5].strip().isdigit() else None
                if len(parts) > 6:
                    cell.mnc = int(parts[6].strip()) if parts[6].strip().isdigit() else None
                return cell
            except (ValueError, IndexError):
                pass

    if not arfcn_match:
        return None

    arfcn = int(arfcn_match.group(1))

    # Get frequency from output or calculate from ARFCN
    if freq_match:
        freq_mhz = float(freq_match.group(1))
    else:
        freq_mhz = arfcn_to_freq(arfcn)

    # Get power (default to weak if not found)
    if power_match:
        power_dbm = float(power_match.group(1))
    else:
        power_dbm = -100.0

    cell = GsmCell(
        arfcn=arfcn,
        freq_mhz=freq_mhz,
        power_dbm=power_dbm,
    )

    if cid_match:
        cell.cell_id = int(cid_match.group(1))
    if lac_match:
        cell.lac = int(lac_match.group(1))
    if mcc_match:
        cell.mcc = int(mcc_match.group(1))
    if mnc_match:
        cell.mnc = int(mnc_match.group(1))
    if bsic_match:
        cell.bsic = bsic_match.group(1)

    return cell


def run_grgsm_scan(
    band: str = 'GSM900',
    device_index: int = 0,
    gain: int = 40,
    ppm: int = 0,
    speed: int = 4,
    timeout: float = 60.0,
) -> Generator[GsmCell, None, None]:
    """
    Run grgsm_scanner and yield detected GSM cells.

    Args:
        band: GSM band to scan ('GSM900', 'GSM1800', 'GSM850', 'GSM1900')
        device_index: RTL-SDR device index
        gain: Gain in dB
        ppm: Frequency correction in PPM
        speed: Scan speed (1-5, higher is faster but less accurate)
        timeout: Maximum scan duration in seconds

    Yields:
        GsmCell objects for each detected cell
    """
    grgsm_scanner = get_grgsm_scanner_path()
    if not grgsm_scanner:
        logger.error("grgsm_scanner not found in PATH")
        return

    # Map band names to grgsm_scanner arguments
    band_args = {
        'GSM900': ['--band', 'P-GSM'],
        'EGSM900': ['--band', 'E-GSM'],
        'GSM1800': ['--band', 'DCS1800'],
        'GSM850': ['--band', 'GSM850'],
        'GSM1900': ['--band', 'PCS1900'],
    }

    band_arg = band_args.get(band.upper(), ['--band', 'P-GSM'])

    cmd = [
        grgsm_scanner,
        *band_arg,
        '-g', str(gain),
        '-p', str(ppm),
        '-s', str(speed),
        '-v',  # Verbose output for more cell details
    ]

    logger.info(f"Starting grgsm_scanner: {' '.join(cmd)}")

    stop_event = threading.Event()
    stderr_thread = None

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # Drain stderr in background
        stderr_thread = threading.Thread(
            target=_drain_stderr,
            args=(process, stop_event),
            daemon=True
        )
        stderr_thread.start()

        # Set up timeout
        import time
        start_time = time.time()

        # Parse output line by line
        for line in iter(process.stdout.readline, ''):
            if time.time() - start_time > timeout:
                logger.info(f"grgsm_scanner timeout after {timeout}s")
                break

            cell = parse_grgsm_output(line)
            if cell:
                yield cell

        # Terminate if still running
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    except Exception as e:
        logger.error(f"grgsm_scanner error: {e}")

    finally:
        stop_event.set()
        if stderr_thread:
            stderr_thread.join(timeout=1.0)


def run_gsm_scan_blocking(
    band: str = 'GSM900',
    device_index: int = 0,
    gain: int = 40,
    ppm: int = 0,
    speed: int = 4,
    timeout: float = 60.0,
) -> GsmScanResult:
    """
    Run a complete GSM scan and return all results.

    Args:
        band: GSM band to scan
        device_index: RTL-SDR device index
        gain: Gain in dB
        ppm: Frequency correction in PPM
        speed: Scan speed (1-5)
        timeout: Maximum scan duration

    Returns:
        GsmScanResult with all detected cells
    """
    import time
    start_time = time.time()

    cells: list[GsmCell] = []
    error: str | None = None

    try:
        for cell in run_grgsm_scan(
            band=band,
            device_index=device_index,
            gain=gain,
            ppm=ppm,
            speed=speed,
            timeout=timeout,
        ):
            cells.append(cell)
    except Exception as e:
        error = str(e)
        logger.error(f"GSM scan error: {e}")

    duration = time.time() - start_time

    # Determine frequency range based on band
    freq_ranges = {
        'GSM900': (935.0, 960.0),
        'EGSM900': (925.0, 960.0),
        'GSM1800': (1805.0, 1880.0),
        'GSM850': (869.0, 894.0),
        'GSM1900': (1930.0, 1990.0),
    }
    freq_start, freq_end = freq_ranges.get(band.upper(), (935.0, 960.0))

    return GsmScanResult(
        cells=cells,
        scan_duration_s=duration,
        device_index=device_index,
        freq_start_mhz=freq_start,
        freq_end_mhz=freq_end,
        error=error,
    )


def format_gsm_cell(cell: GsmCell) -> dict:
    """Format GSM cell for JSON output."""
    return {
        'arfcn': cell.arfcn,
        'freq_mhz': round(cell.freq_mhz, 1),
        'power_dbm': round(cell.power_dbm, 1),
        'mcc': cell.mcc,
        'mnc': cell.mnc,
        'lac': cell.lac,
        'cell_id': cell.cell_id,
        'bsic': cell.bsic,
        'plmn': cell.plmn,
        'cell_global_id': cell.cell_global_id,
        'timestamp': cell.timestamp.isoformat() + 'Z',
    }


def deduplicate_cells(cells: list[GsmCell]) -> list[GsmCell]:
    """
    Deduplicate cells by ARFCN, keeping strongest signal.

    Args:
        cells: List of detected cells

    Returns:
        Deduplicated list with strongest signal per ARFCN
    """
    best_cells: dict[int, GsmCell] = {}

    for cell in cells:
        if cell.arfcn not in best_cells:
            best_cells[cell.arfcn] = cell
        elif cell.power_dbm > best_cells[cell.arfcn].power_dbm:
            best_cells[cell.arfcn] = cell

    return sorted(best_cells.values(), key=lambda c: c.power_dbm, reverse=True)


def get_uk_operator_name(mcc: int | None, mnc: int | None) -> str | None:
    """Get UK mobile operator name from MCC/MNC."""
    if mcc != 234:  # UK MCC
        return None

    uk_operators = {
        10: 'O2',
        15: 'Vodafone',
        20: 'Three',
        30: 'EE',
        31: 'EE',
        32: 'EE',
        33: 'EE',
        34: 'EE',
        50: 'JT',
        55: 'Sure',
        58: 'Manx Telecom',
    }

    return uk_operators.get(mnc)


def identify_gsm_anomalies(
    current_cells: list[GsmCell],
    baseline_cells: list[GsmCell] | None = None,
) -> list[dict]:
    """
    Identify potential anomalies in GSM environment.

    Checks for:
    - New cells not in baseline
    - Cells with unusually strong signals
    - Cells with suspicious MCC/MNC combinations
    - Missing expected cells from baseline

    Args:
        current_cells: Currently detected cells
        baseline_cells: Optional baseline for comparison

    Returns:
        List of anomaly findings
    """
    anomalies = []

    current_arfcns = {c.arfcn for c in current_cells}

    # Check for very strong signals (potential nearby transmitter)
    for cell in current_cells:
        if cell.power_dbm > -40:
            anomalies.append({
                'type': 'strong_signal',
                'severity': 'warn',
                'description': f'Unusually strong GSM signal on ARFCN {cell.arfcn} ({cell.power_dbm:.1f} dBm)',
                'cell': format_gsm_cell(cell),
            })

    if baseline_cells:
        baseline_arfcns = {c.arfcn for c in baseline_cells}
        baseline_cids = {c.cell_global_id for c in baseline_cells if c.cell_global_id}

        # New ARFCNs not in baseline
        new_arfcns = current_arfcns - baseline_arfcns
        for arfcn in new_arfcns:
            cell = next((c for c in current_cells if c.arfcn == arfcn), None)
            if cell:
                anomalies.append({
                    'type': 'new_arfcn',
                    'severity': 'info',
                    'description': f'New ARFCN {arfcn} detected ({cell.freq_mhz:.1f} MHz, {cell.power_dbm:.1f} dBm)',
                    'cell': format_gsm_cell(cell),
                })

        # Missing ARFCNs from baseline
        missing_arfcns = baseline_arfcns - current_arfcns
        for arfcn in missing_arfcns:
            baseline_cell = next((c for c in baseline_cells if c.arfcn == arfcn), None)
            if baseline_cell:
                anomalies.append({
                    'type': 'missing_arfcn',
                    'severity': 'info',
                    'description': f'Expected ARFCN {arfcn} not detected (was {baseline_cell.power_dbm:.1f} dBm)',
                    'cell': format_gsm_cell(baseline_cell),
                })

        # Check for new cell IDs on existing ARFCNs (potential fake base station)
        for cell in current_cells:
            if cell.cell_global_id and cell.cell_global_id not in baseline_cids:
                if cell.arfcn in baseline_arfcns:
                    anomalies.append({
                        'type': 'new_cell_id',
                        'severity': 'warn',
                        'description': f'New Cell ID on existing ARFCN {cell.arfcn}: {cell.cell_global_id}',
                        'cell': format_gsm_cell(cell),
                    })

    return anomalies
