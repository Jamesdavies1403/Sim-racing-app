import sys
from pathlib import Path

_ROOT = Path(__file__).parent
for _sub in ("scripts", "tests"):
    _path = str(_ROOT / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)
