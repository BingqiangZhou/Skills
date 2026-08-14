#!/usr/bin/env python3
"""Check monitored tools for new releases.

Reads the curated tool list from references/tools.json, fetches the latest
release(s) for each tool (GitHub Releases API, npm registry, or an HTML
changelog page), compares against the last-seen version recorded in
.last_seen.json, and writes any NEW releases to latest_updates.json.

Detection model: LAST-SEEN VERSION STATE. A release is "new" if its version
is newer than the last one recorded for that tool. The very first run (no
state file) is a BASELINE run: it records current versions and reports zero
new updates, so the first check doesn't spam 14 "new" entries.

Uses ETag/If-Modified-Since HTTP caching (the GitHub API and npm registry both
honor conditional requests, which lets unchanged endpoints return 304 and skip
parsing). Dependency-free: Python standard library only.
"""

import argparse
import html
import json
import os
import random
import re
import ssl
import sys
import threading
import time
import urllib.error as urllib_error
import urllib.request as urllib_request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Repo convention: display timestamps in CST (UTC+8).
CST = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# HTTP fetching with ETag / If-Modified-Since caching
# (mirrors the wechat-rss-monitor fetch_url pattern: cache is keyed by URL,
#  returns (body, status, cache_entry). 304 = not modified.)
# ---------------------------------------------------------------------------

# HTTP/JSON primitives come from the plugin-level shared module (single
# implementation for the three collector skills;
# see plugins/daily-digest/shared/http_common.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
import http_common  # noqa: E402

TIMEOUT = 30
_CHROME_UA = http_common.CHROME_UA
_DECODE_ORDER = http_common.DECODE_ORDER
_decode_body = http_common.decode_body
create_ssl_context = http_common.create_ssl_context


def fetch_url(url, cache=None, timeout=TIMEOUT, accept=None, user_agent=None,
              bearer_token=None):
    """Fetch a URL with ETag/If-Modified-Since support.

    Returns (body_text, status_code, new_cache_entry):
      - body_text is the decoded body (str), or None on 304 / error.
      - status_code is 304 for not-modified, -1 for network errors, else HTTP status.
      - new_cache_entry is a dict of cache headers to store (etag/last_modified),
        possibly empty.

    When ``bearer_token`` is given, an ``Authorization: Bearer <token>`` header
    is added — used for the GitHub Releases API so requests use the 5000/hour
    authenticated rate limit instead of the 60/hour anonymous one.

    On the first SSL failure, retries once with a relaxed (no-verify) context.
    """
    headers = {"User-Agent": user_agent or _CHROME_UA}
    if accept:
        headers["Accept"] = accept
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    cached = cache.get(url, {}) if cache else {}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]

    req = urllib_request.Request(url, headers=headers)
    new_cache = {}

    def _read(resp):
        body = _decode_body(resp.read())
        etag = resp.headers.get("ETag")
        last_mod = resp.headers.get("Last-Modified")
        if etag:
            new_cache["etag"] = etag
        if last_mod:
            new_cache["last_modified"] = last_mod
        return body, resp.status, new_cache

    try:
        ctx = create_ssl_context()
        with urllib_request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return _read(resp)
    except urllib_error.HTTPError as e:
        if e.code == 304:
            return None, 304, cached
        if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
            # GitHub signals primary rate-limit exhaustion with 403 +
            # X-RateLimit-Remaining: 0 (not 429). Sleep until the reset epoch
            # when it is close, then retry once; otherwise surface the 403 —
            # setting GITHUB_ACCESS_TOKEN is the real fix.
            try:
                delay = int(e.headers.get("X-RateLimit-Reset", 0)) - int(time.time())
            except (TypeError, ValueError):
                delay = -1
            if 0 <= delay <= 300:
                print(f"fetch_url: GitHub rate limit hit for {url}; "
                      f"sleeping {delay}s until reset", file=sys.stderr)
                time.sleep(delay + 2)
                try:
                    ctx = create_ssl_context()
                    with urllib_request.urlopen(req, context=ctx, timeout=timeout) as resp:
                        return _read(resp)
                except urllib_error.HTTPError as e2:
                    if e2.code == 304:
                        return None, 304, cached
                    return None, e2.code, {}
                except Exception:
                    return None, -1, {}
        return None, e.code, {}
    except Exception as first_err:
        # Retry once with a relaxed (no-verify) context ONLY when the failure
        # looks TLS-related — downgrading certificate verification must not
        # happen for plain timeouts/DNS/reset errors, where it cannot help.
        if "SSL" not in str(first_err) and "CERTIFICATE" not in str(first_err):
            print(f"fetch_url: request failed for {url}: "
                  f"{type(first_err).__name__}: {first_err}", file=sys.stderr)
            return None, -1, {}
        print(f"fetch_url: TLS failure for {url} "
              f"({type(first_err).__name__}); retrying with relaxed SSL",
              file=sys.stderr)
        try:
            relaxed = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            relaxed.check_hostname = False
            relaxed.verify_mode = ssl.CERT_NONE
            with urllib_request.urlopen(req, context=relaxed, timeout=timeout) as resp:
                return _read(resp)
        except urllib_error.HTTPError as e:
            if e.code == 304:
                return None, 304, cached
            return None, e.code, {}
        except Exception as retry_err:
            print(f"fetch_url: relaxed-SSL retry also failed for {url}: "
                  f"{type(retry_err).__name__}: {retry_err}", file=sys.stderr)
            return None, -1, {}


