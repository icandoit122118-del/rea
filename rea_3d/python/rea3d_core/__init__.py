"""Headless 3DS asset parsing and GLB conversion. SPDX-License-Identifier: GPL-3.0-or-later."""
from pathlib import Path
import sys

VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "n3ds_importer"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

__version__ = "0.1.0"
