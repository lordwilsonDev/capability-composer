"""sys.path setup for the capability-composer's own tests.

Repo root goes on sys.path (namespace packages: registry, primitives, skills)
so the composer and the composed skill import identically under pytest and
when run as scripts from the repo root.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
