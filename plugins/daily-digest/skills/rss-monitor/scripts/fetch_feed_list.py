#!/usr/bin/env python3
"""Fetch the curated article feed list from the BestBlogs public API.

Reads the (no-auth, paginated) JSON API at:
  https://www.bestblogs.dev/api/proxy/sources/discover?sortBy=subscribers
                                         &pageSize=100&page=N

…filters to ARTICLE sources in the tech-focused categories whose `url` is
itself a real RSS/Atom feed address, and writes a structured JSON file
(references/feeds_wechat.json). Results are cached for 7 days unless --force.

This replaced the old ttttmr/Wechat2RSS `all.md` scraper (326 of 395 entries
were low-signal 安全 feeds). BestBlogs is curated by subscribers + activity, so
the top entries are the high-quality Chinese tech / AI 公众号 (字节跳动技术团队,
腾讯技术工程, 阿里云开发者, DeepSeek, 智谱, MiniMax, 通义实验室 …) served as
real RSS via `wechat2rss.bestblogs.dev/feed/<hash>.xml`, plus native-feed blogs.

Why only real-feed URLs: this repo's check_updates.py (like AINews' poll_feeds)
fetches each `url` directly with no RSS autodiscovery, so a homepage-only URL
(e.g. github.blog, ruanyifeng.com) would 404-as-feed every run. BestBlogs' API
exposes the site link for ~60% of sources — not a usable feed — so we drop them
here and keep only the ~315 tech entries whose `url` is already a feed address.

The output schema stays `{metadata, feeds:[{index,name,url,category,active,...}]}`
so check_updates.py's run_wechat consumes it unchanged. `category` carries the
Chinese categoryDesc (人工智能 / 软件编程 / 商业科技 / 产品设计 / 媒体资讯).

WeChat-style step — run before check_updates.py when the feed list is stale.
"""

import argparse
import json
import re
import sys
import time
import random
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from _common import _decode_body, create_ssl_context, save_json_atomic

CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days

API_BASE = "https://www.bestblogs.dev"
API_PATH = "/api/proxy/sources/discover"
SOURCE_PAGE = "https://www.bestblogs.dev/en/explore/sources"

PAGE_SIZE = 100       # items per API request
PAGE_DELAY = 1.0      # polite seconds between paged requests

# Tech-focused ARTICLE categories (BestBlogs `category` enum keys). Use the
# English enum (stable) for filtering; the Chinese categoryDesc is what we write
# to each feed's `category` field for run_wechat's --category filter + stats.
DEFAULT_CATEGORIES = {
    "Artificial_Intelligence",  # 人工智能
    "Programming_Technology",   # 软件编程
    "Business_Tech",            # 商业科技
    "Product_Development",      # 产品设计
    "News_Media",               # 媒体资讯
}

# A `url` counts as a real feed address if it matches one of these. Everything
# else (site homepages like github.blog / ruanyifeng.com) is dropped because
# check_updates.py cannot autodiscover a feed from it.
_FEED_PATH_RE = re.compile(
    r"(?:^https?://[^/]*wechat2rss\.bestblogs\.dev/feed/"
    r"|^https?://[^/]*rsshub\.bestblogs\.dev/"
    r"|/(?:feed|rss|atom)(?:/|$|\?)"
    r"|/(?:index|rss|atom)\.xml(?:[?#]|$)"
    r"|\.xml(?:[?#]|$)"
    r"|rss\.aspx(?:[?#]|$))",
    re.IGNORECASE,
)


def is_real_feed_url(url):
    """True if `url` looks like a directly pollable RSS/Atom feed address."""
    if not url:
        return False
    return bool(_FEED_PATH_RE.search(url))


