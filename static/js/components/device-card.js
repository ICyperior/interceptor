/**
 * Device Card Component
 * Unified device display for Bluetooth and TSCM modes
 */

const DeviceCard = (function() {
    'use strict';

    // Range band configuration
    const RANGE_BANDS = {
        very_close: { label: 'Very Close', color: '#ef4444', description: '< 3m' },
        close: { label: 'Close', color: '#f97316', description: '3-10m' },
        nearby: { label: 'Nearby', color: '#eab308', description: '10-20m' },
        far: { label: 'Far', color: '#6b7280', description: '> 20m' },
        unknown: { label: 'Unknown', color: '#374151', description: 'N/A' }
    };

    // Protocol badge colors
    const PROTOCOL_COLORS = {
        ble: { bg: 'rgba(59, 130, 246, 0.15)', color: '#3b82f6', border: 'rgba(59, 130, 246, 0.3)' },
        classic: { bg: 'rgba(139, 92, 246, 0.15)', color: '#8b5cf6', border: 'rgba(139, 92, 246, 0.3)' }
    };

    // Heuristic badge configuration
    const HEURISTIC_BADGES = {
        new: { label: 'New', color: '#3b82f6', description: 'Not in baseline' },
        persistent: { label: 'Persistent', color: '#22c55e', description: 'Continuously present' },
        beacon_like: { label: 'Beacon', color: '#f59e0b', description: 'Regular advertising' },
        strong_stable: { label: 'Strong', color: '#ef4444', description: 'Strong stable signal' },
        random_address: { label: 'Random', color: '#6b7280', description: 'Privacy address' }
    };

    /**
     * Escape HTML to prevent XSS
     */
    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }

    /**
     * Format relative time
     */
    function formatRelativeTime(isoString) {
        if (!isoString) return '';
        const date = new Date(isoString);
        const now = new Date();
        const diff = Math.floor((now - date) / 1000);

        if (diff < 10) return 'Just now';
        if (diff < 60) return `${diff}s ago`;
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return date.toLocaleDateString();
    }

    /**
     * Create RSSI sparkline SVG
     */
    function createSparkline(rssiHistory, options = {}) {
        if (!rssiHistory || rssiHistory.length < 2) {
            return '<span class="rssi-sparkline-empty">--</span>';
        }

        const width = options.width || 60;
        const height = options.height || 20;
        const samples = rssiHistory.slice(-20);  // Last 20 samples

        // Normalize RSSI values (-100 to -30 range)
        const minRssi = -100;
        const maxRssi = -30;
        const normalizedValues = samples.map(s => {
            const rssi = s.rssi || s;
            const normalized = (rssi - minRssi) / (maxRssi - minRssi);
            return Math.max(0, Math.min(1, normalized));
        });

        // Generate path
        const stepX = width / (normalizedValues.length - 1);
        let pathD = '';
        normalizedValues.forEach((val, i) => {
            const x = i * stepX;
            const y = height - (val * height);
            pathD += i === 0 ? `M${x},${y}` : ` L${x},${y}`;
        });

        // Determine color based on latest value
        const latestRssi = samples[samples.length - 1].rssi || samples[samples.length - 1];
        let strokeColor = '#6b7280';
        if (latestRssi > -50) strokeColor = '#22c55e';
        else if (latestRssi > -65) strokeColor = '#f59e0b';
        else if (latestRssi > -80) strokeColor = '#f97316';

        return `
            <svg class="rssi-sparkline" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
                <path d="${pathD}" fill="none" stroke="${strokeColor}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
        `;
    }

    /**
     * Create heuristic badges HTML
     */
    function createHeuristicBadges(flags) {
        if (!flags || flags.length === 0) return '';

        return flags.map(flag => {
            const config = HEURISTIC_BADGES[flag];
            if (!config) return '';
            return `
                <span class="device-heuristic-badge ${flag}"
                      style="--badge-color: ${config.color}"
                      title="${escapeHtml(config.description)}">
                    ${escapeHtml(config.label)}
                </span>
            `;
        }).join('');
    }

    /**
     * Create range band indicator
     */
    function createRangeBand(band, confidence) {
        const config = RANGE_BANDS[band] || RANGE_BANDS.unknown;
        const confidencePercent = Math.round((confidence || 0) * 100);

        return `
            <div class="device-range-band" style="--range-color: ${config.color}">
                <span class="range-label">${escapeHtml(config.label)}</span>
                <span class="range-estimate">${escapeHtml(config.description)}</span>
                ${confidence > 0 ? `<span class="range-confidence" title="Confidence">${confidencePercent}%</span>` : ''}
            </div>
        `;
    }

    /**
     * Create protocol badge
     */
    function createProtocolBadge(protocol) {
        const config = PROTOCOL_COLORS[protocol] || PROTOCOL_COLORS.ble;
        const label = protocol === 'classic' ? 'Classic' : 'BLE';

        return `
            <span class="signal-proto-badge device-protocol"
                  style="background: ${config.bg}; color: ${config.color}; border-color: ${config.border}">
                ${escapeHtml(label)}
            </span>
        `;
    }

    /**
     * Create a Bluetooth device card
     */
    function createDeviceCard(device, options = {}) {
        const card = document.createElement('article');
        card.className = 'signal-card device-card';
        card.dataset.deviceId = device.device_id;
        card.dataset.protocol = device.protocol;
        card.dataset.address = device.address;

        // Add status classes
        if (device.heuristic_flags && device.heuristic_flags.includes('new')) {
            card.dataset.status = 'new';
        } else if (device.in_baseline) {
            card.dataset.status = 'baseline';
        }

        // Store full device data for details modal
        card.dataset.deviceData = JSON.stringify(device);

        const relativeTime = formatRelativeTime(device.last_seen);
        const sparkline = createSparkline(device.rssi_history);
        const heuristicBadges = createHeuristicBadges(device.heuristic_flags);
        const rangeBand = createRangeBand(device.range_band, device.range_confidence);
        const protocolBadge = createProtocolBadge(device.protocol);

        card.innerHTML = `
            <div class="signal-card-header">
                <div class="signal-card-badges">
                    ${protocolBadge}
                    ${heuristicBadges}
                </div>
                <span class="signal-status-pill" data-status="${device.in_baseline ? 'baseline' : 'new'}">
                    <span class="status-dot"></span>
                    ${device.in_baseline ? 'Known' : 'New'}
                </span>
            </div>
            <div class="signal-card-body">
                <div class="device-identity">
                    <div class="device-name">${escapeHtml(device.name || 'Unknown Device')}</div>
                    <div class="device-address">
                        <span class="address-value">${escapeHtml(device.address)}</span>
                        <span class="address-type">(${escapeHtml(device.address_type)})</span>
                    </div>
                </div>
                <div class="device-signal-row">
                    <div class="rssi-display">
                        <span class="rssi-current" title="Current RSSI">
                            ${device.rssi_current !== null ? device.rssi_current + ' dBm' : '--'}
                        </span>
                        ${sparkline}
                    </div>
                    ${rangeBand}
                </div>
                ${device.manufacturer_name ? `
                <div class="device-manufacturer">
                    <span class="mfr-icon">🏭</span>
                    <span class="mfr-name">${escapeHtml(device.manufacturer_name)}</span>
                </div>
                ` : ''}
                <div class="device-meta-row">
                    <span class="device-seen-count" title="Observation count">
                        <span class="seen-icon">👁</span>
                        ${device.seen_count}×
                    </span>
                    <span class="device-timestamp" data-timestamp="${escapeHtml(device.last_seen)}">
                        ${escapeHtml(relativeTime)}
                    </span>
                </div>
            </div>
            <div class="signal-card-footer">
                <button class="signal-advanced-toggle" onclick="DeviceCard.toggleAdvanced(this)">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M6 9l6 6 6-6"/>
                    </svg>
                    Details
                </button>
                <div class="signal-card-actions">
                    <button class="signal-action-btn" onclick="DeviceCard.copyAddress('${escapeHtml(device.address)}')">Copy</button>
                    ${options.showInvestigate ? `
                    <button class="signal-action-btn primary" onclick="DeviceCard.investigate('${escapeHtml(device.device_id)}')">Investigate</button>
                    ` : ''}
                </div>
            </div>
            <div class="signal-advanced-panel">
                <div class="signal-advanced-inner">
                    ${createAdvancedPanel(device)}
                </div>
            </div>
        `;

        // Make card clickable
        card.addEventListener('click', (e) => {
            if (e.target.closest('button') || e.target.closest('.signal-advanced-toggle')) {
                return;
            }
            showDeviceDetails(device);
        });

        return card;
    }

    /**
     * Create advanced panel content
     */
    function createAdvancedPanel(device) {
        return `
            <div class="signal-advanced-content">
                <div class="signal-advanced-section">
                    <div class="signal-advanced-title">Device Details</div>
                    <div class="signal-advanced-grid">
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Address</span>
                            <span class="signal-advanced-value">${escapeHtml(device.address)}</span>
                        </div>
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Address Type</span>
                            <span class="signal-advanced-value">${escapeHtml(device.address_type)}</span>
                        </div>
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Protocol</span>
                            <span class="signal-advanced-value">${device.protocol === 'ble' ? 'Bluetooth Low Energy' : 'Classic Bluetooth'}</span>
                        </div>
                        ${device.manufacturer_id ? `
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Manufacturer ID</span>
                            <span class="signal-advanced-value">0x${device.manufacturer_id.toString(16).padStart(4, '0').toUpperCase()}</span>
                        </div>
                        ` : ''}
                    </div>
                </div>
                <div class="signal-advanced-section">
                    <div class="signal-advanced-title">Signal Statistics</div>
                    <div class="signal-advanced-grid">
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Current RSSI</span>
                            <span class="signal-advanced-value">${device.rssi_current !== null ? device.rssi_current + ' dBm' : 'N/A'}</span>
                        </div>
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Median RSSI</span>
                            <span class="signal-advanced-value">${device.rssi_median !== null ? device.rssi_median + ' dBm' : 'N/A'}</span>
                        </div>
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Min/Max</span>
                            <span class="signal-advanced-value">${device.rssi_min || 'N/A'} / ${device.rssi_max || 'N/A'} dBm</span>
                        </div>
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Confidence</span>
                            <span class="signal-advanced-value">${Math.round((device.rssi_confidence || 0) * 100)}%</span>
                        </div>
                    </div>
                </div>
                <div class="signal-advanced-section">
                    <div class="signal-advanced-title">Observation Times</div>
                    <div class="signal-advanced-grid">
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">First Seen</span>
                            <span class="signal-advanced-value">${escapeHtml(formatRelativeTime(device.first_seen))}</span>
                        </div>
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Last Seen</span>
                            <span class="signal-advanced-value">${escapeHtml(formatRelativeTime(device.last_seen))}</span>
                        </div>
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Seen Count</span>
                            <span class="signal-advanced-value">${device.seen_count} observations</span>
                        </div>
                        <div class="signal-advanced-item">
                            <span class="signal-advanced-label">Rate</span>
                            <span class="signal-advanced-value">${device.seen_rate ? device.seen_rate.toFixed(1) : '0'}/min</span>
                        </div>
                    </div>
                </div>
                ${device.service_uuids && device.service_uuids.length > 0 ? `
                <div class="signal-advanced-section">
                    <div class="signal-advanced-title">Service UUIDs</div>
                    <div class="device-uuids">
                        ${device.service_uuids.map(uuid => `<span class="device-uuid">${escapeHtml(uuid)}</span>`).join('')}
                    </div>
                </div>
                ` : ''}
                ${device.heuristics ? `
                <div class="signal-advanced-section">
                    <div class="signal-advanced-title">Behavioral Analysis</div>
                    <div class="device-heuristics-detail">
                        ${Object.entries(device.heuristics).map(([key, value]) => `
                            <div class="heuristic-item ${value ? 'active' : ''}">
                                <span class="heuristic-name">${escapeHtml(key.replace(/_/g, ' '))}</span>
                                <span class="heuristic-status">${value ? '✓' : '−'}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
                ` : ''}
            </div>
        `;
    }

    /**
     * Show device details in modal
     */
    function showDeviceDetails(device) {
        let modal = document.getElementById('deviceDetailsModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'deviceDetailsModal';
            modal.className = 'signal-details-modal';
            modal.innerHTML = `
                <div class="signal-details-modal-backdrop"></div>
                <div class="signal-details-modal-content">
                    <div class="signal-details-modal-header">
                        <span class="signal-details-modal-title"></span>
                        <button class="signal-details-modal-close">&times;</button>
                    </div>
                    <div class="signal-details-modal-body"></div>
                    <div class="signal-details-modal-footer">
                        <button class="signal-details-copy-btn">Copy Device Info</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);

            // Close handlers
            modal.querySelector('.signal-details-modal-backdrop').addEventListener('click', () => {
                modal.classList.remove('show');
            });
            modal.querySelector('.signal-details-modal-close').addEventListener('click', () => {
                modal.classList.remove('show');
            });
            modal.querySelector('.signal-details-copy-btn').addEventListener('click', () => {
                navigator.clipboard.writeText(JSON.stringify(device, null, 2)).then(() => {
                    if (typeof SignalCards !== 'undefined') {
                        SignalCards.showToast('Device info copied to clipboard');
                    }
                });
            });
        }

        // Populate modal
        modal.querySelector('.signal-details-modal-title').textContent =
            device.name || device.address;
        modal.querySelector('.signal-details-modal-body').innerHTML = createAdvancedPanel(device);

        modal.classList.add('show');
    }

    /**
     * Toggle advanced panel
     */
    function toggleAdvanced(button) {
        const card = button.closest('.signal-card');
        const panel = card.querySelector('.signal-advanced-panel');
        button.classList.toggle('open');
        panel.classList.toggle('open');
    }

    /**
     * Copy address to clipboard
     */
    function copyAddress(address) {
        navigator.clipboard.writeText(address).then(() => {
            if (typeof SignalCards !== 'undefined') {
                SignalCards.showToast('Address copied');
            }
        });
    }

    /**
     * Investigate device (placeholder for future implementation)
     */
    function investigate(deviceId) {
        console.log('Investigate device:', deviceId);
        // Could open service discovery, detailed analysis, etc.
    }

    /**
     * Update all device timestamps
     */
    function updateTimestamps(container) {
        container.querySelectorAll('.device-timestamp[data-timestamp]').forEach(el => {
            const timestamp = el.dataset.timestamp;
            if (timestamp) {
                el.textContent = formatRelativeTime(timestamp);
            }
        });
    }

    /**
     * Create device filter bar for Bluetooth mode
     */
    function createDeviceFilterBar(container, options = {}) {
        const filterBar = document.createElement('div');
        filterBar.className = 'signal-filter-bar device-filter-bar';
        filterBar.id = 'btDeviceFilterBar';

        filterBar.innerHTML = `
            <button class="signal-filter-btn active" data-filter="status" data-value="all">
                All
                <span class="signal-filter-count" data-count="all">0</span>
            </button>
            <button class="signal-filter-btn" data-filter="status" data-value="new">
                <span class="filter-dot" style="background: var(--signal-new)"></span>
                New
                <span class="signal-filter-count" data-count="new">0</span>
            </button>
            <button class="signal-filter-btn" data-filter="status" data-value="baseline">
                <span class="filter-dot" style="background: var(--signal-baseline)"></span>
                Known
                <span class="signal-filter-count" data-count="baseline">0</span>
            </button>

            <span class="signal-filter-divider"></span>

            <span class="signal-filter-label">Protocol</span>
            <button class="signal-filter-btn protocol-btn active" data-filter="protocol" data-value="all">All</button>
            <button class="signal-filter-btn protocol-btn" data-filter="protocol" data-value="ble">BLE</button>
            <button class="signal-filter-btn protocol-btn" data-filter="protocol" data-value="classic">Classic</button>

            <span class="signal-filter-divider"></span>

            <span class="signal-filter-label">Range</span>
            <button class="signal-filter-btn range-btn active" data-filter="range" data-value="all">All</button>
            <button class="signal-filter-btn range-btn" data-filter="range" data-value="close">Close</button>
            <button class="signal-filter-btn range-btn" data-filter="range" data-value="far">Far</button>

            <div class="signal-search-container">
                <input type="text" class="signal-search-input" id="btSearchInput" placeholder="Search name or address..." />
            </div>
        `;

        // Filter state
        const filters = { status: 'all', protocol: 'all', range: 'all', search: '' };

        // Apply filters function
        const applyFilters = () => {
            const cards = container.querySelectorAll('.device-card');
            const counts = { all: 0, new: 0, baseline: 0 };

            cards.forEach(card => {
                const cardStatus = card.dataset.status || 'baseline';
                const cardProtocol = card.dataset.protocol;
                const deviceData = JSON.parse(card.dataset.deviceData || '{}');
                const cardName = (deviceData.name || '').toLowerCase();
                const cardAddress = (deviceData.address || '').toLowerCase();
                const cardRange = deviceData.range_band || 'unknown';

                counts.all++;
                if (cardStatus === 'new') counts.new++;
                else counts.baseline++;

                // Check filters
                const statusMatch = filters.status === 'all' || cardStatus === filters.status;
                const protocolMatch = filters.protocol === 'all' || cardProtocol === filters.protocol;
                const rangeMatch = filters.range === 'all' ||
                    (filters.range === 'close' && ['very_close', 'close'].includes(cardRange)) ||
                    (filters.range === 'far' && ['nearby', 'far', 'unknown'].includes(cardRange));
                const searchMatch = !filters.search ||
                    cardName.includes(filters.search) ||
                    cardAddress.includes(filters.search);

                if (statusMatch && protocolMatch && rangeMatch && searchMatch) {
                    card.classList.remove('hidden');
                } else {
                    card.classList.add('hidden');
                }
            });

            // Update counts
            Object.keys(counts).forEach(key => {
                const badge = filterBar.querySelector(`[data-count="${key}"]`);
                if (badge) badge.textContent = counts[key];
            });
        };

        // Status filter handlers
        filterBar.querySelectorAll('.signal-filter-btn[data-filter="status"]').forEach(btn => {
            btn.addEventListener('click', () => {
                filterBar.querySelectorAll('.signal-filter-btn[data-filter="status"]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                filters.status = btn.dataset.value;
                applyFilters();
            });
        });

        // Protocol filter handlers
        filterBar.querySelectorAll('.signal-filter-btn[data-filter="protocol"]').forEach(btn => {
            btn.addEventListener('click', () => {
                filterBar.querySelectorAll('.signal-filter-btn[data-filter="protocol"]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                filters.protocol = btn.dataset.value;
                applyFilters();
            });
        });

        // Range filter handlers
        filterBar.querySelectorAll('.signal-filter-btn[data-filter="range"]').forEach(btn => {
            btn.addEventListener('click', () => {
                filterBar.querySelectorAll('.signal-filter-btn[data-filter="range"]').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                filters.range = btn.dataset.value;
                applyFilters();
            });
        });

        // Search handler
        const searchInput = filterBar.querySelector('#btSearchInput');
        let searchTimeout;
        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                filters.search = e.target.value.toLowerCase();
                applyFilters();
            }, 200);
        });

        filterBar.applyFilters = applyFilters;
        return filterBar;
    }

    // Public API
    return {
        createDeviceCard,
        createSparkline,
        createHeuristicBadges,
        createRangeBand,
        createDeviceFilterBar,
        showDeviceDetails,
        toggleAdvanced,
        copyAddress,
        investigate,
        updateTimestamps,
        escapeHtml,
        formatRelativeTime,
        RANGE_BANDS,
        HEURISTIC_BADGES
    };
})();

// Make globally available
window.DeviceCard = DeviceCard;
