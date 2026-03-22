#!/bin/bash

# scan_and_block.sh - Active background scanner that blocks targets as soon as they appear
# Usage: ./scan_and_block.sh <filename> <duration_seconds>

FILE=$1
DURATION=${2:-60} # Default to 60 seconds if not specified

if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
    echo "Usage: $0 <filename_with_macs> [duration_seconds]"
    exit 1
fi

echo "--- Starting Active Block Mode ($DURATION seconds) ---"
echo "Press Ctrl+C to stop early."

# 1. Start scanning in the background (using a subshell to keep it alive)
(while true; do bluetoothctl scan on; sleep 1; done) > /dev/null 2>&1 &
SCAN_PID=$!

# 2. Function to cleanup on exit
cleanup() {
    echo -e "\n--- Stopping Active Block Mode ---"
    # Kill the subshell and any bluetoothctl children
    pkill -P $SCAN_PID
    kill $SCAN_PID
    exit
}
trap cleanup SIGINT SIGTERM

# 3. Active Loop: Try to block the list repeatedly
START_TIME=$(date +%s)
while true; do
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))
    
    if [ $ELAPSED -ge $DURATION ]; then
        break
    fi

    echo -ne "Scanning... [${ELAPSED}/${DURATION}s] \r"

    while IFS= read -r mac || [ -n "$mac" ]; do
        mac=$(echo "$mac" | xargs | tr 'a-z' 'A-Z')
        [[ -z "$mac" || "$mac" =~ ^# ]] && continue

        # Check if already blocked in this session's success list to avoid noise
        if ! grep -q "$mac" /tmp/blocked_success.tmp 2>/dev/null; then
            output=$(echo "block $mac" | bluetoothctl 2>&1)
            if echo "$output" | grep -q "succeeded"; then
                echo -e "\n[SUCCESS] Blocked $mac"
                echo "$mac" >> /tmp/blocked_success.tmp
            fi
        fi
    done < "$FILE"
    
    sleep 2 # Check every 2 seconds
done

rm -f /tmp/blocked_success.tmp
cleanup
