from __future__ import annotations

import os
import shutil
from pathlib import Path


source = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]).expanduser().resolve()
destination = Path("dist/ms-playwright")
if not source.is_dir():
    raise SystemExit(f"Playwright browser directory does not exist: {source}")
shutil.rmtree(destination, ignore_errors=True)
shutil.copytree(source, destination)
