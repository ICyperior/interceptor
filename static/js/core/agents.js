/**
 * Intercept - Agent Manager
 * Handles remote agent selection and API routing
 */

// ============== AGENT STATE ==============

let agents = [];
let currentAgent = 'local';
let agentEventSource = null;
let multiAgentMode = false;  // Show combined results from all agents
let multiAgentPollInterval = null;

// ============== AGENT LOADING ==============

async function loadAgents() {
    try {
        const response = await fetch('/controller/agents');
        const data = await response.json();
        agents = data.agents || [];
        updateAgentSelector();
        return agents;
    } catch (error) {
        console.error('Failed to load agents:', error);
        agents = [];
        updateAgentSelector();
        return [];
    }
}

function updateAgentSelector() {
    const selector = document.getElementById('agentSelect');
    if (!selector) return;

    // Keep current selection if possible
    const currentValue = selector.value;

    // Clear and rebuild options
    selector.innerHTML = '<option value="local">Local (This Device)</option>';

    agents.forEach(agent => {
        const option = document.createElement('option');
        option.value = agent.id;
        const status = agent.healthy !== false ? '●' : '○';
        option.textContent = `${status} ${agent.name}`;
        option.dataset.baseUrl = agent.base_url;
        option.dataset.healthy = agent.healthy !== false;
        selector.appendChild(option);
    });

    // Restore selection if still valid
    if (currentValue && selector.querySelector(`option[value="${currentValue}"]`)) {
        selector.value = currentValue;
    }

    updateAgentStatus();
}

function updateAgentStatus() {
    const selector = document.getElementById('agentSelect');
    const statusDot = document.getElementById('agentStatusDot');
    const statusText = document.getElementById('agentStatusText');

    if (!selector || !statusDot) return;

    if (currentAgent === 'local') {
        statusDot.className = 'agent-status-dot online';
        if (statusText) statusText.textContent = 'Local';
    } else {
        const agent = agents.find(a => a.id == currentAgent);
        if (agent) {
            const isOnline = agent.healthy !== false;
            statusDot.className = `agent-status-dot ${isOnline ? 'online' : 'offline'}`;
            if (statusText) statusText.textContent = isOnline ? 'Connected' : 'Offline';
        }
    }
}

// ============== AGENT SELECTION ==============

function selectAgent(agentId) {
    currentAgent = agentId;
    updateAgentStatus();

    // Update device list based on selected agent
    if (agentId === 'local') {
        // Use local devices - call refreshDevices if it exists (defined in main page)
        if (typeof refreshDevices === 'function') {
            refreshDevices();
        }
        console.log('Agent selected: Local');
    } else {
        // Fetch devices from remote agent
        refreshAgentDevices(agentId);
        const agentName = agents.find(a => a.id == agentId)?.name || 'Unknown';
        console.log(`Agent selected: ${agentName}`);

        // Show visual feedback
        const statusText = document.getElementById('agentStatusText');
        if (statusText) {
            statusText.textContent = `Loading ${agentName}...`;
            setTimeout(() => updateAgentStatus(), 2000);
        }
    }
}

async function refreshAgentDevices(agentId) {
    console.log(`Refreshing devices for agent ${agentId}...`);
    try {
        const response = await fetch(`/controller/agents/${agentId}?refresh=true`, {
            credentials: 'same-origin'
        });
        const data = await response.json();
        console.log('Agent data received:', data);

        if (data.agent && data.agent.interfaces) {
            const devices = data.agent.interfaces.devices || [];
            console.log(`Found ${devices.length} devices on agent`);
            populateDeviceSelect(devices);

            // Update SDR type dropdown if device has sdr_type
            if (devices.length > 0 && devices[0].sdr_type) {
                const sdrTypeSelect = document.getElementById('sdrTypeSelect');
                if (sdrTypeSelect) {
                    sdrTypeSelect.value = devices[0].sdr_type;
                }
            }
        } else {
            console.warn('No interfaces found in agent data');
        }
    } catch (error) {
        console.error('Failed to refresh agent devices:', error);
    }
}

