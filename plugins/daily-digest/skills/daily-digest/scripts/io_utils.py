"""Shared stdout/JSON-write/time helpers for the daily-digest scripts.

Sibling of the collectors' ``_common.py``, scoped to this skill's scripts.
Covers the failure modes and conventions this pipeline actually hits:

- **Atomic writes.** ``save_json_atomic`` writes to a tmp file in the target
  directory and ``os.replace``s it into place, so a crash mid-write can never
  leave a truncated JSON that a downstream step then treats as corrupt input.
- **win32 stdout.** The repo convention guard, so each script does not
  hand-roll it.
- **CST display time.** ``parse_iso``/``fmt_cst`` render timestamps in
  CST (UTC+8) — the repo-wide display convention; a naive timestamp is
  treated as CST (Chinese sources) rather than local machine time.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))


def setup_win32_stdout():
    """Reconfigure stdout to UTF-8 on Windows (repo convention)."""
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")


def parse_iso(ts):
    """Parse an ISO-8601 timestamp to an aware datetime, or None.

    Naive values (no offset) are pinned to CST — the monitored sources are
    Chinese, so pinning to machine-local time or UTC would silently shift
    rendered times by the machine's UTC offset.
    """
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return dt
    except ValueError:
        return None


def fmt_cst(ts, fmt="%Y-%m-%d %H:%M"):
    """Format an ISO timestamp in CST (default 'YYYY-MM-DD HH:MM'); '' if unparseable."""
    dt = parse_iso(ts)
    if dt is None:
        return ""
    return dt.astimezone(CST).strftime(fmt)


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
