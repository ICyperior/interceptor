"""
ISMS scan presets and band definitions.

Defines frequency ranges and parameters for common RF monitoring scenarios.
"""

from __future__ import annotations

# Scan presets for common monitoring scenarios
ISMS_SCAN_PRESETS: dict[str, dict] = {
    'vhf_airband': {
        'name': 'VHF Airband',
        'description': 'Aviation communications 118-137 MHz',
        'freq_start': 118.0,
        'freq_end': 137.0,
        'bin_size': 25000,  # 25 kHz channel spacing
        'integration': 1.0,
        'category': 'aviation',
    },
    'uhf_airband': {
        'name': 'UHF Airband',
        'description': 'Military aviation 225-400 MHz',
        'freq_start': 225.0,
        'freq_end': 400.0,
        'bin_size': 25000,
        'integration': 1.0,
        'category': 'aviation',
    },
    'uhf_pmr': {
        'name': 'UHF PMR446',
        'description': 'License-free radio 446 MHz',
        'freq_start': 446.0,
        'freq_end': 446.2,
        'bin_size': 12500,  # 12.5 kHz channel spacing
        'integration': 0.5,
        'category': 'pmr',
    },
    'ism_433': {
        'name': 'ISM 433 MHz',
        'description': 'European ISM band (sensors, remotes)',
        'freq_start': 433.0,
        'freq_end': 434.8,
        'bin_size': 10000,
        'integration': 0.5,
        'category': 'ism',
    },
    'ism_868': {
        'name': 'ISM 868 MHz',
        'description': 'European ISM band (LoRa, smart meters)',
        'freq_start': 868.0,
        'freq_end': 870.0,
        'bin_size': 10000,
        'integration': 0.5,
        'category': 'ism',
    },
    'ism_915': {
        'name': 'ISM 915 MHz',
        'description': 'US ISM band',
        'freq_start': 902.0,
        'freq_end': 928.0,
        'bin_size': 50000,
        'integration': 1.0,
        'category': 'ism',
    },
    'wifi_2g': {
        'name': 'WiFi 2.4 GHz Vicinity',
        'description': 'WiFi band activity (requires wideband SDR)',
        'freq_start': 2400.0,
        'freq_end': 2500.0,
        'bin_size': 500000,
        'integration': 2.0,
        'category': 'wifi',
        'note': 'Requires SDR with 2.4 GHz capability (HackRF, LimeSDR)',
    },
    'cellular_700': {
        'name': 'Cellular 700 MHz',
        'description': 'LTE Bands 12/13/17/28 downlink',
        'freq_start': 728.0,
        'freq_end': 803.0,
        'bin_size': 100000,
        'integration': 1.0,
        'category': 'cellular',
    },
    'cellular_850': {
        'name': 'Cellular 850 MHz',
        'description': 'GSM/LTE Band 5 downlink',
        'freq_start': 869.0,
        'freq_end': 894.0,
        'bin_size': 100000,
        'integration': 1.0,
        'category': 'cellular',
    },
    'cellular_900': {
        'name': 'Cellular 900 MHz',
        'description': 'GSM/LTE Band 8 downlink (Europe)',
        'freq_start': 925.0,
        'freq_end': 960.0,
        'bin_size': 100000,
        'integration': 1.0,
        'category': 'cellular',
    },
    'cellular_1800': {
        'name': 'Cellular 1800 MHz',
        'description': 'GSM/LTE Band 3 downlink',
        'freq_start': 1805.0,
        'freq_end': 1880.0,
        'bin_size': 100000,
        'integration': 1.0,
        'category': 'cellular',
    },
    'full_sweep': {
        'name': 'Full Spectrum',
        'description': 'Complete 24 MHz - 1.7 GHz sweep',
        'freq_start': 24.0,
        'freq_end': 1700.0,
        'bin_size': 100000,
        'integration': 5.0,
        'category': 'full',
        'note': 'Takes 3-5 minutes to complete',
    },
}


