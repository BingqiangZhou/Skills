"""Shared HTTP/parsing helpers for the rss-monitor skill.

Consolidates the copy-pasted utilities from the former wechat-rss-monitor,
tech-daily, and podcast-rss-monitor skills into one dependency-free module
(Python standard library only).

Design decisions (see plan):
- fetch_url adopts the dict-return contract (None on 304, dict otherwise)
  with built-in retry — the most featureful version, already used by
  podcast-rss-monitor and github-monitor.
- _HTMLStripper uses the wechat version (most complete entity handling,
  including html.unescape fallback for the long tail of named entities).
- parse_rss_items / parse_rss_date use the tech-daily/wechat versions
  (RSS 2.0 + Atom support, suffix-stripping date fallback).
"""

import html
import json
import re
import sys
import time
import random
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

# Plugin-level shared HTTP/JSON primitives (single implementation for the
# three collector skills; see plugins/daily-digest/shared/http_common.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))
import http_common  # noqa: E402

# Repo convention: timestamps are CST (UTC+8). Chinese feeds that omit a
# timezone offset are emitting local (CST) time, so naive values pin to CST.
CST = timezone(timedelta(hours=8))

# Shared defaults
TIMEOUT = 30
MAX_RETRIES = 2

# Re-exported under the historical private names — sibling scripts
# (fetch_feed_list / fetch_podcast_list / fetch_articles) import these.
_CHROME_UA = http_common.CHROME_UA
_DECODE_ORDER = http_common.DECODE_ORDER
_decode_body = http_common.decode_body
create_ssl_context = http_common.create_ssl_context
_retry_after_seconds = http_common.retry_after_seconds
save_json_atomic = http_common.save_json_atomic


# ---------------------------------------------------------------------------
# SSL / HTTP
# ---------------------------------------------------------------------------
# create_ssl_context / _decode_body / _retry_after_seconds / save_json_atomic
# live in the plugin-level shared module (see imports above); only the
# feed-fetching contract with retry/ETag semantics is defined here.


