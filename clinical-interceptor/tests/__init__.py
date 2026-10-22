"""Tests. The interceptor package lives in src/ and the NLI service in nli_service/, so both are put on the path
here; no install or PYTHONPATH is needed."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
