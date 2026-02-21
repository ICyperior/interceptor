/**
 * Proximity Radar Component
 *
 * SVG-based circular radar visualization for Bluetooth device proximity.
 * Displays devices positioned by estimated distance with concentric rings
 * for proximity bands.
 */

const ProximityRadar = (function() {
    'use strict';

    // Configuration
    const CONFIG = {
        size: 280,
        padding: 20,
        centerRadius: 8,
        rings: [
            { band: 'immediate', radius: 0.25, color: '#22c55e', label: '< 1m' },
            { band: 'near', radius: 0.5, color: '#eab308', label: '1-3m' },
            { band: 'far', radius: 0.85, color: '#ef4444', label: '3-10m' },
        ],
        dotMinSize: 4,
        dotMaxSize: 12,
        pulseAnimationDuration: 2000,
        newDeviceThreshold: 30, // seconds
    };

    // State
    let container = null;
    let svg = null;
    let devices = new Map();
    let isPaused = false;
    let activeFilter = null;
    let onDeviceClick = null;
    let selectedDeviceKey = null;
    let isHovered = false;
    let renderPending = false;
    let renderTimer = null;
    let interactionLockUntil = 0; // timestamp: suppress renders briefly after click

    /**
     * Initialize the radar component
     */
    function init(containerId, options = {}) {
        container = document.getElementById(containerId);
        if (!container) {
            console.error('[ProximityRadar] Container not found:', containerId);
            return;
        }

        if (options.onDeviceClick) {
            onDeviceClick = options.onDeviceClick;
        }

        createSVG();
    }

    /**
     * Create the SVG radar structure
     */
    function createSVG() {
        const size = CONFIG.size;
        const center = size / 2;

        container.innerHTML = `
            <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" class="proximity-radar-svg">
                <defs>
                    <radialGradient id="radarGradient" cx="50%" cy="50%" r="50%">
                        <stop offset="0%" stop-color="rgba(0, 212, 255, 0.1)" />
                        <stop offset="100%" stop-color="rgba(0, 212, 255, 0)" />
                    </radialGradient>
                    <filter id="glow">
                        <feGaussianBlur stdDeviation="2" result="coloredBlur"/>
                        <feMerge>
                            <feMergeNode in="coloredBlur"/>
                            <feMergeNode in="SourceGraphic"/>
                        </feMerge>
                    </filter>
                </defs>

                <!-- Background gradient -->
                <circle cx="${center}" cy="${center}" r="${center - CONFIG.padding}"
                        fill="url(#radarGradient)" />

                <!-- Proximity rings -->
                <g class="radar-rings">
                    ${CONFIG.rings.map((ring, i) => {
                        const r = ring.radius * (center - CONFIG.padding);
                        return `
                            <circle cx="${center}" cy="${center}" r="${r}"
                                    fill="none" stroke="${ring.color}" stroke-opacity="0.3"
                                    stroke-width="1" stroke-dasharray="4,4" />
                            <text x="${center}" y="${center - r + 12}"
                                  text-anchor="middle" fill="${ring.color}" fill-opacity="0.6"
                                  font-size="9" font-family="monospace">${ring.label}</text>
                        `;
                    }).join('')}
                </g>

                <!-- Sweep line (animated) -->
                <line class="radar-sweep" x1="${center}" y1="${center}"
                      x2="${center}" y2="${CONFIG.padding}"
                      stroke="rgba(0, 212, 255, 0.5)" stroke-width="1" />

                <!-- Center point -->
                <circle cx="${center}" cy="${center}" r="${CONFIG.centerRadius}"
                        fill="#00d4ff" filter="url(#glow)" />

                <!-- Device dots container -->
                <g class="radar-devices"></g>

                <!-- Legend -->
                <g class="radar-legend" transform="translate(${size - 70}, ${size - 55})">
                    <text x="0" y="0" fill="#666" font-size="8">PROXIMITY</text>
                    <text x="0" y="0" fill="#666" font-size="7" font-style="italic"
                          transform="translate(0, 10)">(signal strength)</text>
                </g>
            </svg>
        `;

        svg = container.querySelector('svg');

        // Event delegation on the devices group (survives innerHTML rebuilds)
        const devicesGroup = svg.querySelector('.radar-devices');

        devicesGroup.addEventListener('click', (e) => {
            const deviceEl = e.target.closest('.radar-device');
            if (!deviceEl) return;
            const deviceKey = deviceEl.getAttribute('data-device-key');
            if (onDeviceClick && deviceKey) {
                // Lock out re-renders briefly so the DOM stays stable after click
                interactionLockUntil = Date.now() + 500;
                onDeviceClick(deviceKey);
            }
        });

        // mouseover/mouseout bubble, so we get events from all descendants.
        // Use devicesGroup.contains(relatedTarget) to detect true entry/exit
        // rather than capture-phase mouseenter/mouseleave, which can leave
        // isHovered stuck when innerHTML is rebuilt under the cursor.
        devicesGroup.addEventListener('mouseover', (e) => {
            if (e.target.closest('.radar-device')) {
                isHovered = true;
            }
        });

        devicesGroup.addEventListener('mouseout', (e) => {
            if (!e.target.closest('.radar-device')) return;
            // Only clear hover when the mouse leaves the group entirely —
            // moving between sibling children keeps relatedTarget inside the group.
            if (!devicesGroup.contains(e.relatedTarget)) {
                isHovered = false;
                if (renderPending) {
                    renderPending = false;
                    renderDevices();
                }
            }
        });

        // Add sweep animation
        animateSweep();
    }

    /**
     * Animate the radar sweep line
     */
    function animateSweep() {
        const sweepLine = svg.querySelector('.radar-sweep');
        if (!sweepLine) return;

        let angle = 0;
        const center = CONFIG.size / 2;

        function rotate() {
            if (isPaused) {
                requestAnimationFrame(rotate);
                return;
            }

            angle = (angle + 1) % 360;
            const rad = (angle * Math.PI) / 180;
            const radius = center - CONFIG.padding;
            const x2 = center + Math.sin(rad) * radius;
            const y2 = center - Math.cos(rad) * radius;

            sweepLine.setAttribute('x2', x2);
            sweepLine.setAttribute('y2', y2);

            requestAnimationFrame(rotate);
        }

        requestAnimationFrame(rotate);
    }

    /**
     * Update devices on the radar
     */
    function updateDevices(deviceList) {
        if (isPaused) return;

        // Update device map
        deviceList.forEach(device => {
            devices.set(device.device_key, device);
        });

        // Defer render while user is hovering or interacting to prevent DOM rebuild flicker
        if (isHovered || Date.now() < interactionLockUntil) {
            renderPending = true;
            return;
        }

        // Debounce rapid updates (e.g. per-device SSE events)
        if (renderTimer) clearTimeout(renderTimer);
        renderTimer = setTimeout(() => {
            renderTimer = null;
            renderDevices();
        }, 200);
    }

    /**
     * Render device dots on the radar
     */
    function renderDevices() {
        const devicesGroup = svg.querySelector('.radar-devices');
        if (!devicesGroup) return;

        const center = CONFIG.size / 2;
        const maxRadius = center - CONFIG.padding;

        // Filter devices
        let visibleDevices = Array.from(devices.values());

        if (activeFilter === 'newOnly') {
            visibleDevices = visibleDevices.filter(d => d.is_new || d.age_seconds < CONFIG.newDeviceThreshold);
        } else if (activeFilter === 'strongest') {
            visibleDevices = visibleDevices
                .filter(d => d.rssi_current != null)
                .sort((a, b) => (b.rssi_current || -100) - (a.rssi_current || -100))
                .slice(0, 10);
        } else if (activeFilter === 'unapproved') {
            visibleDevices = visibleDevices.filter(d => !d.in_baseline);
        }

        // Build SVG for each device
        const dots = visibleDevices.map(device => {
            // Calculate position
            const { x, y, radius } = calculateDevicePosition(device, center, maxRadius);

            // Calculate dot size based on confidence
            const confidence = device.distance_confidence || 0.5;
            const dotSize = CONFIG.dotMinSize + (CONFIG.dotMaxSize - CONFIG.dotMinSize) * confidence;

            // Get color based on proximity band
            const color = getBandColor(device.proximity_band);

            // Check if newly seen (pulse animation)
            const isNew = device.age_seconds < 5;
            const pulseClass = isNew ? 'radar-dot-pulse' : '';
            const isSelected = selectedDeviceKey && device.device_key === selectedDeviceKey;

            // Hit area size (prevents hover flicker when scaling)
            const hitAreaSize = Math.max(dotSize * 2, 15);

            return `
                <g transform="translate(${x}, ${y})">
                    <g class="radar-device ${pulseClass}${isSelected ? ' selected' : ''}" data-device-key="${escapeAttr(device.device_key)}"
                       style="cursor: pointer;">
                        <!-- Invisible hit area to prevent hover flicker -->
                        <circle class="radar-device-hitarea" r="${hitAreaSize}" fill="transparent" />
                        ${isSelected ? `<circle class="radar-select-ring" r="${dotSize + 8}" fill="none" stroke="#00d4ff" stroke-width="2" stroke-opacity="0.8">
                            <animate attributeName="r" values="${dotSize + 6};${dotSize + 10};${dotSize + 6}" dur="1.5s" repeatCount="indefinite"/>
                            <animate attributeName="stroke-opacity" values="0.8;0.4;0.8" dur="1.5s" repeatCount="indefinite"/>
                        </circle>` : ''}
                        <circle r="${dotSize}" fill="${color}"
                                fill-opacity="${isSelected ? 1 : 0.4 + confidence * 0.5}"
                                stroke="${isSelected ? '#00d4ff' : color}" stroke-width="${isSelected ? 2 : 1}" />
                        ${device.is_new && !isSelected ? `<circle r="${dotSize + 3}" fill="none" stroke="#3b82f6" stroke-width="1" stroke-dasharray="2,2" />` : ''}
                        <title>${escapeHtml(device.name || device.address)} (${device.rssi_current || '--'} dBm)</title>
                    </g>
                </g>
            `;
        }).join('');

        devicesGroup.innerHTML = dots;
    }

    /**
     * Calculate device position on radar
     */
    function calculateDevicePosition(device, center, maxRadius) {
        // Calculate radius based on proximity band/distance
        let radiusRatio;
        const band = device.proximity_band || 'unknown';

        if (device.estimated_distance_m != null) {
            // Use actual distance (log scale)
            const maxDistance = 15;
            radiusRatio = Math.min(1, Math.log10(device.estimated_distance_m + 1) / Math.log10(maxDistance + 1));
        } else {
            // Use band-based positioning
            switch (band) {
                case 'immediate': radiusRatio = 0.15; break;
                case 'near': radiusRatio = 0.4; break;
                case 'far': radiusRatio = 0.7; break;
                default: radiusRatio = 0.9; break;
            }
        }

        // Calculate angle based on device key hash (stable positioning)
        const angle = hashToAngle(device.device_key || device.device_id);
        const radius = radiusRatio * maxRadius;

        const x = center + Math.sin(angle) * radius;
        const y = center - Math.cos(angle) * radius;

        return { x, y, radius };
    }

    /**
     * Hash string to angle for stable positioning
     */
    function hashToAngle(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            hash = ((hash << 5) - hash) + str.charCodeAt(i);
            hash = hash & hash;
        }
        return (Math.abs(hash) % 360) * (Math.PI / 180);
    }

    /**
     * Get color for proximity band
     */
    function getBandColor(band) {
        switch (band) {
            case 'immediate': return '#22c55e';
            case 'near': return '#eab308';
            case 'far': return '#ef4444';
            default: return '#6b7280';
        }
    }

    /**
     * Set filter mode
     */
    function setFilter(filter) {
        activeFilter = filter === activeFilter ? null : filter;
        renderDevices();
    }

    /**
     * Toggle pause state
     */
    function setPaused(paused) {
        isPaused = paused;
    }

    /**
     * Clear all devices
     */
    function clear() {
        devices.clear();
        selectedDeviceKey = null;
        renderDevices();
    }

    /**
     * Highlight a specific device on the radar (in-place update, no full re-render)
     */
    function highlightDevice(deviceKey) {
        const prev = selectedDeviceKey;
        selectedDeviceKey = deviceKey;

        if (!svg) { return; }
        const devicesGroup = svg.querySelector('.radar-devices');
        if (!devicesGroup) { return; }

        // Remove highlight from previously selected node
        if (prev && prev !== deviceKey) {
            const oldEl = devicesGroup.querySelector(`.radar-device[data-device-key="${CSS.escape(prev)}"]`);
            if (oldEl) {
                oldEl.classList.remove('selected');
                // Remove animated selection ring
                const ring = oldEl.querySelector('.radar-select-ring');
                if (ring) ring.remove();
                // Restore dot opacity
                const dot = oldEl.querySelector('circle:not(.radar-device-hitarea):not(.radar-select-ring)');
                if (dot && dot.getAttribute('fill') !== 'none' && dot.getAttribute('fill') !== 'transparent') {
                    const device = devices.get(prev);
                    const confidence = device ? (device.distance_confidence || 0.5) : 0.5;
                    dot.setAttribute('fill-opacity', 0.4 + confidence * 0.5);
                    dot.setAttribute('stroke', dot.getAttribute('fill'));
                    dot.setAttribute('stroke-width', '1');
                }
            }
        }

        // Add highlight to newly selected node
        if (deviceKey) {
            const newEl = devicesGroup.querySelector(`.radar-device[data-device-key="${CSS.escape(deviceKey)}"]`);
            if (newEl) {
                applySelectionToElement(newEl, deviceKey);
            } else {
                // Node not in DOM yet; full render needed on next cycle
                renderDevices();
            }
        }
    }

    /**
     * Apply selection styling to a radar device element in-place
     */
    function applySelectionToElement(el, deviceKey) {
        el.classList.add('selected');
        const device = devices.get(deviceKey);
        const confidence = device ? (device.distance_confidence || 0.5) : 0.5;
        const dotSize = CONFIG.dotMinSize + (CONFIG.dotMaxSize - CONFIG.dotMinSize) * confidence;

        // Update dot styling
        const dot = el.querySelector('circle:not(.radar-device-hitarea):not(.radar-select-ring)');
        if (dot && dot.getAttribute('fill') !== 'none' && dot.getAttribute('fill') !== 'transparent') {
            dot.setAttribute('fill-opacity', '1');
            dot.setAttribute('stroke', '#00d4ff');
            dot.setAttribute('stroke-width', '2');
        }

        // Add animated selection ring if not already present
        if (!el.querySelector('.radar-select-ring')) {
            const ns = 'http://www.w3.org/2000/svg';
            const ring = document.createElementNS(ns, 'circle');
            ring.classList.add('radar-select-ring');
            ring.setAttribute('r', dotSize + 8);
            ring.setAttribute('fill', 'none');
            ring.setAttribute('stroke', '#00d4ff');
            ring.setAttribute('stroke-width', '2');
            ring.setAttribute('stroke-opacity', '0.8');

            const animR = document.createElementNS(ns, 'animate');
            animR.setAttribute('attributeName', 'r');
            animR.setAttribute('values', `${dotSize + 6};${dotSize + 10};${dotSize + 6}`);
            animR.setAttribute('dur', '1.5s');
            animR.setAttribute('repeatCount', 'indefinite');
            ring.appendChild(animR);

            const animO = document.createElementNS(ns, 'animate');
            animO.setAttribute('attributeName', 'stroke-opacity');
            animO.setAttribute('values', '0.8;0.4;0.8');
            animO.setAttribute('dur', '1.5s');
            animO.setAttribute('repeatCount', 'indefinite');
            ring.appendChild(animO);

            // Insert after the hit area
            const hitArea = el.querySelector('.radar-device-hitarea');
            if (hitArea && hitArea.nextSibling) {
                el.insertBefore(ring, hitArea.nextSibling);
            } else {
                el.insertBefore(ring, el.firstChild);
            }
        }
    }

    /**
     * Clear device highlighting (in-place update, no full re-render)
     */
    function clearHighlight() {
        const prev = selectedDeviceKey;
        selectedDeviceKey = null;

        if (!svg || !prev) { return; }
        const devicesGroup = svg.querySelector('.radar-devices');
        if (!devicesGroup) { return; }

        const oldEl = devicesGroup.querySelector(`.radar-device[data-device-key="${CSS.escape(prev)}"]`);
        if (oldEl) {
            oldEl.classList.remove('selected');
            const ring = oldEl.querySelector('.radar-select-ring');
            if (ring) ring.remove();
            const dot = oldEl.querySelector('circle:not(.radar-device-hitarea):not(.radar-select-ring)');
            if (dot && dot.getAttribute('fill') !== 'none' && dot.getAttribute('fill') !== 'transparent') {
                const device = devices.get(prev);
                const confidence = device ? (device.distance_confidence || 0.5) : 0.5;
                dot.setAttribute('fill-opacity', 0.4 + confidence * 0.5);
                dot.setAttribute('stroke', dot.getAttribute('fill'));
                dot.setAttribute('stroke-width', '1');
            }
        }
    }

    /**
     * Get zone counts
     */
    function getZoneCounts() {
        const counts = { immediate: 0, near: 0, far: 0, unknown: 0 };
        devices.forEach(device => {
            const band = device.proximity_band || 'unknown';
            if (counts.hasOwnProperty(band)) {
                counts[band]++;
            } else {
                counts.unknown++;
            }
        });
        return counts;
    }

    /**
     * Escape HTML for safe rendering
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    /**
     * Escape attribute value
     */
    function escapeAttr(text) {
        if (!text) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    // Public API
    return {
        init,
        updateDevices,
        setFilter,
        setPaused,
        clear,
        getZoneCounts,
        highlightDevice,
        clearHighlight,
        isPaused: () => isPaused,
        getFilter: () => activeFilter,
        getSelectedDevice: () => selectedDeviceKey,
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ProximityRadar;
}

window.ProximityRadar = ProximityRadar;
