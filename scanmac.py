import asyncio
import time
from bleak import BleakScanner

# Your specific sensor's MAC address
TARGET_MAC = "D2:2D:84:06:32:0C"
# Govee Manufacturer ID for H5123 (0xEF88)
GOVEE_BT_ID = 61320

def handle_detection(device, advertisement_data):
    # Filter by MAC address (case-insensitive)
    if device.address.upper() == TARGET_MAC.upper():
        print(f"[{time.strftime('%H:%M:%S')}] Advertisement: {advertisement_data}")

        # Check if Manufacturer Data is present
        if GOVEE_BT_ID in advertisement_data.manufacturer_data:
            data = advertisement_data.manufacturer_data[GOVEE_BT_ID]

            # Based on user data:
            # Opening: index 3 is 0xd7 (215)
            # Closing: index 3 is 0xd8 (216)
            if len(data) >= 4:
                state_byte = data[3]
                if state_byte == 0xd7:
                    state = "OPEN"
                elif state_byte == 0xd8:
                    state = "CLOSED"
                else:
                    state = f"UNKNOWN (0x{state_byte:02x})"
                
                rssi = advertisement_data.rssi
                print(f"--- SENSOR UPDATE ---")
                print(f"Status: {state}")
                print(f"Signal Strength (RSSI): {rssi} dBm")
                print(f"Raw Data: {data.hex()}\n")

async def run():
    print(f"Monitoring Govee H5123 [{TARGET_MAC}]...")
    print("Move the magnet to trigger a broadcast.")

    # active=True is vital for Govee on Raspberry Pi
    scanner = BleakScanner(
        detection_callback=handle_detection,
        scanning_mode="active"
    )

    await scanner.start()
    try:
        # We keep the script alive indefinitely
        while True:
            await asyncio.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping scanner...")
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(run())
