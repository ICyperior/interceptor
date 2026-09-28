"""Guard: each mesh dashboard must load the core scripts its module needs.

The Meshtastic/Meshcore modules were written for the SPA (which loads all core
scripts). When they moved to standalone dashboards, missing includes caused
runtime ReferenceErrors that only fire after a device connects (node/message
render), which the browser smoke test can't trigger. This locks the includes in.
"""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

# dashboard template -> globals its module references -> providing script
REQUIRED = {
    "aprs_dashboard.html": {
        "Settings": "js/core/settings-manager.js",  # map tiles via MapUtils
        "MapUtils": "js/map-utils.js",
    },
    "meshtastic_dashboard.html": {
        "Settings": "js/core/settings-manager.js",
        "CopyId": "js/core/copy-id.js",
        "DeviceNotes": "js/core/device-notes.js",
        "MapUtils": "js/map-utils.js",
    },
    "meshcore_dashboard.html": {
        "Settings": "js/core/settings-manager.js",
        "MapUtils": "js/map-utils.js",
    },
    "iss_dashboard.html": {
        "Settings": "js/core/settings-manager.js",  # map tiles via MapUtils
        "MapUtils": "js/map-utils.js",
    },
}


def test_mesh_dashboards_load_required_scripts():
    for template, needs in REQUIRED.items():
        html = (TEMPLATES / template).read_text(encoding="utf-8")
        for global_name, script in needs.items():
            assert script in html, f"{template} must load {script} (provides {global_name})"