# Cellular band definitions (downlink frequencies for reference)
CELLULAR_BANDS: dict[str, dict] = {
    'B1': {
        'name': 'UMTS/LTE Band 1',
        'dl_start': 2110,
        'dl_end': 2170,
        'ul_start': 1920,
        'ul_end': 1980,
        'duplex': 'FDD',
    },
    'B3': {
        'name': 'LTE Band 3',
        'dl_start': 1805,
        'dl_end': 1880,
        'ul_start': 1710,
        'ul_end': 1785,
        'duplex': 'FDD',
    },
    'B5': {
        'name': 'GSM/LTE Band 5',
        'dl_start': 869,
        'dl_end': 894,
        'ul_start': 824,
        'ul_end': 849,
        'duplex': 'FDD',
    },
    'B7': {
        'name': 'LTE Band 7',
        'dl_start': 2620,
        'dl_end': 2690,
        'ul_start': 2500,
        'ul_end': 2570,
        'duplex': 'FDD',
    },
    'B8': {
        'name': 'GSM/LTE Band 8',
        'dl_start': 925,
        'dl_end': 960,
        'ul_start': 880,
        'ul_end': 915,
        'duplex': 'FDD',
    },
    'B20': {
        'name': 'LTE Band 20',
        'dl_start': 791,
        'dl_end': 821,
        'ul_start': 832,
        'ul_end': 862,
        'duplex': 'FDD',
    },
    'B28': {
        'name': 'LTE Band 28',
        'dl_start': 758,
        'dl_end': 803,
        'ul_start': 703,
        'ul_end': 748,
        'duplex': 'FDD',
    },
    'B38': {
        'name': 'LTE Band 38 (TDD)',
        'dl_start': 2570,
        'dl_end': 2620,
        'duplex': 'TDD',
    },
    'B40': {
        'name': 'LTE Band 40 (TDD)',
        'dl_start': 2300,
        'dl_end': 2400,
        'duplex': 'TDD',
    },
    'n77': {
        'name': 'NR Band n77 (5G)',
        'dl_start': 3300,
        'dl_end': 4200,
        'duplex': 'TDD',
    },
    'n78': {
        'name': 'NR Band n78 (5G)',
        'dl_start': 3300,
        'dl_end': 3800,
        'duplex': 'TDD',
    },
}


# UK Mobile Network Operators (for PLMN identification)
UK_OPERATORS: dict[str, str] = {
    '234-10': 'O2 UK',
    '234-15': 'Vodafone UK',
    '234-20': 'Three UK',
    '234-30': 'EE',
    '234-33': 'EE',
    '234-34': 'EE',
    '234-50': 'JT (Jersey)',
    '234-55': 'Sure (Guernsey)',
}


# Common ISM band allocations
ISM_BANDS: dict[str, dict] = {
    'ism_6m': {
        'name': '6.78 MHz ISM',
        'start': 6.765,
        'end': 6.795,
        'region': 'Worldwide',
    },
    'ism_13m': {
        'name': '13.56 MHz ISM (NFC/RFID)',
        'start': 13.553,
        'end': 13.567,
        'region': 'Worldwide',
    },
    'ism_27m': {
        'name': '27 MHz ISM (CB)',
        'start': 26.957,
        'end': 27.283,
        'region': 'Worldwide',
    },
    'ism_40m': {
        'name': '40.68 MHz ISM',
        'start': 40.66,
        'end': 40.70,
        'region': 'Worldwide',
    },
    'ism_433': {
        'name': '433 MHz ISM',
        'start': 433.05,
        'end': 434.79,
        'region': 'ITU Region 1 (Europe)',
    },
    'ism_868': {
        'name': '868 MHz ISM',
        'start': 868.0,
        'end': 870.0,
        'region': 'Europe',
    },
    'ism_915': {
        'name': '915 MHz ISM',
        'start': 902.0,
        'end': 928.0,
        'region': 'Americas',
    },
    'ism_2400': {
        'name': '2.4 GHz ISM (WiFi/BT)',
        'start': 2400.0,
        'end': 2500.0,
        'region': 'Worldwide',
    },
    'ism_5800': {
        'name': '5.8 GHz ISM',
        'start': 5725.0,
        'end': 5875.0,
        'region': 'Worldwide',
    },
}


def get_preset(preset_name: str) -> dict | None:
    """Get a scan preset by name."""
    return ISMS_SCAN_PRESETS.get(preset_name)


def get_presets_by_category(category: str) -> list[dict]:
    """Get all presets in a category."""
    return [
        {**preset, 'id': name}
        for name, preset in ISMS_SCAN_PRESETS.items()
        if preset.get('category') == category
    ]


def get_all_presets() -> list[dict]:
    """Get all presets with their IDs."""
    return [
        {**preset, 'id': name}
        for name, preset in ISMS_SCAN_PRESETS.items()
    ]


def identify_band(freq_mhz: float) -> str | None:
    """Identify which cellular band a frequency belongs to."""
    for band_id, band_info in CELLULAR_BANDS.items():
        dl_start = band_info.get('dl_start', 0)
        dl_end = band_info.get('dl_end', 0)
        if dl_start <= freq_mhz <= dl_end:
            return band_id
    return None


def identify_operator(plmn: str) -> str | None:
    """Identify UK operator from PLMN code."""
    return UK_OPERATORS.get(plmn)
