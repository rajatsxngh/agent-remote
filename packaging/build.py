"""Build the downloadable Windows app.

    uv run python packaging/build.py

Makes dist/ps5-agent/ (ps5-agent.exe plus its files) and dist/ps5-agent-windows.zip.
Local Whisper is left out to keep the download small; the default voice is
Claude's own mic.
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path

import PyInstaller.__main__

NAME = "ps5-agent"
ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
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
        "--console",
        "--noconfirm",
        "--clean",
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT / "src"),
        "--collect-all", "sdl2dll",  # SDL2.dll, the controller library
        "--collect-submodules", "sdl2",
        "--collect-submodules", "ps5_agent_handler",
        "--hidden-import", "comtypes.stream",
        "--add-data", f"{ROOT / 'config.toml'}{os.pathsep}.",
    ]
    for module in LEAVE_OUT:
        args += ["--exclude-module", module]
    PyInstaller.__main__.run(args)
    app = DIST / NAME
    shutil.copy(PACKAGING / "START HERE.txt", app / "START HERE.txt")
    return app


def zip_up(app: Path) -> Path:
    archive = DIST / f"{NAME}-windows.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(app.rglob("*")):
            zf.write(path, Path(NAME) / path.relative_to(app))
    return archive


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Build the Windows app on Windows.")
    app = build()
    archive = zip_up(app)
    size = archive.stat().st_size / 1_000_000
    print(f"\nApp folder: {app}\nDownload:   {archive} ({size:.0f} MB)")
