#!/usr/bin/env python3
"""Fetch and parse the Wechat2RSS feed list from GitHub.

Reads the Markdown source at:
  https://raw.githubusercontent.com/ttttmr/Wechat2RSS/master/list/all.md

Parses category headings and feed links into a structured JSON file.
Results are cached for 7 days unless --force is used.
"""

import argparse
import json
import ssl
import sys
import time
import urllib.request
import re
from datetime import datetime, timezone
from pathlib import Path

CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days
SOURCE_URL = "https://raw.githubusercontent.com/ttttmr/Wechat2RSS/master/list/all.md"

# Regex to match Markdown links, optionally struck through
# Matches: [name](url) or ~~[name](url)~~
FEED_LINK_PATTERN = re.compile(r'^(~~)?\[([^\]]+)\]\(([^)]+)\)(~~)?$')
HEADING_PATTERN = re.compile(r'^##\s+(.+)$')


def create_ssl_context():
    """Create an SSL context with fallback for certificate issues."""
    try:
        ctx = ssl.create_default_context()
        return ctx
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def fetch_url(url, timeout=30):
    """Fetch a URL and return the response body as text."""
    ctx = create_ssl_context()
    req = urllib.request.Request(url, headers={"User-Agent": "WechatRSSMonitor/1.0"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except Exception as first_err:
        # Retry with relaxed SSL
        try:
            relaxed_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            relaxed_ctx.check_hostname = False
            relaxed_ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, context=relaxed_ctx, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except Exception:
            raise first_err


def parse_feed_list(markdown_text):
    """Parse the Markdown feed list into categories and feeds."""
    feeds = []
    current_category = "未分类"
    index = 0

    for line in markdown_text.splitlines():
        line = line.strip()

        # Check for category heading
        heading_match = HEADING_PATTERN.match(line)
        if heading_match:
            current_category = heading_match.group(1).strip()
            continue

        # Check for feed link
        link_match = FEED_LINK_PATTERN.match(line)
        if link_match:
            struck_before = link_match.group(1) is not None
            name = link_match.group(2).strip()
            url = link_match.group(3).strip()
            struck_after = link_match.group(4) is not None
            active = not (struck_before or struck_after)

            feeds.append({
                "index": index,
                "name": name,
                "url": url,
                "category": current_category,
                "active": active,
            })
            index += 1

    return feeds


def build_output(feeds, fetch_time):
    """Build the output JSON structure."""
    # Compute category stats
    category_stats = {}
    active_count = 0
    for feed in feeds:
        cat = feed["category"]
        if cat not in category_stats:
            category_stats[cat] = 0
        category_stats[cat] += 1
        if feed["active"]:
            active_count += 1

    return {
        "metadata": {
            "source": SOURCE_URL,
            "fetch_time": fetch_time,
            "total_count": len(feeds),
            "active_count": active_count,
            "discontinued_count": len(feeds) - active_count,
            "categories": category_stats,
        },
        "feeds": feeds,
    }


def load_cache(cache_path):
    """Load cache file to check if refresh is needed."""
    if not Path(cache_path).exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_cache(cache_path, fetch_time):
    """Save cache metadata."""
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"fetch_time": fetch_time}, f, ensure_ascii=False, indent=2)


def is_cache_valid(cache_data):
    """Check if cached data is still within TTL."""
    if not cache_data or "fetch_time" not in cache_data:
        return False
    try:
        cached_time = datetime.fromisoformat(cache_data["fetch_time"]).timestamp()
        return (time.time() - cached_time) < CACHE_TTL_SECONDS
    except (ValueError, OSError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Fetch and parse Wechat2RSS feed list")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--cache", default=None, help="Cache metadata file path")
    parser.add_argument("--force", action="store_true", help="Force refresh ignoring cache")
    args = parser.parse_args()

    output_path = Path(args.output)
    cache_path = args.cache or str(output_path.parent / ".feed_list_cache.json")

    # Check cache validity
    if not args.force and output_path.exists():
        cache_data = load_cache(cache_path)
        if is_cache_valid(cache_data):
            print(f"Feed list cache is still valid (fetched at {cache_data.get('fetch_time', 'unknown')})")
            print(f"Use --force to force refresh")
            return

    # Fetch the Markdown source
    print(f"Fetching feed list from {SOURCE_URL}...")
    try:
        markdown_text = fetch_url(SOURCE_URL)
    except Exception as e:
        print(f"Error fetching feed list: {e}", file=sys.stderr)
        # If we have an existing output file, keep using it
        if output_path.exists():
            print(f"Using existing cached feed list: {output_path}")
            return
        sys.exit(1)

    # Parse feeds
    feeds = parse_feed_list(markdown_text)
    if not feeds:
        print("Warning: No feeds found in the source Markdown", file=sys.stderr)
        sys.exit(1)

    # Build and save output
    fetch_time = datetime.now(timezone.utc).isoformat()
    output = build_output(feeds, fetch_time)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Save cache metadata
    save_cache(cache_path, fetch_time)

    # Print summary
    meta = output["metadata"]
    print(f"\nFeed list updated: {meta['total_count']} feeds ({meta['active_count']} active, {meta['discontinued_count']} discontinued)")
    for cat, count in meta["categories"].items():
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
