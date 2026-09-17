import tomllib
import unittest
from pathlib import Path

import clickaway

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"


def png_size(path):
    header = path.read_bytes()[:24]
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


class PackagingTests(unittest.TestCase):
    def test_one_version_everywhere(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
        self.assertEqual(pyproject["project"]["version"], clickaway.__version__)
        self.assertIn(
            f'"CFBundleShortVersionString": "{clickaway.__version__}"',
            (ROOT / "ClickAway.spec").read_text("utf-8"),
        )

    def test_windows_installer_script(self):
        script = (PACKAGING / "clickaway.iss").read_text("utf-8")
        self.assertIn("OutputBaseFilename=ClickAway-Setup-x64", script)
        self.assertIn("OutputDir=..\\dist", script)
        self.assertIn("AppVersion={#MyAppVersion}", script)
        # Per-user by default, so installing needs no administrator prompt.
        self.assertIn("PrivilegesRequired=lowest", script)
        self.assertIn(r'Source: "..\dist\ClickAway\*"', script)

    def test_disk_image_drops_the_app_next_to_applications(self):
        settings = {}
        code = (PACKAGING / "dmg_settings.py").read_text("utf-8")
        exec(  # noqa: S102 - the settings file is part of this repository.
            compile(code, "dmg_settings.py", "exec"),
            {"defines": {"app": "/build/ClickAway.app", "background": "bg.tiff"}},
            settings,
        )
        self.assertEqual(settings["files"], ["/build/ClickAway.app"])
        self.assertEqual(settings["background"], "bg.tiff")
        self.assertEqual(settings["symlinks"], {"Applications": "/Applications"})
        self.assertEqual(settings["window_rect"][1], (640, 400))
        self.assertEqual(
            settings["icon_locations"],
            {"ClickAway.app": (160, 190), "Applications": (480, 190)},
        )

    def test_background_artwork_matches_the_window(self):
        self.assertEqual(png_size(PACKAGING / "dmg-background.png"), (640, 400))
        self.assertEqual(png_size(PACKAGING / "dmg-background@2x.png"), (1280, 800))


if __name__ == "__main__":
    unittest.main()
