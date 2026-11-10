"""Build the downloadable app for the computer you run it on (Windows or Mac).

    uv run python packaging/build.py

On Windows: dist/ps5-agent/ (ps5-agent.exe) and dist/ps5-agent-windows.zip.
On a Mac:   dist/ps5-agent.app (a menu bar app) and dist/ps5-agent-mac.dmg,
            which you open and drag into Applications.
PyInstaller can't build for another system, so build each on its own computer.
Local Whisper is left out to keep the download small; the default voice is
Claude's own mic.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import PyInstaller.__main__

NAME = "ps5-agent"
ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
MAC = sys.platform == "darwin"
BUNDLE_ID = "com.rajatsinghh.ps5-agent"
SYSTEM = "mac" if MAC else "windows"
START_HERE = "START HERE (Mac).txt" if MAC else "START HERE.txt"
# Big optional pieces the packaged app doesn't need (Whisper and its helpers).
LEAVE_OUT = [
    "faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers",
    "huggingface_hub", "hf_xet", "pytest", "_pytest", "tkinter",
]


def build() -> Path:
    args = [
        str(PACKAGING / "launch.py"),
        "--name", NAME,
        "--onedir",
        "--windowed" if MAC else "--console",  # the Mac app is a menu bar app
        "--noconfirm",
        "--clean",
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT / "src"),
        "--collect-all", "sdl2dll",  # the SDL2 library that reads the controller
        "--collect-submodules", "sdl2",
        "--collect-submodules", "ps5_agent_handler",
        "--add-data", f"{ROOT / 'config.toml'}{os.pathsep}.",
    ]
    if MAC:
        args += ["--osx-bundle-identifier", BUNDLE_ID]
        for module in ("AppKit", "Foundation", "Quartz", "ApplicationServices", "objc"):
            args += ["--hidden-import", module]
    else:
        args += ["--hidden-import", "comtypes.stream"]
    for module in LEAVE_OUT:
        args += ["--exclude-module", module]
    PyInstaller.__main__.run(args)
    if MAC:
        return finish_mac_app(DIST / f"{NAME}.app")
    app = DIST / NAME
    shutil.copy(PACKAGING / START_HERE, app / START_HERE)
    return app


def finish_mac_app(app: Path) -> Path:
    """Menu bar only (no Dock icon), then re-sign since Info.plist changed."""
    info = app / "Contents" / "Info.plist"
    plist = plistlib.loads(info.read_bytes())
    plist["LSUIElement"] = True
    plist["CFBundleDisplayName"] = NAME
    plist["NSHumanReadableCopyright"] = "Drive Claude's Code tab with a PS5 controller"
    info.write_bytes(plistlib.dumps(plist))
    # An ad-hoc signature (no Apple Developer account); macOS will ask you to
    # confirm opening it the first time.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    return app


def make_dmg(app: Path) -> Path:
    """A disk image with the app, a shortcut to Applications, and START HERE."""
    staging = DIST / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / app.name, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    shutil.copy(PACKAGING / START_HERE, staging / START_HERE)
    image = DIST / f"{NAME}-mac.dmg"
    image.unlink(missing_ok=True)
    subprocess.run(
        ["hdiutil", "create", "-volname", NAME, "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(image)],
        check=True,
    )
    shutil.rmtree(staging, ignore_errors=True)
    return image


def zip_up(app: Path) -> Path:
    archive = DIST / f"{NAME}-{SYSTEM}.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(app.rglob("*")):
            info = zipfile.ZipInfo.from_file(path, Path(NAME) / path.relative_to(app))
            # Keep file permissions (the Mac program must stay runnable after unzipping).
            info.external_attr = (stat.S_IMODE(path.stat().st_mode) | (stat.S_IFDIR if path.is_dir() else stat.S_IFREG)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            if path.is_dir():
                zf.writestr(info, "")
            else:
                zf.writestr(info, path.read_bytes())
    return archive


if __name__ == "__main__":
    if sys.platform not in ("win32", "darwin"):
        raise SystemExit("Build on Windows or a Mac.")
    app = build()
    archive = make_dmg(app) if MAC else zip_up(app)
    size = archive.stat().st_size / 1_000_000
    print(f"\nApp:      {app}\nDownload: {archive} ({size:.0f} MB)")
