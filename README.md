# INTERCEPT

<p align="center">
  <img src="https://img.shields.io/badge/python-3.7+-blue.svg" alt="Python 3.7+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg" alt="Platform">
  <img src="https://img.shields.io/badge/author-smittix-cyan.svg" alt="Author">
</p>

<p align="center">
  <strong>Signal Intelligence</strong>
</p>

<p align="center">
  A sleek, modern web-based front-end for signal intelligence tools.<br>
  Unified interface for pager decoding, 433MHz sensors, WiFi reconnaissance, and Bluetooth scanning.
</p>

## Screenshot
<img src="screenshot.png">

---

## What is INTERCEPT?

INTERCEPT is a **web-based front-end** that provides a unified, modern interface for signal intelligence tools:

- **rtl_fm + multimon-ng** - For decoding POCSAG and FLEX pager signals
- **rtl_433** - For decoding 433MHz ISM band devices (weather stations, sensors, etc.)
- **aircrack-ng / kismet** - For WiFi reconnaissance and network analysis
- **hcitool / bluetoothctl / ubertooth** - For Bluetooth device scanning and tracking

Instead of running command-line tools manually, INTERCEPT handles the process management, output parsing, and presents decoded data in a clean, real-time web interface.

---

## Features

### Pager Decoding
- **Real-time decoding** of POCSAG (512/1200/2400) and FLEX protocols
- **Customizable frequency presets** stored in browser
- **Auto-restart** on frequency change while decoding

### 433MHz Sensor Decoding
- **200+ device protocols** supported via rtl_433
- **Weather stations** - temperature, humidity, wind, rain
- **TPMS** - Tire pressure monitoring sensors
- **Doorbells, remotes, and IoT devices**
- **Smart meters** and utility monitors

### WiFi Reconnaissance
- **Monitor mode** management via airmon-ng
- **Network scanning** with airodump-ng or Kismet
- **Channel hopping** or fixed channel monitoring
- **Deauthentication attacks** for authorized testing
- **Handshake capture** for WPA/WPA2 networks
- **Channel utilization** visualization (2.4GHz)
- **Security overview** chart (WPA3/WPA2/WEP/Open)
- **Real-time radar** display of nearby networks

### Bluetooth Scanning
- **BLE and Classic** Bluetooth device scanning
- **Multiple scan modes** - hcitool, bluetoothctl, Ubertooth, Bettercap
- **Tracker detection** - AirTag, Tile, Samsung SmartTag, Chipolo
- **Device classification** - phones, audio, wearables, computers
- **Manufacturer lookup** via OUI database
- **Service enumeration** via SDP
- **L2CAP ping** for device reachability
- **Proximity radar** visualization
- **Device type breakdown** chart

### General
- **Web-based interface** - no desktop app needed
- **Live message streaming** via Server-Sent Events (SSE)
- **Audio alerts** with mute toggle
- **Message export** to CSV/JSON
- **Signal activity meter** and waterfall display
- **Message logging** to file with timestamps
- **RTL-SDR device detection** and selection
- **Configurable gain and PPM correction**
- **Device intelligence** dashboard with tracking
- **Disclaimer acceptance** on first use
- **Auto-stop** when switching between modes


## Requirements

### Hardware
- RTL-SDR compatible dongle (RTL2832U based)

### Software
- Python 3.7+
- Flask
- rtl-sdr tools (`rtl_fm`)
- multimon-ng (for pager decoding)
- rtl_433 (for 433MHz sensor decoding)
- aircrack-ng (for WiFi reconnaissance)
- kismet (optional, alternative WiFi scanner)
- BlueZ tools - hcitool, bluetoothctl, sdptool, l2ping (for Bluetooth)
- Ubertooth tools (optional, for advanced BLE sniffing)
- Bettercap (optional, alternative BLE scanner)

## Installation

### 1. Install RTL-SDR tools

**macOS (Homebrew):**
```bash
brew install rtl-sdr
```

**Ubuntu/Debian:**
```bash
sudo apt-get install rtl-sdr
```

**Arch Linux:**
```bash
sudo pacman -S rtl-sdr
```

### 2. Install multimon-ng

**macOS (Homebrew):**
```bash
brew install multimon-ng
```

**Ubuntu/Debian:**
```bash
sudo apt-get install multimon-ng
```

**From source:**
```bash
git clone https://github.com/EliasOenal/multimon-ng.git
cd multimon-ng
mkdir build && cd build
cmake ..
make
sudo make install
```

### 3. Install rtl_433 (optional, for 433MHz sensors)

**macOS (Homebrew):**
```bash
brew install rtl_433
```

**Ubuntu/Debian:**
```bash
sudo apt-get install rtl-433
```

**From source:**
```bash
git clone https://github.com/merbanan/rtl_433.git
cd rtl_433
mkdir build && cd build
cmake ..
make
sudo make install
```

### 4. Install aircrack-ng (optional, for WiFi)

