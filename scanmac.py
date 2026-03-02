import asyncio
from bleak import BleakScanner

async def run():
    print("--- SYSTEM BLUETOOTH CHECK ---")
    try:
        # 1. Try to list all controllers/adapters
        print("Searching for Bluetooth adapters...")
        # (This is a simpler way to see if the backend is alive)
        devices = await BleakScanner.discover(timeout=5.0)
        
        if not devices:
            print("RESULT: No devices found in 5 seconds. Bluetooth is likely hung or blocked.")
        else:
            print(f"RESULT: Found {len(devices)} devices. Bluetooth is WORKING.")
            for d in devices:
                print(f"  - [{d.address}] {d.name or 'Unknown'}")
                
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        print("\nPossible fixes:")
        print("1. Run with: sudo python3 scanmac.py")
        print("2. Run: sudo hciconfig hci0 up")

if __name__ == "__main__":
    asyncio.run(run())
