from __future__ import annotations

import os
import shutil
from pathlib import Path

from PyInstaller.__main__ import run


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build" / "pyinstaller"

for path in (DIST / "backend", BUILD):
    shutil.rmtree(path, ignore_errors=True)

data_separator = os.pathsep
arguments = [
    "--clean",
    "--noconfirm",
    "--onedir",
    "--name",
    "douyin-fire-backend",
    "--distpath",
    str(DIST / "backend"),
    "--workpath",
    str(BUILD),
    "--specpath",
    str(BUILD),
    "--collect-all",
    "app",
    "--collect-all",
    "playwright",
    "--collect-all",
    "dotenv",
    "--collect-all",
    "tzdata",
    "--hidden-import",
    "scripts.login_mac",
    "--add-data",
    f"{ROOT / 'ui' / 'static'}{data_separator}ui/static",
    "--add-data",
    f"{ROOT / 'config' / 'stickers.json'}{data_separator}config",
    str(ROOT / "desktop_backend.py"),
]
run(arguments)
