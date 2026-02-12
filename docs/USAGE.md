# INTERCEPT Usage Guide

Detailed instructions for each mode.

## Pager Mode

1. **Select Hardware** - Choose your SDR type (RTL-SDR, LimeSDR, or HackRF)
2. **Select Device** - Choose your SDR device from the dropdown
3. **Set Frequency** - Enter a frequency in MHz or use a preset
4. **Choose Protocols** - Select which protocols to decode (POCSAG/FLEX)
5. **Adjust Settings** - Set gain, squelch, and PPM correction as needed
6. **Start Decoding** - Click the green "Start Decoding" button

### Frequency Presets

- Click a preset button to quickly set a frequency
- Add custom presets using the input field and "Add" button
- Right-click a preset to remove it
- Click "Reset to Defaults" to restore default frequencies

## 433MHz Sensor Mode

1. **Select Hardware** - Choose your SDR type
2. **Select Device** - Choose your SDR device
3. **Start Decoding** - Click "Start Decoding"
4. **View Sensors** - Decoded sensor data appears in real-time

Supports 200+ protocols including weather stations, TPMS, doorbells, and IoT devices.

## WiFi Mode

1. **Select Interface** - Choose a WiFi adapter capable of monitor mode
2. **Enable Monitor Mode** - Click "Enable Monitor" (uncheck "Kill processes" to preserve other connections)
3. **Start Scanning** - Click "Start Scanning" to begin
4. **View Networks** - Networks appear in the output panel with signal strength
5. **Track Devices** - Click the chart icon on any network to track its signal over time
6. **Capture Handshakes** - Click "Capture" on a network to start handshake capture

### Tips

- Run with `sudo` for monitor mode to work
- Check your adapter supports monitor mode: `iw list | grep monitor`
- Use "Kill processes" option if NetworkManager interferes

## Bluetooth Mode

1. **Select Interface** - Choose your Bluetooth adapter
2. **Choose Mode** - Select scan mode (hcitool, bluetoothctl)
3. **Start Scanning** - Click "Start Scanning"
4. **View Devices** - Devices appear with name, address, and classification

### Tracker Detection

INTERCEPT automatically detects known trackers:
- Apple AirTag
- Tile
- Samsung SmartTag
- Chipolo

## Sub-GHz Analyzer

1. **Connect HackRF** - Plug in your HackRF One device
2. **Set Frequency** - Enter a frequency in the 300-928 MHz ISM range or use a preset
3. **Start Capture** - Click "Start Capture" to begin signal analysis
4. **View Spectrum** - Real-time spectrum visualization of the selected band
5. **Protocol Decoding** - Identified protocols are displayed with decoded data

### Supported Protocols

Common ISM band protocols including garage doors, key fobs, weather stations, and IoT devices in the 300-928 MHz range.

## Listening Post

1. **Select Hardware** - Choose your SDR type
2. **Set Frequency Range** - Define start and end frequencies for scanning
3. **Start Scanning** - Click "Start Scan" for wideband sweep
4. **View Signals** - Discovered signals are listed with frequency and SNR
5. **Tune In** - Click a signal to tune the audio demodulator
6. **Listen** - Real-time audio plays in your browser

### Demodulation Modes

- **FM** - Narrowband and wideband FM
- **SSB** - Upper and lower sideband for amateur radio and shortwave

## Aircraft Mode (ADS-B)

1. **Select Hardware** - Choose your SDR type (RTL-SDR uses dump1090, others use readsb)
2. **Check Tools** - Ensure dump1090 or readsb is installed
3. **Set Location** - Choose location source:
   - **Manual Entry** - Type coordinates directly
   - **Browser GPS** - Use browser's built-in geolocation (requires HTTPS)
   - **USB GPS Dongle** - Connect a USB GPS receiver for continuous updates
   - **Shared Location** - By default, the observer location is shared across modules
     (disable with `INTERCEPT_SHARED_OBSERVER_LOCATION=false`)
