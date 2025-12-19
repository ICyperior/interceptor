# INTERCEPT

<p align="center">
  <img src="https://img.shields.io/badge/python-3.7+-blue.svg" alt="Python 3.7+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg" alt="Platform">
</p>

<p align="center">
  <strong>Signal Intelligence</strong>
</p>

<p align="center">
  A sleek, modern web-based pager decoder using RTL-SDR and multimon-ng.<br>
  Decode POCSAG and FLEX pager signals with a futuristic SpaceX-inspired interface.
</p>

## Screenshot
<img src="screenshot.png">

---

## Features

- **Real-time decoding** of POCSAG (512/1200/2400) and FLEX protocols
- **Web-based interface** - no desktop app needed
- **Live message streaming** via Server-Sent Events (SSE)
- **Message logging** to file with timestamps
- **Customizable frequency presets** stored in browser
- **RTL-SDR device detection** and selection
- **Configurable gain, squelch, and PPM correction**


## Requirements

### Hardware
- RTL-SDR compatible dongle (RTL2832U based)

### Software
- Python 3.7+
- Flask
- rtl-sdr tools (`rtl_fm`)
- multimon-ng

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
git clone https://github.com/EliasOewortal/multimon-ng.git
cd multimon-ng
mkdir build && cd build
cmake ..
make
sudo make install
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Clone and run

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

## Default Frequencies (UK)

- **153.350 MHz** - UK pager frequency
- **153.025 MHz** - UK pager frequency

You can customize these presets in the web interface.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main web interface |
| `/devices` | GET | List RTL-SDR devices |
| `/start` | POST | Start decoding |
| `/stop` | POST | Stop decoding |
| `/status` | GET | Get decoder status |
| `/stream` | GET | SSE stream for messages |
| `/logging` | POST | Toggle message logging |
| `/killall` | POST | Kill all decoder processes |

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

## Acknowledgments

- [rtl-sdr](https://osmocom.org/projects/rtl-sdr/wiki) - RTL-SDR drivers
- [multimon-ng](https://github.com/EliasOenal/multimon-ng) - Multi-protocol decoder
- Inspired by the SpaceX mission control aesthetic

## Disclaimer

This software is for educational and authorized use only. Ensure you comply with local laws regarding radio reception and privacy. Intercepting private communications without authorization may be illegal in your jurisdiction.