def fetch_json(url, timeout=30):
    """Fetch a URL and return the parsed JSON.

    Uses the shared _common helpers (SSL context + decode) with a JSON return
    contract (this script talks to a JSON API, not an RSS feed). Retries once
    with a relaxed SSL context on failure, mirroring fetch_podcast_list.py.
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
        try:
            relaxed_ctx = create_ssl_context(relaxed=True)
            with urllib.request.urlopen(req, context=relaxed_ctx, timeout=timeout) as resp:
                return json.loads(_decode_body(resp.read()))
        except Exception as retry_err:
            print(f"fetch_json: relaxed-SSL retry also failed for {url}: "
                  f"{type(retry_err).__name__}: {retry_err}", file=sys.stderr)
            raise retry_err


def fetch_page(page, page_size, max_retries=3):
    """Fetch one API page with exponential-backoff retries. Returns its `data`."""
    url = (f"{API_BASE}{API_PATH}?sortBy=subscribers"
           f"&pageSize={page_size}&page={page}")
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            payload = fetch_json(url)
            if not payload.get("success", True):
                raise RuntimeError(f"API success=false: {payload.get('message')}")
            data = payload.get("data") or {}
            if "dataList" not in data:
                raise RuntimeError(f"response missing dataList: {str(data)[:200]}")
            return data
        except (urllib.error.URLError, RuntimeError, ValueError) as e:
            last_err = e
            wait = 2 ** attempt
            print(f"  page {page} failed (try {attempt}/{max_retries}): "
                  f"{type(e).__name__}: {e}; retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"page {page} failed after {max_retries} retries: {last_err}")


def fetch_all_sources(page_size, delay):
    """Page through the API until the server's pageCount is reached.

    Returns (sources, api_total). Each source keeps the raw API field names
    (resourceType / countInPast3Months / …); map_fields() normalizes them.
    """
    collected = []
    api_total = page_count = None
    page = 1
    while True:
        data = fetch_page(page, page_size)
        if api_total is None:
            api_total = data.get("totalCount")
            page_count = data.get("pageCount")
            print(f"API totalCount={api_total}, pageCount={page_count} "
                  f"(pageSize={page_size})")
        items = data.get("dataList", []) or []
        if not items:
            break
        collected.extend(items)
        print(f"  page {page}/{page_count}: +{len(items)} (cumulative {len(collected)})")
        if page_count and page >= page_count:
            break
        page += 1
        time.sleep(delay + random.uniform(-0.2, 0.3))
    return collected, (api_total if api_total is not None else len(collected))


def map_fields(src):
    """Normalize a raw API item to the fields we keep.

    BestBlogs renamed several fields; we map the new names to readable ones:
        resourceType                 → type
        resourceTypeDesc             → type_desc
        countInPast3Months           → articles_3mo
        qualifiedCountInPast3Months  → quality_3mo
    """
    return {
        "id": src.get("id"),
        "name": (src.get("name") or "").strip(),
        "description": src.get("description"),
        "url": (src.get("url") or "").strip(),
        "type": src.get("resourceType") or src.get("type") or "ARTICLE",
        "type_desc": src.get("resourceTypeDesc") or "",
        "category_key": src.get("category"),
        "category": src.get("categoryDesc") or src.get("category") or "未分类",
        "language": src.get("language") or "",
        "language_desc": src.get("languageDesc") or "",
        "subscribers": src.get("subscriberCount", 0) or 0,
        "articles_3mo": src.get("countInPast3Months", 0) or 0,
        "quality_3mo": src.get("qualifiedCountInPast3Months", 0) or 0,
        "last_live": src.get("lastLiveTime"),
    }


def filter_sources(sources, categories):
    """Keep ARTICLE sources (optionally category-filtered) with a real-feed url.

    `categories` is either a set of BestBlogs `category` enum keys to keep, or
    None to keep every ARTICLE category. Returns (kept, stats) where stats
    records why entries were dropped so the run summary can show the funnel.
    """
    kept = []
    seen_cats = set()           # distinct ARTICLE category keys actually seen
    stats = {"total": len(sources), "non_article": 0, "category_out": 0,
             "homepage_url": 0, "no_url": 0}
    for s in sources:
        m = map_fields(s)
        if m["type"] != "ARTICLE":
            stats["non_article"] += 1
            continue
        if m["category_key"]:
            seen_cats.add(m["category_key"])
        if categories is not None and m["category_key"] not in categories:
            stats["category_out"] += 1
            continue
        if not m["url"]:
            stats["no_url"] += 1
            continue
        if not is_real_feed_url(m["url"]):
            stats["homepage_url"] += 1
            continue
        kept.append(m)
    # Stable order: subscribers desc, then name — so the hottest feeds land
    # first (check_updates' --count N cutoff then keeps the highest-signal ones).
    kept.sort(key=lambda x: (-(x["subscribers"]), x["name"]))
    stats["seen_categories"] = sorted(seen_cats)
    return kept, stats


def build_output(feeds, fetch_time, api_total, category_filter, filter_stats):
    """Build the output JSON structure (contract: {metadata, feeds:[...]}).

    `category_filter` is what we record in metadata: None → "ALL ARTICLE",
    otherwise the sorted list of category enum keys we kept.
    """
    category_stats = {}
    for fd in feeds:
        category_stats[fd["category"]] = category_stats.get(fd["category"], 0) + 1

    meta = {
        "source": SOURCE_PAGE,
        "api": API_PATH + "?sortBy=subscribers",
        "fetch_time": fetch_time,
        "total_count": len(feeds),
        "active_count": len(feeds),   # all retained entries are live
        "categories": category_stats,
        "category_filter": "ALL ARTICLE" if category_filter is None else sorted(category_filter),
        "api_total": api_total,
        "filter_funnel": {k: v for k, v in filter_stats.items() if k != "seen_categories"},
    }
    # Enrich each feed with a stable 0-based index (run_wechat doesn't require
    # it, but the legacy schema carried it and it aids debugging).
    for i, fd in enumerate(feeds):
        fd["index"] = i
        fd["active"] = True

    return {"metadata": meta, "feeds": feeds}


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
    """Check feeds_wechat.json freshness from its own metadata.fetch_time.

    The output records metadata.fetch_time, so it is the source of truth.
    Returns (is_fresh, fetch_time). Identical contract to fetch_podcast_list.py.
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


