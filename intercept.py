#!/usr/bin/env python3
"""
Pager Decoder - POCSAG/FLEX decoder using RTL-SDR and multimon-ng
"""

import subprocess
import shutil
import re
import threading
import queue
import pty
import os
import select
from flask import Flask, render_template_string, jsonify, request, Response, send_file

app = Flask(__name__)

# Global process management
current_process = None
sensor_process = None
output_queue = queue.Queue()
sensor_queue = queue.Queue()
process_lock = threading.Lock()
sensor_lock = threading.Lock()

# Logging settings
logging_enabled = False
log_file_path = 'pager_messages.log'


HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>INTERCEPT // Signal Intelligence</title>
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Rajdhani:wght@400;500;600;700&display=swap');

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        :root {
            --bg-primary: #000000;
            --bg-secondary: #0a0a0a;
            --bg-tertiary: #111111;
            --bg-card: #0d0d0d;
            --accent-cyan: #00d4ff;
            --accent-cyan-dim: #00d4ff40;
            --accent-green: #00ff88;
            --accent-red: #ff3366;
            --accent-orange: #ff8800;
            --text-primary: #ffffff;
            --text-secondary: #888888;
            --text-dim: #444444;
            --border-color: #1a1a1a;
            --border-glow: #00d4ff33;
        }

        body {
            font-family: 'Rajdhani', 'Segoe UI', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            background-image:
                radial-gradient(ellipse at top, #001a2c 0%, transparent 50%),
                radial-gradient(ellipse at bottom, #0a0a0a 0%, var(--bg-primary) 100%);
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }

        header {
            background: linear-gradient(180deg, var(--bg-secondary) 0%, transparent 100%);
            padding: 30px 20px;
            text-align: center;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 25px;
            position: relative;
        }

        header::after {
            content: '';
            position: absolute;
            bottom: -1px;
            left: 50%;
            transform: translateX(-50%);
            width: 200px;
            height: 1px;
            background: linear-gradient(90deg, transparent, var(--accent-cyan), transparent);
        }

        header h1 {
            color: var(--text-primary);
            font-size: 2.5em;
            font-weight: 700;
            letter-spacing: 8px;
            text-transform: uppercase;
            margin-bottom: 8px;
            text-shadow: 0 0 30px var(--accent-cyan-dim);
        }

        header p {
            color: var(--text-secondary);
            font-size: 14px;
            letter-spacing: 3px;
            text-transform: uppercase;
        }

        .logo {
            margin-bottom: 15px;
            animation: logo-pulse 3s ease-in-out infinite;
        }

        .logo svg {
            filter: drop-shadow(0 0 10px var(--accent-cyan-dim));
        }

        @keyframes logo-pulse {
            0%, 100% {
                filter: drop-shadow(0 0 5px var(--accent-cyan-dim));
            }
            50% {
                filter: drop-shadow(0 0 20px var(--accent-cyan));
            }
        }

        .main-content {
            display: grid;
            grid-template-columns: 340px 1fr;
            gap: 25px;
        }

        @media (max-width: 900px) {
            .main-content {
                grid-template-columns: 1fr;
            }
        }

        .sidebar {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            padding: 20px;
            position: relative;
        }

        .sidebar::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, var(--accent-cyan), transparent);
        }

        .section {
            margin-bottom: 25px;
        }

        .section h3 {
            color: var(--accent-cyan);
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border-color);
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 3px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .section h3::before {
            content: '//';
            color: var(--text-dim);
        }

        .form-group {
            margin-bottom: 15px;
        }

        .form-group label {
            display: block;
            margin-bottom: 6px;
            color: var(--text-secondary);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .form-group input,
        .form-group select {
            width: 100%;
            padding: 12px 15px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            font-family: 'JetBrains Mono', monospace;
            font-size: 14px;
            transition: all 0.2s ease;
        }

        .form-group input:focus,
        .form-group select:focus {
            outline: none;
            border-color: var(--accent-cyan);
            box-shadow: 0 0 15px var(--accent-cyan-dim), inset 0 0 15px var(--accent-cyan-dim);
        }

        .checkbox-group {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
        }

        .checkbox-group label {
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--text-secondary);
            font-size: 12px;
            cursor: pointer;
            padding: 8px 12px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            transition: all 0.2s ease;
        }

        .checkbox-group label:hover {
            border-color: var(--accent-cyan);
        }

        .checkbox-group input[type="checkbox"] {
            width: auto;
            accent-color: var(--accent-cyan);
        }

        .preset-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }

        .preset-btn {
            padding: 10px 16px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            cursor: pointer;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.2s ease;
        }

        .preset-btn:hover {
            background: var(--accent-cyan);
            color: var(--bg-primary);
            border-color: var(--accent-cyan);
            box-shadow: 0 0 20px var(--accent-cyan-dim);
        }

        .run-btn {
            width: 100%;
            padding: 16px;
            background: transparent;
            border: 2px solid var(--accent-green);
            color: var(--accent-green);
            font-family: 'Rajdhani', sans-serif;
            font-size: 14px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 4px;
            cursor: pointer;
            transition: all 0.3s ease;
            margin-top: 15px;
            position: relative;
            overflow: hidden;
        }

        .run-btn::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, var(--accent-green), transparent);
            opacity: 0.3;
            transition: left 0.5s ease;
        }

        .run-btn:hover {
            background: var(--accent-green);
            color: var(--bg-primary);
            box-shadow: 0 0 30px rgba(0, 255, 136, 0.4);
        }

        .run-btn:hover::before {
            left: 100%;
        }

        .stop-btn {
            width: 100%;
            padding: 16px;
            background: transparent;
            border: 2px solid var(--accent-red);
            color: var(--accent-red);
            font-family: 'Rajdhani', sans-serif;
            font-size: 14px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 4px;
            cursor: pointer;
            transition: all 0.3s ease;
            margin-top: 15px;
        }

        .stop-btn:hover {
            background: var(--accent-red);
            color: var(--bg-primary);
            box-shadow: 0 0 30px rgba(255, 51, 102, 0.4);
        }

        .output-panel {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
            position: relative;
        }

        .output-panel::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--accent-cyan), transparent);
        }

        .output-header {
            padding: 18px 25px;
            background: var(--bg-secondary);
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
        }

        .output-header h3 {
            color: var(--text-primary);
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 3px;
        }

        .stats {
            display: flex;
            gap: 25px;
            font-size: 11px;
            color: var(--text-secondary);
            font-family: 'JetBrains Mono', monospace;
        }

        .stats span {
            color: var(--accent-cyan);
            font-weight: 500;
        }

        .output-content {
            flex: 1;
            padding: 15px;
            overflow-y: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            background: var(--bg-primary);
            margin: 15px;
            border: 1px solid var(--border-color);
            min-height: 500px;
            max-height: 600px;
        }

        .output-content::-webkit-scrollbar {
            width: 6px;
        }

        .output-content::-webkit-scrollbar-track {
            background: var(--bg-primary);
        }

        .output-content::-webkit-scrollbar-thumb {
            background: var(--border-color);
        }

        .output-content::-webkit-scrollbar-thumb:hover {
            background: var(--accent-cyan);
        }

        .message {
            padding: 15px;
            margin-bottom: 10px;
            border: 1px solid var(--border-color);
            border-left: 3px solid var(--accent-cyan);
            background: var(--bg-secondary);
            position: relative;
            transition: all 0.2s ease;
        }

        .message:hover {
            border-left-color: var(--accent-cyan);
            box-shadow: 0 0 20px var(--accent-cyan-dim);
        }

        .message.pocsag {
            border-left-color: var(--accent-cyan);
        }

        .message.flex {
            border-left-color: var(--accent-orange);
        }

        .message .header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 10px;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .message .protocol {
            color: var(--accent-cyan);
            font-weight: 600;
        }

        .message.pocsag .protocol {
            color: var(--accent-cyan);
        }

        .message.flex .protocol {
            color: var(--accent-orange);
        }

        .message .address {
            color: var(--accent-green);
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            margin-bottom: 8px;
        }

        .message .content {
            color: var(--text-primary);
            word-wrap: break-word;
            font-size: 13px;
            line-height: 1.5;
        }

        .message .content.numeric {
            font-family: 'JetBrains Mono', monospace;
            font-size: 15px;
            letter-spacing: 2px;
            color: var(--accent-cyan);
        }

        .status-bar {
            padding: 15px 25px;
            background: var(--bg-secondary);
            border-top: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 11px;
        }

        .status-indicator {
            display: flex;
            align-items: center;
            gap: 10px;
            text-transform: uppercase;
            letter-spacing: 2px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background: var(--text-dim);
            position: relative;
        }

        .status-dot.running {
            background: var(--accent-green);
            box-shadow: 0 0 10px var(--accent-green);
            animation: pulse-glow 2s infinite;
        }

        @keyframes pulse-glow {
            0%, 100% {
                opacity: 1;
                box-shadow: 0 0 10px var(--accent-green);
            }
            50% {
                opacity: 0.7;
                box-shadow: 0 0 20px var(--accent-green), 0 0 30px var(--accent-green);
            }
        }

        .clear-btn {
            padding: 8px 16px;
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 2px;
            transition: all 0.2s ease;
        }

        .clear-btn:hover {
            border-color: var(--accent-cyan);
            color: var(--accent-cyan);
        }

        .tool-status {
            font-size: 10px;
            padding: 4px 10px;
            margin-left: 8px;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 600;
        }

        .tool-status.ok {
            background: transparent;
            border: 1px solid var(--accent-green);
            color: var(--accent-green);
        }

        .tool-status.missing {
            background: transparent;
            border: 1px solid var(--accent-red);
            color: var(--accent-red);
        }

        .info-text {
            font-size: 10px;
            color: var(--text-dim);
            margin-top: 8px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .header-controls {
            display: flex;
            align-items: center;
            gap: 20px;
        }

        .signal-meter {
            display: flex;
            align-items: flex-end;
            gap: 2px;
            height: 20px;
            padding: 0 10px;
        }

        .signal-bar {
            width: 4px;
            background: var(--border-color);
            transition: all 0.1s ease;
        }

        .signal-bar:nth-child(1) { height: 4px; }
        .signal-bar:nth-child(2) { height: 8px; }
        .signal-bar:nth-child(3) { height: 12px; }
        .signal-bar:nth-child(4) { height: 16px; }
        .signal-bar:nth-child(5) { height: 20px; }

        .signal-bar.active {
            background: var(--accent-cyan);
            box-shadow: 0 0 8px var(--accent-cyan);
        }

        .waterfall-container {
            padding: 0 15px;
            margin-bottom: 10px;
        }

        #waterfallCanvas {
            width: 100%;
            height: 60px;
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            transition: box-shadow 0.3s ease;
        }

        #waterfallCanvas.active {
            box-shadow: 0 0 15px var(--accent-cyan-dim);
            border-color: var(--accent-cyan);
        }

        .status-controls {
            display: flex;
            gap: 8px;
            align-items: center;
        }

        .control-btn {
            padding: 6px 12px;
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
            transition: all 0.2s ease;
            font-family: 'Rajdhani', sans-serif;
        }

        .control-btn:hover {
            border-color: var(--accent-cyan);
            color: var(--accent-cyan);
        }

        .control-btn.active {
            border-color: var(--accent-green);
            color: var(--accent-green);
        }

        .control-btn.muted {
            border-color: var(--accent-red);
            color: var(--accent-red);
        }

        /* Mode tabs */
        .mode-tabs {
            display: flex;
            gap: 0;
            margin-bottom: 20px;
            border: 1px solid var(--border-color);
        }

        .mode-tab {
            flex: 1;
            padding: 12px 16px;
            background: var(--bg-primary);
            border: none;
            color: var(--text-secondary);
            cursor: pointer;
            font-family: 'Rajdhani', sans-serif;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 2px;
            transition: all 0.2s ease;
        }

        .mode-tab:not(:last-child) {
            border-right: 1px solid var(--border-color);
        }

        .mode-tab:hover {
            background: var(--bg-secondary);
            color: var(--text-primary);
        }

        .mode-tab.active {
            background: var(--accent-cyan);
            color: var(--bg-primary);
        }

        .mode-content {
            display: none;
        }

        .mode-content.active {
            display: block;
        }

        /* Sensor card styling */
        .sensor-card {
            padding: 15px;
            margin-bottom: 10px;
            border: 1px solid var(--border-color);
            border-left: 3px solid var(--accent-green);
            background: var(--bg-secondary);
        }

        .sensor-card .device-name {
            color: var(--accent-green);
            font-weight: 600;
            font-size: 13px;
            margin-bottom: 8px;
        }

        .sensor-card .sensor-data {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 8px;
        }

        .sensor-card .data-item {
            background: var(--bg-primary);
            padding: 8px 10px;
            border: 1px solid var(--border-color);
        }

        .sensor-card .data-label {
            font-size: 9px;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .sensor-card .data-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 14px;
            color: var(--accent-cyan);
        }

        /* Scanline effect overlay */
        body::before {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            background: repeating-linear-gradient(
                0deg,
                rgba(0, 0, 0, 0.03),
                rgba(0, 0, 0, 0.03) 1px,
                transparent 1px,
                transparent 2px
            );
            z-index: 1000;
        }
    </style>
