import asyncio
import json
import time
import urllib.request
import os
import sys
from bleak import BleakScanner

CONFIG_FILE = "config.json"
GOVEE_MANUFACTURER_ID = 61320

# Global state to track sensor status
# mac_address -> is_open (bool)
sensors_current_state = {}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: {CONFIG_FILE} not found. Please create it.")
        sys.exit(1)
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error parsing {CONFIG_FILE}: {e}")
        sys.exit(1)

def send_notification(channel_id, message):
    url = f"https://ntfy.sh/{channel_id}"
    try:
        req = urllib.request.Request(url, data=message.encode('utf-8'), method='POST')
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Notification sent to {channel_id}: {message}")
            else:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Failed to send notification, status: {response.status}")
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error sending notification: {e}")

def handle_detection(device, advertisement_data, monitored_macs):
    try:
        mac = device.address.upper()
        if GOVEE_MANUFACTURER_ID in advertisement_data.manufacturer_data:
            if mac in monitored_macs:
                data = advertisement_data.manufacturer_data[GOVEE_MANUFACTURER_ID]
                if len(data) >= 4:
                    is_open = (data[3] == 0xd7)
                    sensors_current_state[mac] = is_open
            else:
                # Log once in a while or just once per unknown sensor to help user find the right MAC
                if not hasattr(handle_detection, "_seen_unknown"):
                    handle_detection._seen_unknown = set()
                if mac not in handle_detection._seen_unknown:
                    print(f"[{time.strftime('%H:%M:%S')}] Info: Detected Govee sensor {mac} (Not in config.json)")
                    handle_detection._seen_unknown.add(mac)
    except Exception as e:
        print(f"Error in detection callback: {e}")

async def main():
    config = load_config()
    sensors = [mac.strip().upper() for mac in config.get("sensors", [])]
    interval = config.get("polling_interval_seconds", 1.0)
    channel_id = config.get("ntfy_channel_id")
    threshold_minutes = config.get("door_open_threshold_minutes", 10.0)
    repeat_interval = config.get("notification_repeat_interval_seconds", 60.0)
    
    threshold_seconds = threshold_minutes * 60

    if not channel_id:
        print("Error: 'ntfy_channel_id' must be specified in config.")
        sys.exit(1)
        
    if not sensors:
        print("Warning: No sensors specified in config. Application will run but do nothing.")
        
    print(f"Starting door monitor for {len(sensors)} sensors.")
    print(f"Monitored MACs: {', '.join(sensors)}")
    print(f"Check interval: {interval} seconds.")
    print(f"Notification threshold: {threshold_minutes} minutes.")
    print(f"Notification channel: {channel_id}")

    # State tracking for notifications
    # mac_address -> timestamp of when it was first detected open
    open_sensors_start_time = {}
    
    # Track when we last sent a notification to avoid spamming
    last_notification_time = {}

    monitored_macs = set(sensors)

    # Start the scanner
    scanner = BleakScanner(
        detection_callback=lambda d, a: handle_detection(d, a, monitored_macs),
        scanning_mode="active"
    )

    await scanner.start()
    print("Scanner started. Waiting for advertisements...")

    # Track if we have already printed a 'waiting' message to avoid spam
    waiting_message_shown = set()

    try:
        while True:
            now = time.time()
            for mac in sensors:
                is_open = sensors_current_state.get(mac)
                
                if is_open is None:
                    # Sensor not yet detected in advertisements
                    if mac not in waiting_message_shown:
                        print(f"[{time.strftime('%H:%M:%S')}] Waiting for first advertisement from {mac}...")
                        waiting_message_shown.add(mac)
                    continue
                
                # If we were waiting, we got it now
                if mac in waiting_message_shown:
                    waiting_message_shown.remove(mac)

                if is_open is True:
                    # Door is currently open
                    if mac not in open_sensors_start_time:
                        open_sensors_start_time[mac] = now
                        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Sensor {mac} detected OPEN.")
                    
                    open_duration = now - open_sensors_start_time[mac]
                    
                    if open_duration >= threshold_seconds:
                        # Check if we should send a notification
                        last_notified = last_notification_time.get(mac, 0)
                        if now - last_notified >= repeat_interval:
                            message = f"Door Sensor {mac} has been open for {int(open_duration // 60)} minutes!"
                            send_notification(channel_id, message)
                            last_notification_time[mac] = now
                            
                elif is_open is False:
                    # Door is closed
                    if mac in open_sensors_start_time:
                        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Sensor {mac} detected CLOSED.")
                        del open_sensors_start_time[mac]
                    if mac in last_notification_time:
                        del last_notification_time[mac]
                        
                # If is_open is None, we haven't seen an advertisement for this sensor yet

            await asyncio.sleep(interval)
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        print("Stopping scanner...")
        await scanner.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting door monitor...")
