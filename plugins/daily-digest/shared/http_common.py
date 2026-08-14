"""HTTP/JSON primitives shared by the daily-digest plugin's collector skills.

The three collectors (rss-monitor, github-monitor, tool-update-monitor) each
kept a private copy of this stack; the copies had already drifted (different
fetch contracts, different SSL-downgrade triggers, a Retry-After parser that
crashed on HTTP-dates in two of the three). This module is the single home
for the pieces that are genuinely identical:

- the Chrome User-Agent string and the Chinese-encoding decode order
- SSL context creation (strict; no silent CERT_NONE fallback — a broken trust
  store must fail loudly, not silently disable verification)
- Retry-After parsing (delay-seconds OR HTTP-date, capped at 60s)
- atomic JSON writes (tmp + os.replace)

Skill-specific layers keep their own contracts on top: rss/github ``_common``
re-export from here, and each skill's fetch_url/github_get stays where it is.

Dependency-free: Python standard library only. Scripts reach this module by
putting ``<plugin_root>/shared`` on sys.path (see any ``_common.py``).
"""

import email.utils
import json
import os
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Fallback decode order for response bytes (handles Chinese GBK/GB2312 feeds).
# GBK before GB2312: GBK is a superset, so trying GB2312 after it is dead.
DECODE_ORDER = ["utf-8", "gbk", "latin-1"]


def decode_body(content):
    """Best-effort decode of response bytes using common Chinese encodings."""
    for encoding in DECODE_ORDER:
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


def create_ssl_context(relaxed=False):
    """Create an SSL context. ``relaxed=True`` disables certificate
    verification (retry paths only, after a TLS-specific failure).

    The non-relaxed path has NO fallback: an environment whose default
    context cannot be created should fail loudly, not silently downgrade to
    an unauthenticated connection.
    """
    ctx = ssl.create_default_context()
    if relaxed:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def retry_after_seconds(value, default, cap=60):
    """Parse a Retry-After header into seconds.

    Per RFC 7231 the value is either delay-seconds or an HTTP-date; an
    int-only parse raises ValueError on the HTTP-date form. The wait is
    capped so a hostile/buggy server cannot stall the run.
    Returns ``default`` when the value is missing or unparseable.
    """
    if value is None:
        return default
    value = value.strip()
    try:
        return min(max(int(value), 0), cap)
    except ValueError:
        pass
    try:
        target = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return default
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    delta = (target - datetime.now(timezone.utc)).total_seconds()
    if delta <= 0:
        return 0
    return min(delta, cap)


def save_json_atomic(path, data, indent=2):
    """Write ``data`` as JSON to ``path`` atomically (tmp + os.replace).

    A crash mid-write otherwise leaves a truncated file that the next run
    either silently discards (ETag history / baseline state lost) or treats
    as corrupt input.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def warn_unreadable(path, exc):
    """One-line stderr warning used by cache/state loaders on corrupt files."""
    print(f"WARNING: {path} unreadable ({type(exc).__name__}: {exc}); "
          f"starting empty.", file=sys.stderr)
