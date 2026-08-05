#!/usr/bin/env python3
"""Fetch the Top-N Chinese podcast list from xyzrank.com.

Reads the JSON API at:
  https://xyzrank.com/api/podcasts?offset=N&limit=M

and writes a structured JSON file (references/podcasts.json) holding the Top-N
podcasts by rank. Results are cached for 7 days unless --force.

Podcast-specific step — run before check_updates.py when the podcast list is
stale or missing. This mirrors scripts/fetch_feed_list.py (the WeChat feed-list
fetcher): the same 7-day TTL contract (read from the output file's own
metadata.fetch_time), the same --force override, and the same "keep the old
file if the network fails" fallback so a transient xyzrank outage never wipes a
working podcast list.

Why a dedicated script (vs. the legacy convert_to_json.py, now removed): the
old script parsed a *manually prepared* references/podcast_rss_list.md (it
never scraped xyzrank itself — that markdown was scraped by hand outside the
repo). When the skills were merged into rss-monitor that half-automatic flow
was dropped, but the .gitignored podcasts.json had no way to regenerate — a
fresh install ended up with an empty/missing list and the collector silently
produced zero podcasts. This script closes that gap by hitting the real API.
"""

import argparse
import json
import sys
import time
import random
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from _common import _decode_body, create_ssl_context

CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days
SOURCE_URL = "https://xyzrank.com/api/podcasts"
DEFAULT_COUNT = 1000   # Top-N to fetch; matches check_updates.py's count default
PAGE_LIMIT = 100       # items per API request