def fetch_url_with_retry(url, cache=None, timeout=TIMEOUT, max_retries=2,
                         accept=None, user_agent=None, bearer_token=None):
    """Fetch with retry + exponential backoff on transient errors.

    Retries on network errors (status -1), 429 (honoring Retry-After), and 5xx.
    Returns the same (body, status, cache_entry) tuple as fetch_url.
    """
    for attempt in range(max_retries + 1):
        body, status, new_cache = fetch_url(
            url, cache=cache, timeout=timeout, accept=accept, user_agent=user_agent,
            bearer_token=bearer_token,
        )
        if body is not None or status == 304:
            return body, status, new_cache
        # No body: terminal HTTP error or network error.
        if status in (429,) or (500 <= status < 600):
            if attempt < max_retries:
                # 429 backoff: honor Retry-After if we had headers; we don't here,
                # so use exponential + jitter (reasonable default).
                delay = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(delay)
                continue
        if status == -1 and attempt < max_retries:
            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)
            continue
        return None, status, new_cache
    return None, -1, {}


# ---------------------------------------------------------------------------
# Version parsing & comparison
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*")


def normalize_version(tag):
    """Strip a leading 'v' and return the version string (e.g. 'v2.5.1' -> '2.5.1').

    For tags that don't start with a clean number (e.g. Warp's
    'v0.2026.06.09.19.54.stable_04'), returns the leading numeric run joined
    by dots so the caller can still parse tuple components.
    """
    if not tag:
        return ""
    s = tag.strip()
    s = s.lstrip("vV")
    m = _SEMVER_RE.match(s)
    return m.group(0) if m else s


def version_tuple(v):
    """Convert a dotted version string to a tuple of ints for comparison.

    Non-numeric components are dropped (Warp's date tags degrade to a long
    numeric tuple, which is fine since Warp uses compare_by='date' anyway).
    Returns (0,) for empty/garbage input so comparisons never crash.
    """
    if not v:
        return (0,)
    parts = []
    for p in str(v).split("."):
        try:
            parts.append(int(p))
        except ValueError:
            # Stop at the first non-numeric component (e.g. 'stable_04').
            break
    return tuple(parts) if parts else (0,)


def pick_latest_release(tool, releases):
    """Pick the latest release from a list, respecting compare_by.

    GitHub returns releases ordered by created_at, NOT by version, so taking
    releases[0] can record a lower version as the baseline than a pre-release
    published slightly earlier (e.g. openclaw: stable 2026.7.1-2 at [0] while
    betas 2026.7.2-beta.* exist below it). That made the betas look "new" on
    every run. This helper picks by max version (semver) or max published_at
    (date) so the recorded baseline is the true latest, and dedup callers can
    pick the newest representative of a version.
    """
    if not releases:
        return None
    compare_by = tool.get("compare_by", "semver")
    if compare_by == "date":
        best = None
        best_dt = None
        for rel in releases:
            dt = parse_iso_datetime(rel.get("published_at"))
            if dt is None:
                continue
            if best_dt is None or dt > best_dt:
                best_dt = dt
                best = rel
        return best or releases[0]
    # semver: max version_tuple; ties broken by latest published_at.
    best = None
    best_key = None
    for rel in releases:
        key = (version_tuple(rel.get("version")),)
        dt = parse_iso_datetime(rel.get("published_at"))
        tie = dt or datetime.min.replace(tzinfo=timezone.utc)
        cur = (key, tie)
        if best_key is None or cur > best_key:
            best_key = cur
            best = rel
    return best or releases[0]


