#!/usr/bin/env python3
"""Check Wechat2RSS feeds for new articles within a time window.

Reads the feed list from references/feeds.json, fetches each RSS feed,
and outputs articles published within the specified hours to
latest_updates.json.

Uses ETag/If-Modified-Since HTTP caching and concurrent requests
with rate limiting for the single-domain wechat2rss.xlab.app host.
"""

import argparse
import json
import random
import re
import ssl
import sys
import threading
import time
import urllib.error as urllib_error
import urllib.request as urllib_request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path


# ---------------------------------------------------------------------------
# HTML to plain text
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """HTML to plain text parser with block tag awareness and entity handling."""

    BLOCK_TAGS = frozenset({'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                            'blockquote', 'tr', 'ul', 'ol', 'table', 'hr', 'section', 'article'})
    SKIP_TAGS = frozenset({'style', 'script'})

    def __init__(self):
        super().__init__()
        self._pieces = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS and self._pieces and self._pieces[-1] != '\n':
            self._pieces.append('\n')

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK_TAGS:
            self._pieces.append('\n')

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._pieces.append(data)

    def handle_entityref(self, name):
        """Handle named entities like &amp; &nbsp;"""
        if self._skip_depth > 0:
            return
        entities = {'amp': '&', 'lt': '<', 'gt': '>', 'nbsp': '', 'quot': '"',
                     'apos': "'", 'mdash': '—', 'ndash': '–', 'bull': '•'}
        self._pieces.append(entities.get(name, f'&{name};'))

    def handle_charref(self, name):
        """Handle numeric entities like &#39; &#x27;"""
        if self._skip_depth > 0:
            return
        try:
            if name.startswith('x') or name.startswith('X'):
                char = chr(int(name[1:], 16))
            else:
                char = chr(int(name))
            self._pieces.append(char)
        except (ValueError, OverflowError):
            self._pieces.append(f'&#{name};')

    def get_text(self):
        text = ''.join(self._pieces)
        text = text.replace('\xa0', ' ')  # non-breaking space -> regular space
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()


def strip_html(html_text):
    """Convert HTML string to plain text."""
    if not html_text:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html_text)
    return stripper.get_text()


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_RSS_DATE_FORMATS = [
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S GMT",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
]


def parse_rss_date(date_str):
    """Parse an RSS date string into a datetime object. Returns None on failure."""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in _RSS_DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
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
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except ValueError:
                    continue
    return None


# ---------------------------------------------------------------------------
# HTTP fetching with ETag / If-Modified-Since caching
# ---------------------------------------------------------------------------

def create_ssl_context():
    """Create an SSL context with graceful fallback."""
    try:
        return ssl.create_default_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def fetch_url(url, cache=None, timeout=30):
    """Fetch a URL with ETag/If-Modified-Since support.

    Returns (body_text, status_code, new_cache_entry) or (None, status, new_cache_entry).
    status_code is 304 for not-modified, -1 for network errors.
    """
    headers = {"User-Agent": "WechatRSSMonitor/1.0"}

    # Add conditional headers from cache
    cached = cache.get(url, {}) if cache else {}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]

    req = urllib_request.Request(url, headers=headers)

    new_cache = {}
    try:
        ctx = create_ssl_context()
        with urllib_request.urlopen(req, context=ctx, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status

            # Save cache headers
            etag = resp.headers.get("ETag")
            last_mod = resp.headers.get("Last-Modified")
            if etag:
                new_cache["etag"] = etag
            if last_mod:
                new_cache["last_modified"] = last_mod

            return body, status, new_cache

    except urllib_error.HTTPError as e:
        if e.code == 304:
            return None, 304, cached
        return None, e.code, {}
    except Exception:
        # Retry with relaxed SSL
        try:
            relaxed = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            relaxed.check_hostname = False
            relaxed.verify_mode = ssl.CERT_NONE
            with urllib_request.urlopen(req, context=relaxed, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                status = resp.status
                etag = resp.headers.get("ETag")
                last_mod = resp.headers.get("Last-Modified")
                if etag:
                    new_cache["etag"] = etag
                if last_mod:
                    new_cache["last_modified"] = last_mod
                return body, status, new_cache
        except urllib_error.HTTPError as e:
            if e.code == 304:
                return None, 304, cached
            return None, e.code, {}
        except Exception:
            return None, -1, {}


def fetch_url_with_retry(url, cache=None, timeout=30, max_retries=2):
    """Fetch a URL with retry on network errors and exponential backoff.

    Returns the same tuple as fetch_url: (body, status, cache_entry).
    Retries up to max_retries times when status is -1 (network error).
    """
    for attempt in range(max_retries + 1):
        body, status, new_cache = fetch_url(url, cache=cache, timeout=timeout)
        if body is not None or status == 304:
            return body, status, new_cache
        # Network error — retry with backoff
        if attempt < max_retries:
            delay = (attempt + 1) * 2 + random.uniform(0, 1)
            time.sleep(delay)
    return None, -1, {}


# ---------------------------------------------------------------------------
# RSS parsing
# ---------------------------------------------------------------------------

def parse_rss_items(xml_text):
    """Parse RSS XML and return a list of item dicts."""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    # Handle RSS 2.0
    for item in root.iter("item"):
        entry = {}

        # Title
        title_el = item.find("title")
        if title_el is not None and title_el.text:
            entry["title"] = title_el.text.strip()

        # Link
        link_el = item.find("link")
        if link_el is not None and link_el.text:
            entry["link"] = link_el.text.strip()

        # Publication date
        pub_el = item.find("pubDate")
        if pub_el is not None and pub_el.text:
            entry["pub_date_raw"] = pub_el.text.strip()

        # Description / content:encoded
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

        items.append(entry)

    # Handle Atom feeds (entry elements) as fallback
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        item = {}

        title_el = entry.find("atom:title", ns)
        if title_el is not None and title_el.text:
            item["title"] = title_el.text.strip()

        link_el = entry.find("atom:link", ns)
        if link_el is not None:
            item["link"] = link_el.get("href", "").strip()

        published_el = entry.find("atom:published", ns)
        if published_el is not None and published_el.text:
            item["pub_date_raw"] = published_el.text.strip()

        summary_el = entry.find("atom:summary", ns)
        if summary_el is not None and summary_el.text:
            item["description"] = summary_el.text.strip()

        items.append(item)

    return items


# ---------------------------------------------------------------------------
# Feed checking
# ---------------------------------------------------------------------------

def check_feed(feed, cutoff_time, cache):
    """Check a single RSS feed for new articles.

    Returns (feed_info, articles, error, new_cache_entry).
    """
    url = feed["url"]

    if not feed.get("active", True):
        return feed, [], None, {}

    body, status, new_cache = fetch_url_with_retry(url, cache=cache)

    if body is None:
        if status == 304:
            return feed, [], "not_modified", new_cache
        return feed, [], f"HTTP {status}", new_cache

    # Parse RSS items
    rss_items = parse_rss_items(body)
    articles = []

    for item in rss_items:
        # Parse and filter by date
        pub_date_raw = item.get("pub_date_raw", "")
        pub_date = parse_rss_date(pub_date_raw)

        if pub_date and pub_date > cutoff_time:
            # Get the best available description / full content
            desc_html = item.get("content_encoded") or item.get("description") or ""
            full_text = strip_html(desc_html)
            # Short preview for reports
            summary_text = full_text[:2000] + ("..." if len(full_text) > 2000 else "")

            articles.append({
                "article_title": item.get("title", "(no title)"),
                "article_url": item.get("link", ""),
                "pub_date": pub_date.strftime("%Y-%m-%d %H:%M"),
                "pub_date_raw": pub_date_raw,
                "summary_text": summary_text,
                "full_text": full_text,
            })

    return feed, articles, None, new_cache


def process_feed_with_semaphore(feed, cutoff_time, cache, semaphore):
    """Process a single feed with global concurrency limiting via semaphore."""
    if not feed.get("active", True):
        return feed, [], None, {}

    with semaphore:
        # Global rate limit: small random delay to avoid bursts
        time.sleep(random.uniform(0.3, 0.5))
        feed_info, articles, error, new_cache = check_feed(feed, cutoff_time, cache)
    return feed_info, articles, error, new_cache


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def load_cache(cache_path):
    """Load HTTP cache from JSON file."""
    if not Path(cache_path).exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache_path, cache_data):
    """Save HTTP cache to JSON file."""
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Check Wechat2RSS feeds for new articles")
    parser.add_argument("--feeds", default=None, help="Path to feeds.json (default: ../references/feeds.json)")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--cache", default=None, help="HTTP cache file path")
    parser.add_argument("--hours", type=int, default=24, help="Time window in hours (default: 24)")
    parser.add_argument("--workers", type=int, default=10, help="Number of concurrent workers (default: 10)")
    parser.add_argument("--category", default=None, help="Filter by category name")
    parser.add_argument("--count", type=int, default=0, help="Max feeds to check (0 = all)")
    args = parser.parse_args()

    # Resolve paths
    script_dir = Path(__file__).resolve().parent
    feeds_path = Path(args.feeds) if args.feeds else script_dir.parent / "references" / "feeds.json"
    output_path = Path(args.output)
    cache_path = Path(args.cache) if args.cache else output_path.parent / ".http_cache.json"

    # Load feed list
    if not feeds_path.exists():
        print(f"Error: Feed list not found at {feeds_path}", file=sys.stderr)
        print("Run fetch_feed_list.py first.", file=sys.stderr)
        sys.exit(1)

    with open(feeds_path, "r", encoding="utf-8") as f:
        feed_data = json.load(f)

    feeds = feed_data["feeds"]

    # Filter by category
    if args.category:
        feeds = [f for f in feeds if f["category"] == args.category]
        if not feeds:
            print(f"No feeds found in category: {args.category}", file=sys.stderr)
            sys.exit(1)

    # Filter active only
    feeds = [f for f in feeds if f.get("active", True)]

    # Limit count
    if args.count > 0:
        feeds = feeds[:args.count]

    print(f"Checking {len(feeds)} feeds for articles in the last {args.hours} hours...")

    # Compute cutoff time
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    # Load HTTP cache
    cache = load_cache(cache_path)

    # Global semaphore limits concurrent HTTP connections to the same domain
    num_workers = min(args.workers, len(feeds))
    semaphore = threading.Semaphore(value=num_workers)

    # Process feeds concurrently with global rate limiting
    all_results = []
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = []
        for feed in feeds:
            future = executor.submit(
                process_feed_with_semaphore, feed, cutoff_time, cache, semaphore
            )
            futures.append(future)

        for future in as_completed(futures):
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                print(f"Feed error: {e}", file=sys.stderr)

    # Aggregate results
    updates = []
    error_count = 0
    not_modified_count = 0
    error_details = {}
    category_stats = {}

    for feed_info, articles, error, new_cache in all_results:
        # Update HTTP cache
        if new_cache:
            cache[feed_info["url"]] = new_cache

        cat = feed_info["category"]
        if cat not in category_stats:
            category_stats[cat] = {"checked": 0, "updates": 0}
        category_stats[cat]["checked"] += 1

        if error:
            if error == "not_modified":
                not_modified_count += 1
            else:
                error_count += 1
                error_details[error] = error_details.get(error, 0) + 1
            continue

        if articles:
            category_stats[cat]["updates"] += len(articles)
            for article in articles:
                updates.append({
                    "account_name": feed_info["name"],
                    "category": cat,
                    **article,
                })

    # Deduplicate by article URL (same article may appear from different feed fetches)
    seen_urls = set()
    unique_updates = []
    for update in updates:
        url = update.get("article_url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        unique_updates.append(update)
    updates = unique_updates

    # Sort updates by pub_date descending
    updates.sort(key=lambda x: x.get("pub_date", ""), reverse=True)

    # Build output
    output = {
        "metadata": {
            "checked_count": len(feeds),
            "error_count": error_count,
            "not_modified_count": not_modified_count,
            "error_details": error_details,
            "hours": args.hours,
            "update_count": len(updates),
            "check_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "categories": category_stats,
        },
        "updates": updates,
    }

    # Save output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Save updated cache
    save_cache(cache_path, cache)

    # Print summary
    meta = output["metadata"]
    total_before_dedup = sum(s["updates"] for s in category_stats.values())
    dedup_count = total_before_dedup - meta["update_count"]
    print(f"\nResults: {meta['update_count']} new articles from {meta['checked_count']} feeds")
    if dedup_count > 0:
        print(f"Deduplicated: {dedup_count} duplicate articles removed")
    if meta["error_count"] > 0:
        print(f"Errors: {meta['error_count']} ({meta['error_details']})")
        error_rate = meta["error_count"] / max(meta["checked_count"], 1)
        if error_rate > 0.05:
            print(f"WARNING: Error rate {error_rate:.1%} exceeds 5% threshold")
    print(f"Not modified: {meta['not_modified_count']}")

    for cat, stats in category_stats.items():
        if stats["updates"] > 0:
            print(f"  {cat}: {stats['updates']} updates from {stats['checked']} feeds")


if __name__ == "__main__":
    main()