4. **Start Tracking** - Click "Start Tracking" to begin ADS-B reception
5. **View Map** - Aircraft appear on the interactive Leaflet map
6. **Click Aircraft** - Click markers for detailed information
7. **Display Options** - Toggle callsigns, altitude, trails, range rings, clustering
8. **Filter Aircraft** - Use dropdown to show all, military, civil, or emergency only
9. **Full Dashboard** - Click "Full Screen Dashboard" for dedicated radar view

> Note: ADS-B auto-start is disabled by default. To enable auto-start on dashboard load,
> set `INTERCEPT_ADSB_AUTO_START=true`.

### Emergency Squawks

The system highlights aircraft transmitting emergency squawks:
- **7500** - Hijack
- **7600** - Radio failure
- **7700** - General emergency

## ADS-B History (Optional)

The history dashboard persists aircraft messages and per-aircraft snapshots to Postgres for long-running tracking and reporting.

### Enable History

Set the following environment variables (Docker recommended):

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERCEPT_ADSB_HISTORY_ENABLED` | `false` | Enables history storage and reporting |
| `INTERCEPT_ADSB_DB_HOST` | `localhost` | Postgres host (use `adsb_db` in Docker) |
| `INTERCEPT_ADSB_DB_PORT` | `5432` | Postgres port |
| `INTERCEPT_ADSB_DB_NAME` | `intercept_adsb` | Database name |
| `INTERCEPT_ADSB_DB_USER` | `intercept` | Database user |
| `INTERCEPT_ADSB_DB_PASSWORD` | `intercept` | Database password |

### Other ADS-B Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERCEPT_ADSB_AUTO_START` | `false` | Auto-start ADS-B tracking when the dashboard loads |
| `INTERCEPT_SHARED_OBSERVER_LOCATION` | `true` | Share observer location across ADS-B/AIS/SSTV/Satellite modules |

**Local install example**

```bash
INTERCEPT_ADSB_AUTO_START=true \
INTERCEPT_SHARED_OBSERVER_LOCATION=false \
python app.py
```

**Docker example (.env)**

```bash
INTERCEPT_ADSB_AUTO_START=true
INTERCEPT_SHARED_OBSERVER_LOCATION=false
```

### Docker Setup

`docker-compose.yml` includes an `adsb_db` service and a persistent volume for history storage:

```bash
docker compose --profile history up -d
```

To store Postgres data on external storage, set `PGDATA_PATH` (defaults to `./pgdata`):

```bash
PGDATA_PATH=/mnt/usbpi1/intercept/pgdata
```

### Using the History Dashboard

1. Open **/adsb/history**
2. Use **Start Tracking** to run ADS-B in headless mode
3. View aircraft history and timelines
4. Stop tracking when desired (session history is recorded)

If the History dashboard shows **HISTORY DISABLED**, enable `INTERCEPT_ADSB_HISTORY_ENABLED=true` and ensure Postgres is running.

## Satellite Mode

1. **Set Location** - Choose location source:
   - **Manual Entry** - Type coordinates directly
   - **Browser GPS** - Use browser's built-in geolocation
   - **USB GPS Dongle** - Connect a USB GPS receiver for continuous updates
2. **Add Satellites** - Click "Add Satellite" to enter TLE data or fetch from Celestrak
3. **Calculate Passes** - Click "Calculate Passes" to predict upcoming passes
4. **View Sky Plot** - Polar plot shows satellite positions in real-time
5. **Ground Track** - Map displays satellite orbit path and current position
6. **Full Dashboard** - Click "Full Screen Dashboard" for dedicated satellite view

### Adding Satellites from Celestrak

1. Click "Add Satellite"
2. Select "Fetch from Celestrak"
3. Choose a category (Amateur, Weather, ISS, Starlink, etc.)
4. Select satellites to add

## Weather Satellites

1. **Set Location** - Enter observer coordinates or use GPS
2. **Select Satellite** - Choose NOAA (APT) or Meteor (LRPT)
3. **View Passes** - Upcoming passes shown with polar plot and ground track
4. **Start Capture** - Click "Start Capture" when a satellite is overhead, or enable auto-scheduler
5. **View Images** - Decoded imagery appears in the gallery

### Auto-Scheduler

