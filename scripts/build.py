"""Build and archive the native app on Windows or Apple Silicon macOS."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
if sys.platform not in ("win32", "darwin"):
    raise SystemExit("Build on Windows or Apple Silicon macOS")
subprocess.run(
    [sys.executable, "-m", "PyInstaller", "--noconfirm", "ClickAway.spec"], check=True
)
if sys.platform == "darwin":
    output = ROOT / "dist" / "ClickAway-macOS-AppleSilicon.zip"
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
else:
    output = shutil.make_archive(
        str(ROOT / "dist" / "ClickAway-Windows-x64"), "zip", "dist", "ClickAway"
    )
print(f"Ready: {output}")