def parse_iso_datetime(s):
    """Parse an ISO-8601 timestamp (e.g. GitHub 'published_at' or npm time) to
    an aware datetime in UTC. Returns None on failure."""
    if not s:
        return None
    s = s.strip()
    # GitHub returns e.g. '2026-07-30T12:45:22Z'; fromisoformat handles the
    # 'Z' in 3.11+, replace it for older interpreters.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def parse_month_date(s):
    """Parse a 'Released Mon DD, YYYY' style date (e.g. 'Jul 27, 2026') from
    the zcode changelog. Assumes the current year timezone context. Returns an
    aware UTC datetime or None."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Source fetchers — each returns a dict describing the tool's latest release(s),
# or raises/returns an error. Shape returned:
#   {
#     "latest_version": "2.5.1",
#     "latest_published_at": "2026-05-20T...Z" | None,
#     "releases": [ {version, published_at, name, body, html_url, prerelease}, ... ]
#   }
# `releases` is ordered newest-first and is what "new since last-seen" is
# computed from. For npm/html there is exactly one entry (the current version).
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com/repos/{repo}/releases?per_page={per_page}"
NPM_API = "https://registry.npmjs.org/{pkg}"


def _github_url(repo, per_page=10):
    return GITHUB_API.format(repo=repo, per_page=per_page)


def fetch_github(tool, cache):
    """Fetch GitHub Releases for a tool. Returns (result_dict, status_str).

    Uses ``GITHUB_ACCESS_TOKEN`` if set (same env var as github-monitor) to get
    the 5000/hour authenticated rate limit instead of the 60/hour anonymous
    one. Without a token the request is still made, but concurrent GitHub
    tools may hit 403 secondary rate limits.
    """
    repo = tool["repo"]
    url = _github_url(repo)
    exclude_pre = tool.get("exclude_prerelease", False)
    token = os.environ.get("GITHUB_ACCESS_TOKEN")
    body, status, new_cache = fetch_url_with_retry(
        url, cache=cache, accept="application/vnd.github+json",
        user_agent="tool-update-monitor/1.0",
        bearer_token=token,
    )
    if status == 304:
        return None, "not_modified"
    if body is None:
        return None, f"HTTP {status}" if status != -1 else "network error"

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None, "invalid JSON"

    if isinstance(data, dict) and data.get("message"):
        # GitHub API error blob, e.g. {"message": "Not Found", ...} or rate-limit.
        msg = data.get("message", "")[:120]
        return None, f"API: {msg}"

    if not isinstance(data, list) or not data:
        return None, "no releases"

    releases = []
    tag_prefix = tool.get("tag_prefix")          # e.g. "rust-" to strip from codex tags
    tag_pattern = tool.get("tag_pattern")        # optional regex the tag must match
    tag_re = re.compile(tag_pattern) if tag_pattern else None
    for rel in data:
        if not isinstance(rel, dict):
            continue
        if exclude_pre and rel.get("prerelease"):
            continue
        tag = rel.get("tag_name") or ""
        # Optional whitelist: skip tags that don't match the expected scheme
        # (e.g. Warp occasionally publishes stray build-metadata tags that are
        # not real versions but aren't flagged prerelease).
        if tag_re and not tag_re.search(tag):
            continue
        if tag_prefix and tag.startswith(tag_prefix):
            tag_for_version = tag[len(tag_prefix):]
        else:
            tag_for_version = tag
        version = normalize_version(tag_for_version)
        if not version:
            continue
        releases.append({
            "version": version,
            "tag": tag,
            "published_at": rel.get("published_at") or rel.get("created_at"),
            "name": rel.get("name") or tag,
            # Some releases (e.g. VS Code) put only a link to the real notes
            # in the body — follow it and extract the page text.
            "body": _enrich_url_only_body(rel.get("body") or "", cache),
            "html_url": rel.get("html_url") or
                f"https://github.com/{repo}/releases/tag/{tag}",
            "prerelease": bool(rel.get("prerelease")),
        })

    if not releases:
        return None, "no matching releases (check prerelease/tag_pattern)"

    latest = pick_latest_release(tool, releases)
    return {
        "latest_version": latest["version"],
        "latest_published_at": latest["published_at"],
        "releases": releases,
    }, new_cache


def _fetch_github_release_body(repo, version, cache):
    """Fetch the release notes body for one version from a GitHub repo.

    Enriches an npm-sourced tool whose changelog lives on GitHub (e.g. Claude
    Code: version detected via npm, notes on anthropics/claude-code releases).
    Tries both ``v{version}`` and ``{version}`` tag forms — npm tags lack the
    ``v`` prefix GitHub releases often use. Returns the body text, or '' if the
    release / tag is not found (non-fatal — the tool still reports the version).
    """
    token = os.environ.get("GITHUB_ACCESS_TOKEN")
    for tag in (f"v{version}", version):
        url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
        try:
            rbody, rstatus, _ = fetch_url_with_retry(
                url, cache=cache, accept="application/vnd.github+json",
                user_agent="tool-update-monitor/1.0", bearer_token=token,
            )
        except Exception:
            continue
        if not rbody or rstatus == 304:
            continue
        try:
            data = json.loads(rbody)
        except (json.JSONDecodeError, ValueError):
            continue
        notes = (data.get("body") or "").strip()
        if notes:
            return notes
    return ""


def fetch_npm(tool, cache):
    """Fetch the latest version of an npm package from the registry. Returns
    (result_dict, status_str). npm has no per-release changelog; when
    ``changelog_repo`` is set, the release notes are pulled from that GitHub
    repo's release for the same version."""
    pkg = tool["package"]
    url = NPM_API.format(pkg=pkg)
    link = tool.get("link_url") or f"https://www.npmjs.com/package/{pkg}"
    body, status, new_cache = fetch_url_with_retry(url, cache=cache)
    if status == 304:
        return None, "not_modified"
    if body is None:
        return None, f"HTTP {status}" if status != -1 else "network error"
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None, "invalid JSON"

    dist_tags = data.get("dist-tags") or {}
    latest = dist_tags.get("latest")
    if not latest:
        return None, "no dist-tags.latest"
    time_map = data.get("time") or {}
    published_at = time_map.get(latest)

    # npm carries no changelog; pull notes from GitHub if a repo is configured.
    notes_body = ""
    changelog_repo = tool.get("changelog_repo")
    if changelog_repo:
        notes_body = _fetch_github_release_body(changelog_repo, latest, cache)

    return {
        "latest_version": latest,
        "latest_published_at": published_at,
        "releases": [{
            "version": latest,
            "tag": latest,
            "published_at": published_at,
            "name": f"{pkg}@{latest}",
            "body": notes_body,
            "html_url": link,
            "prerelease": False,
        }],
    }, new_cache


