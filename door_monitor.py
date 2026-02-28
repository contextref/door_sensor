import asyncio
import json
import time
import urllib.request
import os
import sys

# Optional import, user needs to implement the logic inside check_sensor_is_open
try:
    from bleak import BleakClient
except ImportError:
    print("Warning: bleak library not found. Please install it with 'pip install -r requirements.txt'")

CONFIG_FILE = "config.json"

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

async def check_sensor_is_open(mac_address):
    """
    Connects to the Bluetooth device and checks if the door is open.
    Returns:
        True if the door is open.
        False if the door is closed.
        None if the state could not be determined.
    """
    # TODO: Implement the exact door closure logic here.
    # The bleak code will go here, currently returning None so it doesn't do anything
    # Example using BleakClient:
    # try:
    #     async with BleakClient(mac_address, timeout=5.0) as client:
    #         if not client.is_connected:
    #             return None
    #         # Replace with your actual characteristic UUID
    #         # data = await client.read_gatt_char("0000xxxx-0000-1000-8000-00805f9b34fb")
    #         # Parse data to determine if open or closed
    #         # is_open = (data[0] == 0x01)
    #         # return is_open
    #         pass
    # except Exception as e:
    #     print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error connecting to {mac_address}: {e}")
    #     return None
    return None

async def main():
    config = load_config()
    sensors = config.get("sensors", [])
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
    print(f"Polling interval: {interval} seconds.")
    print(f"Notification threshold: {threshold_minutes} minutes.")
    print(f"Notification channel: {channel_id}")

    # State tracking
    # mac_address -> timestamp of when it was first detected open
    open_sensors_start_time = {}
    
    # Track when we last sent a notification to avoid spamming
    last_notification_time = {}

    while True:
        for mac in sensors:
            try:
                is_open = await check_sensor_is_open(mac)
            except Exception as e:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error checking sensor {mac}: {e}")
                is_open = None
            
            now = time.time()
            
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
                    
            elif is_open is None:
                # Could not read state or not implemented yet
                pass

        await asyncio.sleep(interval)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting door monitor...")
