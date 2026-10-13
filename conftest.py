import sys
from pathlib import Path

# Make `src` importable no matter which directory pytest is invoked from.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
