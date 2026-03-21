import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import time
import sys
import os

# Mock missing modules for the test environment
mock_govee = MagicMock()
mock_ha_bt = MagicMock()
mock_dotenv = MagicMock()
sys.modules["govee_ble"] = mock_govee
sys.modules["home_assistant_bluetooth"] = mock_ha_bt
sys.modules["dotenv"] = mock_dotenv

# Add the current directory to sys.path so we can import door_monitor
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import door_monitor

class TestDoorMonitor(unittest.TestCase):

    def setUp(self):
        # Clear global state before each test
        door_monitor.sensors_current_state = {}
        door_monitor.sensors_metadata = {}

    def setup_mock_scanner(self, mock_scanner):
        mock_scanner_instance = mock_scanner.return_value
        mock_scanner_instance.start = AsyncMock()
        mock_scanner_instance.stop = AsyncMock()
        return mock_scanner_instance

    @patch('door_monitor.urllib.request.urlopen')
    def test_send_notification(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        door_monitor.send_notification("test-channel", "Test Message")
        mock_urlopen.assert_called_once()

    @patch('door_monitor.os.getenv')
    @patch('door_monitor.BleakScanner')
    @patch('door_monitor.send_notification')
    @patch('door_monitor.asyncio.sleep')
    def test_main_loop_discovery_and_detection(self, mock_sleep, mock_notify, mock_scanner, mock_getenv):
        # Setup mocks
        mock_getenv.side_effect = lambda k, default=None: {
            "NTFY_CHANNEL_ID": "test-channel",
            "POLLING_INTERVAL_SECONDS": "0.1",
            "DOOR_OPEN_THRESHOLD_MINUTES": "0.01",
            "NOTIFICATION_REPEAT_INTERVAL_SECONDS": "0.5"
        }.get(k, default)
        
        self.setup_mock_scanner(mock_scanner)
        
        mac = "D2:2D:84:06:32:0C"
        
        def sleep_side_effect(seconds):
            if mac not in door_monitor.sensors_current_state:
                # Simulate discovery
                door_monitor.sensors_current_state[mac] = True
                door_monitor.sensors_metadata[mac] = {"model": "H5123", "name": "Test Sensor"}
                return None
            elif door_monitor.sensors_current_state[mac] is True:
                # Simulate state change
                door_monitor.sensors_current_state[mac] = False
                return None
            else:
                raise asyncio.CancelledError()

        mock_sleep.side_effect = sleep_side_effect

        async def run_test():
            try:
                await door_monitor.main()
            except asyncio.CancelledError:
                pass

        asyncio.run(run_test())

    def test_handle_detection_discovery(self):
        # Setup mocks manually since we mocked the module in sys.modules
        mock_parser_class = door_monitor.GoveeBluetoothDeviceData
        mock_parser_instance = mock_parser_class.return_value
        mock_parser_instance.supported.return_value = True
        
        update = MagicMock()
        
        # Mock binary_entity_values
        device_key = MagicMock()
        device_key.key = "window"
        sensor_value = MagicMock()
        sensor_value.native_value = True
        update.binary_entity_values = {device_key: sensor_value}
        
        # Mock devices for metadata
        device_info = MagicMock()
        device_info.model = "H5123"
        device_info.name = "Front Door"
        update.devices = {None: device_info}
        
        mock_parser_instance.update.return_value = update

        device = MagicMock()
        device.address = "D2:2D:84:06:32:0C"
        adv_data = MagicMock()
        adv_data.local_name = "GV5123"
        adv_data.rssi = -50
        
        door_monitor.handle_detection(device, adv_data)
        
        self.assertIn("D2:2D:84:06:32:0C", door_monitor.sensors_current_state)
        self.assertTrue(door_monitor.sensors_current_state["D2:2D:84:06:32:0C"])
        self.assertEqual(door_monitor.sensors_metadata["D2:2D:84:06:32:0C"]["model"], "H5123")

    def test_handle_detection_fallback(self):
        # Library doesn't support this packet
        mock_parser_class = door_monitor.GoveeBluetoothDeviceData
        mock_parser_instance = mock_parser_class.return_value
        mock_parser_instance.supported.return_value = True
        update = MagicMock()
        update.binary_entity_values = {} # No binary values found by lib
        mock_parser_instance.update.return_value = update

        device = MagicMock()
        device.address = "D2:2D:84:06:32:0C"
        adv_data = MagicMock()
        adv_data.rssi = -50
        # Manual bitwise state at index 4: bit 0 = 1 (OPEN)
        adv_data.manufacturer_data = {61320: bytes([0x13, 0xb6, 0x03, 0xff, 0x01])}
        
        door_monitor.handle_detection(device, adv_data)
        self.assertIn("D2:2D:84:06:32:0C", door_monitor.sensors_current_state)
        self.assertTrue(door_monitor.sensors_current_state["D2:2D:84:06:32:0C"])

if __name__ == '__main__':
    unittest.main()
