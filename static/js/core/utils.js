/**
 * Intercept - Core Utility Functions
 * Pure utility functions with no DOM dependencies
 */

// ============== HTML ESCAPING ==============

/**
 * Escape HTML to prevent XSS
 * @param {string} text - Text to escape
 * @returns {string} Escaped HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Escape text for use in HTML attributes (especially onclick handlers)
 * @param {string} text - Text to escape
 * @returns {string} Escaped attribute value
 */
function escapeAttr(text) {
    if (text === null || text === undefined) return '';
    var s = String(text);
    s = s.replace(/&/g, '&amp;');
    s = s.replace(/'/g, '&#39;');
    s = s.replace(/"/g, '&quot;');
    s = s.replace(/</g, '&lt;');
    s = s.replace(/>/g, '&gt;');
    return s;
}

// ============== VALIDATION ==============

/**
 * Validate MAC address format (XX:XX:XX:XX:XX:XX)
 * @param {string} mac - MAC address to validate
 * @returns {boolean} True if valid
 */
function isValidMac(mac) {
    return /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(mac);
}

/**
 * Validate WiFi channel (1-200 covers all bands)
 * @param {string|number} ch - Channel number
 * @returns {boolean} True if valid
 */
function isValidChannel(ch) {
    const num = parseInt(ch, 10);
    return !isNaN(num) && num >= 1 && num <= 200;
}

// ============== TIME FORMATTING ==============

/**
 * Get relative time string from timestamp
 * @param {string} timestamp - Time string in HH:MM:SS format
 * @returns {string} Relative time like "5s ago", "2m ago"
 */
function getRelativeTime(timestamp) {
    if (!timestamp) return '';
    const now = new Date();
    const parts = timestamp.split(':');
    const msgTime = new Date();
    msgTime.setHours(parseInt(parts[0]), parseInt(parts[1]), parseInt(parts[2]));

    const diff = Math.floor((now - msgTime) / 1000);
    if (diff < 5) return 'just now';
    if (diff < 60) return diff + 's ago';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    return timestamp;
}

/**
 * Format UTC time string
 * @param {Date} date - Date object
 * @returns {string} UTC time in HH:MM:SS format
 */
function formatUtcTime(date) {
    return date.toISOString().substring(11, 19);
}

// ============== DISTANCE CALCULATIONS ==============

/**
 * Calculate distance between two points in nautical miles
 * Uses Haversine formula
 * @param {number} lat1 - Latitude of first point
 * @param {number} lon1 - Longitude of first point
 * @param {number} lat2 - Latitude of second point
 * @param {number} lon2 - Longitude of second point
 * @returns {number} Distance in nautical miles
 */
function calculateDistanceNm(lat1, lon1, lat2, lon2) {
    const R = 3440.065;  // Earth radius in nautical miles
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
}

/**
 * Calculate distance between two points in kilometers
 * @param {number} lat1 - Latitude of first point
 * @param {number} lon1 - Longitude of first point
 * @param {number} lat2 - Latitude of second point
 * @param {number} lon2 - Longitude of second point
 * @returns {number} Distance in kilometers
 */
function calculateDistanceKm(lat1, lon1, lat2, lon2) {
    const R = 6371;  // Earth radius in kilometers
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
}

// ============== FILE OPERATIONS ==============

/**
 * Download content as a file
 * @param {string} content - File content
 * @param {string} filename - Name for the downloaded file
 * @param {string} type - MIME type
 */
function downloadFile(content, filename, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

// ============== FREQUENCY FORMATTING ==============

/**
 * Format frequency value with proper units
 * @param {number} freqMhz - Frequency in MHz
 * @param {number} decimals - Number of decimal places (default 3)
 * @returns {string} Formatted frequency string
 */
function formatFrequency(freqMhz, decimals = 3) {
    return freqMhz.toFixed(decimals) + ' MHz';
}

/**
 * Parse frequency string to MHz
 * @param {string} freqStr - Frequency string (e.g., "118.0", "118.0 MHz")
 * @returns {number} Frequency in MHz
 */
function parseFrequency(freqStr) {
    return parseFloat(freqStr.replace(/[^\d.-]/g, ''));
}

// ============== LOCAL STORAGE HELPERS ==============

/**
 * Get item from localStorage with JSON parsing
 * @param {string} key - Storage key
 * @param {*} defaultValue - Default value if key doesn't exist
 * @returns {*} Parsed value or default
 */
function getStorageItem(key, defaultValue = null) {
    const saved = localStorage.getItem(key);
    if (saved === null) return defaultValue;
    try {
        return JSON.parse(saved);
    } catch (e) {
        return saved;
    }
}

/**
 * Set item in localStorage with JSON stringification
 * @param {string} key - Storage key
 * @param {*} value - Value to store
 */
function setStorageItem(key, value) {
    if (typeof value === 'object') {
        localStorage.setItem(key, JSON.stringify(value));
    } else {
        localStorage.setItem(key, value);
    }
}

// ============== ARRAY/OBJECT UTILITIES ==============

/**
 * Debounce function execution
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Throttle function execution
 * @param {Function} func - Function to throttle
 * @param {number} limit - Time limit in milliseconds
 * @returns {Function} Throttled function
 */
function throttle(func, limit) {
    let inThrottle;
    return function executedFunction(...args) {
        if (!inThrottle) {
            func(...args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

// ============== NUMBER FORMATTING ==============

/**
 * Format large numbers with K/M suffixes
 * @param {number} num - Number to format
 * @returns {string} Formatted string
 */
function formatNumber(num) {
    if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    }
    if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toString();
}

/**
 * Clamp a number between min and max
 * @param {number} num - Number to clamp
 * @param {number} min - Minimum value
 * @param {number} max - Maximum value
 * @returns {number} Clamped value
 */
function clamp(num, min, max) {
    return Math.min(Math.max(num, min), max);
}

/**
 * Map a value from one range to another
 * @param {number} value - Value to map
 * @param {number} inMin - Input range minimum
 * @param {number} inMax - Input range maximum
 * @param {number} outMin - Output range minimum
 * @param {number} outMax - Output range maximum
 * @returns {number} Mapped value
 */
function mapRange(value, inMin, inMax, outMin, outMax) {
    return (value - inMin) * (outMax - outMin) / (inMax - inMin) + outMin;
}

// ============== ICON SYSTEM ==============
// Minimal SVG icons. Each returns HTML string.
// Designed for screenshot legibility - standard symbols only.

const Icons = {
    /**
     * WiFi icon - standard arc/fan shape
     */
    wifi: function(className) {
        return `<span class="icon icon-wifi ${className || ''}" aria-label="WiFi">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M5 12.55a11 11 0 0 1 14.08 0"/>
                <path d="M1.42 9a16 16 0 0 1 21.16 0"/>
                <path d="M8.53 16.11a6 6 0 0 1 6.95 0"/>
                <circle cx="12" cy="20" r="1" fill="currentColor" stroke="none"/>
            </svg>
        </span>`;
    },

    /**
     * Bluetooth icon - standard rune
     */
    bluetooth: function(className) {
        return `<span class="icon icon-bluetooth ${className || ''}" aria-label="Bluetooth">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="6.5 6.5 17.5 17.5 12 22 12 2 17.5 6.5 6.5 17.5"/>
            </svg>
        </span>`;
    },

    /**
     * Cellular icon - ascending bars
     */
    cellular: function(className) {
        return `<span class="icon icon-cellular ${className || ''}" aria-label="Cellular">
            <svg viewBox="0 0 24 24" fill="currentColor">
                <rect x="2" y="16" width="4" height="6" rx="1"/>
                <rect x="8" y="12" width="4" height="10" rx="1"/>
                <rect x="14" y="8" width="4" height="14" rx="1"/>
                <rect x="20" y="4" width="4" height="18" rx="1" opacity="0.3"/>
            </svg>
        </span>`;
    },

    /**
     * Unknown/RF signal - generic wave
     */
    signalUnknown: function(className) {
        return `<span class="icon icon-signal-unknown ${className || ''}" aria-label="Unknown signal">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                <path d="M2 12c0-3 2-6 5-6s4 3 5 6c1 3 2 6 5 6s5-3 5-6"/>
            </svg>
        </span>`;
    },

    /**
     * Recording indicator - filled circle
     */
    recording: function(className) {
        return `<span class="icon icon-recording ${className || ''}" aria-label="Recording">
            <svg viewBox="0 0 24 24" fill="currentColor">
                <circle cx="12" cy="12" r="8"/>
            </svg>
        </span>`;
    },

    /**
     * Anomaly indicator - filled circle (amber by default via CSS)
     */
    anomaly: function(className) {
        return `<span class="icon icon-anomaly ${className || ''}" aria-label="Anomaly">
            <svg viewBox="0 0 24 24" fill="currentColor">
                <circle cx="12" cy="12" r="6"/>
            </svg>
        </span>`;
    },

    /**
     * Export icon - arrow out of box
     */
    export: function(className) {
        return `<span class="icon icon-export ${className || ''}" aria-label="Export">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
        </span>`;
    },

    /**
     * Refresh icon - circular arrows
     */
    refresh: function(className) {
        return `<span class="icon icon-refresh ${className || ''}" aria-label="Refresh">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="23 4 23 10 17 10"/>
                <polyline points="1 20 1 14 7 14"/>
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
            </svg>
        </span>`;
    },

    /**
     * Get icon by signal type
     * Maps protocol names to appropriate icons
     */
    forSignalType: function(type, className) {
        const t = (type || '').toLowerCase();
        if (t.includes('wifi') || t.includes('802.11')) {
            return this.wifi(className);
        }
        if (t.includes('bluetooth') || t.includes('bt') || t.includes('ble')) {
            return this.bluetooth(className);
        }
        if (t.includes('cellular') || t.includes('lte') || t.includes('gsm') || t.includes('5g')) {
            return this.cellular(className);
        }
        return this.signalUnknown(className);
    }
};
