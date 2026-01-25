#!/usr/bin/env python3
"""
Local test script to simulate AIS-catcher TCP JSON output.
This helps verify the AIS parsing and vessel display without real hardware.

Usage:
  Terminal 1: python test_ais_local.py --server   (starts mock AIS-catcher)
  Terminal 2: sudo -E venv/bin/python intercept.py  (start the app)
  Then click "Start Tracking" in the AIS page - it should show test vessels
"""

import argparse
import json
import socket
import time
import random
import threading


# Sample vessel data mimicking AIS-catcher JSON_FULL output
# Uses 'latitude'/'longitude' as per AIS-catcher JSON_FULL format
SAMPLE_VESSELS = [
    {
        "mmsi": 316039000,
        "shipname": "ATLANTIC EAGLE",
        "callsign": "CFG4521",
        "shiptype": 70,
        "shiptype_text": "Cargo",
        "latitude": 45.5017,
        "longitude": -73.5673,
        "speed": 12.3,
        "course": 45.0,
        "heading": 47,
        "status": 0,
        "status_text": "Under way using engine",
        "destination": "MONTREAL",
        "to_bow": 150,
        "to_stern": 30,
        "to_port": 15,
        "to_starboard": 15,
        "type": 1
    },
    {
        "mmsi": 316007861,
        "shipname": "PACIFIC STAR",
        "callsign": "CFG9912",
        "shiptype": 60,
        "shiptype_text": "Passenger",
        "latitude": 45.4817,
        "longitude": -73.5873,
        "speed": 8.5,
        "course": 270.0,
        "heading": 268,
        "status": 0,
        "status_text": "Under way using engine",
        "destination": "QUEBEC CITY",
        "to_bow": 200,
        "to_stern": 50,
        "to_port": 20,
        "to_starboard": 20,
        "type": 1
    },
    {
        "mmsi": 316001103,
        "shipname": "RIVER QUEEN",
        "callsign": "CFG1234",
        "shiptype": 52,
        "shiptype_text": "Tug",
        "latitude": 45.5117,
        "longitude": -73.5473,
        "speed": 5.2,
        "course": 180.0,
        "heading": 182,
        "status": 0,
        "status_text": "Under way using engine",
        "destination": "SOREL",
        "to_bow": 25,
        "to_stern": 10,
        "to_port": 5,
        "to_starboard": 5,
        "type": 1
    },
]


def update_vessel_position(vessel):
    """Simulate vessel movement."""
    # Small random movement
    vessel["latitude"] += random.uniform(-0.001, 0.001)
    vessel["longitude"] += random.uniform(-0.001, 0.001)
    # Small speed variation
    vessel["speed"] = max(0, vessel["speed"] + random.uniform(-0.5, 0.5))
    # Slight course change
    vessel["course"] = (vessel["course"] + random.uniform(-2, 2)) % 360
    vessel["heading"] = int(vessel["course"]) % 360
    return vessel


def mock_ais_server(port=10110):
    """Run a mock AIS-catcher TCP server sending JSON."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('localhost', port))
    server.listen(5)
    print(f"Mock AIS-catcher TCP server running on port {port}")
    print(f"Sending JSON format (like 'AIS-catcher -S {port} JSON')")
    print("Waiting for connections...")

    clients = []

    def handle_client(client_sock, addr):
        print(f"Client connected: {addr}")
        clients.append(client_sock)
        try:
            while True:
                # Keep connection alive, actual sending is done in broadcast
                time.sleep(1)
        except Exception as e:
            print(f"Client {addr} disconnected: {e}")
        finally:
            if client_sock in clients:
                clients.remove(client_sock)
            client_sock.close()

    def broadcast_vessels():
        """Periodically send vessel updates to all clients."""
        vessels = [v.copy() for v in SAMPLE_VESSELS]
        while True:
            for vessel in vessels:
                vessel = update_vessel_position(vessel)
                json_line = json.dumps(vessel) + "\n"

                dead_clients = []
                for client in clients:
                    try:
                        client.send(json_line.encode('utf-8'))
                    except Exception:
                        dead_clients.append(client)

                for client in dead_clients:
                    clients.remove(client)

                if clients:
                    print(f"Sent: MMSI {vessel['mmsi']} @ ({vessel['latitude']:.4f}, {vessel['longitude']:.4f})")

            time.sleep(2)  # Send updates every 2 seconds

    # Start broadcast thread
    broadcast_thread = threading.Thread(target=broadcast_vessels, daemon=True)
    broadcast_thread.start()

    # Accept connections
    while True:
        try:
            client_sock, addr = server.accept()
            thread = threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True)
            thread.start()
        except KeyboardInterrupt:
            print("\nShutting down...")
            break


def test_parse_json():
    """Test that our JSON matches what the parser expects."""
    # Import the parser
    import sys
    sys.path.insert(0, '/opt/intercept')
    from routes.ais import process_ais_message

    print("Testing JSON parsing...")
    for vessel_data in SAMPLE_VESSELS:
        result = process_ais_message(vessel_data)
        if result:
            print(f"  MMSI {result['mmsi']}: {result.get('name', 'Unknown')} @ ({result.get('lat')}, {result.get('lon')})")
            assert result.get('lat') is not None, "lat should be set"
            assert result.get('lon') is not None, "lon should be set"
            assert result.get('name') is not None, "name should be set"
        else:
            print(f"  FAILED to parse: {vessel_data}")
    print("All JSON parsing tests passed!")


def test_tcp_client():
    """Test connecting to the mock server as a client."""
    print("Connecting to mock AIS server on localhost:10110...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)

    try:
        sock.connect(('localhost', 10110))
        print("Connected! Receiving data...")

        buffer = ""
        received = 0
        while received < 5:
            data = sock.recv(4096).decode('utf-8')
            if not data:
                break
            buffer += data

            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                line = line.strip()
                if line:
                    try:
                        msg = json.loads(line)
                        print(f"  Received: MMSI {msg.get('mmsi')} - {msg.get('shipname')}")
                        received += 1
                    except json.JSONDecodeError as e:
                        print(f"  JSON ERROR: {e}")
                        print(f"  Line was: {line[:100]}")

        print(f"Successfully received {received} vessel updates!")
    except socket.timeout:
        print("Connection timed out - is the mock server running?")
    except ConnectionRefusedError:
        print("Connection refused - start the mock server first with: python test_ais_local.py --server")
    finally:
        sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test AIS functionality locally")
    parser.add_argument("--server", action="store_true", help="Run mock AIS-catcher TCP server")
    parser.add_argument("--client", action="store_true", help="Test TCP client connection")
    parser.add_argument("--parse", action="store_true", help="Test JSON parsing")
    parser.add_argument("--port", type=int, default=10110, help="TCP port (default: 10110)")

    args = parser.parse_args()

    if args.server:
        mock_ais_server(args.port)
    elif args.client:
        test_tcp_client()
    elif args.parse:
        test_parse_json()
    else:
        print("Usage:")
        print("  python test_ais_local.py --server   # Start mock AIS-catcher")
        print("  python test_ais_local.py --client   # Test client connection")
        print("  python test_ais_local.py --parse    # Test JSON parsing")
        print()
        print("Full test workflow:")
        print("  1. Terminal 1: python test_ais_local.py --server")
        print("  2. Terminal 2: python test_ais_local.py --client  (verify mock works)")
        print("  3. Terminal 2: sudo -E venv/bin/python intercept.py")
        print("  4. Browser: Open AIS page and click 'Start Tracking'")
        print("  5. Vessels should appear on the map!")