function populateDeviceSelect(devices) {
    const select = document.getElementById('deviceSelect');
    if (!select) return;

    select.innerHTML = '';

    if (devices.length === 0) {
        const option = document.createElement('option');
        option.value = '0';
        option.textContent = 'No devices found';
        select.appendChild(option);
    } else {
        devices.forEach(device => {
            const option = document.createElement('option');
            option.value = device.index;
            option.dataset.sdrType = device.sdr_type || 'rtlsdr';
            option.textContent = `${device.index}: ${device.name}`;
            select.appendChild(option);
        });
    }
}

// ============== API ROUTING ==============

/**
 * Route an API call to local or remote agent based on current selection.
 * @param {string} localPath - Local API path (e.g., '/sensor/start')
 * @param {Object} options - Fetch options
 * @returns {Promise<Response>}
 */
async function agentFetch(localPath, options = {}) {
    if (currentAgent === 'local') {
        return fetch(localPath, options);
    }

    // Route through controller proxy
    const proxyPath = `/controller/agents/${currentAgent}${localPath}`;
    return fetch(proxyPath, options);
}

/**
 * Start a mode on the selected agent.
 * @param {string} mode - Mode name (pager, sensor, adsb, wifi, etc.)
 * @param {Object} params - Mode parameters
 * @returns {Promise<Object>}
 */
async function agentStartMode(mode, params = {}) {
    const path = `/${mode}/start`;
    const options = {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params)
    };

    try {
        const response = await agentFetch(path, options);
        return await response.json();
    } catch (error) {
        console.error(`Failed to start ${mode} on agent:`, error);
        throw error;
    }
}

/**
 * Stop a mode on the selected agent.
 * @param {string} mode - Mode name
 * @returns {Promise<Object>}
 */
async function agentStopMode(mode) {
    const path = `/${mode}/stop`;
    const options = { method: 'POST' };

    try {
        const response = await agentFetch(path, options);
        return await response.json();
    } catch (error) {
        console.error(`Failed to stop ${mode} on agent:`, error);
        throw error;
    }
}

/**
 * Get data from a mode on the selected agent.
 * @param {string} mode - Mode name
 * @returns {Promise<Object>}
 */
async function agentGetData(mode) {
    const path = `/${mode}/data`;

    try {
        const response = await agentFetch(path);
        return await response.json();
    } catch (error) {
        console.error(`Failed to get ${mode} data from agent:`, error);
        throw error;
    }
}

// ============== SSE STREAM ==============

/**
 * Connect to SSE stream (local or multi-agent).
 * @param {string} mode - Mode name for the stream
 * @param {function} onMessage - Callback for messages
 * @returns {EventSource}
 */
function connectAgentStream(mode, onMessage) {
    // Close existing connection
    if (agentEventSource) {
        agentEventSource.close();
    }

    let streamUrl;
    if (currentAgent === 'local') {
        streamUrl = `/${mode}/stream`;
    } else {
        // For remote agents, we could either:
        // 1. Use the multi-agent stream: /controller/stream/all
        // 2. Or proxy through controller (not implemented yet)
        // For now, use multi-agent stream which includes agent_name tagging
        streamUrl = '/controller/stream/all';
    }

    agentEventSource = new EventSource(streamUrl);

    agentEventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);

            // If using multi-agent stream, filter by current agent if needed
            if (streamUrl === '/controller/stream/all' && currentAgent !== 'local') {
                const agent = agents.find(a => a.id == currentAgent);
                if (agent && data.agent_name && data.agent_name !== agent.name) {
                    return; // Skip messages from other agents
                }
            }

            onMessage(data);
        } catch (e) {
            console.error('Error parsing SSE message:', e);
        }
    };

    agentEventSource.onerror = (error) => {
        console.error('SSE connection error:', error);
    };

    return agentEventSource;
}

