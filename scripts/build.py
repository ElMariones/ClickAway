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
env = dict(os.environ)
if sys.platform == "win32":
    # PyInstaller finds DLLs through PATH. Put this interpreter's own OpenSSL first so
    # another app's copy (MySQL, Git, ...) cannot break `import ssl` in the package.
    env["PATH"] = os.pathsep.join(
        [str(Path(sys.base_prefix) / "DLLs"), env.get("PATH", "")]
    )
subprocess.run(
    [sys.executable, "-m", "PyInstaller", "--noconfirm", "ClickAway.spec"],
    check=True,
    env=env,
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
