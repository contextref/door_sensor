import asyncio
import json
import time
import urllib.request
import os
import sys
from bleak import BleakScanner
from govee_ble import GoveeBluetoothDeviceData
from home_assistant_bluetooth import BluetoothServiceInfo

CONFIG_FILE = "config.json"
GOVEE_MANUFACTURER_ID = 61320

# Global state to track sensor status
# mac_address -> is_open (bool)
sensors_current_state = {}
# mac_address -> metadata (dict with model, name)
sensors_metadata = {}

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
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Notification sent: {message}")
            else:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Failed to send notification, status: {response.status}")
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error sending notification: {e}")

def handle_detection(device, advertisement_data):
    try:
        mac = device.address.upper()
        
        # Create service info for the parser
        service_info = BluetoothServiceInfo(
            name=advertisement_data.local_name or device.name or device.address,
            address=device.address,
            rssi=advertisement_data.rssi,
            manufacturer_data=advertisement_data.manufacturer_data,
            service_data=advertisement_data.service_data,
            service_uuids=advertisement_data.service_uuids,
            source="local",
        )

        parser = GoveeBluetoothDeviceData()
        is_open = None
        
        if parser.supported(service_info):
            update = parser.update(service_info)
            for device_key, sensor_value in update.binary_entity_values.items():
                if hasattr(device_key, "key") and device_key.key in ("window", "opening"):
                    is_open = sensor_value.native_value
                    # If we found it, extract metadata too
                    if mac not in sensors_metadata and update.devices:
                        device_info = next(iter(update.devices.values()))
                        sensors_metadata[mac] = {
                            "model": device_info.model or "Unknown",
                            "name": device_info.name or mac
                        }
                        print(f"[{time.strftime('%H:%M:%S')}] Discovered Govee {sensors_metadata[mac]['model']} sensor: {mac}")
                    break
        
        # FALLBACK: If library didn't get a state, use our manual bitwise logic
        if is_open is None and GOVEE_MANUFACTURER_ID in advertisement_data.manufacturer_data:
            data = advertisement_data.manufacturer_data[GOVEE_MANUFACTURER_ID]
            # Use index 4 bit 0 as the primary state bit we found earlier
            if len(data) >= 5:
                is_open = (data[4] & 0x01 == 1)
                if mac not in sensors_metadata:
                    sensors_metadata[mac] = {"model": "H5123 (Manual)", "name": mac}
                    print(f"[{time.strftime('%H:%M:%S')}] Discovered Govee sensor via fallback: {mac}")

        if is_open is not None:
            sensors_current_state[mac] = is_open
                
    except Exception as e:
        print(f"Error in detection callback: {e}")

async def main():
    config = load_config()
    interval = config.get("polling_interval_seconds", 1.0)
    channel_id = config.get("ntfy_channel_id")
    threshold_minutes = config.get("door_open_threshold_minutes", 10.0)
    repeat_interval = config.get("notification_repeat_interval_seconds", 60.0)
    
    threshold_seconds = threshold_minutes * 60

    if not channel_id:
        print("Error: 'ntfy_channel_id' must be specified in config.")
        sys.exit(1)
        
    print(f"Starting auto-discovery door monitor.")
    print(f"Check interval: {interval} seconds.")
    print(f"Notification threshold: {threshold_minutes} minutes.")

    # State tracking for notifications
    # mac_address -> timestamp of when it was first detected open
    open_sensors_start_time = {}
    last_notification_time = {}

    scanner = BleakScanner(
        detection_callback=lambda d, a: handle_detection(d, a),
        scanning_mode="active"
    )

    await scanner.start()
    print("Scanner started. Listening for Govee door/window sensors...")

    try:
        while True:
            now = time.time()
            # Iterate over a copy of discovered sensors
            for mac in list(sensors_current_state.keys()):
                is_open = sensors_current_state[mac]
                metadata = sensors_metadata.get(mac, {"name": mac, "model": "Unknown"})
                sensor_display = f"{metadata['name']} ({metadata['model']})"

                if is_open is True:
                    if mac not in open_sensors_start_time:
                        open_sensors_start_time[mac] = now
                        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {sensor_display} detected OPEN.")
                    
                    open_duration = now - open_sensors_start_time[mac]
                    if open_duration >= threshold_seconds:
                        last_notified = last_notification_time.get(mac, 0)
                        if now - last_notified >= repeat_interval:
                            message = f"Door Sensor {sensor_display} has been open for {int(open_duration // 60)} minutes!"
                            send_notification(channel_id, message)
                            last_notification_time[mac] = now
                            
                else: # is_open is False
                    if mac in open_sensors_start_time:
                        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {sensor_display} detected CLOSED.")
                        del open_sensors_start_time[mac]
                    if mac in last_notification_time:
                        del last_notification_time[mac]

            await asyncio.sleep(interval)
    finally:
        await scanner.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting door monitor...")
