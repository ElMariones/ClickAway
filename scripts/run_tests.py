"""Run the test suite, and in CI repeat any failure as a public annotation.

GitHub hides workflow logs from anonymous visitors, so a failure that lives only
in the log cannot be read without signing in to this repository. Annotations stay
public, which keeps a red build explainable to anyone who can see it.
"""

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 4000  # Annotations are truncated well before GitHub's own limit.

result = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    cwd=ROOT,
    capture_output=True,
    text=True,
)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)

if result.returncode and os.environ.get("GITHUB_ACTIONS"):
    output = result.stdout + result.stderr
    start = output.find("=" * 70)
    report = output[start:] if start >= 0 else output[-LIMIT:]
    # A literal newline would end the annotation after its first line.
    message = report[:LIMIT].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::error title=Tests failed on {sys.platform}::{message}")

sys.exit(result.returncode)
