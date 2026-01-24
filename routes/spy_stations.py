"""Spy Stations routes - Number stations and diplomatic HF networks."""

from flask import Blueprint, jsonify, request

spy_stations_bp = Blueprint('spy_stations', __name__, url_prefix='/spy-stations')

# Active spy stations data from priyom.org
STATIONS = [
    # Number Stations (Intelligence)
    {
        "id": "e06",
        "name": "E06",
        "nickname": "English Man",
        "type": "number",
        "country": "Russia",
        "country_code": "RU",
        "frequencies": [
            {"freq_khz": 4310, "primary": True},
            {"freq_khz": 4800, "primary": False},
            {"freq_khz": 5370, "primary": False},
        ],
        "mode": "USB+carrier",
        "description": "Russian intelligence number station operated by 'Russian 6'. Male voice reads 5-figure groups. Broadcasts from Moscow, Orenburg, Smolensk, and Chita.",
        "operator": "Russian 6",
        "schedule": "Weekdays, 2 transmissions 1 hour apart",
        "source_url": "https://priyom.org/number-stations/english/e06"
    },
    {
        "id": "s06",
        "name": "S06",
        "nickname": "Russian Man",
        "type": "number",
        "country": "Russia",
        "country_code": "RU",
        "frequencies": [
            {"freq_khz": 4310, "primary": True},
            {"freq_khz": 4800, "primary": False},
            {"freq_khz": 5370, "primary": False},
        ],
        "mode": "USB+carrier",
        "description": "Russian language mode of the Russian 6 operator. Male voice reads 5-figure groups in Russian.",
        "operator": "Russian 6",
        "schedule": "Same schedule as E06, alternating languages",
        "source_url": "https://priyom.org/number-stations/russian/s06"
    },
    {
        "id": "uvb76",
        "name": "UVB-76",
        "nickname": "The Buzzer",
        "type": "number",
        "country": "Russia",
        "country_code": "RU",
        "frequencies": [
            {"freq_khz": 4625, "primary": True},
            {"freq_khz": 5779, "primary": False},
            {"freq_khz": 6810, "primary": False},
            {"freq_khz": 7490, "primary": False},
        ],
        "mode": "USB",
        "description": "Russian military command network. Continuous buzzing tone with occasional voice messages. Active since 1982. One of the most famous number stations.",
        "operator": "Russian Military",
        "schedule": "24/7 continuous operation",
        "source_url": "https://priyom.org/number-stations/russia/uvb-76"
    },
    {
        "id": "hm01",
        "name": "HM01",
        "nickname": "Cuban Numbers",
        "type": "number",
        "country": "Cuba",
        "country_code": "CU",
        "frequencies": [
            {"freq_khz": 9065, "primary": True},
            {"freq_khz": 9155, "primary": False},
            {"freq_khz": 9240, "primary": False},
            {"freq_khz": 9330, "primary": False},
            {"freq_khz": 10345, "primary": False},
            {"freq_khz": 10715, "primary": False},
            {"freq_khz": 10860, "primary": False},
            {"freq_khz": 11435, "primary": False},
            {"freq_khz": 11462, "primary": False},
            {"freq_khz": 11530, "primary": False},
            {"freq_khz": 11635, "primary": False},
            {"freq_khz": 12180, "primary": False},
            {"freq_khz": 13435, "primary": False},
            {"freq_khz": 14375, "primary": False},
            {"freq_khz": 16180, "primary": False},
            {"freq_khz": 17480, "primary": False},
        ],
        "mode": "AM/OFDM",
        "description": "Cuban DGI intelligence station. Spanish female voice 'Atencion' followed by number groups. Also uses RDFT OFDM digital mode.",
        "operator": "DGI (Cuban Intelligence)",
        "schedule": "Multiple daily transmissions",
        "source_url": "https://priyom.org/number-stations/cuba/hm01"
    },
    # Diplomatic Stations
    {
        "id": "bulgaria_mfa",
        "name": "Bulgaria MFA",
        "nickname": "Sofia Diplomatic",
        "type": "diplomatic",
        "country": "Bulgaria",
        "country_code": "BG",
        "frequencies": [
            {"freq_khz": 5145, "primary": True},
            {"freq_khz": 6755, "primary": False},
            {"freq_khz": 7670, "primary": False},
            {"freq_khz": 9155, "primary": False},
            {"freq_khz": 10175, "primary": False},
            {"freq_khz": 11445, "primary": False},
            {"freq_khz": 14725, "primary": False},
            {"freq_khz": 18520, "primary": False},
        ],
        "mode": "RFSM-8000/MIL-STD-188-110",
        "description": "Bulgarian Ministry of Foreign Affairs diplomatic network. Sofia to 14 embassies worldwide. Uses RFSM-8000 modem with MIL-STD-188-110.",
        "operator": "Bulgarian MFA",
        "schedule": "Daily scheduled transmissions",
        "source_url": "https://priyom.org/diplomatic/bulgaria"
    },
    {
        "id": "czechia_mfa",
        "name": "Czechia MFA",
        "nickname": "Czech Diplomatic",
        "type": "diplomatic",
        "country": "Czechia",
        "country_code": "CZ",
        "frequencies": [
            {"freq_khz": 6830, "primary": True},
            {"freq_khz": 8130, "primary": False},
            {"freq_khz": 10232, "primary": False},
            {"freq_khz": 13890, "primary": False},
        ],
        "mode": "PACTOR-III",
        "description": "Czech diplomatic network using PACTOR-III. Callsigns OLZ52-OLZ88. MoD station OL1A also active.",
        "operator": "Czech MFA / MoD",
        "schedule": "Regular scheduled traffic",
        "source_url": "https://priyom.org/diplomatic/czechia"
    },
    {
        "id": "egypt_mfa",
        "name": "Egypt MFA",
        "nickname": "Egyptian Diplomatic",
        "type": "diplomatic",
        "country": "Egypt",
        "country_code": "EG",
        "frequencies": [
            {"freq_khz": 7830, "primary": True},
            {"freq_khz": 9048, "primary": False},
            {"freq_khz": 10780, "primary": False},
            {"freq_khz": 13950, "primary": False},
        ],
        "mode": "SITOR/Codan 3012",
        "description": "Egyptian diplomatic network. 5-digit station IDs (66601=Washington, 11107=London). Uses SITOR and Codan 3012 modems.",
        "operator": "Egyptian MFA",
        "schedule": "Daily traffic windows",
        "source_url": "https://priyom.org/diplomatic/egypt"
    },
    {
        "id": "dprk_mfa",
        "name": "DPRK MFA",
        "nickname": "North Korea Diplomatic",
        "type": "diplomatic",
        "country": "North Korea",
        "country_code": "KP",
        "frequencies": [
            {"freq_khz": 7200, "primary": True},
            {"freq_khz": 9450, "primary": False},
            {"freq_khz": 11475, "primary": False},
            {"freq_khz": 13785, "primary": False},
            {"freq_khz": 15245, "primary": False},
            {"freq_khz": 17550, "primary": False},
            {"freq_khz": 21680, "primary": False},
            {"freq_khz": 25120, "primary": False},
        ],
        "mode": "DPRK-ARQ (LSB/BFSK 600Bd/MSK 1200Bd)",
        "description": "North Korean diplomatic network spanning 7-25 MHz. Uses proprietary DPRK-ARQ protocol. Daily encrypted traffic to embassies.",
        "operator": "DPRK MFA",
        "schedule": "Daily, multiple time slots",
        "source_url": "https://priyom.org/diplomatic/north-korea"
    },
    {
        "id": "russia_mfa",
        "name": "Russia MFA",
        "nickname": "Russian Diplomatic",
        "type": "diplomatic",
        "country": "Russia",
        "country_code": "RU",
        "frequencies": [
            {"freq_khz": 5154, "primary": True},
            {"freq_khz": 7654, "primary": False},
            {"freq_khz": 9045, "primary": False},
            {"freq_khz": 10755, "primary": False},
            {"freq_khz": 13455, "primary": False},
            {"freq_khz": 16354, "primary": False},
            {"freq_khz": 18954, "primary": False},
        ],
        "mode": "Perelivt/Serdolik/X06/OFDM",
        "description": "Extensive Russian diplomatic network using multiple proprietary modes including Perelivt, Serdolik, and OFDM variants.",
        "operator": "Russian MFA",
        "schedule": "24/7 network operations",
        "source_url": "https://priyom.org/diplomatic/russia"
    },
    {
        "id": "tunisia_mfa",
        "name": "Tunisia MFA",
        "nickname": "Tunisian Diplomatic",
        "type": "diplomatic",
        "country": "Tunisia",
        "country_code": "TN",
        "frequencies": [
            {"freq_khz": 5810, "primary": True},
            {"freq_khz": 7954, "primary": False},
            {"freq_khz": 8014, "primary": False},
            {"freq_khz": 8180, "primary": False},
            {"freq_khz": 10113, "primary": False},
            {"freq_khz": 10176, "primary": False},
            {"freq_khz": 11111, "primary": False},
            {"freq_khz": 12140, "primary": False},
            {"freq_khz": 13945, "primary": False},
            {"freq_khz": 14700, "primary": False},
            {"freq_khz": 14724, "primary": False},
            {"freq_khz": 15635, "primary": False},
            {"freq_khz": 16125, "primary": False},
            {"freq_khz": 16285, "primary": False},
            {"freq_khz": 16290, "primary": False},
            {"freq_khz": 18295, "primary": False},
            {"freq_khz": 19675, "primary": False},
            {"freq_khz": 23540, "primary": False},
            {"freq_khz": 24080, "primary": False},
            {"freq_khz": 24170, "primary": False},
            {"freq_khz": 26890, "primary": False},
        ],
        "mode": "2G ALE/PACTOR-II",
        "description": "Tunisian MFA network. Callsigns STAT151-155. Uses 2G ALE for linking and PACTOR-II for traffic. MAPI email format.",
        "operator": "Tunisian MFA",
        "schedule": "Regular diplomatic traffic",
        "source_url": "https://priyom.org/diplomatic/tunisia"
    },
    {
        "id": "usa_state",
        "name": "US State Dept",
        "nickname": "American Diplomatic",
        "type": "diplomatic",
        "country": "United States",
        "country_code": "US",
        "frequencies": [
            {"freq_khz": 5749, "primary": True},
            {"freq_khz": 6903, "primary": False},
            {"freq_khz": 8059, "primary": False},
            {"freq_khz": 10734, "primary": False},
            {"freq_khz": 11169, "primary": False},
            {"freq_khz": 13504, "primary": False},
            {"freq_khz": 16284, "primary": False},
            {"freq_khz": 18249, "primary": False},
            {"freq_khz": 20811, "primary": False},
            {"freq_khz": 24884, "primary": False},
        ],
        "mode": "2G ALE (MIL-STD-188-141A)",
        "description": "US State Department diplomatic network. 140+ embassy callsigns (KWX57=Warsaw, KRH50=Tokyo, etc.). Uses 2G ALE linking.",
        "operator": "US State Department",
        "schedule": "24/7 global network",
        "source_url": "https://priyom.org/diplomatic/united-states"
    },
]


