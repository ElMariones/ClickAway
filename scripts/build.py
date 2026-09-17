"""Build the app and its installer on Windows or Apple Silicon macOS."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from clickaway import __version__  # noqa: E402

DIST = ROOT / "dist"
PACKAGING = ROOT / "packaging"
if sys.platform not in ("win32", "darwin"):
    raise SystemExit("Build on Windows or Apple Silicon macOS")


def app_bundle():
    env = dict(os.environ)
    if sys.platform == "win32":
        # PyInstaller finds DLLs through PATH. Put this interpreter's own OpenSSL first
        # so another app's copy (MySQL, Git, ...) cannot break `import ssl` in the app.
        env["PATH"] = os.pathsep.join(
            [str(Path(sys.base_prefix) / "DLLs"), env.get("PATH", "")]
        )
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "ClickAway.spec"],
        check=True,
        env=env,
    )


def inno_setup_compiler():
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        candidate = Path(base or "C:\\") / "Inno Setup 6" / "ISCC.exe"
        if candidate.exists():
            return str(candidate)
    return None


def windows_installer():
    compiler = inno_setup_compiler()
    if not compiler:
        print("Skipping the installer: Inno Setup (ISCC.exe) was not found.")
        return None
    subprocess.run(
        [compiler, f"/DMyAppVersion={__version__}", str(PACKAGING / "clickaway.iss")],
        check=True,
    )
    return DIST / "ClickAway-Setup-x64.exe"


def windows_archive():
    return Path(
        shutil.make_archive(
            str(DIST / "ClickAway-Windows-x64"), "zip", "dist", "ClickAway"
        )
    )


def disk_image_background():
    """Combine the 1x and 2x artwork so Finder picks the right one per display."""
    combined = ROOT / "build" / "dmg-background.tiff"
    combined.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "tiffutil",
                "-cathidpicheck",
                str(PACKAGING / "dmg-background.png"),
                str(PACKAGING / "dmg-background@2x.png"),
                "-out",
                str(combined),
            ],
            check=True,
            capture_output=True,
        )
        return combined
    except (OSError, subprocess.CalledProcessError):
        return PACKAGING / "dmg-background.png"


def mac_disk_image():
    import dmgbuild

    output = DIST / "ClickAway-macOS-AppleSilicon.dmg"
    output.unlink(missing_ok=True)
    dmgbuild.build_dmg(
        filename=str(output),
        volume_name="ClickAway",
        settings_file=str(PACKAGING / "dmg_settings.py"),
        defines={
            "app": str(DIST / "ClickAway.app"),
            "background": str(disk_image_background()),
        },
    )
    return output


def mac_archive():
    output = DIST / "ClickAway-macOS-AppleSilicon.zip"
    output.unlink(missing_ok=True)
    subprocess.run(
        [
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            "dist/ClickAway.app",
            str(output),
        ],
        check=True,
    )
    return output


app_bundle()
if sys.platform == "darwin":
    outputs = [mac_disk_image(), mac_archive()]
else:
    outputs = [windows_installer(), windows_archive()]
for output in outputs:
    if output:
        print(f"Ready: {output}")
