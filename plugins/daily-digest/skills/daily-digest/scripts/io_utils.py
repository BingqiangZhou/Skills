"""Shared stdout/JSON-write helpers for the daily-digest orchestration scripts.

Sibling of the collectors' ``_common.py``, scoped to this skill's four
scripts. Covers two failure modes this pipeline actually hits:

- **Atomic writes.** ``save_json_atomic`` writes to a tmp file in the target
  directory and ``os.replace``s it into place, so a crash mid-write can never
  leave a truncated JSON that a downstream step then treats as corrupt input.
- **win32 stdout.`` The repo convention guard, so each script does not
  hand-roll it.
"""

import json
import os
import sys
from pathlib import Path


def setup_win32_stdout():
    """Reconfigure stdout to UTF-8 on Windows (repo convention)."""
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")


def save_json_atomic(path, data, indent=2):
    """Write ``data`` as JSON to ``path`` atomically (tmp + os.replace).

    ``os.replace`` is atomic on POSIX and on Windows when source and target
    are on the same volume — guaranteed here because the tmp file lives in
    the target's own directory.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)
