import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

from clickaway.geometry import Screen


class MacReceiverTests(unittest.TestCase):
    """Verify injection semantics with a Quartz test double on either build host."""

    def setUp(self):
        self.quartz = MagicMock()
        self.quartz.CGEventCreateMouseEvent.side_effect = lambda *a: {"args": a}
        self.quartz.CGEventCreateScrollWheelEvent.return_value = {"scroll": True}
        self.quartz.AXIsProcessTrustedWithOptions.return_value = True
        self.appkit = MagicMock()
        self.appkit.NSEvent.doubleClickInterval.return_value = 0.5
        with patch.dict(
            sys.modules,
            {
                "Quartz": self.quartz,
                "AppKit": self.appkit,
                "ApplicationServices": self.quartz,
            },
        ):
            spec = importlib.util.spec_from_file_location(
                "clickaway._test_macos",
                Path(__file__).parents[1] / "clickaway" / "macos.py",
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.receiver = module.MouseReceiver(Screen(-1512, 100, 1512, 982))

    def test_input_ignored_until_enter(self):
        self.receiver.handle({"type": "button", "button": "left", "down": True})
        self.quartz.CGEventPost.assert_not_called()

    def test_logical_coordinates_respect_display_origin(self):
        self.receiver.handle({"type": "enter", "x": 20, "y": 30})
        self.assertEqual(self.receiver.point, (-1492, 130))
        self.receiver.handle({"type": "move", "x": 9999, "y": 9999})
        self.assertEqual(self.receiver.point, (-1, 1081))

    def test_release_clears_held_buttons_on_connection_loss(self):
        self.receiver.handle({"type": "enter", "x": 20, "y": 30})
        self.receiver.handle({"type": "button", "button": "left", "down": True})
        self.receiver.release()
        args = self.quartz.CGEventCreateMouseEvent.call_args.args
        self.assertIs(args[1], self.quartz.kCGEventLeftMouseUp)
        self.assertFalse(self.receiver.buttons)
        self.assertFalse(self.receiver.active)

    def test_double_click_and_drag_use_native_event_types(self):
        self.receiver.handle({"type": "enter", "x": 20, "y": 30})
        for down in (True, False, True):
            self.receiver.handle({"type": "button", "button": "left", "down": down})
        self.assertEqual(self.quartz.CGEventSetIntegerValueField.call_args.args[2], 2)
        self.receiver.handle({"type": "move", "x": 25, "y": 30})
        self.assertIs(
            self.quartz.CGEventCreateMouseEvent.call_args.args[1],
            self.quartz.kCGEventLeftMouseDragged,
        )

    def test_permission_loss_rejects_entry(self):
        self.quartz.AXIsProcessTrustedWithOptions.return_value = False
        with self.assertRaises(PermissionError):
            self.receiver.handle({"type": "enter", "x": 20, "y": 30})
        self.quartz.CGEventPost.assert_not_called()


if __name__ == "__main__":
    unittest.main()