**macOS (Homebrew):**
```bash
brew install aircrack-ng
```

**Ubuntu/Debian:**
```bash
sudo apt-get install aircrack-ng
```

### 5. Install Bluetooth tools (optional)

**Ubuntu/Debian:**
```bash
sudo apt-get install bluez bluetooth
```

**macOS:**
Bluetooth tools are built-in, though with limited functionality compared to Linux.

### 6. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 7. Clone and run

```bash
git clone https://github.com/yourusername/intercept.git
cd intercept
python3 intercept.py
```

Open your browser to `http://localhost:5050`

## Usage

1. **Select Device** - Choose your RTL-SDR device from the dropdown
2. **Set Frequency** - Enter a frequency in MHz or use a preset
3. **Choose Protocols** - Select which protocols to decode (POCSAG/FLEX)
4. **Adjust Settings** - Set gain, squelch, and PPM correction as needed
5. **Start Decoding** - Click the green "Start Decoding" button
6. **View Messages** - Decoded messages appear in real-time in the output panel

### Frequency Presets

- Click a preset button to quickly set a frequency
- Add custom presets using the input field and "Add" button
- Right-click a preset to remove it
- Click "Reset to Defaults" to restore default frequencies

### Message Logging

Enable logging in the Logging section to save decoded messages to a file. Messages are saved with timestamp, protocol, address, and content.

## Default Frequencies

### Pager (UK)
- **153.350 MHz** - UK pager frequency
- **153.025 MHz** - UK pager frequency

### 433MHz Sensors
- **433.92 MHz** - EU/UK ISM band (most common)
- **315.00 MHz** - US ISM band
- **868.00 MHz** - EU ISM band
- **915.00 MHz** - US ISM band

You can customize pager presets in the web interface.

## API Endpoints

### Pager & Sensor
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main web interface |
| `/devices` | GET | List RTL-SDR devices |
| `/start` | POST | Start pager decoding |
| `/stop` | POST | Stop pager decoding |
| `/start_sensor` | POST | Start 433MHz sensor listening |
| `/stop_sensor` | POST | Stop 433MHz sensor listening |
| `/status` | GET | Get decoder status |
| `/stream` | GET | SSE stream for pager messages |
| `/stream_sensor` | GET | SSE stream for sensor data |
| `/logging` | POST | Toggle message logging |
| `/killall` | POST | Kill all decoder processes |

### WiFi
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/wifi/interfaces` | GET | List WiFi interfaces and tools |
| `/wifi/monitor` | POST | Enable/disable monitor mode |
| `/wifi/scan/start` | POST | Start WiFi scanning |
| `/wifi/scan/stop` | POST | Stop WiFi scanning |
| `/wifi/deauth` | POST | Send deauthentication packets |
| `/wifi/networks` | GET | Get discovered networks |
| `/wifi/stream` | GET | SSE stream for WiFi events |

### Bluetooth
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/bt/interfaces` | GET | List Bluetooth interfaces and tools |
| `/bt/scan/start` | POST | Start Bluetooth scanning |
| `/bt/scan/stop` | POST | Stop Bluetooth scanning |
| `/bt/enum` | POST | Enumerate device services |
| `/bt/ping` | POST | L2CAP ping a device |
| `/bt/devices` | GET | Get discovered devices |
| `/bt/stream` | GET | SSE stream for Bluetooth events |

## Troubleshooting

### No devices found
- Ensure your RTL-SDR is plugged in
- Check `rtl_test` works from command line
- On Linux, you may need to blacklist the DVB-T driver

### No messages appearing
- Verify the frequency is correct for your area
- Adjust the gain (try 30-40 dB)
- Check that pager services are active in your area
- Ensure antenna is connected

### Device busy error
- Click "Kill All Processes" to stop any stale processes
- Unplug and replug the RTL-SDR device

## License

MIT License - see [LICENSE](LICENSE) for details.

## Author

Created by **smittix** - [GitHub](https://github.com/smittix)

## Acknowledgments

- [rtl-sdr](https://osmocom.org/projects/rtl-sdr/wiki) - RTL-SDR drivers
- [multimon-ng](https://github.com/EliasOenal/multimon-ng) - Multi-protocol pager decoder
- [rtl_433](https://github.com/merbanan/rtl_433) - 433MHz sensor decoder
- Inspired by the SpaceX mission control aesthetic

## ⚠️ Disclaimer

**This software is for educational purposes only and intended for use by cybersecurity professionals in controlled environments.**

By using INTERCEPT, you acknowledge that:
- You will only use this tool with proper authorization
- Intercepting communications without consent may be illegal in your jurisdiction
- WiFi deauthentication and Bluetooth attacks should only be performed on networks/devices you own or have explicit permission to test
- You are solely responsible for ensuring compliance with all applicable laws and regulations
- The developers assume no liability for misuse of this software

A disclaimer must be accepted when first launching the application.


