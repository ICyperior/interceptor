# INTERCEPT - Signal Intelligence Platform
# Docker container for running the web interface

FROM python:3.11-slim

LABEL maintainer="INTERCEPT Project"
LABEL description="Signal Intelligence Platform for SDR monitoring"

# Set working directory
WORKDIR /app

# Install system dependencies for SDR tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    # RTL-SDR tools
    rtl-sdr \
    librtlsdr-dev \
    libusb-1.0-0-dev \
    # 433MHz decoder
    rtl-433 \
    # Pager decoder
    multimon-ng \
    # Audio tools for Listening Post
    sox \
    libsox-fmt-all \
    # WiFi tools (aircrack-ng suite)
    aircrack-ng \
    iw \
    wireless-tools \
    # Bluetooth tools
    bluez \
    bluetooth \
    # GPS support
    gpsd-clients \
    # Utilities
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Install dump1090 for ADS-B (package name varies by distribution)
RUN apt-get update && \
    (apt-get install -y --no-install-recommends dump1090-mutability || \
     apt-get install -y --no-install-recommends dump1090-fa || \
     apt-get install -y --no-install-recommends dump1090 || \
     echo "Note: dump1090 not available in repos, ADS-B features limited") && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory for persistence
RUN mkdir -p /app/data

# Expose web interface port
EXPOSE 5050

# Environment variables with defaults
ENV INTERCEPT_HOST=0.0.0.0 \
    INTERCEPT_PORT=5050 \
    INTERCEPT_LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

# Health check using the new endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost:5050/health || exit 1

# Run the application
CMD ["python", "intercept.py"]
