#!/usr/bin/env python3
"""Entry point shared by the desktop app and PyInstaller builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Douyin Fire"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Douyin Fire"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "douyin-fire"


def main() -> int:
    code_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    if str(code_root) not in sys.path:
        sys.path.insert(0, str(code_root))

    data_root = Path(os.environ.get("DOUYIN_DATA_DIR", default_data_dir())).expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    os.environ["DOUYIN_DATA_DIR"] = str(data_root)
    os.environ.setdefault("DOUYIN_ENV_FILE", str(data_root / ".env"))
    os.environ["DOUYIN_DESKTOP_MODE"] = "1"
    os.chdir(data_root)

    arguments = sys.argv[1:]
    if not arguments:
        print("用法: douyin-fire-backend serve|run|login [参数]", file=sys.stderr)
        return 2

    mode, *rest = arguments
    if mode == "serve":
        os.environ.setdefault("UI_PORT", "0")
        from ui.server import main as serve

        return serve()
    if mode == "run":
        sys.argv = ["run.py", *rest]
        from run import main as run_task

        return run_task()
    if mode == "login":
        sys.argv = ["login_mac.py", *rest]
        from scripts.login_mac import main as login

        return login()

    print(f"未知命令: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
