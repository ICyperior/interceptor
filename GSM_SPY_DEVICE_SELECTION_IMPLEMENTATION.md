# GSM Spy SDR Device Selection Implementation

## Summary

Successfully implemented dynamic SDR device detection, selection, and management for the GSM Spy feature, following the same pattern used in the Aircraft/ADS-B implementation.

## Changes Made

### Frontend Changes (`templates/gsm_spy_dashboard.html`)

#### 1. Dynamic Device Selector
- **Changed**: Device dropdown from hardcoded options to dynamic detection
- **Location**: Line ~1155 (Signal Source Panel)
- **Before**: Static options (Device 0, Device 1, etc.)
- **After**: Dynamic population with "Detecting devices..." placeholder

#### 2. Device Detection on Page Load
- **Added**: `initDeviceSelector()` function
- **Location**: ~Line 1395
- **Functionality**:
  - Fetches available SDR devices from `/devices` endpoint
  - Populates dropdown with detected devices
  - Shows device name, type (RTL-SDR, HackRF, etc.), and serial number
  - Handles errors gracefully with user-friendly messages
  - Logs detection results to console

#### 3. Scanner Controls Update
- **Modified**: `startScanner()` function (~Line 1410)
- **Changes**:
  - Made async for better error handling
  - Reads device index from `deviceSelect` dropdown
  - Disables device and region selectors during active scanning
  - Enhanced error handling with device conflict detection
  - Shows user-friendly alerts for device busy errors

#### 4. Stop Scanner Enhancements
- **Modified**: `stopScanner()` function (~Line 1494)
- **Changes**:
  - Re-enables device and region selectors after stopping
  - Maintains UI consistency

#### 5. Region Selector Sync
- **Modified**: `selectRegion()` function (~Line 1882)
- **Changes**:
  - Capitalizes region name to match backend API expectations
  - Syncs region button selection with dropdown

#### 6. Removed Redundant Controls
- **Removed**: `scannerDevice` dropdown from bottom controls bar
- **Reason**: Consolidated to single device selector in left sidebar

### Backend Changes (`routes/gsm_spy.py`)

#### 1. Enhanced Error Response
- **Modified**: `/start` endpoint device claiming logic (~Line 115)
- **Changes**:
  - Added `error_type: 'DEVICE_BUSY'` to 409 conflict responses
  - Enables frontend to distinguish device conflicts from other errors
  - Allows for targeted user-friendly error messages

#### 2. Existing Device Management (Verified)
- **Confirmed**: Device claiming/releasing already implemented
  - `claim_sdr_device()` called at line 115
  - `release_sdr_device()` called at line 289
  - Device index stored in `gsm_spy_active_device`
  - Region stored in `gsm_spy_region`

#### 3. Status Endpoint (Verified)
- **Confirmed**: `/status` endpoint returns device info
  - Returns `device` (active device index)
  - Returns `region` (selected region)
  - Returns all necessary status information

## Features Implemented

### ✅ Device Detection
- Dynamically detects all available SDR devices on page load
- Supports all 5 SDR types: RTL-SDR, HackRF, LimeSDR, Airspy, SDRPlay
- Shows device name, type, and serial number in dropdown

### ✅ Device Registry Integration
- Properly claims devices before starting scanner
- Releases devices when stopping scanner
- Prevents conflicts with other INTERCEPT modes

### ✅ UI State Management
- Disables device selector during active scanning
- Re-enables selector after stopping
- Provides clear visual feedback to user

### ✅ Error Handling
- User-friendly error messages for device conflicts
- Graceful handling of "no devices detected" scenario
- Clear console logging for debugging

### ✅ Validation
- Uses existing `validate_device_index()` function (already in code)
- Validates region against `REGIONAL_BANDS` dictionary
- Checks for already running scanner

## Architecture Pattern

The implementation follows the same pattern as Aircraft/ADS-B:

