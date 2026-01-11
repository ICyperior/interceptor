# Hardware & Advanced Setup

## Supported SDR Hardware

| Hardware | Frequency Range | Price | Notes |
|----------|-----------------|-------|-------|
| **RTL-SDR** | 24 - 1766 MHz | ~$25-35 | Recommended for beginners |
| **LimeSDR** | 0.1 - 3800 MHz | ~$300 | Wide range, requires SoapySDR |
| **HackRF** | 1 - 6000 MHz | ~$300 | Ultra-wide range, requires SoapySDR |

INTERCEPT automatically detects connected devices.

---

## Quick Install

### macOS (Homebrew)

```bash
# Install Homebrew if needed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Core tools (required)
brew install python@3.11 librtlsdr multimon-ng rtl_433 ffmpeg

# ADS-B aircraft tracking
brew install dump1090-mutability

# WiFi tools (optional)
brew install aircrack-ng

# LimeSDR support (optional)
brew install soapysdr limesuite soapylms7

# HackRF support (optional)
brew install hackrf soapyhackrf
```

### Debian / Ubuntu / Raspberry Pi OS

```bash
# Update package lists
sudo apt update

# Core tools (required)
sudo apt install -y python3 python3-pip python3-venv python3-skyfield
sudo apt install -y rtl-sdr multimon-ng rtl-433 ffmpeg

# ADS-B aircraft tracking
sudo apt install -y dump1090-mutability
# Alternative: dump1090-fa (FlightAware version)

# WiFi tools (optional)
sudo apt install -y aircrack-ng

# Bluetooth tools (optional)
sudo apt install -y bluez bluetooth

# LimeSDR support (optional)
sudo apt install -y soapysdr-tools limesuite soapysdr-module-lms7

# HackRF support (optional)
sudo apt install -y hackrf soapysdr-module-hackrf
```

---

## RTL-SDR Setup (Linux)

### Add udev rules

If your RTL-SDR isn't detected, create udev rules:

```bash
sudo bash -c 'cat > /etc/udev/rules.d/20-rtlsdr.rules << EOF
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666"
EOF'

sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug and replug your RTL-SDR.

### Blacklist DVB-T driver

The default DVB-T driver conflicts with rtl-sdr:

```bash
echo "blacklist dvb_usb_rtl28xxu" | sudo tee /etc/modprobe.d/blacklist-rtl.conf
sudo modprobe -r dvb_usb_rtl28xxu
```

---

## Verify Installation

### Check dependencies
```bash
python3 intercept.py --check-deps
```

### Test SDR detection
```bash
# RTL-SDR
rtl_test

# LimeSDR/HackRF (via SoapySDR)
SoapySDRUtil --find
```

---

## Python Environment

### Using setup.sh (Recommended)
```bash
./setup.sh
```

This automatically:
- Detects your OS
- Creates a virtual environment if needed (for PEP 668 systems)
- Installs Python dependencies
- Checks for required tools

### Manual setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Running INTERCEPT

After installation:

```bash
sudo -E venv/bin/python intercept.py

# Custom port
INTERCEPT_PORT=8080 sudo -E venv/bin/python intercept.py
```

Open **http://localhost:5050** in your browser.

---

## Complete Tool Reference

| Tool | Package (Debian) | Package (macOS) | Required For |
|------|------------------|-----------------|--------------|
| `rtl_fm` | rtl-sdr | librtlsdr | Pager, Listening Post |
| `rtl_test` | rtl-sdr | librtlsdr | SDR detection |
| `multimon-ng` | multimon-ng | multimon-ng | Pager decoding |
| `rtl_433` | rtl-433 | rtl_433 | 433MHz sensors |
| `dump1090` | dump1090-mutability | dump1090-mutability | ADS-B tracking |
| `ffmpeg` | ffmpeg | ffmpeg | Listening Post audio |
| `airmon-ng` | aircrack-ng | aircrack-ng | WiFi monitor mode |
| `airodump-ng` | aircrack-ng | aircrack-ng | WiFi scanning |
| `aireplay-ng` | aircrack-ng | aircrack-ng | WiFi deauth (optional) |
| `hcitool` | bluez | N/A | Bluetooth scanning |
| `bluetoothctl` | bluez | N/A | Bluetooth control |
| `hciconfig` | bluez | N/A | Bluetooth config |

### Optional tools:
| Tool | Package (Debian) | Package (macOS) | Purpose |
|------|------------------|-----------------|---------|
| `ffmpeg` | ffmpeg | ffmpeg | Alternative audio encoder |
| `SoapySDRUtil` | soapysdr-tools | soapysdr | LimeSDR/HackRF support |
| `LimeUtil` | limesuite | limesuite | LimeSDR native tools |
| `hackrf_info` | hackrf | hackrf | HackRF native tools |

### Python dependencies (requirements.txt):
| Package | Purpose |
|---------|---------|
| `flask` | Web server |
| `skyfield` | Satellite tracking |

---

## dump1090 Notes

### Package names vary by distribution:
- `dump1090-mutability` - Most common
- `dump1090-fa` - FlightAware version (recommended)
- `dump1090` - Generic

### Not in repositories (Debian Trixie)?

Install FlightAware's version:
https://flightaware.com/adsb/piaware/install

Or build from source:
https://github.com/flightaware/dump1090

---

## Notes

- **Bluetooth on macOS**: Uses native CoreBluetooth, bluez tools not needed
- **WiFi on macOS**: Monitor mode has limited support, full functionality on Linux
- **System tools**: `iw`, `iwconfig`, `rfkill`, `ip` are pre-installed on most Linux systems

