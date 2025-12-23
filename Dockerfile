# INTERCEPT - Signal Intelligence Platform
# Docker container for running the web interface

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for RTL-SDR tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    # RTL-SDR tools
    rtl-sdr \
    # 433MHz decoder
    rtl-433 \
    # Pager decoder
    multimon-ng \
    # WiFi tools (aircrack-ng suite)
    aircrack-ng \
    # Bluetooth tools
    bluez \
    # Cleanup
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose web interface port
EXPOSE 5050

# Environment variables with defaults
ENV INTERCEPT_HOST=0.0.0.0 \
    INTERCEPT_PORT=5050 \
    INTERCEPT_LOG_LEVEL=INFO

# Run the application
CMD ["python", "intercept.py"]