def fetch_url(url, etag=None, last_modified=None, accept=None,
              max_retries=MAX_RETRIES, timeout=TIMEOUT):
    """Fetch a URL with ETag/If-Modified-Since support and retry.

    Args:
        url: target URL.
        etag / last_modified: optional conditional-request headers.
        accept: optional Accept header value. Defaults to a permissive RSS/XML
            value (suitable for feed + HTML pages).
        max_retries: number of retries on transient errors.
        timeout: per-request timeout in seconds.

    Returns:
        - None when the server responds 304 Not Modified.
        - Otherwise a dict: {'content': str, 'etag': str|None, 'last_modified': str|None}.

    Raises:
        urllib.error.HTTPError for non-304/429/5xx terminal statuses.
        urllib.error.URLError / OSError for terminal network failures.
    """
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": accept or "application/rss+xml, application/xml, text/xml, */*",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            # Use relaxed SSL context on retry when the previous error was SSL-related.
            use_relaxed = attempt > 0 and last_error and "SSL" in str(last_error)
            ctx = create_ssl_context(relaxed=use_relaxed)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                if response.status == 304:
                    return None  # Not Modified
                content = response.read()
                return {
                    "content": _decode_body(content),
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                }
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 304:
                return None  # Not Modified — not an error
            if e.code == 429 and attempt < max_retries:
                wait = _retry_after_seconds(
                    e.headers.get("Retry-After"), 2 * (attempt + 1))
                time.sleep(wait + random.uniform(0, 1))
                continue
            if e.code >= 500 and attempt < max_retries:
                time.sleep((2 ** attempt) + random.uniform(0, 1))
                continue
            raise
        except (urllib.error.URLError, OSError) as e:
            last_error = e
            if attempt < max_retries:
                time.sleep((2 ** attempt) + random.uniform(0, 1))
                continue
            raise


# ---------------------------------------------------------------------------
# HTML → plain text
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """HTML to plain text parser with block tag awareness and entity handling.

    This is the wechat-rss-monitor version, which has the most complete entity
    handling (html.unescape fallback for the long tail of named entities like
    hellip, ldquo, rdquo, middot, rsaquo that are common in Chinese articles).
    """

    BLOCK_TAGS = frozenset({
        "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "tr", "ul", "ol", "table", "hr", "section", "article",
    })
    SKIP_TAGS = frozenset({"style", "script"})

    def __init__(self):
        super().__init__()
        self._pieces = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS and self._pieces and self._pieces[-1] != "\n":
            self._pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._pieces.append(data)

    def handle_entityref(self, name):
        """Handle named entities like &amp; &nbsp; &hellip;"""
        if self._skip_depth > 0:
            return
        # Fast path for common entities, then fall back to html.unescape for
        # the long tail (hellip, ldquo, rdquo, middot, rsaquo, etc.) which
        # WeChat articles contain frequently.
        entities = {"amp": "&", "lt": "<", "gt": ">", "nbsp": "", "quot": '"',
                    "apos": "'", "mdash": "—", "ndash": "–", "bull": "•"}
        if name in entities:
            self._pieces.append(entities[name])
        else:
            decoded = html.unescape(f"&{name};")
            self._pieces.append(decoded)

    def handle_charref(self, name):
        """Handle numeric entities like &#39; &#x27;"""
        if self._skip_depth > 0:
            return
        try:
            if name.startswith("x") or name.startswith("X"):
                char = chr(int(name[1:], 16))
            else:
                char = chr(int(name))
            self._pieces.append(char)
        except (ValueError, OverflowError):
            self._pieces.append(f"&#{name};")

    def get_text(self):
        text = "".join(self._pieces)
        text = text.replace("\xa0", " ")  # non-breaking space -> regular space
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def strip_html(html_text):
    """Convert HTML string to plain text."""
    if not html_text:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html_text)
    return stripper.get_text()


def fmt_cst(dt, fmt="%Y-%m-%d %H:%M"):
    """Render a datetime in CST (UTC+8) — the repo's display convention.

    Accepts None (returns ""). Without the astimezone conversion, items whose
    feed carried an explicit UTC offset rendered as UTC wall time while naive
    (CST-pinned) items rendered as CST — mixed clocks in one report.
    """
    if dt is None:
        return ""
    return dt.astimezone(CST).strftime(fmt)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_RSS_DATE_FORMATS = [
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S GMT",
    "%a, %d %b %Y %H:%M:%S",  # RFC822 without timezone (some RSS feeds)
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
]


def parse_rss_date(date_str):
    """Parse an RSS/Atom date string into a timezone-aware datetime.

    Returns None on failure. Tries common RSS date formats, then strips
    timezone suffixes and retries. Naive datetimes are pinned to CST — the
    monitored feeds are Chinese sources whose offset-less dates are local
    (UTC+8) time; pinning them to UTC made items look 8 hours stale and the
    time-window filter dropped fresh content.
    """
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in _RSS_DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST)
            return dt
        except ValueError:
            continue
    # Try stripping extra timezone text
    for suffix in (" +0000", " -0000", " UTC", " GMT"):
        if date_str.endswith(suffix):
            date_str = date_str[: -len(suffix)]
            for fmt in _RSS_DATE_FORMATS:
                try:
                    dt = datetime.strptime(date_str, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=CST)
                    return dt
                except ValueError:
                    continue
    return None


# ---------------------------------------------------------------------------
# Duration parsing (podcast itunes:duration)
# ---------------------------------------------------------------------------

def parse_duration(value):
    """Parse a podcast duration string into seconds (int).

    Handles three formats commonly used in ``itunes:duration``:
      - Pure seconds:  "3725"
      - MM:SS:         "62:05"
      - HH:MM:SS:      "01:02:05"

    Returns None on failure.
    """
    if not value:
        return None
    value = value.strip()
    # Pure integer seconds
    try:
        return int(value)
    except ValueError:
        pass
    # Colon-separated time
    parts = value.split(":")
    if not all(p.isdigit() for p in parts) or not (2 <= len(parts) <= 3):
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    # Right-align to H:M:S
    while len(nums) < 3:
        nums.insert(0, 0)
    hours, minutes, seconds = nums
    return hours * 3600 + minutes * 60 + seconds


# ---------------------------------------------------------------------------
# RSS / Atom parsing
# ---------------------------------------------------------------------------

def parse_rss_items(xml_text):
    """Parse RSS 2.0 or Atom XML into a list of item dicts.

    Handles both RSS 2.0 (<item>) and Atom (<entry>) feeds. Each item dict
    may contain: title, link, pub_date_raw, description, content_encoded,
    duration_raw (podcast itunes:duration).
    """
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    # RSS 2.0
    for item in root.iter("item"):
        entry = {}

        title_el = item.find("title")
        if title_el is not None and title_el.text:
            entry["title"] = title_el.text.strip()

        link_el = item.find("link")
        if link_el is not None and link_el.text:
            entry["link"] = link_el.text.strip()

        pub_el = item.find("pubDate")
        if pub_el is not None and pub_el.text:
            entry["pub_date_raw"] = pub_el.text.strip()

        desc_el = item.find("description")
        if desc_el is not None and desc_el.text:
            entry["description"] = desc_el.text.strip()

        # content:encoded (namespaced)
        for child in item:
            tag = child.tag
            if tag.endswith("}encoded") or tag == "content:encoded":
                if child.text:
                    entry["content_encoded"] = child.text.strip()
                break

        # itunes:duration (namespaced) — podcast episode length
        for child in item:
            if child.tag.endswith("}duration") and child.text:
                entry["duration_raw"] = child.text.strip()
                break

        items.append(entry)

    # Atom feeds (entry elements) as fallback — only when the document had no
    # RSS <item> elements: a hybrid feed would otherwise be parsed by both
    # passes and every entry double-counted.
    if not items:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            item = {}

            title_el = entry.find("atom:title", ns)
            if title_el is not None and title_el.text:
                item["title"] = title_el.text.strip()

            # Prefer rel="alternate" (the article itself). Taking the FIRST
            # link used to grab rel="enclosure"/rel="related" hrefs when they
            # appear first, pointing the item at media/related URLs instead
            # of the article.
            link = ""
            for link_el in entry.findall("atom:link", ns):
                href = (link_el.get("href") or "").strip()
                if not href:
                    continue
                rel = (link_el.get("rel") or "alternate").lower()
                if rel == "alternate":
                    link = href
                    break
                if not link:
                    link = href
            if link:
                item["link"] = link

            # Prefer published, fall back to updated
            published_el = entry.find("atom:published", ns)
            updated_el = entry.find("atom:updated", ns)
            date_el = published_el or updated_el
            if date_el is not None and date_el.text:
                item["pub_date_raw"] = date_el.text.strip()

            summary_el = entry.find("atom:summary", ns)
            content_el = entry.find("atom:content", ns)
            desc_el = content_el or summary_el
            if desc_el is not None and desc_el.text:
                item["description"] = desc_el.text.strip()

            items.append(item)

    return items


# ---------------------------------------------------------------------------
# Xiaoyuzhou (小宇宙) parsing — podcast-specific, used by check_updates + resolver
# ---------------------------------------------------------------------------

def parse_xiaoyuzhou_next_data(html_content):
    """Extract and parse the __NEXT_DATA__ JSON from a Xiaoyuzhou HTML page.

    Returns the parsed page-data dict, or None on failure.
    """
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html_content,
        re.DOTALL,
    )
    if not match:
        return None

    try:
        page_data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None

    try:
        page_data["props"]["pageProps"]["podcast"]["episodes"]
        return page_data
    except (KeyError, TypeError):
        return None


def extract_xiaoyuzhou_episodes(html_content):
    """Return the episodes array from a Xiaoyuzhou page, or [] on failure."""
    page_data = parse_xiaoyuzhou_next_data(html_content)
    if page_data is None:
        return []
    return page_data["props"]["pageProps"]["podcast"]["episodes"]


def parse_iso8601_date(date_str):
    """Parse an ISO 8601 date string (e.g. Xiaoyuzhou's '2024-11-04T04:19:30.561Z').

    Returns a timezone-aware datetime, or None on failure.

    Uses datetime.fromisoformat which natively handles fractional seconds
    (milliseconds). The trailing 'Z' is converted to '+00:00' for compatibility.
    Naive values are pinned to CST (repo convention for Chinese sources).
    """
    if not date_str:
        return None
    try:
        normalized = date_str.replace("Z", "+00:00") if date_str.endswith("Z") else date_str
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return dt
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Cache I/O (HTTP ETag cache)
# ---------------------------------------------------------------------------

def load_cache(cache_path):
    """Load HTTP cache from JSON file. Returns {} on missing/corrupt."""
    if not Path(cache_path).exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        print(f"WARNING: HTTP cache {cache_path} unreadable; starting empty "
              f"(ETag history lost).", file=sys.stderr)
        return {}


def save_cache(cache_path, cache_data):
    """Save HTTP cache to JSON file (atomic: tmp + os.replace)."""
    save_json_atomic(cache_path, cache_data)
