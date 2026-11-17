# PyInstaller recipe for the stand-alone OD Studio app.  Run packaging/build.py.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "vendor")]
from odstudio import __version__  # noqa: E402

a = Analysis(
    [os.path.join(SPECPATH, "odstudio_app.py")],
    pathex=[os.path.join(ROOT, "src"), os.path.join(ROOT, "vendor")],
    datas=collect_data_files("odstudio"),
    hiddenimports=collect_submodules("odstudio") + collect_submodules("serial"),
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="OD Studio", console=False,
              icon=os.path.join(ROOT, "docs", "logo", "odstudio.icns"))
    coll = COLLECT(exe, a.binaries, a.datas, name="OD Studio")
    app = BUNDLE(coll, name="OD Studio.app", icon=os.path.join(ROOT, "docs", "logo", "odstudio.icns"),
                 bundle_identifier="io.github.odstudio",
                 version=__version__,
                 info_plist={
                     "CFBundleDisplayName": "OD Studio",
                     "CFBundleShortVersionString": __version__,
                     "LSUIElement": True,          # its window is a browser tab; no Dock icon
                     "NSHumanReadableCopyright": "MIT License. Not affiliated with Molecular Devices.",
                 })
else:
    # one file: OD Studio.exe on Windows, OD Studio on Linux
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="OD Studio",
              console=sys.platform != "win32",
              icon=os.path.join(ROOT, "docs", "logo", "odstudio.ico"))