</head>
<body>
    <header>
        <div class="logo">
            <svg width="50" height="50" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                <!-- Outer hexagon -->
                <path d="M50 5 L90 27.5 L90 72.5 L50 95 L10 72.5 L10 27.5 Z" stroke="#00d4ff" stroke-width="2" fill="none"/>
                <!-- Inner signal waves -->
                <path d="M30 50 Q40 35, 50 50 Q60 65, 70 50" stroke="#00d4ff" stroke-width="2.5" fill="none" stroke-linecap="round"/>
                <path d="M35 50 Q42 40, 50 50 Q58 60, 65 50" stroke="#00ff88" stroke-width="2" fill="none" stroke-linecap="round"/>
                <path d="M40 50 Q45 45, 50 50 Q55 55, 60 50" stroke="#ffffff" stroke-width="1.5" fill="none" stroke-linecap="round"/>
                <!-- Center dot -->
                <circle cx="50" cy="50" r="3" fill="#00d4ff"/>
                <!-- Corner accents -->
                <path d="M50 12 L55 17 L50 17 Z" fill="#00d4ff"/>
                <path d="M50 88 L45 83 L50 83 Z" fill="#00d4ff"/>
            </svg>
        </div>
        <h1>INTERCEPT</h1>
        <p>Signal Intelligence // by smittix</p>
    </header>

    <div class="container">
        <div class="main-content">
            <div class="sidebar">
                <!-- Mode Tabs -->
                <div class="mode-tabs">
                    <button class="mode-tab active" onclick="switchMode('pager')">Pager</button>
                    <button class="mode-tab" onclick="switchMode('sensor')">433MHz</button>
                </div>

                <div class="section">
                    <h3>Device</h3>
                    <div class="form-group">
                        <select id="deviceSelect">
                            {% if devices %}
                                {% for device in devices %}
                                <option value="{{ device.index }}">{{ device.index }}: {{ device.name }}</option>
                                {% endfor %}
                            {% else %}
                                <option value="0">No devices found</option>
                            {% endif %}
                        </select>
                    </div>
                    <button class="preset-btn" onclick="refreshDevices()" style="width: 100%;">
                        Refresh Devices
                    </button>
                    <div class="info-text" style="display: grid; grid-template-columns: auto auto; gap: 4px 8px; align-items: center;">
                        <span>rtl_fm:</span><span class="tool-status {{ 'ok' if tools.rtl_fm else 'missing' }}">{{ 'OK' if tools.rtl_fm else 'Missing' }}</span>
                        <span>multimon-ng:</span><span class="tool-status {{ 'ok' if tools.multimon else 'missing' }}">{{ 'OK' if tools.multimon else 'Missing' }}</span>
                        <span>rtl_433:</span><span class="tool-status {{ 'ok' if tools.rtl_433 else 'missing' }}">{{ 'OK' if tools.rtl_433 else 'Missing' }}</span>
                    </div>
                </div>

                <!-- PAGER MODE -->
                <div id="pagerMode" class="mode-content active">
                    <div class="section">
                        <h3>Frequency</h3>
                        <div class="form-group">
                            <label>Frequency (MHz)</label>
                            <input type="text" id="frequency" value="153.350" placeholder="e.g., 153.350">
                        </div>
                        <div class="preset-buttons" id="presetButtons">
                            <!-- Populated by JavaScript -->
                        </div>
                        <div style="margin-top: 8px; display: flex; gap: 5px;">
                            <input type="text" id="newPresetFreq" placeholder="New freq (MHz)" style="flex: 1; padding: 6px; background: #0f3460; border: 1px solid #1a1a2e; color: #fff; border-radius: 4px; font-size: 12px;">
                            <button class="preset-btn" onclick="addPreset()" style="background: #2ecc71;">Add</button>
                        </div>
                        <div style="margin-top: 5px;">
                            <button class="preset-btn" onclick="resetPresets()" style="font-size: 11px;">Reset to Defaults</button>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Protocols</h3>
                        <div class="checkbox-group">
                            <label><input type="checkbox" id="proto_pocsag512" checked> POCSAG-512</label>
                            <label><input type="checkbox" id="proto_pocsag1200" checked> POCSAG-1200</label>
                            <label><input type="checkbox" id="proto_pocsag2400" checked> POCSAG-2400</label>
                            <label><input type="checkbox" id="proto_flex" checked> FLEX</label>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Settings</h3>
                        <div class="form-group">
                            <label>Gain (dB, 0 = auto)</label>
                            <input type="text" id="gain" value="0" placeholder="0-49 or 0 for auto">
                        </div>
                        <div class="form-group">
                            <label>Squelch Level</label>
                            <input type="text" id="squelch" value="0" placeholder="0 = off">
                        </div>
                        <div class="form-group">
                            <label>PPM Correction</label>
                            <input type="text" id="ppm" value="0" placeholder="Frequency correction">
                        </div>
                    </div>

                    <div class="section">
                        <h3>Logging</h3>
                        <div class="checkbox-group" style="margin-bottom: 15px;">
                            <label>
                                <input type="checkbox" id="loggingEnabled" onchange="toggleLogging()">
                                Enable Logging
                            </label>
                        </div>
                        <div class="form-group">
                            <label>Log file path</label>
                            <input type="text" id="logFilePath" value="pager_messages.log" placeholder="pager_messages.log">
                        </div>
                    </div>

                    <button class="run-btn" id="startBtn" onclick="startDecoding()">
                        Start Decoding
                    </button>
                    <button class="stop-btn" id="stopBtn" onclick="stopDecoding()" style="display: none;">
                        Stop Decoding
                    </button>
                </div>

                <!-- 433MHz SENSOR MODE -->
                <div id="sensorMode" class="mode-content">
                    <div class="section">
                        <h3>Frequency</h3>
                        <div class="form-group">
                            <label>Frequency (MHz)</label>
                            <input type="text" id="sensorFrequency" value="433.92" placeholder="e.g., 433.92">
                        </div>
                        <div class="preset-buttons">
                            <button class="preset-btn" onclick="setSensorFreq('433.92')">433.92</button>
                            <button class="preset-btn" onclick="setSensorFreq('315.00')">315.00</button>
                            <button class="preset-btn" onclick="setSensorFreq('868.00')">868.00</button>
                            <button class="preset-btn" onclick="setSensorFreq('915.00')">915.00</button>
                        </div>
                    </div>

                    <div class="section">
                        <h3>Settings</h3>
                        <div class="form-group">
                            <label>Gain (dB, 0 = auto)</label>
                            <input type="text" id="sensorGain" value="0" placeholder="0-49 or 0 for auto">
                        </div>
                        <div class="form-group">
                            <label>PPM Correction</label>
                            <input type="text" id="sensorPpm" value="0" placeholder="Frequency correction">
                        </div>
                    </div>

                    <div class="section">
                        <h3>Protocols</h3>
                        <div class="info-text" style="margin-bottom: 10px;">
                            rtl_433 auto-detects 200+ device protocols including weather stations, TPMS, doorbells, and more.
                        </div>
                        <div class="checkbox-group">
                            <label>
                                <input type="checkbox" id="sensorLogging" onchange="toggleSensorLogging()">
                                Enable Logging
                            </label>
                        </div>
                    </div>

                    <button class="run-btn" id="startSensorBtn" onclick="startSensorDecoding()">
                        Start Listening
                    </button>
                    <button class="stop-btn" id="stopSensorBtn" onclick="stopSensorDecoding()" style="display: none;">
                        Stop Listening
                    </button>
                </div>

                <button class="preset-btn" onclick="killAll()" style="width: 100%; margin-top: 10px; border-color: #ff3366; color: #ff3366;">
                    Kill All Processes
                </button>
            </div>

            <div class="output-panel">
                <div class="output-header">
                    <h3>Decoded Messages</h3>
                    <div class="header-controls">
                        <div id="signalMeter" class="signal-meter" title="Signal Activity">
                            <div class="signal-bar"></div>
                            <div class="signal-bar"></div>
                            <div class="signal-bar"></div>
                            <div class="signal-bar"></div>
                            <div class="signal-bar"></div>
                        </div>
                        <div class="stats" id="pagerStats">
                            <div>MSG: <span id="msgCount">0</span></div>
                            <div>POCSAG: <span id="pocsagCount">0</span></div>
                            <div>FLEX: <span id="flexCount">0</span></div>
                        </div>
                        <div class="stats" id="sensorStats" style="display: none;">
                            <div>SENSORS: <span id="sensorCount">0</span></div>
                            <div>DEVICES: <span id="deviceCount">0</span></div>
                        </div>
                    </div>
                </div>

                <div class="waterfall-container">
                    <canvas id="waterfallCanvas" width="800" height="60"></canvas>
                </div>

                <div class="output-content" id="output">
                    <div class="placeholder" style="color: #888; text-align: center; padding: 50px;">
                        Configure settings and click "Start Decoding" to begin.
                    </div>
                </div>

                <div class="status-bar">
                    <div class="status-indicator">
                        <div class="status-dot" id="statusDot"></div>
                        <span id="statusText">Idle</span>
                    </div>
                    <div class="status-controls">
                        <button id="muteBtn" class="control-btn" onclick="toggleMute()">🔊 MUTE</button>
                        <button id="autoScrollBtn" class="control-btn" onclick="toggleAutoScroll()">⬇ AUTO-SCROLL ON</button>
                        <button class="control-btn" onclick="exportCSV()">📄 CSV</button>
                        <button class="control-btn" onclick="exportJSON()">📋 JSON</button>
                        <button class="clear-btn" onclick="clearMessages()">Clear</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let eventSource = null;
        let isRunning = false;
        let isSensorRunning = false;
        let currentMode = 'pager';
        let msgCount = 0;
        let pocsagCount = 0;
        let flexCount = 0;
        let sensorCount = 0;
        let deviceList = {{ devices | tojson | safe }};

        // Mode switching
        function switchMode(mode) {
            currentMode = mode;
            document.querySelectorAll('.mode-tab').forEach(tab => {
                tab.classList.toggle('active', tab.textContent.toLowerCase().includes(mode === 'pager' ? 'pager' : '433'));
            });
            document.getElementById('pagerMode').classList.toggle('active', mode === 'pager');
            document.getElementById('sensorMode').classList.toggle('active', mode === 'sensor');
            document.getElementById('pagerStats').style.display = mode === 'pager' ? 'flex' : 'none';
            document.getElementById('sensorStats').style.display = mode === 'sensor' ? 'flex' : 'none';
        }

        // Track unique sensor devices
        let uniqueDevices = new Set();

        // Sensor frequency
        function setSensorFreq(freq) {
            document.getElementById('sensorFrequency').value = freq;
            if (isSensorRunning) {
                fetch('/stop_sensor', {method: 'POST'})
                    .then(() => setTimeout(() => startSensorDecoding(), 500));
            }
        }

        // Start sensor decoding
        function startSensorDecoding() {
            const freq = document.getElementById('sensorFrequency').value;
            const gain = document.getElementById('sensorGain').value;
            const ppm = document.getElementById('sensorPpm').value;
            const device = getSelectedDevice();

            const config = {
                frequency: freq,
                gain: gain,
                ppm: ppm,
                device: device
            };

            fetch('/start_sensor', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(config)
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'started') {
                      setSensorRunning(true);
                      startSensorStream();
                  } else {
                      alert('Error: ' + data.message);
                  }
              });
        }

        // Stop sensor decoding
        function stopSensorDecoding() {
            fetch('/stop_sensor', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    setSensorRunning(false);
                    if (eventSource) {
                        eventSource.close();
                        eventSource = null;
                    }
                });
        }

        function setSensorRunning(running) {
            isSensorRunning = running;
            document.getElementById('statusDot').classList.toggle('running', running);
            document.getElementById('statusText').textContent = running ? 'Listening...' : 'Idle';
            document.getElementById('startSensorBtn').style.display = running ? 'none' : 'block';
            document.getElementById('stopSensorBtn').style.display = running ? 'block' : 'none';
        }

        function startSensorStream() {
            if (eventSource) {
                eventSource.close();
            }

            eventSource = new EventSource('/stream_sensor');

            eventSource.onopen = function() {
                showInfo('Sensor stream connected...');
            };

            eventSource.onmessage = function(e) {
                const data = JSON.parse(e.data);
                if (data.type === 'sensor') {
                    addSensorReading(data);
                } else if (data.type === 'status') {
                    if (data.text === 'stopped') {
                        setSensorRunning(false);
                    }
                } else if (data.type === 'info' || data.type === 'raw') {
                    showInfo(data.text);
                }
            };

            eventSource.onerror = function(e) {
                console.error('Sensor stream error');
            };
        }

        function addSensorReading(data) {
            const output = document.getElementById('output');
            const placeholder = output.querySelector('.placeholder');
            if (placeholder) placeholder.remove();

            // Store for export
            allMessages.push(data);
            playAlert();
            pulseSignal();
            addWaterfallPoint(Date.now(), 0.8);

            sensorCount++;
            document.getElementById('sensorCount').textContent = sensorCount;

            // Track unique devices by model + id
            const deviceKey = (data.model || 'Unknown') + '_' + (data.id || data.channel || '0');
            if (!uniqueDevices.has(deviceKey)) {
                uniqueDevices.add(deviceKey);
                document.getElementById('deviceCount').textContent = uniqueDevices.size;
            }

            const card = document.createElement('div');
            card.className = 'sensor-card';

            let dataItems = '';
            const skipKeys = ['type', 'time', 'model', 'raw'];
            for (const [key, value] of Object.entries(data)) {
                if (!skipKeys.includes(key) && value !== null && value !== undefined) {
                    const label = key.replace(/_/g, ' ');
                    let displayValue = value;
                    if (key === 'temperature_C') displayValue = value + ' °C';
                    else if (key === 'temperature_F') displayValue = value + ' °F';
                    else if (key === 'humidity') displayValue = value + ' %';
                    else if (key === 'pressure_hPa') displayValue = value + ' hPa';
                    else if (key === 'wind_avg_km_h') displayValue = value + ' km/h';
                    else if (key === 'rain_mm') displayValue = value + ' mm';
                    else if (key === 'battery_ok') displayValue = value ? 'OK' : 'Low';

                    dataItems += '<div class="data-item"><div class="data-label">' + label + '</div><div class="data-value">' + displayValue + '</div></div>';
                }
            }

            const relTime = data.time ? getRelativeTime(data.time.split(' ')[1] || data.time) : 'now';

            card.innerHTML =
                '<div class="header" style="display: flex; justify-content: space-between; margin-bottom: 8px;">' +
                    '<span class="device-name">' + (data.model || 'Unknown Device') + '</span>' +
                    '<span class="msg-time" data-timestamp="' + (data.time || '') + '" style="color: #444; font-size: 10px;">' + relTime + '</span>' +
                '</div>' +
                '<div class="sensor-data">' + dataItems + '</div>';

            output.insertBefore(card, output.firstChild);

            if (autoScroll) output.scrollTop = 0;
            while (output.children.length > 100) {
                output.removeChild(output.lastChild);
            }
        }

        function toggleSensorLogging() {
            const enabled = document.getElementById('sensorLogging').checked;
            fetch('/logging', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled: enabled, log_file: 'sensor_data.log'})
            });
        }

        // Audio alert settings
        let audioMuted = localStorage.getItem('audioMuted') === 'true';
        let audioContext = null;

        function initAudio() {
            if (!audioContext) {
                audioContext = new (window.AudioContext || window.webkitAudioContext)();
            }
        }

        function playAlert() {
            if (audioMuted || !audioContext) return;
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();
            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);
            oscillator.frequency.value = 880;
            oscillator.type = 'sine';
            gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.2);
            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + 0.2);
        }

        function toggleMute() {
            audioMuted = !audioMuted;
            localStorage.setItem('audioMuted', audioMuted);
            updateMuteButton();
        }

        function updateMuteButton() {
            const btn = document.getElementById('muteBtn');
            if (btn) {
                btn.innerHTML = audioMuted ? '🔇 UNMUTE' : '🔊 MUTE';
                btn.classList.toggle('muted', audioMuted);
            }
        }

        // Message storage for export
        let allMessages = [];

        function exportCSV() {
            if (allMessages.length === 0) {
                alert('No messages to export');
                return;
            }
            const headers = ['Timestamp', 'Protocol', 'Address', 'Function', 'Type', 'Message'];
            const csv = [headers.join(',')];
            allMessages.forEach(msg => {
                const row = [
                    msg.timestamp || '',
                    msg.protocol || '',
                    msg.address || '',
                    msg.function || '',
                    msg.msg_type || '',
                    '"' + (msg.message || '').replace(/"/g, '""') + '"'
                ];
                csv.push(row.join(','));
            });
            downloadFile(csv.join('\\n'), 'intercept_messages.csv', 'text/csv');
        }

        function exportJSON() {
            if (allMessages.length === 0) {
                alert('No messages to export');
                return;
            }
            downloadFile(JSON.stringify(allMessages, null, 2), 'intercept_messages.json', 'application/json');
        }

        function downloadFile(content, filename, type) {
            const blob = new Blob([content], { type });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            a.click();
            URL.revokeObjectURL(url);
        }

        // Auto-scroll setting
        let autoScroll = localStorage.getItem('autoScroll') !== 'false';

        function toggleAutoScroll() {
            autoScroll = !autoScroll;
            localStorage.setItem('autoScroll', autoScroll);
            updateAutoScrollButton();
        }

        function updateAutoScrollButton() {
            const btn = document.getElementById('autoScrollBtn');
            if (btn) {
                btn.innerHTML = autoScroll ? '⬇ AUTO-SCROLL ON' : '⬇ AUTO-SCROLL OFF';
                btn.classList.toggle('active', autoScroll);
            }
        }

        // Signal activity meter
        let signalActivity = 0;
        let lastMessageTime = 0;

        function updateSignalMeter() {
            const now = Date.now();
            const timeSinceLastMsg = now - lastMessageTime;

            // Decay signal activity over time
            if (timeSinceLastMsg > 1000) {
                signalActivity = Math.max(0, signalActivity - 0.05);
            }

            const meter = document.getElementById('signalMeter');
            const bars = meter?.querySelectorAll('.signal-bar');
            if (bars) {
                const activeBars = Math.ceil(signalActivity * bars.length);
                bars.forEach((bar, i) => {
                    bar.classList.toggle('active', i < activeBars);
                });
            }
        }

        function pulseSignal() {
            signalActivity = Math.min(1, signalActivity + 0.4);
            lastMessageTime = Date.now();

            // Flash waterfall canvas
            const canvas = document.getElementById('waterfallCanvas');
            if (canvas) {
                canvas.classList.add('active');
                setTimeout(() => canvas.classList.remove('active'), 500);
            }
        }

        // Waterfall display
        const waterfallData = [];
        const maxWaterfallRows = 50;

        function addWaterfallPoint(timestamp, intensity) {
            waterfallData.push({ time: timestamp, intensity });
            if (waterfallData.length > maxWaterfallRows * 100) {
                waterfallData.shift();
            }
            renderWaterfall();
        }

        function renderWaterfall() {
            const canvas = document.getElementById('waterfallCanvas');
            if (!canvas) return;
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            const width = canvas.width;
            const height = canvas.height;

            // Shift existing image down
            const imageData = ctx.getImageData(0, 0, width, height - 2);
            ctx.putImageData(imageData, 0, 2);

            // Draw new row at top
            ctx.fillStyle = '#000';
            ctx.fillRect(0, 0, width, 2);

            // Add activity markers
            const now = Date.now();
            const recentData = waterfallData.filter(d => now - d.time < 100);
            recentData.forEach(d => {
                const x = Math.random() * width;
                const hue = 180 + (d.intensity * 60); // cyan to green
                ctx.fillStyle = `hsla(${hue}, 100%, 50%, ${d.intensity})`;
                ctx.fillRect(x - 2, 0, 4, 2);
            });
        }

        // Relative timestamps
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

        function updateRelativeTimes() {
            document.querySelectorAll('.msg-time').forEach(el => {
                const ts = el.dataset.timestamp;
                if (ts) el.textContent = getRelativeTime(ts);
            });
        }

        // Update timers
        setInterval(updateSignalMeter, 100);
        setInterval(updateRelativeTimes, 10000);

        // Default presets (UK frequencies)
        const defaultPresets = ['153.350', '153.025'];

        // Load presets from localStorage or use defaults
        function loadPresets() {
            const saved = localStorage.getItem('pagerPresets');
            return saved ? JSON.parse(saved) : [...defaultPresets];
        }

        function savePresets(presets) {
            localStorage.setItem('pagerPresets', JSON.stringify(presets));
        }

        function renderPresets() {
            const presets = loadPresets();
            const container = document.getElementById('presetButtons');
            container.innerHTML = presets.map(freq =>
                `<button class="preset-btn" onclick="setFreq('${freq}')" oncontextmenu="removePreset('${freq}'); return false;" title="Right-click to remove">${freq}</button>`
            ).join('');
        }

        function addPreset() {
            const input = document.getElementById('newPresetFreq');
            const freq = input.value.trim();
            if (!freq || isNaN(parseFloat(freq))) {
                alert('Please enter a valid frequency');
                return;
            }
            const presets = loadPresets();
            if (!presets.includes(freq)) {
                presets.push(freq);
                savePresets(presets);
                renderPresets();
            }
            input.value = '';
        }

        function removePreset(freq) {
            if (confirm('Remove preset ' + freq + ' MHz?')) {
                let presets = loadPresets();
                presets = presets.filter(p => p !== freq);
                savePresets(presets);
                renderPresets();
            }
        }

        function resetPresets() {
            if (confirm('Reset to default presets?')) {
                savePresets([...defaultPresets]);
                renderPresets();
            }
        }

        // Initialize presets on load
        renderPresets();

        // Initialize button states on load
        updateMuteButton();
        updateAutoScrollButton();

        // Initialize audio context on first user interaction (required by browsers)
        document.addEventListener('click', function initAudioOnClick() {
            initAudio();
            document.removeEventListener('click', initAudioOnClick);
        }, { once: true });

        function setFreq(freq) {
            document.getElementById('frequency').value = freq;
            // Auto-restart decoder with new frequency if currently running
            if (isRunning) {
                fetch('/stop', {method: 'POST'})
                    .then(() => {
                        setTimeout(() => startDecoding(), 500);
                    });
            }
        }

        function refreshDevices() {
            fetch('/devices')
                .then(r => r.json())
                .then(devices => {
                    deviceList = devices;
                    const select = document.getElementById('deviceSelect');
                    if (devices.length === 0) {
                        select.innerHTML = '<option value="0">No devices found</option>';
                    } else {
                        select.innerHTML = devices.map(d =>
                            `<option value="${d.index}">${d.index}: ${d.name}</option>`
                        ).join('');
                    }
                });
        }

        function getSelectedDevice() {
            return document.getElementById('deviceSelect').value;
        }

        function getSelectedProtocols() {
            const protocols = [];
            if (document.getElementById('proto_pocsag512').checked) protocols.push('POCSAG512');
            if (document.getElementById('proto_pocsag1200').checked) protocols.push('POCSAG1200');
            if (document.getElementById('proto_pocsag2400').checked) protocols.push('POCSAG2400');
            if (document.getElementById('proto_flex').checked) protocols.push('FLEX');
            return protocols;
        }

        function startDecoding() {
            const freq = document.getElementById('frequency').value;
            const gain = document.getElementById('gain').value;
            const squelch = document.getElementById('squelch').value;
            const ppm = document.getElementById('ppm').value;
            const device = getSelectedDevice();
            const protocols = getSelectedProtocols();

            if (protocols.length === 0) {
                alert('Please select at least one protocol');
                return;
            }

            const config = {
                frequency: freq,
                gain: gain,
                squelch: squelch,
                ppm: ppm,
                device: device,
                protocols: protocols
            };

            fetch('/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(config)
            }).then(r => r.json())
              .then(data => {
                  if (data.status === 'started') {
                      setRunning(true);
                      startStream();
                  } else {
                      alert('Error: ' + data.message);
                  }
              })
              .catch(err => {
                  console.error('Start error:', err);
              });
        }

        function stopDecoding() {
            fetch('/stop', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    setRunning(false);
                    if (eventSource) {
                        eventSource.close();
                        eventSource = null;
                    }
                });
        }

        function killAll() {
            fetch('/killall', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    setRunning(false);
                    if (eventSource) {
                        eventSource.close();
                        eventSource = null;
                    }
                    showInfo('Killed all processes: ' + (data.processes.length ? data.processes.join(', ') : 'none running'));
                });
        }

        function checkStatus() {
            fetch('/status')
                .then(r => r.json())
                .then(data => {
                    if (data.running !== isRunning) {
                        setRunning(data.running);
                        if (data.running && !eventSource) {
                            startStream();
                        }
                    }
                });
        }

        // Periodic status check every 5 seconds
        setInterval(checkStatus, 5000);

        function toggleLogging() {
            const enabled = document.getElementById('loggingEnabled').checked;
            const logFile = document.getElementById('logFilePath').value;
            fetch('/logging', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled: enabled, log_file: logFile})
            }).then(r => r.json())
              .then(data => {
                  showInfo(data.logging ? 'Logging enabled: ' + data.log_file : 'Logging disabled');
              });
        }

        function setRunning(running) {
            isRunning = running;
            document.getElementById('statusDot').classList.toggle('running', running);
            document.getElementById('statusText').textContent = running ? 'Decoding...' : 'Idle';
            document.getElementById('startBtn').style.display = running ? 'none' : 'block';
            document.getElementById('stopBtn').style.display = running ? 'block' : 'none';
        }

        function startStream() {
            if (eventSource) {
                eventSource.close();
            }

            eventSource = new EventSource('/stream');

            eventSource.onopen = function() {
                showInfo('Stream connected...');
            };

            eventSource.onmessage = function(e) {
                const data = JSON.parse(e.data);

                if (data.type === 'message') {
                    addMessage(data);
                } else if (data.type === 'status') {
                    if (data.text === 'stopped') {
                        setRunning(false);
                    } else if (data.text === 'started') {
                        showInfo('Decoder started, waiting for signals...');
                    }
                } else if (data.type === 'info') {
                    showInfo(data.text);
                } else if (data.type === 'raw') {
                    showInfo(data.text);
                }
            };

            eventSource.onerror = function(e) {
                checkStatus();
            };
        }

        function addMessage(msg) {
            const output = document.getElementById('output');

            // Remove placeholder if present
            const placeholder = output.querySelector('.placeholder');
            if (placeholder) {
                placeholder.remove();
            }

            // Store message for export
            allMessages.push(msg);

            // Play audio alert
            playAlert();

            // Update signal meter
            pulseSignal();

            // Add to waterfall
            addWaterfallPoint(Date.now(), 0.8);

            msgCount++;
            document.getElementById('msgCount').textContent = msgCount;

            let protoClass = '';
            if (msg.protocol.includes('POCSAG')) {
                pocsagCount++;
                protoClass = 'pocsag';
                document.getElementById('pocsagCount').textContent = pocsagCount;
            } else if (msg.protocol.includes('FLEX')) {
                flexCount++;
                protoClass = 'flex';
                document.getElementById('flexCount').textContent = flexCount;
            }

            const isNumeric = /^[0-9\s\-\*\#U]+$/.test(msg.message);
            const relativeTime = getRelativeTime(msg.timestamp);

            const msgEl = document.createElement('div');
            msgEl.className = 'message ' + protoClass;
            msgEl.innerHTML = `
                <div class="header">
                    <span class="protocol">${msg.protocol}</span>
                    <span class="msg-time" data-timestamp="${msg.timestamp}" title="${msg.timestamp}">${relativeTime}</span>
                </div>
                <div class="address">Address: ${msg.address}${msg.function ? ' | Func: ' + msg.function : ''}</div>
                <div class="content ${isNumeric ? 'numeric' : ''}">${escapeHtml(msg.message)}</div>
            `;

            output.insertBefore(msgEl, output.firstChild);

            // Auto-scroll to top (newest messages)
            if (autoScroll) {
                output.scrollTop = 0;
            }

            // Limit messages displayed
            while (output.children.length > 100) {
                output.removeChild(output.lastChild);
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function showInfo(text) {
            const output = document.getElementById('output');

            // Clear placeholder only (has the 'placeholder' class)
            const placeholder = output.querySelector('.placeholder');
            if (placeholder) {
                placeholder.remove();
            }

            const infoEl = document.createElement('div');
            infoEl.className = 'info-msg';
            infoEl.style.cssText = 'padding: 12px 15px; margin-bottom: 8px; background: #0a0a0a; border: 1px solid #1a1a1a; border-left: 2px solid #00d4ff; font-family: "JetBrains Mono", monospace; font-size: 11px; color: #888; word-break: break-all;';
            infoEl.textContent = text;
            output.insertBefore(infoEl, output.firstChild);
        }

        function clearMessages() {
            document.getElementById('output').innerHTML = `
                <div class="placeholder" style="color: #888; text-align: center; padding: 50px;">
                    Messages cleared. ${isRunning || isSensorRunning ? 'Waiting for new messages...' : 'Start decoding to receive messages.'}
                </div>
            `;
            msgCount = 0;
            pocsagCount = 0;
            flexCount = 0;
            sensorCount = 0;
            uniqueDevices.clear();
            document.getElementById('msgCount').textContent = '0';
            document.getElementById('pocsagCount').textContent = '0';
            document.getElementById('flexCount').textContent = '0';
            document.getElementById('sensorCount').textContent = '0';
            document.getElementById('deviceCount').textContent = '0';
        }
    </script>
</body>
</html>
'''


def check_tool(name):
    """Check if a tool is installed."""
    return shutil.which(name) is not None


def detect_devices():
    """Detect RTL-SDR devices."""
    devices = []

    if not check_tool('rtl_test'):
        return devices

    try:
        result = subprocess.run(
            ['rtl_test', '-t'],
            capture_output=True,
            text=True,
            timeout=5
        )
        output = result.stderr + result.stdout

        # Parse device info
        device_pattern = r'(\d+):\s+(.+?)(?:,\s*SN:\s*(\S+))?$'

        for line in output.split('\n'):
            line = line.strip()
            match = re.match(device_pattern, line)
            if match:
                devices.append({
                    'index': int(match.group(1)),
                    'name': match.group(2).strip().rstrip(','),
                    'serial': match.group(3) or 'N/A'
                })

        if not devices:
            found_match = re.search(r'Found (\d+) device', output)
            if found_match:
                count = int(found_match.group(1))
                for i in range(count):
                    devices.append({
                        'index': i,
                        'name': f'RTL-SDR Device {i}',
                        'serial': 'Unknown'
                    })

    except Exception:
        pass

    return devices


def parse_multimon_output(line):
    """Parse multimon-ng output line."""
    # POCSAG formats:
    # POCSAG512: Address: 1234567  Function: 0  Alpha:   Message here
    # POCSAG1200: Address: 1234567  Function: 0  Numeric: 123-456-7890
    # POCSAG2400: Address: 1234567  Function: 0  (no message)
    # FLEX formats:
    # FLEX: NNNN-NN-NN NN:NN:NN NNNN/NN/C NN.NNN [NNNNNNN] ALN Message here
    # FLEX|NNNN-NN-NN|NN:NN:NN|NNNN/NN/C|NN.NNN|NNNNNNN|ALN|Message

    line = line.strip()

    # POCSAG parsing - with message content
    pocsag_match = re.match(
        r'(POCSAG\d+):\s*Address:\s*(\d+)\s+Function:\s*(\d+)\s+(Alpha|Numeric):\s*(.*)',
        line
    )
    if pocsag_match:
        return {
            'protocol': pocsag_match.group(1),
            'address': pocsag_match.group(2),
            'function': pocsag_match.group(3),
            'msg_type': pocsag_match.group(4),
            'message': pocsag_match.group(5).strip() or '[No Message]'
        }

    # POCSAG parsing - address only (no message content)
    pocsag_addr_match = re.match(
        r'(POCSAG\d+):\s*Address:\s*(\d+)\s+Function:\s*(\d+)\s*$',
        line
    )
    if pocsag_addr_match:
        return {
            'protocol': pocsag_addr_match.group(1),
            'address': pocsag_addr_match.group(2),
            'function': pocsag_addr_match.group(3),
            'msg_type': 'Tone',
            'message': '[Tone Only]'
        }

    # FLEX parsing (standard format)
    flex_match = re.match(
        r'FLEX[:\|]\s*[\d\-]+[\s\|]+[\d:]+[\s\|]+([\d/A-Z]+)[\s\|]+([\d.]+)[\s\|]+\[?(\d+)\]?[\s\|]+(\w+)[\s\|]+(.*)',
        line
    )
    if flex_match:
        return {
            'protocol': 'FLEX',
            'address': flex_match.group(3),
            'function': flex_match.group(1),
            'msg_type': flex_match.group(4),
            'message': flex_match.group(5).strip() or '[No Message]'
        }

    # Simple FLEX format
    flex_simple = re.match(r'FLEX:\s*(.+)', line)
    if flex_simple:
        return {
            'protocol': 'FLEX',
            'address': 'Unknown',
            'function': '',
            'msg_type': 'Unknown',
            'message': flex_simple.group(1).strip()
        }

    return None


def stream_decoder(master_fd, process):
    """Stream decoder output to queue using PTY for unbuffered output."""
    global current_process

    try:
        output_queue.put({'type': 'status', 'text': 'started'})

        buffer = ""
        while True:
            try:
                ready, _, _ = select.select([master_fd], [], [], 1.0)
            except Exception:
                break

            if ready:
                try:
                    data = os.read(master_fd, 1024)
                    if not data:
                        break
                    buffer += data.decode('utf-8', errors='replace')

                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue

                        parsed = parse_multimon_output(line)
                        if parsed:
                            from datetime import datetime
                            parsed['timestamp'] = datetime.now().strftime('%H:%M:%S')
                            output_queue.put({'type': 'message', **parsed})
                            log_message(parsed)
                        else:
                            output_queue.put({'type': 'raw', 'text': line})
                except OSError:
                    break

            if process.poll() is not None:
                break

    except Exception as e:
        output_queue.put({'type': 'error', 'text': str(e)})
    finally:
        try:
            os.close(master_fd)
        except:
            pass
        process.wait()
        output_queue.put({'type': 'status', 'text': 'stopped'})
        with process_lock:
            current_process = None


@app.route('/')
def index():
    tools = {
        'rtl_fm': check_tool('rtl_fm'),
        'multimon': check_tool('multimon-ng'),
        'rtl_433': check_tool('rtl_433')
    }
    devices = detect_devices()
    return render_template_string(HTML_TEMPLATE, tools=tools, devices=devices)


@app.route('/favicon.svg')
def favicon():
    return send_file('favicon.svg', mimetype='image/svg+xml')


@app.route('/devices')
def get_devices():
    return jsonify(detect_devices())


@app.route('/start', methods=['POST'])
def start_decoding():
    global current_process

    with process_lock:
        if current_process:
            return jsonify({'status': 'error', 'message': 'Already running'})

        data = request.json
        freq = data.get('frequency', '929.6125')
        gain = data.get('gain', '0')
        squelch = data.get('squelch', '0')
        ppm = data.get('ppm', '0')
        device = data.get('device', '0')
        protocols = data.get('protocols', ['POCSAG512', 'POCSAG1200', 'POCSAG2400', 'FLEX'])

        # Clear queue
        while not output_queue.empty():
            try:
                output_queue.get_nowait()
            except:
                break

        # Build multimon-ng decoder arguments
        decoders = []
        for proto in protocols:
            if proto == 'POCSAG512':
                decoders.extend(['-a', 'POCSAG512'])
            elif proto == 'POCSAG1200':
                decoders.extend(['-a', 'POCSAG1200'])
            elif proto == 'POCSAG2400':
                decoders.extend(['-a', 'POCSAG2400'])
            elif proto == 'FLEX':
                decoders.extend(['-a', 'FLEX'])

        # Build rtl_fm command
        # rtl_fm -d <device> -f <freq>M -M fm -s 22050 -g <gain> -p <ppm> -l <squelch> - | multimon-ng -t raw -a POCSAG512 -a POCSAG1200 -a FLEX -f alpha -
        rtl_cmd = [
            'rtl_fm',
            '-d', str(device),
            '-f', f'{freq}M',
            '-M', 'fm',
            '-s', '22050',
        ]

        if gain and gain != '0':
            rtl_cmd.extend(['-g', str(gain)])

        if ppm and ppm != '0':
            rtl_cmd.extend(['-p', str(ppm)])

        if squelch and squelch != '0':
            rtl_cmd.extend(['-l', str(squelch)])

        rtl_cmd.append('-')

        multimon_cmd = ['multimon-ng', '-t', 'raw'] + decoders + ['-f', 'alpha', '-']

        # Log the command being run
        full_cmd = ' '.join(rtl_cmd) + ' | ' + ' '.join(multimon_cmd)
        print(f"Running: {full_cmd}")

        try:
            # Create pipe: rtl_fm | multimon-ng
            # Use PTY for multimon-ng to get unbuffered output
            rtl_process = subprocess.Popen(
                rtl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Start a thread to monitor rtl_fm stderr for errors
            def monitor_rtl_stderr():
                for line in rtl_process.stderr:
                    err_text = line.decode('utf-8', errors='replace').strip()
                    if err_text:
                        print(f"[RTL_FM] {err_text}", flush=True)
                        output_queue.put({'type': 'raw', 'text': f'[rtl_fm] {err_text}'})

            rtl_stderr_thread = threading.Thread(target=monitor_rtl_stderr)
            rtl_stderr_thread.daemon = True
            rtl_stderr_thread.start()

            # Create a pseudo-terminal for multimon-ng output
            # This tricks it into thinking it's connected to a terminal,
            # which disables output buffering
            master_fd, slave_fd = pty.openpty()

            multimon_process = subprocess.Popen(
                multimon_cmd,
                stdin=rtl_process.stdout,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True
            )

            os.close(slave_fd)  # Close slave fd in parent process
            rtl_process.stdout.close()  # Allow rtl_process to receive SIGPIPE

            current_process = multimon_process
            current_process._rtl_process = rtl_process  # Store reference to kill later
            current_process._master_fd = master_fd  # Store for cleanup

            # Start output thread with PTY master fd
            thread = threading.Thread(target=stream_decoder, args=(master_fd, multimon_process))
            thread.daemon = True
            thread.start()

            # Send the command info to the client
            output_queue.put({'type': 'info', 'text': f'Command: {full_cmd}'})

            return jsonify({'status': 'started', 'command': full_cmd})

        except FileNotFoundError as e:
            return jsonify({'status': 'error', 'message': f'Tool not found: {e.filename}'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@app.route('/stop', methods=['POST'])
def stop_decoding():
    global current_process

    with process_lock:
        if current_process:
            # Kill rtl_fm process first
            if hasattr(current_process, '_rtl_process'):
                try:
                    current_process._rtl_process.terminate()
                    current_process._rtl_process.wait(timeout=2)
                except:
                    try:
                        current_process._rtl_process.kill()
                    except:
                        pass

            # Close PTY master fd
            if hasattr(current_process, '_master_fd'):
                try:
                    os.close(current_process._master_fd)
                except:
                    pass

            # Kill multimon-ng
            current_process.terminate()
            try:
                current_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                current_process.kill()

            current_process = None
            return jsonify({'status': 'stopped'})

        return jsonify({'status': 'not_running'})


@app.route('/status')
def get_status():
    """Check if decoder is currently running."""
    with process_lock:
        if current_process and current_process.poll() is None:
            return jsonify({'running': True, 'logging': logging_enabled, 'log_file': log_file_path})
        return jsonify({'running': False, 'logging': logging_enabled, 'log_file': log_file_path})


@app.route('/logging', methods=['POST'])
def toggle_logging():
    """Toggle message logging."""
    global logging_enabled, log_file_path
    data = request.json
    if 'enabled' in data:
        logging_enabled = data['enabled']
    if 'log_file' in data and data['log_file']:
        log_file_path = data['log_file']
    return jsonify({'logging': logging_enabled, 'log_file': log_file_path})


def log_message(msg):
    """Log a message to file if logging is enabled."""
    if not logging_enabled:
        return
    try:
        with open(log_file_path, 'a') as f:
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            f.write(f"{timestamp} | {msg.get('protocol', 'UNKNOWN')} | {msg.get('address', '')} | {msg.get('message', '')}\n")
    except Exception as e:
        print(f"[ERROR] Failed to log message: {e}", flush=True)


@app.route('/killall', methods=['POST'])
def kill_all():
    """Kill all rtl_fm, multimon-ng, and rtl_433 processes."""
    global current_process, sensor_process

    killed = []
    try:
        result = subprocess.run(['pkill', '-f', 'rtl_fm'], capture_output=True)
        if result.returncode == 0:
            killed.append('rtl_fm')
    except:
        pass

    try:
        result = subprocess.run(['pkill', '-f', 'multimon-ng'], capture_output=True)
        if result.returncode == 0:
            killed.append('multimon-ng')
    except:
        pass

    try:
        result = subprocess.run(['pkill', '-f', 'rtl_433'], capture_output=True)
        if result.returncode == 0:
            killed.append('rtl_433')
    except:
        pass

    with process_lock:
        current_process = None

    with sensor_lock:
        sensor_process = None

    return jsonify({'status': 'killed', 'processes': killed})


@app.route('/stream')
def stream():
    def generate():
        import json
        while True:
            try:
                msg = output_queue.get(timeout=1)
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


# ============== RTL_433 SENSOR ROUTES ==============

def stream_sensor_output(process):
    """Stream rtl_433 JSON output to queue."""
    global sensor_process
    import json as json_module

    try:
        sensor_queue.put({'type': 'status', 'text': 'started'})

        for line in iter(process.stdout.readline, b''):
            line = line.decode('utf-8', errors='replace').strip()
            if not line:
                continue

            try:
                # rtl_433 outputs JSON objects, one per line
                data = json_module.loads(line)
                data['type'] = 'sensor'
                sensor_queue.put(data)

                # Log if enabled
                if logging_enabled:
                    try:
                        with open(log_file_path, 'a') as f:
                            from datetime import datetime
                            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            f.write(f"{timestamp} | {data.get('model', 'Unknown')} | {json_module.dumps(data)}\n")
                    except Exception:
                        pass
            except json_module.JSONDecodeError:
                # Not JSON, send as raw
                sensor_queue.put({'type': 'raw', 'text': line})

    except Exception as e:
        sensor_queue.put({'type': 'error', 'text': str(e)})
    finally:
        process.wait()
        sensor_queue.put({'type': 'status', 'text': 'stopped'})
        with sensor_lock:
            sensor_process = None


@app.route('/start_sensor', methods=['POST'])
def start_sensor():
    global sensor_process

    with sensor_lock:
        if sensor_process:
            return jsonify({'status': 'error', 'message': 'Sensor already running'})

        data = request.json
        freq = data.get('frequency', '433.92')
        gain = data.get('gain', '0')
        ppm = data.get('ppm', '0')
        device = data.get('device', '0')

        # Clear queue
        while not sensor_queue.empty():
            try:
                sensor_queue.get_nowait()
            except:
                break

        # Build rtl_433 command
        # rtl_433 -d <device> -f <freq>M -g <gain> -p <ppm> -F json
        cmd = [
            'rtl_433',
            '-d', str(device),
            '-f', f'{freq}M',
            '-F', 'json'
        ]

        if gain and gain != '0':
            cmd.extend(['-g', str(gain)])

        if ppm and ppm != '0':
            cmd.extend(['-p', str(ppm)])

        full_cmd = ' '.join(cmd)
        print(f"Running: {full_cmd}")

        try:
            sensor_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1
            )

            # Start output thread
            thread = threading.Thread(target=stream_sensor_output, args=(sensor_process,))
            thread.daemon = True
            thread.start()

            # Monitor stderr
            def monitor_stderr():
                for line in sensor_process.stderr:
                    err = line.decode('utf-8', errors='replace').strip()
                    if err:
                        print(f"[rtl_433] {err}")
                        sensor_queue.put({'type': 'info', 'text': f'[rtl_433] {err}'})

            stderr_thread = threading.Thread(target=monitor_stderr)
            stderr_thread.daemon = True
            stderr_thread.start()

            sensor_queue.put({'type': 'info', 'text': f'Command: {full_cmd}'})

            return jsonify({'status': 'started', 'command': full_cmd})

        except FileNotFoundError:
            return jsonify({'status': 'error', 'message': 'rtl_433 not found. Install with: brew install rtl_433'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)})


@app.route('/stop_sensor', methods=['POST'])
def stop_sensor():
    global sensor_process

    with sensor_lock:
        if sensor_process:
            sensor_process.terminate()
            try:
                sensor_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                sensor_process.kill()
            sensor_process = None
            return jsonify({'status': 'stopped'})

        return jsonify({'status': 'not_running'})


@app.route('/stream_sensor')
def stream_sensor():
    def generate():
        import json
        while True:
            try:
                msg = sensor_queue.get(timeout=1)
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response


def main():
    print("=" * 50)
    print("  INTERCEPT // Signal Intelligence")
    print("  POCSAG / FLEX / 433MHz Sensors")
    print("=" * 50)
    print()
    print("Open http://localhost:5050 in your browser")
    print()
    print("Press Ctrl+C to stop")
    print()

    app.run(host='0.0.0.0', port=5050, debug=False, threaded=True)


if __name__ == '__main__':
    main()
