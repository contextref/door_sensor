import unittest
from unittest.mock import patch, MagicMock
import asyncio
import json
import time
import io
import sys
import os

# Add the current directory to sys.path so we can import door_monitor
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import door_monitor

class TestDoorMonitor(unittest.TestCase):

    def setUp(self):
        self.config = {
            "sensors": ["00:11:22:33:44:55"],
            "polling_interval_seconds": 0.1,
            "ntfy_channel_id": "test-channel",
            "door_open_threshold_minutes": 0.01, # ~0.6 seconds for testing
            "notification_repeat_interval_seconds": 0.5
        }

    @patch('door_monitor.urllib.request.urlopen')
    def test_send_notification(self, mock_urlopen):
        # Mock the response
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        door_monitor.send_notification("test-channel", "Test Message")

        # Verify urlopen was called with correct URL and data
        mock_urlopen.assert_called_once()
        args, kwargs = mock_urlopen.call_args
        req = args[0]
        self.assertEqual(req.full_url, "https://ntfy.sh/test-channel")
        self.assertEqual(req.data, b"Test Message")
        self.assertEqual(req.method, "POST")

    @patch('door_monitor.load_config')
    @patch('door_monitor.check_sensor_is_open')
    @patch('door_monitor.send_notification')
    @patch('door_monitor.asyncio.sleep', side_effect=asyncio.CancelledError)
    def test_main_loop_detection(self, mock_sleep, mock_notify, mock_check, mock_load_config):
        # Setup mocks
        mock_load_config.return_value = self.config
        
        # Scenario: Door starts open, then closes
        # We'll use a side_effect to return True then False, then stop the loop via CancelledError
        mock_check.side_effect = [True, False]

        async def run_test():
            try:
                await door_monitor.main()
            except asyncio.CancelledError:
                pass

        # Use a fresh event loop for the test
        asyncio.run(run_test())

        # Verify check_sensor_is_open was called
        self.assertTrue(mock_check.called)

    @patch('door_monitor.load_config')
    @patch('door_monitor.check_sensor_is_open')
    @patch('door_monitor.send_notification')
    @patch('door_monitor.time.time')
    @patch('door_monitor.asyncio.sleep')
    def test_notification_sent_after_threshold_and_repeat(self, mock_sleep, mock_time, mock_notify, mock_check, mock_load_config):
        # Configuration: 10 min threshold (600s), 1 min repeat (60s)
        config = self.config.copy()
        config["door_open_threshold_minutes"] = 10.0
        config["notification_repeat_interval_seconds"] = 60.0
        mock_load_config.return_value = config
        
        mock_check.return_value = True # Sensor stays open
        
        # Time steps:
        # 1. t=0: Detected open
        # 2. t=300 (5m): No notification
        # 3. t=601 (10m+): FIRST notification
        # 4. t=1261 (21m+): SECOND notification (exceeds 10m threshold AND 1m repeat interval since last)
        mock_time.side_effect = [0.0, 300.0, 601.0, 1261.0]
        
        # Exit the loop after 4 iterations
        mock_sleep.side_effect = [None, None, None, asyncio.CancelledError]

        async def run_test():
            try:
                await door_monitor.main()
            except asyncio.CancelledError:
                pass

        asyncio.run(run_test())

        # Should have notified twice
        self.assertEqual(mock_notify.call_count, 2)
        
        # Check first call content (at 10 min mark)
        args_1 = mock_notify.call_args_list[0][0]
        self.assertIn("has been open for 10 minutes", args_1[1])
        
        # Check second call content (at 21 min mark)
        args_2 = mock_notify.call_args_list[1][0]
        self.assertIn("has been open for 21 minutes", args_2[1])

    @patch('door_monitor.load_config')
    @patch('door_monitor.check_sensor_is_open')
    @patch('door_monitor.send_notification')
    @patch('door_monitor.time.time')
    @patch('door_monitor.asyncio.sleep')
    def test_no_notification_if_closed_before_threshold(self, mock_sleep, mock_time, mock_notify, mock_check, mock_load_config):
        # Configuration: 10 min threshold (600s)
        config = self.config.copy()
        config["door_open_threshold_minutes"] = 10.0
        mock_load_config.return_value = config
        
        # State sequence: Open, Still Open (9m), Closed (11m)
        mock_check.side_effect = [True, True, False]
        
        # Time steps:
        # 1. t=0: Detected open
        # 2. t=540 (9m): Still open, but under 10m threshold
        # 3. t=660 (11m): Now closed
        mock_time.side_effect = [0.0, 540.0, 660.0]
        
        # Exit loop after 3 iterations
        mock_sleep.side_effect = [None, None, asyncio.CancelledError]

        async def run_test():
            try:
                await door_monitor.main()
            except asyncio.CancelledError:
                pass

        asyncio.run(run_test())

        # Should NOT have notified at all
        mock_notify.assert_not_called()

    def test_load_config_missing(self):
        with patch('os.path.exists', return_value=False):
            with self.assertRaises(SystemExit):
                door_monitor.load_config()

    @patch('builtins.open', new_callable=unittest.mock.mock_open, read_data='{"invalid": json}')
    @patch('os.path.exists', return_value=True)
    def test_load_config_invalid_json(self, mock_exists, mock_file):
        with self.assertRaises(SystemExit):
            door_monitor.load_config()

if __name__ == '__main__':
    unittest.main()
