#!/usr/bin/env python3
"""Check tech RSS feeds and Hacker News for new articles within a time window.

Reads the feed list from references/feeds.json, fetches each RSS/Atom feed
and HN RSS feeds concurrently, and outputs articles published within the
specified hours to latest_updates.json.

Uses ETag/If-Modified-Since HTTP caching and cross-source deduplication.
"""

import argparse
import json
import random
import re
import ssl
import sys
import time
import urllib.error as urllib_error
import urllib.request as urllib_request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlunparse


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
        if self._skip_depth > 0:
            return
        entities = {'amp': '&', 'lt': '<', 'gt': '>', 'nbsp': '', 'quot': '"',
                     'apos': "'", 'mdash': '—', 'ndash': '–', 'bull': '•'}
        self._pieces.append(entities.get(name, f'&{name};'))

    def handle_charref(self, name):
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
        text = text.replace('\xa0', ' ')
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
# URL normalization for deduplication
# ---------------------------------------------------------------------------

_TRACKING_PARAMS = re.compile(
    r'^(utm_[a-z]+|ref|source|fbclid|gclid|mc_eid|campaign|medium|content|term)$',
    re.IGNORECASE
)


def normalize_url(url):
    """Normalize a URL by removing tracking query parameters and fragments."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        # Remove tracking params
        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=True)
            clean_params = {k: v for k, v in params.items() if not _TRACKING_PARAMS.match(k)}
            if clean_params:
                # Rebuild query string
                parts = []
                for k, vs in sorted(clean_params.items()):
                    for v in vs:
                        parts.append(f"{k}={v}")
                query = "&".join(parts)
            else:
                query = ""
        else:
            query = ""
        # Remove fragment, trailing slash
        path = parsed.path.rstrip("/")
        return urlunparse((parsed.scheme, parsed.netloc.lower(), path, parsed.params, query, ""))
    except Exception:
        return url.lower().strip()


# ---------------------------------------------------------------------------
# Title similarity for deduplication
# ---------------------------------------------------------------------------

def title_similarity(t1, t2):
    """Compute Jaccard similarity between two titles based on word sets."""
    if not t1 or not t2:
        return 0.0
    # Tokenize by splitting on whitespace and common punctuation
    pattern = re.compile(r'[\s\-_:,;|/\\]+')
    words1 = set(pattern.split(t1.lower().strip()))
    words2 = set(pattern.split(t2.lower().strip()))
    words1.discard("")
    words2.discard("")
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


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
    """Fetch a URL with ETag/If-Modified-Since support."""
    headers = {"User-Agent": "TechDailyRSSMonitor/1.0"}

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
    """Fetch a URL with retry on network errors and exponential backoff."""
    for attempt in range(max_retries + 1):
        body, status, new_cache = fetch_url(url, cache=cache, timeout=timeout)
        if body is not None or status == 304:
            return body, status, new_cache
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

        # HN-specific fields: hnrss.org embeds Points/Comments in description HTML
        # Try extracting from description text first
        desc_text = entry.get("description", "")
        if desc_text:
            m_points = re.search(r'Points:\s*(\d+)', desc_text)
            if m_points:
                entry["hn_points"] = int(m_points.group(1))
            m_comments = re.search(r'# Comments:\s*(\d+)', desc_text)
            if m_comments:
                entry["hn_comments"] = int(m_comments.group(1))

        items.append(entry)

    # Handle Atom feeds (entry elements) as fallback
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry_elem in root.findall(".//atom:entry", ns):
        item = {}

        title_el = entry_elem.find("atom:title", ns)
        if title_el is not None and title_el.text:
            item["title"] = title_el.text.strip()

        link_el = entry_elem.find("atom:link", ns)
        if link_el is not None:
            item["link"] = link_el.get("href", "").strip()

        published_el = entry_elem.find("atom:published", ns)
        if published_el is not None and published_el.text:
            item["pub_date_raw"] = published_el.text.strip()
        elif entry_elem.find("atom:updated", ns) is not None:
            updated_el = entry_elem.find("atom:updated", ns)
            if updated_el.text:
                item["pub_date_raw"] = updated_el.text.strip()

        summary_el = entry_elem.find("atom:summary", ns)
        if summary_el is not None and summary_el.text:
            item["description"] = summary_el.text.strip()

        content_el = entry_elem.find("atom:content", ns)
        if content_el is not None and content_el.text:
            item["content_encoded"] = content_el.text.strip()

        items.append(item)

    return items


# ---------------------------------------------------------------------------
# Feed checking
# ---------------------------------------------------------------------------

def check_feed(feed_info, cutoff_time, cache):
    """Check a single RSS feed for new articles.

    feed_info is a dict with: name, url, category, language
    Returns (feed_info, articles, error, new_cache_entry).
    """
    url = feed_info["url"]

    body, status, new_cache = fetch_url_with_retry(url, cache=cache)

    if body is None:
        if status == 304:
            return feed_info, [], "not_modified", new_cache
        return feed_info, [], f"HTTP {status}", new_cache

    rss_items = parse_rss_items(body)
    articles = []

    for item in rss_items:
        pub_date_raw = item.get("pub_date_raw", "")
        pub_date = parse_rss_date(pub_date_raw)

        if pub_date and pub_date > cutoff_time:
            desc_html = item.get("content_encoded") or item.get("description") or ""
            full_text = strip_html(desc_html)
            summary_text = full_text[:2000] + ("..." if len(full_text) > 2000 else "")

            articles.append({
                "title": item.get("title", "(no title)"),
                "url": item.get("link", ""),
                "published": pub_date.strftime("%Y-%m-%d %H:%M"),
                "published_raw": pub_date_raw,
                "description": summary_text,
                "full_text": full_text,
                "hn_points": item.get("hn_points"),
                "hn_comments": item.get("hn_comments"),
            })

    return feed_info, articles, None, new_cache


def check_hn_feed(hn_feed, cutoff_time, cache):
    """Check a HN RSS feed with minimum points filtering.

    hn_feed is a dict with: name, url, min_points
    Returns (feed_info, articles, error, new_cache_entry).
    """
    feed_info = {"name": hn_feed["name"], "url": hn_feed["url"],
                 "category": "Hacker News", "language": "en", "is_hn": True}
    min_points = hn_feed.get("min_points", 0)

    body, status, new_cache = fetch_url_with_retry(hn_feed["url"], cache=cache)

    if body is None:
        if status == 304:
            return feed_info, [], "not_modified", new_cache
        return feed_info, [], f"HTTP {status}", new_cache

    rss_items = parse_rss_items(body)
    articles = []

    for item in rss_items:
        pub_date_raw = item.get("pub_date_raw", "")
        pub_date = parse_rss_date(pub_date_raw)
        points = item.get("hn_points")
        comments = item.get("hn_comments")

        # Filter by time window
        if pub_date and pub_date > cutoff_time:
            # Filter by minimum points
            if points is not None and points < min_points:
                continue

            desc_html = item.get("description") or item.get("content_encoded") or ""
            full_text = strip_html(desc_html)
            summary_text = full_text[:2000] + ("..." if len(full_text) > 2000 else "")

            articles.append({
                "title": item.get("title", "(no title)"),
                "url": item.get("link", ""),
                "published": pub_date.strftime("%Y-%m-%d %H:%M"),
                "published_raw": pub_date_raw,
                "description": summary_text,
                "full_text": full_text,
                "hn_points": points,
                "hn_comments": comments,
            })

    return feed_info, articles, None, new_cache


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
    parser = argparse.ArgumentParser(description="Check tech RSS feeds for new articles")
    parser.add_argument("--feeds", default=None, help="Path to feeds.json")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--cache", default=None, help="HTTP cache file path")
    parser.add_argument("--hours", type=int, default=24, help="Time window in hours (default: 24)")
    parser.add_argument("--workers", type=int, default=20, help="Number of concurrent workers (default: 20)")
    parser.add_argument("--category", default=None, help="Filter by category name")
    args = parser.parse_args()

    # Resolve paths
    script_dir = Path(__file__).resolve().parent
    feeds_path = Path(args.feeds) if args.feeds else script_dir.parent / "references" / "feeds.json"
    output_path = Path(args.output)
    cache_path = Path(args.cache) if args.cache else output_path.parent / ".http_cache.json"

    # Load feed list
    if not feeds_path.exists():
        print(f"Error: Feed list not found at {feeds_path}", file=sys.stderr)
        sys.exit(1)

    with open(feeds_path, "r", encoding="utf-8") as f:
        feed_data = json.load(f)

    # Build flat list of feeds with category info
    all_feeds = []
    for cat in feed_data.get("categories", []):
        cat_name = cat["name"]
        for feed in cat.get("feeds", []):
            all_feeds.append({
                "name": feed["name"],
                "url": feed["url"],
                "category": cat_name,
                "language": feed.get("language", "en"),
            })

    hn_feeds = feed_data.get("hacker_news", [])

    # Filter by category if specified
    if args.category:
        if args.category == "Hacker News":
            all_feeds = []
        else:
            all_feeds = [f for f in all_feeds if f["category"] == args.category]
            hn_feeds = []

    print(f"Checking {len(all_feeds)} RSS feeds + {len(hn_feeds)} HN feeds "
          f"for articles in the last {args.hours} hours...")

    # Compute cutoff time
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    # Load HTTP cache
    cache = load_cache(cache_path)

    num_workers = min(args.workers, len(all_feeds) + len(hn_feeds))

    # Process all feeds concurrently
    all_results = []
    with ThreadPoolExecutor(max_workers=max(num_workers, 1)) as executor:
        futures = []
        for feed in all_feeds:
            futures.append(executor.submit(check_feed, feed, cutoff_time, cache))
        for hn_feed in hn_feeds:
            futures.append(executor.submit(check_hn_feed, hn_feed, cutoff_time, cache))

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

    for feed_info, articles, error, new_cache in all_results:
        # Update HTTP cache
        if new_cache:
            cache[feed_info["url"]] = new_cache

        if error:
            if error == "not_modified":
                not_modified_count += 1
            else:
                error_count += 1
                error_details[error] = error_details.get(error, 0) + 1
            continue

        for article in articles:
            updates.append({
                "source_name": feed_info["name"],
                "source_category": feed_info["category"],
                "language": feed_info.get("language", "en"),
                **article,
            })

    # Deduplicate: first by normalized URL, then by title similarity
    # Pass 1: URL dedup
    seen_urls = {}
    unique_updates = []
    for update in updates:
        norm_url = normalize_url(update.get("url", ""))
        if norm_url and norm_url in seen_urls:
            # Keep the one with more info (prefer non-HN source)
            existing_idx = seen_urls[norm_url]
            existing = unique_updates[existing_idx]
            # If existing is HN and new is not, replace
            if existing.get("source_category") == "Hacker News" and update.get("source_category") != "Hacker News":
                unique_updates[existing_idx] = update
            continue
        if norm_url:
            seen_urls[norm_url] = len(unique_updates)
        unique_updates.append(update)
    updates = unique_updates

    # Pass 2: Title similarity dedup (only for non-HN items)
    # Group by similar titles and keep only one per group
    title_dedup_indices = set()
    non_hn_items = [(i, u) for i, u in enumerate(updates) if u.get("source_category") != "Hacker News"]
    for idx_a in range(len(non_hn_items)):
        i, a = non_hn_items[idx_a]
        if i in title_dedup_indices:
            continue
        for idx_b in range(idx_a + 1, len(non_hn_items)):
            j, b = non_hn_items[idx_b]
            if j in title_dedup_indices:
                continue
            if title_similarity(a.get("title", ""), b.get("title", "")) > 0.7:
                # Keep the one with longer full_text
                if len(b.get("full_text", "")) > len(a.get("full_text", "")):
                    title_dedup_indices.add(i)
                else:
                    title_dedup_indices.add(j)

    if title_dedup_indices:
        updates = [u for i, u in enumerate(updates) if i not in title_dedup_indices]

    # Sort: non-HN by published date desc, then HN by points desc
    def sort_key(u):
        if u.get("hn_points") is not None:
            return (1, -u["hn_points"])
        return (0, u.get("published", ""))

    updates.sort(key=sort_key)

    # Build output
    total_checked = len(all_feeds) + len(hn_feeds)
    output = {
        "metadata": {
            "checked_count": total_checked,
            "rss_feed_count": len(all_feeds),
            "hn_feed_count": len(hn_feeds),
            "error_count": error_count,
            "not_modified_count": not_modified_count,
            "error_details": error_details,
            "hours": args.hours,
            "update_count": len(updates),
            "check_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
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
    print(f"\nResults: {meta['update_count']} new articles from {meta['checked_count']} feeds")
    if meta["error_count"] > 0:
        print(f"Errors: {meta['error_count']} ({meta['error_details']})")
    print(f"Not modified: {meta['not_modified_count']}")

    # Category breakdown
    cat_counts = {}
    hn_count = 0
    for u in updates:
        cat = u.get("source_category", "其他")
        if cat == "Hacker News":
            hn_count += 1
        else:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
    for cat, count in cat_counts.items():
        print(f"  {cat}: {count} updates")
    if hn_count:
        print(f"  Hacker News: {hn_count} updates")


if __name__ == "__main__":
    main()
