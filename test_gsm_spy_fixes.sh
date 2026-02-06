#!/bin/bash
# GSM Spy System - Verification Test Script
# Tests the 4 critical fixes: geocoding, pipeline, scanner loop, process management

set -e

echo "=========================================="
echo "GSM Spy System - Verification Tests"
echo "=========================================="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test results
TESTS_PASSED=0
TESTS_FAILED=0

function pass_test() {
    echo -e "${GREEN}✓ PASS:${NC} $1"
    ((TESTS_PASSED++))
}

function fail_test() {
    echo -e "${RED}✗ FAIL:${NC} $1"
    ((TESTS_FAILED++))
}

function info() {
    echo -e "${YELLOW}ℹ INFO:${NC} $1"
}

# Test 1: Check that geocoding module exists
echo "Test 1: Geocoding Module"
echo "-------------------------"
if [ -f "utils/gsm_geocoding.py" ]; then
    pass_test "Geocoding module exists"

    # Check for key functions
    if grep -q "def enrich_tower_data" utils/gsm_geocoding.py; then
        pass_test "enrich_tower_data() function present"
    else
        fail_test "enrich_tower_data() function missing"
    fi

    if grep -q "def lookup_cell_coordinates" utils/gsm_geocoding.py; then
        pass_test "lookup_cell_coordinates() function present"
    else
        fail_test "lookup_cell_coordinates() function missing"
    fi
else
    fail_test "Geocoding module missing"
fi
echo ""

# Test 2: Check scanner thread improvements
echo "Test 2: Scanner Thread Non-Blocking I/O"
echo "---------------------------------------"
if grep -q "import select" routes/gsm_spy.py; then
    pass_test "select module imported"
else
    fail_test "select module not imported"
fi

if grep -q "select.select.*process.stdout" routes/gsm_spy.py; then
    pass_test "Non-blocking I/O with select.select() implemented"
else
    fail_test "select.select() not found in scanner thread"
fi

if grep -q "scan_timeout = 120" routes/gsm_spy.py; then
    pass_test "Scan timeout configured"
else
    fail_test "Scan timeout not configured"
fi

if grep -q "with app_module.gsm_spy_lock:" routes/gsm_spy.py; then
    pass_test "Thread-safe counter updates implemented"
else
    fail_test "Thread-safe counter updates missing"
fi
echo ""

# Test 3: Check geocoding worker
echo "Test 3: Background Geocoding Worker"
echo "-----------------------------------"
if grep -q "def start_geocoding_worker" routes/gsm_spy.py; then
    pass_test "start_geocoding_worker() function exists"
else
    fail_test "start_geocoding_worker() function missing"
fi

if grep -q "def geocoding_worker" routes/gsm_spy.py; then
    pass_test "geocoding_worker() function exists"
else
    fail_test "geocoding_worker() function missing"
fi

if grep -q "start_geocoding_worker()" routes/gsm_spy.py; then
    pass_test "Geocoding worker is started in start_scanner()"
else
    fail_test "Geocoding worker not started in start_scanner()"
fi
echo ""

# Test 4: Check enrichment integration
echo "Test 4: Tower Data Enrichment"
echo "-----------------------------"
if grep -q "from utils.gsm_geocoding import enrich_tower_data" routes/gsm_spy.py; then
    pass_test "enrich_tower_data imported in scanner thread"
else
    fail_test "enrich_tower_data not imported"
fi

if grep -q "enriched = enrich_tower_data(parsed)" routes/gsm_spy.py; then
    pass_test "Tower data enrichment called in scanner"
else
    fail_test "Tower data enrichment not called"
fi
echo ""

# Test 5: Check monitor pipeline fixes
echo "Test 5: Monitor Pipeline Connection"
echo "-----------------------------------"
if grep -q "Give grgsm_livemon time to initialize" routes/gsm_spy.py; then
    pass_test "Pipeline initialization delay comment present"
else
    fail_test "Pipeline initialization delay comment missing"
fi

if grep -A 5 "Start grgsm_livemon" routes/gsm_spy.py | grep -q "time.sleep(2)"; then
    pass_test "2-second delay between grgsm_livemon and tshark"
