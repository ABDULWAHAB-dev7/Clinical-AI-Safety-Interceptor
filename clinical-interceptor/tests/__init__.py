"""Tests. The package lives in src/, so it is put on the path here; no install or PYTHONPATH is needed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