1. **Device Detection**: `/devices` endpoint (shared across all modes)
2. **Device Claiming**: `claim_sdr_device()` before starting
3. **Device Releasing**: `release_sdr_device()` on stop
4. **UI Consistency**: Dynamic dropdown, disabled during operation
5. **Error Handling**: Clear user messages, console logging

## Testing Recommendations

### 1. Device Detection
```bash
# Start application
sudo -E venv/bin/python intercept.py

# Open GSM Spy dashboard in browser
# Open DevTools console
# Should see: "[GSM SPY] Detected X SDR device(s)"
# Verify dropdown shows detected devices
```

### 2. Device Claiming
```bash
# Start GSM scanner on device 0
# Try to start another mode (e.g., ADS-B) on device 0
# Should see conflict error message
# Stop GSM scanner
# Now ADS-B should be able to claim device 0
```

### 3. Multiple Devices
```bash
# Connect multiple SDR devices
# Open GSM Spy dashboard
# Verify all devices appear in dropdown
# Select different devices and verify they work independently
```

### 4. UI State
```bash
# Start GSM scanner
# Verify device selector is disabled
# Verify region selector is disabled
# Stop scanner
# Verify both selectors are re-enabled
```

### 5. Error Scenarios
```bash
# Disconnect SDR device
# Try to start scanner
# Should see graceful error message
# Reconnect device
# Refresh page - device should be detected
```

## Known Limitations

1. **gr-gsm Hardware Support**: The `gr-gsm` tools may have limited support for non-RTL-SDR devices. This implementation handles device selection properly, but `gr-gsm` itself may only work with RTL-SDR.

2. **Command Builder Integration**: Full SDRFactory integration (using device-specific command builders) would require adding GSM-specific methods to command builders in `utils/sdr/`. This is a future enhancement.

3. **Remote Device Support**: Unlike ADS-B which supports remote dump1090 connections, GSM Spy currently only supports local SDR devices.

## Future Enhancements

### 1. SDRFactory Integration
```python
# In start_scanner():
from utils.sdr import SDRFactory

devices = SDRFactory.detect_devices()
sdr_device = next((d for d in devices if d.index == device_index), None)

builder = SDRFactory.get_builder(sdr_device.sdr_type)
cmd = builder.build_gsm_scanner_command(device=sdr_device, bands=REGIONAL_BANDS[region])
```

Note: This requires adding `build_gsm_scanner_command()` method to command builders.

### 2. Device-Specific Tuning
- Different gain settings per SDR type
- Frequency correction (PPM) based on device calibration
- Sample rate optimization per hardware

### 3. Multi-Device Monitoring
- Simultaneously monitor multiple towers on different devices
- Parallel scanning across multiple frequency bands

## Compatibility

- **Frontend**: Modern browsers with ES6+ support (async/await)
- **Backend**: Python 3.8+
- **SDR Hardware**: RTL-SDR, HackRF, LimeSDR, Airspy, SDRPlay
- **gr-gsm**: Requires gr-gsm toolkit installed

## Files Modified

1. `/opt/intercept/templates/gsm_spy_dashboard.html` - Frontend UI and JavaScript
2. `/opt/intercept/routes/gsm_spy.py` - Backend route handlers

## Files Referenced (Not Modified)

1. `/opt/intercept/routes/adsb.py` - Reference implementation
2. `/opt/intercept/utils/sdr/detection.py` - Device detection
3. `/opt/intercept/utils/sdr/__init__.py` - SDRFactory
4. `/opt/intercept/utils/validation.py` - Input validation
5. `/opt/intercept/app.py` - Device registry functions

## Verification

All changes have been implemented according to the plan. The implementation:
- ✅ Follows existing INTERCEPT patterns
- ✅ Maintains UI consistency across modes
- ✅ Includes proper error handling
- ✅ Uses centralized validation
- ✅ Integrates with device registry
- ✅ Provides clear user feedback

## Implementation Date

2026-02-06
