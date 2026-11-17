#!/usr/bin/python3
"""Start Lyrik without installing the package first."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lyrik.app import main

if __name__ == "__main__":
    raise SystemExit(main())
