"""Build the stand-alone OD Studio app for this computer's system.

    pip install pyinstaller
    python packaging/build.py

Result in dist/: "OD Studio.app" (macOS), "OD Studio.exe" (Windows) or "OD Studio" (Linux).
PyInstaller builds for the system it runs on; build on each system you need.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.exit(subprocess.call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                          "--distpath", os.path.join(ROOT, "dist"),
                          "--workpath", os.path.join(ROOT, "build"),
                          os.path.join(HERE, "odstudio.spec")]))