@spy_stations_bp.route('/stations')
def get_stations():
    """Return all spy stations, optionally filtered."""
    station_type = request.args.get('type')
    country = request.args.get('country')
    mode = request.args.get('mode')

    filtered = STATIONS

    if station_type:
        filtered = [s for s in filtered if s['type'] == station_type]

    if country:
        filtered = [s for s in filtered if s['country_code'].upper() == country.upper()]

    if mode:
        mode_lower = mode.lower()
        filtered = [s for s in filtered if mode_lower in s['mode'].lower()]

    return jsonify({
        'status': 'success',
        'count': len(filtered),
        'stations': filtered
    })


@spy_stations_bp.route('/stations/<station_id>')
def get_station(station_id):
    """Get a single station by ID."""
    for station in STATIONS:
        if station['id'] == station_id:
            return jsonify({
                'status': 'success',
                'station': station
            })

    return jsonify({
        'status': 'error',
        'message': 'Station not found'
    }), 404


@spy_stations_bp.route('/filters')
def get_filters():
    """Return available filter options."""
    types = list(set(s['type'] for s in STATIONS))
    countries = sorted(list(set((s['country'], s['country_code']) for s in STATIONS)))
    modes = sorted(list(set(s['mode'].split('/')[0] for s in STATIONS)))

    return jsonify({
        'status': 'success',
        'filters': {
            'types': types,
            'countries': [{'name': c[0], 'code': c[1]} for c in countries],
            'modes': modes
        }
    })
