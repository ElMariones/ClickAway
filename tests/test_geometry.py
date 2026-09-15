import unittest

from clickaway.geometry import Portal, Screen


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.pc = Screen(-2560, 200, 2560, 1440)
        self.mac = Screen(0, 0, 1512, 982)

    def test_left_crossing_preserves_relative_height_and_returns_inside_edge(self):
        p = Portal(self.pc, self.mac)
        x, y = p.enter(200 + 1439 / 2)
        self.assertEqual(x, 1509)
        self.assertAlmostEqual(y, 981 / 2)
        self.assertFalse(p.move(-300, 0)[0])
        self.assertTrue(p.move(400, 0)[0])
        self.assertEqual(p.return_position(), (-2548, 920))

    def test_right_crossing_and_speed(self):
        p = Portal(self.pc, self.mac, "right", 2)
        p.enter(200)
        self.assertEqual(p.move(20, 10), (False, 42, 20))
        self.assertTrue(p.move(-50, 0)[0])
        self.assertEqual(p.return_position()[0], -13)

    def test_drag_never_leaves_screen(self):
        for side, dx in (("left", 10000), ("right", -10000)):
            p = Portal(self.pc, self.mac, side)
            p.enter(500)
            self.assertFalse(p.move(dx, 0, dragging=True)[0])
            self.assertTrue(p.move(dx, 0, dragging=False)[0])

    def test_outer_edges_clamp(self):
        p = Portal(self.pc, self.mac)
        p.enter(-999)
        self.assertEqual(p.move(-99999, -99999), (False, 0, 0))
        self.assertEqual(p.move(0, 99999), (False, 0, 981))

    def test_portal_only_on_selected_display(self):
        p = Portal(self.pc, self.mac)
        self.assertTrue(p.at_edge(-2560, 200))
        self.assertFalse(p.at_edge(-2561, 200))
        self.assertFalse(p.at_edge(-2560, 199))
        self.assertFalse(p.at_edge(-2560, 1640))
        self.assertFalse(p.at_edge(-1280, 900))

    def test_reject_invalid_geometry(self):
        for width in (0, -2, float("nan"), float("inf"), 99999):
            with self.assertRaises(ValueError):
                Screen(0, 0, width, 100)
        for speed in (0, float("nan"), 4):
            with self.assertRaises(ValueError):
                Portal(self.pc, self.mac, speed=speed)


if __name__ == "__main__":
    unittest.main()
