"""Source-checkout entry point for the read-only chat-list sync."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.contacts import main

if __name__ == "__main__":
    raise SystemExit(main())