def _warn_stale_keep(output_path, label):
    """Loudly report that a stale list is being kept after a failed refresh.

    Exit code stays 0 (graceful degradation by design), but the pipeline must
    be able to TELL "refreshed" from "stale for three weeks" — a quiet note
    on stdout was invisible to anyone not reading the full transcript.
    """
    _, fetch_time = output_age_valid(output_path)
    age_note = ""
    if fetch_time:
        try:
            age_days = ((datetime.now(timezone.utc)
                         - datetime.fromisoformat(fetch_time)).total_seconds() / 86400)
            age_note = f", last refreshed {age_days:.1f} days ago"
        except ValueError:
            pass
    print(f"WARNING: refresh failed; keeping STALE {label}{age_note}: "
          f"{output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch the BestBlogs article feed list (tech categories, real-feed URLs)"
    )
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--force", action="store_true", help="Force refresh ignoring cache")
    parser.add_argument("--all-categories", action="store_true",
                        help="Keep all ARTICLE categories (not just the tech-focused default set)")
    parser.add_argument("--categories", default="",
                        help="Comma-separated BestBlogs category enum keys to keep "
                             "(e.g. Artificial_Intelligence,Programming_Technology). "
                             "Default: tech-focused set; --all-categories overrides this.")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    # Resolve the category filter set. `categories` is either a set of enum keys
    # or None (meaning: keep all ARTICLE categories, --all-categories).
    if args.all_categories:
        categories = None
    elif args.categories:
        categories = {c.strip() for c in args.categories.split(",") if c.strip()}
    else:
        categories = set(DEFAULT_CATEGORIES)

    output_path = Path(args.output)

    # Cache validity: feeds_wechat.json's own metadata.fetch_time is the source
    # of truth — identical contract to fetch_podcast_list.py.
    if not args.force and output_path.exists():
        fresh, fetch_time = output_age_valid(output_path)
        if fresh:
            print(f"Feed list is still valid (fetched at {fetch_time})")
            print("Use --force to force refresh")
            return

    # Fetch the full source list from the BestBlogs API
    print(f"Fetching source list from {API_BASE}{API_PATH} ...")
    try:
        raw_sources, api_total = fetch_all_sources(PAGE_SIZE, PAGE_DELAY)
    except Exception as e:
        print(f"Error fetching source list: {e}", file=sys.stderr)
        if output_path.exists():
            _warn_stale_keep(output_path, "feed list")
            return
        sys.exit(1)

    # Filter: ARTICLE + (tech categories unless --all-categories) + real-feed URL
    kept, stats = filter_sources(raw_sources, categories)

    if not kept:
        print("Error: no feeds passed the filters (ARTICLE + category + real-feed URL).",
              file=sys.stderr)
        if output_path.exists():
            _warn_stale_keep(output_path, "feed list")
            return
        sys.exit(1)

    fetch_time = datetime.now(timezone.utc).isoformat()
    output = build_output(kept, fetch_time, api_total, categories, stats)

    save_json_atomic(output_path, output)

    meta = output["metadata"]
    print(f"\nFeed list updated: {meta['total_count']} feeds "
          f"(API total: {meta['api_total']})")
    print("  filter funnel: "
          f"total={stats['total']}, "
          f"non_article={stats['non_article']}, "
          f"category_out={stats['category_out']}, "
          f"homepage_url={stats['homepage_url']}, "
          f"no_url={stats['no_url']}")
    print("  by category:")
    for cat, count in sorted(meta["categories"].items(), key=lambda kv: -kv[1]):
        print(f"    {cat}: {count}")


if __name__ == "__main__":
    main()