function disconnectAgentStream() {
    if (agentEventSource) {
        agentEventSource.close();
        agentEventSource = null;
    }
}

// ============== INITIALIZATION ==============

function initAgentManager() {
    // Load agents on page load
    loadAgents();

    // Set up agent selector change handler
    const selector = document.getElementById('agentSelect');
    if (selector) {
        selector.addEventListener('change', (e) => {
            selectAgent(e.target.value);
        });
    }

    // Refresh agents periodically
    setInterval(loadAgents, 30000);
}

// ============== MULTI-AGENT MODE ==============

/**
 * Toggle multi-agent mode to show combined results from all agents.
 */
function toggleMultiAgentMode() {
    const checkbox = document.getElementById('showAllAgents');
    multiAgentMode = checkbox ? checkbox.checked : false;

    const selector = document.getElementById('agentSelect');
    const statusText = document.getElementById('agentStatusText');

    if (multiAgentMode) {
        // Disable individual agent selection
        if (selector) selector.disabled = true;
        if (statusText) statusText.textContent = 'All Agents';

        // Connect to multi-agent stream
        connectMultiAgentStream();

        console.log('Multi-agent mode enabled - showing all agents');
    } else {
        // Re-enable individual selection
        if (selector) selector.disabled = false;
        updateAgentStatus();

        // Disconnect multi-agent stream
        disconnectMultiAgentStream();

        console.log('Multi-agent mode disabled');
    }
}

/**
 * Connect to the combined multi-agent SSE stream.
 */
function connectMultiAgentStream() {
    disconnectMultiAgentStream();

    agentEventSource = new EventSource('/controller/stream/all');

    agentEventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);

            // Skip keepalive messages
            if (data.type === 'keepalive') return;

            // Route to appropriate handler based on scan_type
            handleMultiAgentData(data);
        } catch (e) {
            console.error('Error parsing multi-agent SSE:', e);
        }
    };

    agentEventSource.onerror = (error) => {
        console.error('Multi-agent SSE error:', error);
    };
}

function disconnectMultiAgentStream() {
    if (agentEventSource) {
        agentEventSource.close();
        agentEventSource = null;
    }
    if (multiAgentPollInterval) {
        clearInterval(multiAgentPollInterval);
        multiAgentPollInterval = null;
    }
}

/**
 * Handle data from multi-agent stream and route to display.
 */
function handleMultiAgentData(data) {
    const agentName = data.agent_name || 'Unknown';
    const scanType = data.scan_type;
    const payload = data.payload;

    // Add agent badge to the data for display
    if (payload) {
        payload._agent = agentName;
    }

    // Route based on scan type
    switch (scanType) {
        case 'sensor':
            if (payload && payload.sensors) {
                payload.sensors.forEach(sensor => {
                    sensor._agent = agentName;
                    if (typeof displaySensorMessage === 'function') {
                        displaySensorMessage(sensor);
                    }
                });
            }
            break;

        case 'pager':
            if (payload && payload.messages) {
                payload.messages.forEach(msg => {
                    msg._agent = agentName;
                    // Display pager message if handler exists
                    if (typeof addPagerMessage === 'function') {
                        addPagerMessage(msg);
                    }
                });
            }
            break;

        case 'adsb':
            if (payload && payload.aircraft) {
                Object.values(payload.aircraft).forEach(ac => {
                    ac._agent = agentName;
                    // Update aircraft display if handler exists
                    if (typeof updateAircraft === 'function') {
                        updateAircraft(ac);
                    }
                });
            }
            break;

        case 'wifi':
            if (payload && payload.networks) {
                Object.values(payload.networks).forEach(net => {
                    net._agent = agentName;
                });
                // Update WiFi display if handler exists
                if (typeof WiFiMode !== 'undefined' && WiFiMode.updateNetworks) {
                    WiFiMode.updateNetworks(payload.networks);
                }
            }
            break;

        default:
            console.log(`Multi-agent data from ${agentName}: ${scanType}`, payload);
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', initAgentManager);