def fetch_json(url, timeout=30):
    """Fetch a URL and return the parsed JSON.

    Uses the shared _common helpers (SSL context + decode) but with a JSON
    return contract (this script talks to a JSON API, not an RSS feed). Retries
    once with a relaxed SSL context on failure, mirroring fetch_feed_list.py.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    })
    try:
        ctx = create_ssl_context()
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return json.loads(_decode_body(resp.read()))
    except Exception as first_err:
        print(f"fetch_json: primary request failed for {url}: "
              f"{type(first_err).__name__}: {first_err}", file=sys.stderr)
        # Retry with relaxed SSL
        try:
            relaxed_ctx = create_ssl_context(relaxed=True)
            with urllib.request.urlopen(req, context=relaxed_ctx, timeout=timeout) as resp:
                return json.loads(_decode_body(resp.read()))
        except Exception as retry_err:
            print(f"fetch_json: relaxed-SSL retry also failed for {url}: "
                  f"{type(retry_err).__name__}: {retry_err}", file=sys.stderr)
            raise retry_err


def fetch_all_podcasts(count):
    """Page through /api/podcasts until ``count`` items are collected.

    The API returns {items, total, offset, limit, medianValues}, sorted by
    rank. Pages are fetched sequentially with light spacing to be polite to the
    public endpoint. Returns (items, total).
    """
    collected = []
    total = None
    offset = 0
    while len(collected) < count:
        limit = min(PAGE_LIMIT, count - len(collected))
        url = f"{SOURCE_URL}?offset={offset}&limit={limit}"
        data = fetch_json(url)
        page = data.get("items", [])
        if total is None:
            total = data.get("total", len(collected) + len(page))
        if not page:
            break  # exhausted before reaching count
        collected.extend(page)
        offset += len(page)
        if offset >= (total or 0):
            break
        # Polite spacing between requests (skip after the last page)
        if len(collected) < count:
            time.sleep(random.uniform(0.2, 0.5))
    return collected, (total if total is not None else len(collected))


def transform(item):
    """Map an API item to the podcasts.json entry schema.

    Output schema is identical to the legacy podcasts.json, so check_updates.py
    and resolve_xiaoyuzhou_urls.py need no changes:

      {rank, name, author, episode_count, link_type, url,
       description, category, xiaoyuzhou_url}

    link_type dispatch (consumed by check_updates.run_podcast):
      - "rss"        when an RSS feed link is present  → RSS parser (most robust)
      - "xiaoyuzhou" otherwise, when an xyz link exists → Xiaoyuzhou page scraper
      - dropped when neither is available

    xiaoyuzhou_url is filled whenever an xyz/website link exists, even for
    rss-typed entries, so resolve_xiaoyuzhou_urls.py can still resolve episode
    URLs for rss-typed podcasts — matching the legacy semantics.

    Returns None when the item has no usable link.
    """
    links = {}
    for link in item.get("links", []):
        name = (link.get("name") or "").strip()
        url = (link.get("url") or "").strip()
        if name and url and name not in links:
            links[name] = url

    rss = links.get("rss")
    # Prefer the canonical "xyz" link; "website" is often the same xyz page and
    # serves as a fallback when "xyz" is absent.
    xyz = links.get("xyz") or links.get("website")

    if rss:
        link_type, url = "rss", rss
    elif xyz:
        link_type, url = "xiaoyuzhou", xyz
    else:
        return None  # no usable link — skip

    return {
        "rank": item.get("rank", 0),
        "name": (item.get("name") or "").strip(),
        "author": (item.get("authorsText") or "").strip(),
        "episode_count": item.get("trackCount", 0),
        "link_type": link_type,
        "url": url,
        "description": "",  # API has no description field; unused by consumers
        "category": (item.get("primaryGenreName") or "").strip(),
        "xiaoyuzhou_url": xyz or "",
    }


def build_output(podcasts, fetch_time, site_total):
    """Build the output JSON structure."""
    rss_count = sum(1 for p in podcasts if p["link_type"] == "rss")
    xiaoyuzhou_count = sum(1 for p in podcasts if p["link_type"] == "xiaoyuzhou")
    xyz_link_count = sum(1 for p in podcasts if p.get("xiaoyuzhou_url"))
    return {
        "metadata": {
            "source": "xyzrank.com",
            "fetch_time": fetch_time,
            "total_count": len(podcasts),
            "rss_count": rss_count,
            "xiaoyuzhou_count": xiaoyuzhou_count,
            "xiaoyuzhou_url_count": xyz_link_count,
            "site_total": site_total,
        },
        "podcasts": podcasts,
    }


def is_cache_valid(fetch_time):
    """Check whether a fetch_time ISO string is within the TTL."""
    if not fetch_time:
        return False
    try:
        cached_time = datetime.fromisoformat(fetch_time).timestamp()
        return (time.time() - cached_time) < CACHE_TTL_SECONDS
    except (ValueError, OSError):
        return False


def output_age_valid(output_path):
    """Check podcasts.json freshness from its own metadata.fetch_time.

    The output records metadata.fetch_time, so it is the source of truth.
    Returns (is_fresh, fetch_time).
    """
    if not output_path.exists():
        return False, None
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False, None
    fetch_time = data.get("metadata", {}).get("fetch_time")
    if not fetch_time:
        return False, None
    if is_cache_valid(fetch_time):
        return True, fetch_time
    return False, fetch_time


def main():
    parser = argparse.ArgumentParser(
        description="Fetch the Top-N Chinese podcast list from xyzrank.com"
    )
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help=f"Number of top podcasts to fetch (default: {DEFAULT_COUNT})")
    parser.add_argument("--force", action="store_true", help="Force refresh ignoring cache")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    output_path = Path(args.output)

    # Cache validity: podcasts.json's own metadata.fetch_time is the source of
    # truth — identical contract to fetch_feed_list.py.
    if not args.force and output_path.exists():
        fresh, fetch_time = output_age_valid(output_path)
        if fresh:
            print(f"Podcast list is still valid (fetched at {fetch_time})")
            print("Use --force to force refresh")
            return

    # Fetch the ranked list from the API
    print(f"Fetching podcast list from {SOURCE_URL} (top {args.count})...")
    try:
        items, site_total = fetch_all_podcasts(args.count)
    except Exception as e:
        print(f"Error fetching podcast list: {e}", file=sys.stderr)
        # If we have an existing output file, keep using it
        if output_path.exists():
            print(f"Using existing cached podcast list: {output_path}")
            return
        sys.exit(1)

    # Transform to the legacy schema, dropping items with no usable link
    podcasts = []
    skipped = 0
    for item in items:
        entry = transform(item)
        if entry is None:
            skipped += 1
            continue
        podcasts.append(entry)

    if not podcasts:
        print("Error: No podcasts with usable links were found in the API response.",
              file=sys.stderr)
        if output_path.exists():
            print(f"Keeping existing cached podcast list: {output_path}")
            return
        sys.exit(1)

    # Build and save output
    fetch_time = datetime.now(timezone.utc).isoformat()
    output = build_output(podcasts, fetch_time, site_total)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Print summary
    meta = output["metadata"]
    print(f"\nPodcast list updated: {meta['total_count']} podcasts "
          f"(site total: {meta['site_total']})")
    print(f"  link types: {meta['rss_count']} RSS, "
          f"{meta['xiaoyuzhou_count']} Xiaoyuzhou-only")
    print(f"  with xiaoyuzhou_url: {meta['xiaoyuzhou_url_count']} "
          f"(used by resolve_xiaoyuzhou_urls.py)")
    if skipped:
        print(f"  skipped (no usable link): {skipped}", file=sys.stderr)


if __name__ == "__main__":
    main()
