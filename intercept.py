#!/usr/bin/env python3
"""
INTERCEPT - Signal Intelligence Platform

A comprehensive signal intelligence tool featuring:
- Pager decoding (POCSAG/FLEX)
- 433MHz sensor monitoring
- ADS-B aircraft tracking with WarGames-style display
- Satellite pass prediction and Iridium burst detection
- WiFi reconnaissance and drone detection
- Bluetooth scanning

Requires RTL-SDR hardware for RF modes.
"""

import sys
import site

# Ensure user site-packages is available (may be disabled when running as root/sudo)
if not site.ENABLE_USER_SITE:
    user_site = site.getusersitepackages()
    if user_site and user_site not in sys.path:
        sys.path.insert(0, user_site)

from app import main

if __name__ == '__main__':
    main()