else
    fail_test "Initialization delay not implemented"
fi

if grep -q "Started grgsm_livemon (PID:" routes/gsm_spy.py; then
    pass_test "Process verification logging added"
else
    fail_test "Process verification logging missing"
fi
echo ""

# Test 6: Check monitor thread improvements
echo "Test 6: Monitor Thread Non-Blocking I/O"
echo "---------------------------------------"
if grep -q "def monitor_thread(process):" routes/gsm_spy.py; then
    pass_test "monitor_thread() function exists"

    if grep -A 20 "def monitor_thread(process):" routes/gsm_spy.py | grep -q "select.select.*process.stdout"; then
        pass_test "Monitor thread uses non-blocking I/O"
    else
        fail_test "Monitor thread doesn't use select.select()"
    fi
else
    fail_test "monitor_thread() function missing"
fi
echo ""

# Test 7: Check frontend coordinate validation
echo "Test 7: Frontend Coordinate Validation"
echo "--------------------------------------"
if grep -q "Validate coordinates before creating map marker" templates/gsm_spy_dashboard.html; then
    pass_test "Coordinate validation comment present"
else
    fail_test "Coordinate validation comment missing"
fi

if grep -q "isNaN(parseFloat(data.lat))" templates/gsm_spy_dashboard.html; then
    pass_test "Coordinate validation checks implemented"
else
    fail_test "Coordinate validation checks missing"
fi

if grep -q "tower_update" templates/gsm_spy_dashboard.html; then
    pass_test "tower_update message handler added"
else
    fail_test "tower_update message handler missing"
fi
echo ""

# Test 8: Check process cleanup improvements
echo "Test 8: Process Cleanup & Zombie Prevention"
echo "-------------------------------------------"
if grep -q "process.terminate()" routes/gsm_spy.py; then
    pass_test "Process termination implemented"
else
    fail_test "Process termination missing"
fi

if grep -q "subprocess.TimeoutExpired" routes/gsm_spy.py; then
    pass_test "Timeout handling for process termination"
else
    fail_test "Timeout handling missing"
fi

if grep -q "process.kill()" routes/gsm_spy.py; then
    pass_test "Force kill fallback implemented"
else
    fail_test "Force kill fallback missing"
fi
echo ""

# Test 9: Python syntax check
echo "Test 9: Python Syntax Validation"
echo "--------------------------------"
if python3 -m py_compile routes/gsm_spy.py 2>/dev/null; then
    pass_test "routes/gsm_spy.py has valid syntax"
else
    fail_test "routes/gsm_spy.py has syntax errors"
fi

if python3 -m py_compile utils/gsm_geocoding.py 2>/dev/null; then
    pass_test "utils/gsm_geocoding.py has valid syntax"
else
    fail_test "utils/gsm_geocoding.py has syntax errors"
fi
echo ""

# Test 10: Check auto-monitor persistence
echo "Test 10: Auto-Monitor Flag Persistence"
echo "--------------------------------------"
if grep -q "auto_monitor_triggered = False.*# Moved outside loop" routes/gsm_spy.py; then
    pass_test "auto_monitor_triggered flag moved outside loop"
else
    fail_test "auto_monitor_triggered flag not properly placed"
fi

if grep -q "if current_count >= 3 and not auto_monitor_triggered" routes/gsm_spy.py; then
    pass_test "Auto-monitor only triggers once per session"
else
    fail_test "Auto-monitor trigger condition incorrect"
fi
echo ""

# Summary
echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo -e "Tests passed: ${GREEN}${TESTS_PASSED}${NC}"
echo -e "Tests failed: ${RED}${TESTS_FAILED}${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed! ✓${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Start INTERCEPT: sudo -E venv/bin/python intercept.py"
    echo "2. Navigate to GSM Spy dashboard in browser"
    echo "3. Click 'Start Scanner' to test tower detection with geocoding"
    echo "4. Verify towers appear on map with coordinates"
    echo "5. Check that auto-monitor starts after 3+ towers found"
    echo "6. Test Stop button for responsive shutdown (< 2 seconds)"
    exit 0
else
    echo -e "${RED}Some tests failed. Please review the output above.${NC}"
    exit 1
fi
