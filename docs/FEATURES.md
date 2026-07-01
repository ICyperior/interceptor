# INTERCEPT Features

Quick reference for all supported modes. Click any mode for full usage instructions.

| Mode | Description | Tools Required |
|------|-------------|----------------|
| [Pager Decoding](USAGE.md#pager-mode) | POCSAG 512/1200/2400 and FLEX decoding | RTL-SDR, multimon-ng |
| [433MHz Sensors](USAGE.md#433mhz-sensor-mode) | 200+ device protocols — weather, TPMS, IoT | RTL-SDR, rtl_433 |
| [Sub-GHz Analyzer](USAGE.md#sub-ghz-analyzer) | 300–928 MHz ISM capture, decode, replay | HackRF |
| [Aircraft Tracking (ADS-B)](USAGE.md#aircraft-mode-ads-b) | Real-time radar map, virtual radar scope, filtering | RTL-SDR, dump1090 |
| [ADS-B History](USAGE.md#ads-b-history-optional) | Persistent aircraft history and reporting dashboard | PostgreSQL (optional) |
| [Vessel Tracking (AIS)](USAGE.md#ais-vessel-tracking) | Maritime map, DSC Channel 70 distress monitoring | RTL-SDR, AIS-catcher |
| [ACARS Messaging](USAGE.md#acars-messaging) | Aircraft datalink messages | RTL-SDR, acarsdec |
| [VDL2](USAGE.md#vdl2-aircraft-datalink) | VHF Data Link Mode 2 aircraft datalink | RTL-SDR, dumpvdl2 |
| [Listening Post](USAGE.md#listening-post) | Wideband scanner with real-time audio streaming | RTL-SDR/HackRF, Icecast |
| [Weather Satellites](USAGE.md#weather-satellites) | NOAA APT and Meteor LRPT image decoding | RTL-SDR, SatDump |
| [WebSDR](USAGE.md#websdr) | Remote HF/shortwave listening via KiwiSDR network | None (web-based) |
| [ISS SSTV](USAGE.md#iss-sstv) | Slow-scan TV image reception from the ISS | RTL-SDR, slowrx |
| [HF SSTV](USAGE.md#hf-sstv) | Terrestrial SSTV on shortwave and VHF | RTL-SDR, slowrx |
| [APRS](USAGE.md#aprs) | Amateur packet radio position reports and telemetry | RTL-SDR/TNC, direwolf |
| [Satellite Tracking](USAGE.md#satellite-mode) | Pass prediction, polar plot, ground track map | RTL-SDR (optional) |
| [Utility Meters](USAGE.md#utility-meters) | Electric, gas, and water meter reading | RTL-SDR, rtlamr |
| [WiFi Scanning](USAGE.md#wifi-mode) | Monitor mode reconnaissance, network discovery | Monitor-mode WiFi adapter |
| [Bluetooth Scanning](USAGE.md#bluetooth-mode) | Device discovery, tracker detection (AirTag, Tile…) | Bluetooth adapter |
| [BT Locate](USAGE.md#bt-locate-sar-device-location) | GPS-tagged signal trail and proximity alerts | Bluetooth + GPS |
| [WiFi Locate](USAGE.md#wifi-locate-mode) | Locate APs by BSSID with signal meter and distance | WiFi adapter |
| [GPS](USAGE.md#gps-mode) | Real-time position, speed, altitude, satellite map | GPS receiver, gpsd |
| [TSCM](USAGE.md#tscm-counter-surveillance) | RF baseline comparison and threat detection | RTL-SDR + BT + WiFi |
| [Drone Intelligence](USAGE.md#drone-intelligence) | UAV detection via Remote ID, RF, and HackRF | RTL-SDR / HackRF |
| [Spy Stations](USAGE.md#spy-stations) | Number stations and diplomatic HF network database | None (database lookup) |
| [Meshtastic](USAGE.md#meshtastic) | LoRa mesh network integration | Meshtastic device |
| [Space Weather](USAGE.md#space-weather) | Solar and geomagnetic data from NOAA/NASA | None (web-based) |
| [Remote Agents](USAGE.md#remote-agents-distributed-sigint) | Distributed SIGINT with remote sensor nodes | Multiple SDR nodes |
| [Offline Mode](USAGE.md#offline-mode) | Bundled assets for air-gapped/field deployments | Any |

## Detailed Docs

- [Usage Guide](USAGE.md) — per-mode setup and operation
- [Hardware Guide](HARDWARE.md) — SDR hardware, drivers, multiple dongles, Icecast
- [Webhooks](WEBHOOKS.md) — alert rules and webhook integration
- [Distributed Agents](DISTRIBUTED_AGENTS.md) — remote sensor node deployment
- [Troubleshooting](TROUBLESHOOTING.md) — common issues and solutions
- [Security](SECURITY.md) — network security and best practices
