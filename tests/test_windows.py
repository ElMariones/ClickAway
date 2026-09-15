import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clickaway.geometry import Portal, Screen


@unittest.skipUnless(sys.platform == "win32", "Windows native adapter")
class WindowsTests(unittest.TestCase):
    def setUp(self):
        from clickaway import windows

        self.windows = windows
        self.api_patch = patch.object(windows, "user32")
        self.api = self.api_patch.start()
        self.addCleanup(self.api_patch.stop)
        self.api.GetAsyncKeyState.return_value = 0
        self.api.SetCursorPos.return_value = True
        self.controller = windows.MouseController(lambda s: None)
        self.peer = Mock()
        self.peer.closed = threading.Event()
        self.peer.send.return_value = True
        self.controller.peer = self.peer
        self.controller.portal = Portal(
            Screen(0, 0, 1920, 1080), Screen(0, 0, 1512, 982)
        )

    def data(self, x=0, y=500, wheel=0):
        return SimpleNamespace(pt=SimpleNamespace(x=x, y=y), mouseData=wheel)

    def enter(self):
        self.controller._handle(0x200, self.data(10))
        self.assertTrue(self.controller._handle(0x200, self.data(0)))
        self.assertTrue(self.controller.remote)

    def test_local_input_passes_through_and_edge_enters_remote(self):
        self.assertFalse(self.controller._handle(0x201, self.data(500)))
        self.enter()
        self.assertEqual(self.peer.send.call_args.args[0]["type"], "enter")
        self.api.SetCursorPos.assert_called_with(960, 540)

    def test_relative_motion_and_mouse_buttons_are_suppressed_locally(self):
        self.enter()
        self.controller._handle(0x200, self.data(950, 545))
        self.assertEqual(self.peer.send.call_args.args[0]["x"], 1499)
        self.assertTrue(self.controller._handle(0x201, self.data()))
        self.assertEqual(
            self.peer.send.call_args.args[0],
            {"type": "button", "button": "left", "down": True},
        )
        self.assertTrue(self.controller._handle(0x202, self.data()))
        self.assertFalse(self.controller.buttons)

    def test_remote_drag_does_not_cross_back_until_released(self):
        self.enter()
        self.controller._handle(0x201, self.data())
        self.controller._handle(0x200, self.data(970, 540))
        self.assertTrue(self.controller.remote)
        self.controller._handle(0x202, self.data())
        self.controller._handle(0x200, self.data(970, 540))
        self.assertFalse(self.controller.remote)

    def test_disconnection_returns_mouse_and_passes_local_input(self):
        self.enter()
        self.peer.closed.set()
        self.assertFalse(self.controller._handle(0x200, self.data(900, 500)))
        self.assertFalse(self.controller.remote)
        self.assertEqual(self.peer.send.call_args.args[0], {"type": "leave"})

    def test_queue_failure_returns_control(self):
        self.enter()
        self.peer.send.return_value = False
        self.controller._handle(0x200, self.data(900, 500))
        self.assertFalse(self.controller.remote)

    def test_pushing_past_the_desktop_edge_enters_remote(self):
        # Hooks report the unclamped position while the cursor itself stays at x = 0.
        self.api.MonitorFromPoint.return_value = None
        self.controller._handle(0x200, self.data(2))
        self.assertTrue(self.controller._handle(0x200, self.data(-3)))
        self.assertTrue(self.controller.remote)

    def test_repeated_pushes_against_the_edge_enter_after_cooldown(self):
        self.api.MonitorFromPoint.return_value = None
        self.controller.cooldown = float("inf")
        self.controller._handle(0x200, self.data(-3))
        self.assertFalse(self.controller._handle(0x200, self.data(-3)))
        self.controller.cooldown = 0
        self.assertTrue(self.controller._handle(0x200, self.data(-3)))

    def test_moving_onto_a_neighboring_windows_display_does_not_enter(self):
        self.api.MonitorFromPoint.return_value = 1234
        self.controller._handle(0x200, self.data(2))
        self.assertFalse(self.controller._handle(0x200, self.data(-3)))
        self.assertFalse(self.controller.remote)

    def test_networks_show_wifi_names_and_hide_unusable_adapters(self):
        adapters = [
            {
                "guid": "{A}",
                "name": "vEthernet (WSL)",
                "type": 6,
                "up": 1,
                "gateway": False,
                "ips": ["172.21.48.1"],
            },
            {
                "guid": "{B}",
                "name": "Ethernet",
                "type": 6,
                "up": 2,
                "gateway": False,
                "ips": ["169.254.1.1"],
            },
            {
                "guid": "{c}",
                "name": "Wi-Fi 2",
                "type": 71,
                "up": 1,
                "gateway": True,
                "ips": ["192.168.1.153"],
            },
            {
                "guid": "{D}",
                "name": "Loopback",
                "type": 24,
                "up": 1,
                "gateway": False,
                "ips": ["127.0.0.1"],
            },
            {
                "guid": "{E}",
                "name": "Wi-Fi 3",
                "type": 71,
                "up": 1,
                "gateway": True,
                "ips": ["10.0.0.7"],
            },
        ]
        self.assertEqual(
            self.windows.describe_networks(adapters, {"{C}": "Home"}),
            [
                ("Wi-Fi: Home · 192.168.1.153", "192.168.1.153"),
                ("Wi-Fi 3 · 10.0.0.7", "10.0.0.7"),
                ("vEthernet (WSL) · 172.21.48.1", "172.21.48.1"),
            ],
        )

    def test_held_local_button_blocks_crossing(self):
        self.api.GetAsyncKeyState.return_value = -32768
        self.controller._handle(0x200, self.data(10))
        self.assertFalse(self.controller._handle(0x200, self.data(0)))
        self.assertFalse(self.controller.remote)


if __name__ == "__main__":
    unittest.main()
