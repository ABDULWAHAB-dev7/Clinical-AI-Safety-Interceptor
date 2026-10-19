"""Where the data files live: knowledge/, config/, db/, fixtures/ at the project root, next to src/.

INTERCEPTOR_HOME overrides the location, for a deployment that keeps data elsewhere.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("INTERCEPTOR_HOME") or Path(__file__).resolve().parents[2])