Enable the auto-scheduler to automatically capture passes:
- Calculates upcoming NOAA and Meteor passes for your location
- Starts SatDump at the correct time and frequency
- Decoded images are saved with timestamps

## AIS Vessel Tracking

1. **Select Hardware** - Choose your SDR type
2. **Start Tracking** - Click "Start Tracking" to monitor AIS frequencies (161.975/162.025 MHz)
3. **View Map** - Vessels appear on the interactive maritime map
4. **Click Vessels** - View name, MMSI, callsign, destination, speed, heading
5. **Full Dashboard** - Click "Full Screen Dashboard" for dedicated maritime view

### VHF DSC Channel 70

Digital Selective Calling monitoring runs alongside AIS:
- Distress, Urgency, Safety, and Routine messages
- Distress positions plotted with pulsing alert markers
- Audio alerts for critical messages

## APRS

1. **Select Hardware** - Choose your SDR type
2. **Set Frequency** - Defaults to regional APRS frequency (144.390 MHz NA, 144.800 MHz EU)
3. **Start Decoding** - Click "Start Decoding" to begin packet radio reception via direwolf
4. **View Map** - Station positions appear on the interactive map
5. **View Messages** - Position reports, telemetry, and messages displayed in real time

## Utility Meters

1. **Start Monitoring** - Click "Start" to begin meter broadcast reception via rtl_amr
2. **View Meters** - Decoded meter data appears with meter ID, type, and consumption
3. **Filter** - Filter by meter type (electric, gas, water) or meter ID

## Meshtastic

1. **Connect Device** - Plug in a Meshtastic device via USB or connect via TCP
2. **Start** - Click "Start" to connect to the mesh network
3. **View Messages** - Real-time message stream from the mesh
4. **View Nodes** - Connected nodes displayed with signal metrics (RSSI, SNR)
5. **Send Messages** - Type messages to broadcast on the mesh

## Remote Agents (Distributed SIGINT)

Deploy lightweight sensor nodes across multiple locations and aggregate data to a central controller.

### Setting Up an Agent

1. **Install INTERCEPT** on the remote machine
2. **Create config file** (`intercept_agent.cfg`):
   ```ini
   [agent]
   name = sensor-node-1
   port = 8020

   [controller]
   url = http://192.168.1.100:5050
   api_key = your-secret-key
   push_enabled = true

   [modes]
   pager = true
   sensor = true
   adsb = true
   ```
3. **Start the agent**:
   ```bash
   python intercept_agent.py --config intercept_agent.cfg
   ```

### Registering Agents in the Controller

1. Navigate to `/controller/manage` in the main INTERCEPT instance
2. Enter agent details:
   - **Name**: Must match config file (e.g., `sensor-node-1`)
   - **Base URL**: Agent address (e.g., `http://192.168.1.50:8020`)
   - **API Key**: Must match config file
3. Click "Register Agent"
4. Use "Test" to verify connectivity

### Using Remote Agents

Once registered, agents appear in mode dropdowns:

1. **Select agent** from the dropdown in supported modes
2. **Start mode** - Commands are proxied to the remote agent
3. **View data** - Data streams back to your browser via SSE

### Multi-Agent Streaming

Enable "Show All Agents" to aggregate data from all registered agents simultaneously.

For complete documentation, see [Distributed Agents Guide](DISTRIBUTED_AGENTS.md).

## Configuration

INTERCEPT can be configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERCEPT_HOST` | `0.0.0.0` | Server bind address |
| `INTERCEPT_PORT` | `5050` | Server port |
| `INTERCEPT_DEBUG` | `false` | Enable debug mode |
| `INTERCEPT_LOG_LEVEL` | `WARNING` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `INTERCEPT_DEFAULT_GAIN` | `40` | Default RTL-SDR gain |

Example: `INTERCEPT_PORT=8080 sudo -E venv/bin/python intercept.py`

## Command-line Options

```
python3 intercept.py --help

  -p, --port PORT    Port to run server on (default: 5050)
  -H, --host HOST    Host to bind to (default: 0.0.0.0)
  -d, --debug        Enable debug mode
  --check-deps       Check dependencies and exit
```