def _html_block_to_text(html_fragment, max_chars=2000):
    """Best-effort convert an HTML changelog fragment to readable text.

    Mirrors what GitHub release notes already give us pre-formatted: bullets
    for <li>, line breaks at block boundaries, tags stripped, entities
    unescaped. Used by fetch_html_changelog to fill a release ``body`` from a
    scraped page (which otherwise carries no notes at all, so the AI
    highlights used to say "no details provided").
    """
    s = html_fragment
    # drop non-content blocks wholesale (script/style/svg + page chrome)
    s = re.sub(r"<(script|style|svg|nav|header|footer|aside|noscript)\b[^>]*>.*?</\1>",
               " ", s, flags=re.S | re.I)
    # block-level closes → newline; <li> → bullet
    s = re.sub(r"</(p|div|li|h[1-6]|ul|ol|article|section|tr)>", "\n", s, flags=re.I)
    s = re.sub(r"<li\b[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    # strip remaining tags, unescape entities
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    # collapse inline whitespace, drop blank lines
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    lines = [ln for ln in lines if ln]
    s = "\n".join(lines)
    if len(s) > max_chars:
        s = s[:max_chars].rsplit("\n", 1)[0] + "\n…"
    return s.strip()


def _looks_like_url_only(s):
    """If `s` is just a single http(s) URL (optionally wrapped as a markdown
    link `[txt](url)`), return the bare URL; otherwise None. Used to detect
    GitHub releases whose body is only a pointer to the real notes page
    (e.g. VS Code: body = 'https://code.visualstudio.com/updates/v1_133')."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"\[[^\]]*\]\((https?://\S+)\)", s, re.I)
    if m:
        return m.group(1)
    if re.fullmatch(r"https?://\S+", s, re.I):
        return s
    return None


def _enrich_url_only_body(body, cache):
    """Follow a URL-only release body and extract the linked page's text.

    Some GitHub releases (VS Code is the canonical case) put nothing but a
    link to the official update notes in the release body, so the AI
    highlights used to say "release 未附明细". When the body is just such a
    URL, fetch it and convert the page to text so the real changes are
    summarized. On any failure, return the original body unchanged.
    """
    url = _looks_like_url_only(body)
    if not url:
        return body
    try:
        page_html, status, _ = fetch_url_with_retry(url, cache=cache)
    except Exception:
        return body
    if not page_html or status == 304:
        return body
    # Start at the first content landmark to skip nav/header chrome.
    low = page_html.lower()
    landmarks = [low.find(t) for t in ("<h1", "<main", "<article")]
    start = min((p for p in landmarks if p >= 0), default=0)
    extracted = _html_block_to_text(page_html[start:])
    return extracted or body


def fetch_html_changelog(tool, cache):
    """Fetch an HTML changelog page and scrape the top version (and its notes)
    with a regex. Best-effort: if the page is JS-rendered and the regex finds
    nothing, the tool is recorded as an error and the rest of the run
    continues."""
    url = tool["url"]
    version_re = re.compile(tool["version_regex"])
    date_re = re.compile(tool.get("date_regex", "")) if tool.get("date_regex") else None
    body, status, new_cache = fetch_url_with_retry(url, cache=cache)
    if status == 304:
        return None, "not_modified"
    if body is None:
        return None, f"HTTP {status}" if status != -1 else "network error"

    m = version_re.search(body)
    if not m:
        return None, "version not found in HTML (JS-rendered?)"
    version = m.group(1) if m.groups() else m.group(0)

    # Scrape this version's changelog notes. Prefer the <article> wrapper when
    # present (a clean content boundary — the next version panel's header UI
    # lives OUTSIDE the article, so it never bleeds in). Fall back to the text
    # between this version marker and the next one for pages without <article>.
    block_start = m.end()
    art_m = re.search(r"<article\b[^>]*>(.*?)</article>",
                      body[block_start:block_start + 8000], re.S | re.I)
    if art_m:
        release_notes = _html_block_to_text(art_m.group(1))
    else:
        next_m = version_re.search(body, block_start)
        block_end = next_m.start() if next_m else min(len(body), block_start + 6000)
        release_notes = _html_block_to_text(body[block_start:block_end])

    published_at = None
    if date_re:
        dm = date_re.search(body)
        if dm:
            dt = parse_month_date(dm.group(1) if dm.groups() else dm.group(0))
            if dt:
                published_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "latest_version": version,
        "latest_published_at": published_at,
        "releases": [{
            "version": version,
            "tag": f"v{version}" if not version.startswith("v") else version,
            "published_at": published_at,
            "name": (tool.get("display_name", "Release v{version}")
                     .replace("{version}", version)),
            "body": release_notes,
            "html_url": tool.get("link_url") or url,
            "prerelease": False,
        }],
    }, new_cache


# ---------------------------------------------------------------------------
# Operating-system table scrapers (Windows release-health, macOS Wikipedia).
# These pages publish update info in HTML tables rather than a release feed,
# so we extract rows with the stdlib html.parser. Each tool entry in tools.json
# picks ONE row of interest (e.g. Windows "25H2", macOS "Tahoe") via a filter.
# ---------------------------------------------------------------------------

from html.parser import HTMLParser


class _TableParser(HTMLParser):
    """Minimal HTML table extractor (stdlib only).

    Collects all <table> elements into self.tables, each as a list of rows;
    each row is a list of cells. Header cells use <th>, data <td>, both
    captured. Each cell is ``{"text": str, "hrefs": [str]}`` — the anchor
    hrefs matter because Microsoft's "Latest update" cell hides the KB
    number in a support.microsoft.com/help/<KB> link, not in the cell text.
    Robust to nested tags.
    """

    def __init__(self):
        super().__init__()
        self.tables = []
        self._cur_table = None
        self._cur_row = None
        self._cur_cell = None
        self._cell_pieces = None
        self._cell_hrefs = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._cur_table = []
        elif tag == "tr" and self._cur_table is not None:
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._cur_cell = tag
            self._cell_pieces = []
            self._cell_hrefs = []
        elif tag == "a" and self._cur_cell is not None:
            for k, v in attrs:
                if k == "href" and v:
                    self._cell_hrefs.append(v)

    def handle_endtag(self, tag):
        if tag == "table" and self._cur_table is not None:
            self.tables.append(self._cur_table)
            self._cur_table = None
        elif tag == "tr" and self._cur_row is not None:
            self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif tag in ("td", "th") and self._cur_cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell_pieces)).strip()
            self._cur_row.append({"text": text, "hrefs": self._cell_hrefs})
            self._cur_cell = None
            self._cell_pieces = None
            self._cell_hrefs = None

    def handle_data(self, data):
        if self._cell_pieces is not None:
            self._cell_pieces.append(data)


def _parse_tables(html_text):
    p = _TableParser()
    try:
        p.feed(html_text)
    except Exception:
        pass
    return p.tables


def _iso_date(s):
    """Parse 'YYYY-MM-DD' (ISO 8601, used by both sources) to an ISO datetime
    string at 00:00:00 UTC. Returns None if it doesn't match."""
    if not s:
        return None
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if not m:
        return None
    try:
        datetime.strptime(m.group(0), "%Y-%m-%d")
        return m.group(0) + "T00:00:00Z"
    except ValueError:
        return None


def fetch_windows(tool, cache):
    """Scrape Microsoft's Windows 11 release-health "current versions" table.

    tools.json selects which version to track via `win_version` (e.g. "25H2").
    The page's "Servicing channels" table has columns:
      Version | Servicing option | Availability date | ... | Latest update |
      Latest revision date | Latest build
    We extract the matching version's latest build + KB + revision date.
    """
    url = tool["url"]
    want = tool.get("win_version", "25H2")
    body, status, new_cache = fetch_url_with_retry(url, cache=cache)
    if status == 304:
        return None, "not_modified"
    if body is None:
        return None, f"HTTP {status}" if status != -1 else "network error"

    tables = _parse_tables(body)
    # Find the "current versions" table by looking for one whose header row
    # contains 'Latest build'.
    target = None
    for t in tables:
        if not t:
            continue
        header = " ".join(c["text"] for c in t[0]) if t[0] else ""
        if "Latest build" in header:
            target = t
            break
    if target is None:
        return None, "current-versions table not found"

    # Column index lookup from the header row.
    header = [c["text"] for c in target[0]]

    def col(name):
        for i, h in enumerate(header):
            if name.lower() in h.lower():
                return i
        return None

    vi, build_i, date_i, update_i = (col("Version"), col("Latest build"),
                                     col("Latest revision date"),
                                     col("Latest update"))
    if None in (vi, build_i, date_i):
        return None, "expected columns not found"

    for row in target[1:]:
        if len(row) <= max(vi, build_i, date_i):
            continue
        version = row[vi]["text"].strip()
        if want.lower() not in version.lower():
            continue
        build = re.sub(r"[^\d.]", "", row[build_i]["text"]).strip()
        if not build:
            continue
        published = _iso_date(row[date_i]["text"])
        # KB + link: the "Latest update" cell text is like "2026-07 D" with an
        # anchor to support.microsoft.com/help/<KB> — the KB number lives in
        # the HREF, not the cell text, so prefer the link and fall back to a
        # text regex.
        kb = ""
        if update_i is not None and update_i < len(row):
            cell = row[update_i]
            for href in cell.get("hrefs", []):
                km = re.search(r"/help/(?:kb)?(\d{4,})", href, re.IGNORECASE)
                if km:
                    kb = f"KB{km.group(1)}"
                    break
            if not kb:
                km = re.search(r"KB\s*(\d{4,})", cell["text"], re.IGNORECASE)
                if km:
                    kb = f"KB{km.group(1)}"
        name = f"Windows 11 {version} (build {build})"
        if kb:
            name += f" · {kb}"
        # Build numbers compare correctly as dotted version tuples.
        return {
            "latest_version": build,
            "latest_published_at": published,
            "releases": [{
                "version": build,
                "tag": build,
                "published_at": published,
                "name": name,
                "body": f"Windows 11 {version} latest cumulative update; "
                        f"build {build}" + (f", {kb}" if kb else "")
                        + f", revised {row[date_i]['text']}.",
                "html_url": tool.get("link_url") or url,
                "prerelease": False,
                "kb": kb,
                "win_version": version,
            }],
        }, new_cache

    return None, f"version {want} not in current-versions table"


_FETCHERS = {
    "github": fetch_github,
    "npm": fetch_npm,
    "html_changelog": fetch_html_changelog,
    "windows": fetch_windows,
}


# ---------------------------------------------------------------------------
# Core per-tool check
# ---------------------------------------------------------------------------

def _classify_error(status_str):
    """Bucket a status string into a short error category for metadata stats."""
    s = status_str.lower()
    if "http 404" in s or "not found" in s:
        return "HTTP 404"
    if "http 403" in s or "forbidden" in s or "rate limit" in s:
        return "HTTP 403 / rate limit"
    if s.startswith("http 4"):
        return "HTTP 4xx"
    if s.startswith("http 5"):
        return "HTTP 5xx"
    if "network" in s or "timeout" in s:
        return "network error"
    if "js-rendered" in s:
        return "scrape failed (JS-rendered)"
    return "other"


def _compute_new_releases(tool, releases, prev):
    """Given the current releases list and the last-seen state entry, return the
    subset that is NEW (newer than the last-seen version), newest first.

    A release is "new" if:
      - compare_by='date': its published_at is after the last-seen published_at
        (falls back to version inequality if either date is missing).
      - otherwise (semver): its version tuple is greater than the last-seen one.

    DEDUP: releases are first grouped by normalized version. Within a version
    group only the newest (by published_at) representative is kept. This folds
    cases like openclaw's v2026.7.2-beta.1..beta.7 — which all normalize to
    2026.7.2 — into a single entry instead of seven.
    """
    prev_version = prev.get("version")
    if not prev_version:
        return []
    compare_by = tool.get("compare_by", "semver")

    # Dedup by normalized version, keeping the newest release in each group.
    by_version = {}
    for rel in releases:
        v = rel.get("version") or ""
        existing = by_version.get(v)
        if existing is None:
            by_version[v] = rel
            continue
        existing_dt = parse_iso_datetime(existing.get("published_at"))
        rel_dt = parse_iso_datetime(rel.get("published_at"))
        if rel_dt is not None and (existing_dt is None or rel_dt > existing_dt):
            by_version[v] = rel

    new_releases = []
    if compare_by == "date":
        prev_dt = parse_iso_datetime(prev.get("published_at"))
        for rel in by_version.values():
            rel_dt = parse_iso_datetime(rel.get("published_at"))
            if rel_dt and prev_dt:
                if rel_dt > prev_dt:
                    new_releases.append(rel)
            elif version_tuple(rel["version"]) != version_tuple(prev_version):
                new_releases.append(rel)
    else:
        prev_tuple = version_tuple(prev_version)
        for rel in by_version.values():
            if version_tuple(rel["version"]) > prev_tuple:
                new_releases.append(rel)
    new_releases.sort(
        key=lambda r: parse_iso_datetime(r.get("published_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return new_releases


def check_one_tool(tool, cache, state):
    """Fetch one tool and decide which releases are NEW since last-seen.

    Returns a dict:
      {"ok": True/False, "status": str, "tool": tool,
       "result": {...} | None,      # fetch result on success
       "new_releases": [...],       # releases newer than last-seen
       "latest": {...} | None,      # the latest release (for state recording)
       "releases": [...] | None}    # full release list (to persist as snapshot)
    """
    src = tool.get("source_type")
    fetcher = _FETCHERS.get(src)
    tid = tool["id"]

    if fetcher is None:
        return {"ok": False, "status": f"unknown source_type: {src}",
                "tool": tool, "result": None, "new_releases": [],
                "latest": None, "releases": None}

    try:
        result, ret = fetcher(tool, cache)
    except Exception as e:  # defensive: never let one tool crash the run
        return {"ok": False, "status": f"fetcher exception: {e}",
                "tool": tool, "result": None, "new_releases": [],
                "latest": None, "releases": None}

    prev = state.get(tid, {})

    # Deadlock guard: 304 says "unchanged", but we hold no state at all for
    # this tool (fresh/corrupt/deleted state file, or --force-baseline, which
    # passes an empty state). The snapshot-recompute path below would then
    # yield latest=None, which the state-write loop in main() skips — so the
    # state would stay empty forever: every run a spurious baseline and
    # --force-baseline a silent no-op, until the ETag happens to expire.
    # Re-fetch once ignoring the ETag; the fresh body then lands in both the
    # state snapshot and the cache, breaking the cycle.
    if ret == "not_modified" and not (prev.get("releases") or prev.get("version")):
        try:
            result, ret = fetcher(tool, {})
        except Exception as e:
            return {"ok": False, "status": f"refetch after empty-state 304: {e}",
                    "tool": tool, "result": None, "new_releases": [],
                    "latest": None, "releases": None}

    # 304 Not Modified: the upstream release list is unchanged, so recompute
    # NEW releases against the release snapshot we persisted last successful run.
    # This keeps detection correct even when the API only returns a cache hit.
    if ret == "not_modified":
        cached_releases = prev.get("releases") or []
        latest = pick_latest_release(tool, cached_releases) if cached_releases else {
            "version": prev.get("version"),
            "published_at": prev.get("published_at"),
        }
        return {"ok": True, "status": "not_modified",
                "tool": tool, "result": None,
                "new_releases": _compute_new_releases(tool, cached_releases, prev),
                "latest": latest, "releases": cached_releases}

    if result is None:
        return {"ok": False, "status": ret, "tool": tool,
                "result": None, "new_releases": [],
                "latest": None, "releases": None}

    releases = result["releases"]
    latest = pick_latest_release(tool, releases) if releases else None

    return {"ok": True, "status": "ok",
            "tool": tool, "result": result,
            "new_releases": _compute_new_releases(tool, releases, prev),
            "latest": latest, "releases": releases,
            "cache_entry": ret if isinstance(ret, dict) else {}}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_json(path, default=None):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        # Corrupt state/cache is survivable (worst case: a fresh baseline),
        # but say so instead of degrading silently.
        print(f"WARNING: {path} unreadable ({type(e).__name__}: {e}); "
              f"using default.", file=sys.stderr)
        return default


def save_json(path, data):
    """Write JSON atomically (tmp + os.replace) — shared implementation.

    A crash mid-write of the state file would otherwise leave truncated JSON
    that the next run silently treats as empty — resetting every baseline.
    """
    http_common.save_json_atomic(path, data)


def main():
    parser = argparse.ArgumentParser(
        description="Check monitored tools for new releases (GitHub/npm/HTML)."
    )
    here = Path(__file__).resolve().parent.parent
    parser.add_argument("--tools", default=str(here / "references" / "tools.json"),
                        help="Path to tools.json (default: references/tools.json)")
    parser.add_argument("--hours", type=int, default=168,
                        help="Time window in hours for the report header (default: 168 = 1 week)")
    parser.add_argument("--workers", type=int, default=6,
                        help="Concurrent workers (default: 6)")
    parser.add_argument("--output", required=True,
                        help="Output latest_updates.json path")
    parser.add_argument("--cache", required=True,
                        help="HTTP cache (.http_cache.json) path")
    parser.add_argument("--state", required=True,
                        help="Last-seen state (.last_seen.json) path")
    parser.add_argument("--category", default=None,
                        help="Filter tools by category (substring match)")
    parser.add_argument("--tool", default=None,
                        help="Filter to a single tool id")
    parser.add_argument("--force-baseline", action="store_true",
                        help="Re-record current versions as baseline; report 0 new")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    config = load_json(args.tools)
    if not config or not isinstance(config, dict) or not config.get("tools"):
        print(f"ERROR: could not load tools from {args.tools}", file=sys.stderr)
        sys.exit(2)

    tools = config["tools"]
    cat_order = config.get("categories_order", [])
    if args.category:
        tools = [t for t in tools if args.category.lower() in t.get("category", "").lower()]
    if args.tool:
        tools = [t for t in tools if t["id"] == args.tool]
    if not tools:
        print("No tools matched the --category/--tool filter.", file=sys.stderr)
        sys.exit(0)

    # Load cache + state.
    cache = load_json(args.cache, default={}) or {}
    state = load_json(args.state, default={}) or {}
    is_baseline = (not state) or args.force_baseline
    now = datetime.now(timezone.utc)

    # State-path visibility: surface the ABSOLUTE path of the state file and how
    # many tools were already tracked there. Without this, a wrong --state path
    # (e.g. when an orchestrator mis-resolves {project_root}) silently produces
    # an empty state, which turns every run into a spurious "baseline" run —
    # the state file still gets written, but to the wrong place, so the next
    # run sees an empty state again and never detects real version changes.
    state_path_abs = str(Path(args.state).resolve())
    # Capture BEFORE the state-write loop mutates `state` later in main(); the
    # output metadata needs the count as it was at the start of this run.
    state_prior_count = len(state)
    print(f"State file: {state_path_abs}")
    print(f"State held {state_prior_count} tool(s) before this run. "
          f"{'BASELINE run' if is_baseline else 'Normal run (have prior state)'}.")
    print(f"Monitoring {len(tools)} tool(s).")

    # Concurrency: shared cache guarded by a lock (each tool updates its own URL).
    cache_lock = threading.Lock()
    results = []
    results_lock = threading.Lock()

    def worker(tool):
        # Pass a view of the cache for this tool's URL; fetchers read/write by URL key.
        r = check_one_tool(tool, cache, state if not args.force_baseline else {})
        # Merge any new cache entry returned.
        ce = r.get("cache_entry") or {}
        if ce:
            url = _url_for_tool(tool)
            with cache_lock:
                cache[url] = ce
        with results_lock:
            results.append(r)
        status = r["status"]
        n = len(r["new_releases"])
        tag = "NEW" if n else ("ok" if r["ok"] else "ERR")
        print(f"  [{tag:<3}] {tool['id']:<16} {status}"
              + (f"  ({n} new)" if n else ""))

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(worker, tools))

    # Build the output.
    updates = []
    error_details = {}
    error_count = 0
    not_modified_count = 0
    checked_count = len(tools)

    for r in results:
        tool = r["tool"]
        if not r["ok"]:
            error_count += 1
            cat = _classify_error(r["status"])
            error_details[cat] = error_details.get(cat, 0) + 1
            continue
        if r["status"] == "not_modified":
            not_modified_count += 1
        # On baseline runs, record current versions but emit no updates.
        if is_baseline:
            continue
        new_rels = r["new_releases"]
        if not new_rels:
            continue
        # _compute_new_releases already deduped by version (newest→oldest),
        # so new_rels holds the tool's DISTINCT new versions. Collapse them
        # into ONE entry per tool: representative = newest version; a `versions`
        # array (oldest→newest) is added only when more than one distinct
        # version is new, so the renderer can show a range like
        # `v2.0` → `v2.1` instead of N separate blocks.
        prev = state.get(tool["id"], {})
        rep = new_rels[0]
        all_versions = [rel.get("version", "") for rel in new_rels]
        entry = {
            "tool_id": tool["id"],
            "name": tool["name"],
            "category": tool["category"],
            "source_type": tool["source_type"],
            "version": rep["version"],
            "previous_version": prev.get("version"),
            "published_at": rep.get("published_at"),
            "name_release": rep.get("name"),
            "body": rep.get("body") or "",
            "html_url": rep.get("html_url"),
            "prerelease": rep.get("prerelease", False),
        }
        if len(all_versions) > 1:
            entry["versions"] = list(reversed(all_versions))  # oldest→newest
        updates.append(entry)

    # Update last-seen state: record the latest version + the release snapshot
    # for every tool that produced a result this run (even on baseline). The
    # snapshot lets a later 304-Not-Modified response still recompute "new"
    # releases without re-fetching the list. Keep prior state for tools that
    # errored.
    for r in results:
        if not r["ok"]:
            continue
        latest = r.get("latest")
        if latest and latest.get("version"):
            tid = r["tool"]["id"]
            state[tid] = {
                "version": latest["version"],
                "published_at": latest.get("published_at"),
                "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "releases": r.get("releases") or [],
            }

    # Persist cache + state.
    save_json(args.cache, cache)
    save_json(args.state, state)

    output = {
        "metadata": {
            "checked_count": checked_count,
            "error_count": error_count,
            "not_modified_count": not_modified_count,
            "error_details": error_details,
            "hours": args.hours,
            "update_count": len(updates),
            "baseline_run": is_baseline,
            "check_time": now.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S CST"),
            "categories_order": cat_order,
            # Diagnoses spurious baseline runs: if this baseline was produced
            # with a --state path that previously held 0 tools but the file at
            # that same path was non-empty beforehand, an upstream path
            # mis-resolution (e.g. an orchestrator mis-substituting
            # {project_root}) is almost certainly pointing --state at the wrong
            # location.
            "state_path": state_path_abs,
            "state_prior_count": state_prior_count,
        },
        "updates": updates,
    }
    save_json(args.output, output)

    # Console summary.
    print()
    print("=" * 60)
    if is_baseline:
        print(f"BASELINE recorded for {checked_count} tool(s). "
              f"No 'new' updates reported on the first run.")
    else:
        print(f"Checked {checked_count} tool(s): "
              f"{len(updates)} new release(s), {error_count} error(s), "
              f"{not_modified_count} not-modified.")
    if error_details:
        print(f"Errors: {error_details}")
    if error_count / max(checked_count, 1) > 0.2:
        print("WARNING: error rate above 20% — some tools may have changed "
              "their source layout or need attention.", file=sys.stderr)
    print(f"Output: {args.output}")
    print(f"State : {state_path_abs}")
    print("=" * 60)


def _url_for_tool(tool):
    """The URL whose ETag/Last-Modified we cache for a tool."""
    src = tool.get("source_type")
    if src == "github":
        return _github_url(tool["repo"])
    if src == "npm":
        return NPM_API.format(pkg=tool["package"])
    if src in ("html_changelog", "windows"):
        return tool["url"]
    return ""


if __name__ == "__main__":
    main()
