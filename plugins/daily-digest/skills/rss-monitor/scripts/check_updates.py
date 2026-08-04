#!/usr/bin/env python3
"""Unified RSS update checker for the rss-monitor skill.

Checks multiple RSS information sources for new content within a time window:

  --source wechat   WeChat Official Account feeds (Wechat2RSS, ~395 feeds)
  --source tech      Tech blog feeds (~24 feeds) + Hacker News (~2 feeds)
  --source podcast   Chinese podcast feeds (~1000 feeds, RSS + Xiaoyuzhou)
  --source all       Run all three sources sequentially (default)

Outputs a unified latest_updates.json with all sources' updates merged.
Each update carries a ``source`` field (wechat/tech/podcast) so downstream
steps (report generation) can group by source.

Uses ETag/If-Modified-Since HTTP caching via _common.fetch_url.
"""

import argparse
import json
import random
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlunparse

import _common
from _common import (
    fetch_url,
    parse_rss_items,
    parse_rss_date,
    strip_html,
    load_cache,
    save_cache,
    parse_xiaoyuzhou_next_data,
    extract_xiaoyuzhou_episodes,
    parse_duration,
)

# Named limits
TEXT_MAX = 2000          # cap for description/shownotes (full_text is uncapped for wechat/tech)
SHOWNOTES_MAX = 2000     # shownotes truncation (podcast)
TITLE_SIM_THRESHOLD = 0.7  # Jaccard similarity above which two titles are dupes
ERROR_RATE_WARN = 0.05   # warn when fetch error rate exceeds this ratio
WECHAT_SHORT_TEXT_THRESHOLD = 200  # articles with full_text below this may need enrichment


# ===========================================================================
# WECHAT source
# ===========================================================================

def check_wechat_feed(feed, cutoff_time, cache):
    """Check a single WeChat RSS feed for new articles.

    Returns (feed, articles, error_str_or_None, new_cache_entry, date_unparsed_count).
    """
    url = feed["url"]
    if not feed.get("active", True):
        return feed, [], None, {}, 0

    cached = cache.get(url, {})
    etag = cached.get("etag")
    last_modified = cached.get("last_modified")

    try:
        result = fetch_url(url, etag=etag, last_modified=last_modified)
    except Exception as e:
        return feed, [], f"{type(e).__name__}: {e}", {}, 0

    if result is None:
        return feed, [], "not_modified", cached, 0

    new_cache = {}
    if result.get("etag") or result.get("last_modified"):
        new_cache = {"etag": result.get("etag"), "last_modified": result.get("last_modified")}

    rss_items = parse_rss_items(result["content"])
    articles = []
    date_unparsed = 0

    for item in rss_items:
        pub_date_raw = item.get("pub_date_raw", "")
        pub_date = parse_rss_date(pub_date_raw)

        if not pub_date:
            date_unparsed += 1
            continue

        if pub_date > cutoff_time:
            desc_html = item.get("content_encoded") or item.get("description") or ""
            full_text = strip_html(desc_html)
            articles.append({
                "source": "wechat",
                "account_name": feed["name"],
                "category": feed["category"],
                "title": item.get("title", "(no title)"),
                "url": item.get("link", ""),
                "pub_date": pub_date.strftime("%Y-%m-%d %H:%M"),
                "pub_date_raw": pub_date_raw,
                "full_text": full_text,
            })

    return feed, articles, None, new_cache, date_unparsed


def run_wechat(feeds_path, cutoff_time, cache, workers=10, category=None, count=0):
    """Run the wechat source check. Returns (updates, stats_dict)."""
    with open(feeds_path, "r", encoding="utf-8") as f:
        feed_data = json.load(f)

    feeds = feed_data["feeds"]
    if category:
        feeds = [fd for fd in feeds if fd["category"] == category]
    feeds = [fd for fd in feeds if fd.get("active", True)]
    if count > 0:
        feeds = feeds[:count]

    updates = []
    checked = 0
    errors = 0
    not_modified = 0
    date_unparsed_total = 0
    error_details = {}
    category_stats = {}

    semaphore = _make_semaphore(workers)

    def process(feed):
        with semaphore:
            time.sleep(random.uniform(0.3, 0.5))
            return check_wechat_feed(feed, cutoff_time, cache)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process, fd): fd for fd in feeds}
        for future in as_completed(futures):
            try:
                feed, articles, error, new_cache, date_unparsed = future.result()
            except Exception as e:
                errors += 1
                error_details[type(e).__name__] = error_details.get(type(e).__name__, 0) + 1
                continue

            checked += 1
            date_unparsed_total += date_unparsed

            if new_cache:
                cache[feed["url"]] = new_cache

            cat = feed["category"]
            if cat not in category_stats:
                category_stats[cat] = {"checked": 0, "updates": 0}
            category_stats[cat]["checked"] += 1

            if error:
                if error == "not_modified":
                    not_modified += 1
                else:
                    errors += 1
                    error_details[error] = error_details.get(error, 0) + 1
                continue

            if articles:
                category_stats[cat]["updates"] += len(articles)
            updates.extend(articles)

    # Error rate warning (matches original wechat-rss-monitor behavior)
    if checked > 0:
        error_rate = errors / checked
        if error_rate > ERROR_RATE_WARN:
            print(f"  WARNING: wechat error rate {error_rate:.1%} exceeds 5% threshold",
                  file=sys.stderr)

    # Deduplicate by URL, then by title similarity (catches cross-account reposts)
    before_title_dedup = len(updates)
    updates = _dedup_by_url(updates)
    updates = _dedup_wechat_by_title(updates)
    repost_removed = before_title_dedup - len(updates)
    if repost_removed > 0:
        print(f"  Title-dedup: removed {repost_removed} cross-account reposts",
              file=sys.stderr)
    updates.sort(key=lambda x: x.get("pub_date", ""), reverse=True)

    # Count articles with suspiciously short full_text — signals whether
    # Step 4 (fetch_articles.py enrichment) should be run.
    short_text_count = sum(
        1 for u in updates
        if len(u.get("full_text", "")) < WECHAT_SHORT_TEXT_THRESHOLD
    )

    return updates, {
        "checked": checked,
        "errors": errors,
        "not_modified": not_modified,
        "date_unparsed": date_unparsed_total,
        "error_details": error_details,
        "categories": category_stats,
        "repost_removed": repost_removed,
        "short_text_count": short_text_count,
    }


# ===========================================================================
# TECH source (RSS + Hacker News)
# ===========================================================================

def _check_tech_feed(feed_info, cutoff_time, cache):
    """Check a tech RSS feed. Returns (feed_info, articles, error, new_cache)."""
    url = feed_info["url"]
    cached = cache.get(url, {})
    etag = cached.get("etag")
    last_modified = cached.get("last_modified")

    try:
        result = fetch_url(url, etag=etag, last_modified=last_modified)
    except Exception as e:
        return feed_info, [], f"{type(e).__name__}: {e}", {}

    if result is None:
        return feed_info, [], "not_modified", cached

    new_cache = {}
    if result.get("etag") or result.get("last_modified"):
        new_cache = {"etag": result.get("etag"), "last_modified": result.get("last_modified")}

    rss_items = parse_rss_items(result["content"])
    articles = []

    for item in rss_items:
        pub_date_raw = item.get("pub_date_raw", "")
        pub_date = parse_rss_date(pub_date_raw)

        if pub_date and pub_date > cutoff_time:
            desc_html = item.get("content_encoded") or item.get("description") or ""
            full_text = strip_html(desc_html)

            # Extract HN-specific fields from description (hnrss.org format)
            hn_points = None
            hn_comments = None
            desc_text = item.get("description", "")
            if desc_text:
                m_points = re.search(r"Points:\s*(\d+)", desc_text)
                if m_points:
                    hn_points = int(m_points.group(1))
                m_comments = re.search(r"# Comments:\s*(\d+)", desc_text)
                if m_comments:
                    hn_comments = int(m_comments.group(1))

            articles.append({
                "source": "tech",
                "source_name": feed_info["name"],
                "source_category": feed_info["category"],
                "language": feed_info.get("language", "en"),
                "title": item.get("title", "(no title)"),
                "url": item.get("link", ""),
                "pub_date": pub_date.strftime("%Y-%m-%d %H:%M"),
                "full_text": full_text,
                "hn_points": hn_points,
                "hn_comments": hn_comments,
            })

    return feed_info, articles, None, new_cache


def _check_hn_feed(hn_feed, cutoff_time, cache):
    """Check a Hacker News RSS feed with min_points filtering."""
    feed_info = {
        "name": hn_feed["name"], "url": hn_feed["url"],
        "category": "Hacker News", "language": "en",
    }
    min_points = hn_feed.get("min_points", 0)

    cached = cache.get(hn_feed["url"], {})
    try:
        result = fetch_url(hn_feed["url"], etag=cached.get("etag"),
                           last_modified=cached.get("last_modified"))
    except Exception as e:
        return feed_info, [], f"{type(e).__name__}: {e}", {}

    if result is None:
        return feed_info, [], "not_modified", cached

    new_cache = {}
    if result.get("etag") or result.get("last_modified"):
        new_cache = {"etag": result.get("etag"), "last_modified": result.get("last_modified")}

    rss_items = parse_rss_items(result["content"])
    articles = []

    for item in rss_items:
        pub_date_raw = item.get("pub_date_raw", "")
        pub_date = parse_rss_date(pub_date_raw)

        if not (pub_date and pub_date > cutoff_time):
            continue

        desc_text = item.get("description", "")
        points = None
        comments = None
        if desc_text:
            m_points = re.search(r"Points:\s*(\d+)", desc_text)
            if m_points:
                points = int(m_points.group(1))
            m_comments = re.search(r"# Comments:\s*(\d+)", desc_text)
            if m_comments:
                comments = int(m_comments.group(1))

        if points is not None and points < min_points:
            continue

        desc_html = item.get("description") or item.get("content_encoded") or ""
        full_text = strip_html(desc_html)

        articles.append({
            "source": "tech",
            "source_name": hn_feed["name"],
            "source_category": "Hacker News",
            "language": "en",
            "title": item.get("title", "(no title)"),
            "url": item.get("link", ""),
            "pub_date": pub_date.strftime("%Y-%m-%d %H:%M"),
            "full_text": full_text,
            "hn_points": points,
            "hn_comments": comments,
        })

    return feed_info, articles, None, new_cache


def run_tech(feeds_path, cutoff_time, cache, workers=20, category=None):
    """Run the tech source check (RSS + HN). Returns (updates, stats_dict)."""
    with open(feeds_path, "r", encoding="utf-8") as f:
        feed_data = json.load(f)

    all_feeds = []
    for cat in feed_data.get("categories", []):
        cat_name = cat["name"]
        for feed in cat.get("feeds", []):
            all_feeds.append({
                "name": feed["name"], "url": feed["url"],
                "category": cat_name, "language": feed.get("language", "en"),
            })

    hn_feeds = feed_data.get("hacker_news", [])

    if category:
        if category == "Hacker News":
            all_feeds = []
        else:
            all_feeds = [fd for fd in all_feeds if fd["category"] == category]
            hn_feeds = []

    updates = []
    checked = 0
    errors = 0
    not_modified = 0
    error_details = {}

    with ThreadPoolExecutor(max_workers=max(min(workers, len(all_feeds) + len(hn_feeds)), 1)) as executor:
        futures = []
        for feed in all_feeds:
            futures.append(executor.submit(_check_tech_feed, feed, cutoff_time, cache))
        for hn_feed in hn_feeds:
            futures.append(executor.submit(_check_hn_feed, hn_feed, cutoff_time, cache))

        for future in as_completed(futures):
            try:
                feed_info, articles, error, new_cache = future.result()
            except Exception as e:
                errors += 1
                error_details[type(e).__name__] = error_details.get(type(e).__name__, 0) + 1
                continue

            checked += 1
            if new_cache:
                cache[feed_info["url"]] = new_cache

            if error:
                if error == "not_modified":
                    not_modified += 1
                else:
                    errors += 1
                    error_details[error] = error_details.get(error, 0) + 1
                continue

            updates.extend(articles)

    # Dedup pass 1: by normalized URL (prefer non-HN)
    updates = _dedup_by_url_tech(updates)

    # Dedup pass 2: by title similarity (non-HN only)
    updates = _dedup_by_title_similarity(updates)

    # Sort: non-HN by pub_date asc, then HN by points desc
    updates.sort(key=lambda u: (1, -u["hn_points"]) if u.get("hn_points") is not None else (0, u.get("pub_date", "")))

    return updates, {
        "checked": checked,
        "errors": errors,
        "not_modified": not_modified,
        "error_details": error_details,
    }


# ===========================================================================
# PODCAST source (RSS + Xiaoyuzhou)
# ===========================================================================

def _check_podcast_rss(podcast, cutoff_time, cache):
    """Check a podcast RSS feed. Returns (updates_list_or_None, error, new_cache)."""
    url = podcast["url"]
    cached = cache.get(url, {})
    try:
        result = fetch_url(url, etag=cached.get("etag"), last_modified=cached.get("last_modified"))
    except Exception as e:
        return [], f"{type(e).__name__}: {e}", {}

    if result is None:
        return None, None, cached  # None updates = not_modified signal

    new_cache = {}
    if result.get("etag") or result.get("last_modified"):
        new_cache = {"etag": result.get("etag"), "last_modified": result.get("last_modified")}

    # Use the shared parse_rss_items (supports RSS 2.0 + Atom)
    rss_items = parse_rss_items(result["content"])
    updates = []

    for item in rss_items:
        pub_date_raw = item.get("pub_date_raw", "")
        pub_date = parse_rss_date(pub_date_raw)

        if pub_date and pub_date > cutoff_time:
            desc_html = item.get("content_encoded") or item.get("description") or ""
            shownotes = strip_html(desc_html)
            updates.append({
                "source": "podcast",
                "podcast_name": podcast["name"],
                "rank": podcast.get("rank", 0),
                "title": item.get("title", "(no title)"),
                "url": item.get("link", ""),
                "pub_date": pub_date.strftime("%Y-%m-%d %H:%M"),
                "shownotes": shownotes[:SHOWNOTES_MAX],
                "duration": parse_duration(item.get("duration_raw")),
            })

    return updates, None, new_cache


def _check_xiaoyuzhou_fallback(podcast, content):
    """Regex fallback: extract the latest episode when __NEXT_DATA__ is unavailable.

    Preserved from the original podcast-rss-monitor skill for robustness.
    """
    updates = []
    episode_pattern = r'-\s*!\[Image[^\]]*\]\([^)]+\)\s*\n+([^\n]+)\s*\n+(.*?)(?=-\s*!\[Image|$)'
    matches = re.findall(episode_pattern, content, re.DOTALL)

    if matches:
        title, desc = matches[0]
        title = title.strip()
        desc = desc.strip()

        episode_url_match = re.search(r'href="([^"]*episode[^"]*)"', content)
        episode_url = episode_url_match.group(1) if episode_url_match else podcast["url"]

        pub_date = None
        date_match = re.search(r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)', content)
        if date_match:
            date_str = date_match.group(1).replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-')
            try:
                parsed = datetime.strptime(date_str, '%Y-%m-%d')
                pub_date = parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        plain = strip_html(desc)
        updates.append({
            "source": "podcast",
            "podcast_name": podcast["name"],
            "rank": podcast.get("rank", 0),
            "title": title,
            "url": episode_url,
            "pub_date": pub_date.strftime("%Y-%m-%d %H:%M") if pub_date else "未知（小宇宙最新一集）",
            "shownotes": plain[:SHOWNOTES_MAX] if len(plain) > SHOWNOTES_MAX else plain,
        })

    return updates


def _check_podcast_xiaoyuzhou(podcast, cutoff_time, cache):
    """Check a Xiaoyuzhou podcast page. Returns (updates_list_or_None, error, new_cache)."""
    url = podcast["url"]
    cached = cache.get(url, {})
    try:
        result = fetch_url(url, etag=cached.get("etag"), last_modified=cached.get("last_modified"))
    except Exception as e:
        return [], f"{type(e).__name__}: {e}", {}

    if result is None:
        return None, None, cached

    new_cache = {}
    if result.get("etag") or result.get("last_modified"):
        new_cache = {"etag": result.get("etag"), "last_modified": result.get("last_modified")}

    episodes = extract_xiaoyuzhou_episodes(result["content"])
    updates = []

    if episodes:
        for ep in episodes:
            title = ep.get("title", "").strip()
            if not title:
                continue

            eid = ep.get("eid", "")
            pub_date_str = ep.get("pubDate", "")
            pub_date = _common.parse_iso8601_date(pub_date_str)

            if pub_date and pub_date <= cutoff_time:
                continue

            description = ep.get("description", "") or ""
            shownotes = strip_html(description)
            episode_url = f"https://www.xiaoyuzhoufm.com/episode/{eid}" if eid else url

            # Xiaoyuzhou duration is in seconds (int); normalize via parse_duration
            duration = parse_duration(str(ep.get("duration", ""))) if ep.get("duration") else None

            updates.append({
                "source": "podcast",
                "podcast_name": podcast["name"],
                "rank": podcast.get("rank", 0),
                "title": title,
                "url": episode_url,
                "pub_date": pub_date.strftime("%Y-%m-%d %H:%M") if pub_date else "未知（小宇宙）",
                "shownotes": shownotes[:SHOWNOTES_MAX],
                "duration": duration,
            })
    else:
        # Fallback: regex parsing when __NEXT_DATA__ is unavailable
        updates = _check_xiaoyuzhou_fallback(podcast, result["content"])

    return updates, None, new_cache


def run_podcast(podcasts_path, cutoff_time, cache, workers=30, link_type="all", count=1000):
    """Run the podcast source check. Returns (updates, stats_dict)."""
    with open(podcasts_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    podcasts = data["podcasts"]
    if link_type != "all":
        podcasts = [p for p in podcasts if p.get("link_type") == link_type]
    podcasts = podcasts[:count]

    all_updates = []
    checked = 0
    errors = 0
    not_modified = 0
    error_details = {}

    def process_podcast(podcast):
        if podcast.get("link_type") == "xiaoyuzhou":
            return _check_podcast_xiaoyuzhou(podcast, cutoff_time, cache)
        return _check_podcast_rss(podcast, cutoff_time, cache)

    def process_domain_group(podcast_group):
        """Process same-domain podcasts serially to avoid rate-limiting."""
        results = []
        for p in podcast_group:
            results.append((p, process_podcast(p)))
            time.sleep(random.uniform(0.1, 0.3))
        return results

    # Group by domain — same domain serial, different domains parallel
    domain_groups = defaultdict(list)
    for p in podcasts:
        try:
            domain = urlparse(p["url"]).netloc
        except Exception:
            domain = "unknown"
        domain_groups[domain].append(p)

    print(f"域名分组: {len(domain_groups)} 个域名", file=sys.stderr)

    num_workers = min(len(domain_groups), workers)
    with ThreadPoolExecutor(max_workers=max(num_workers, 1)) as executor:
        future_to_domain = {
            executor.submit(process_domain_group, group): domain
            for domain, group in domain_groups.items()
        }

        for future in as_completed(future_to_domain):
            domain = future_to_domain[future]
            try:
                results = future.result()
                for podcast, (updates, error_info, cache_entry) in results:
                    checked += 1
                    if error_info:
                        errors += 1
                        error_details[error_info] = error_details.get(error_info, 0) + 1
                    if updates is None:
                        not_modified += 1
                        continue
                    if updates:
                        all_updates.extend(updates)
                    if cache_entry:
                        cache[podcast["url"]] = cache_entry
            except Exception as e:
                group_size = len(domain_groups[domain])
                checked += group_size
                errors += group_size
                err_name = type(e).__name__
                error_details[err_name] = error_details.get(err_name, 0) + 1

            # Progress logging
            if checked % 100 == 0:
                print(f"  进度: {checked}/{len(podcasts)} (更新: {len(all_updates)}, 错误: {errors})",
                      file=sys.stderr)

    # Sort by rank
    all_updates.sort(key=lambda x: x.get("rank", 9999))

    return all_updates, {
        "checked": checked,
        "errors": errors,
        "not_modified": not_modified,
        "error_details": error_details,
    }


# ===========================================================================
# Shared helpers (dedup, concurrency)
# ===========================================================================

import threading


def _make_semaphore(workers):
    """Create a threading semaphore for concurrency limiting."""
    return threading.Semaphore(value=workers)


def _dedup_by_url(updates):
    """Deduplicate updates by URL. First occurrence wins."""
    seen = set()
    unique = []
    for u in updates:
        url = u.get("url", "")
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        unique.append(u)
    return unique


def _dedup_wechat_by_title(updates, threshold=TITLE_SIM_THRESHOLD):
    """Deduplicate wechat updates by title similarity (Jaccard > threshold).

    WeChat articles are frequently reposted across different accounts.
    URL-based dedup cannot catch these because each repost has a unique URL.
    This function identifies near-duplicate titles and keeps the version with
    the longest ``full_text`` (most complete content), dropping the rest.

    Only compares updates from DIFFERENT accounts — same-account dupes are
    already handled by URL dedup.
    """
    drop = set()
    for idx_a in range(len(updates)):
        if idx_a in drop:
            continue
        a = updates[idx_a]
        for idx_b in range(idx_a + 1, len(updates)):
            if idx_b in drop:
                continue
            b = updates[idx_b]
            # Only cross-account comparison (reposts); same-account dupes
            # are URL-deduplicated already.
            if a.get("account_name", "") == b.get("account_name", ""):
                continue
            if _title_similarity(a.get("title", ""), b.get("title", "")) > threshold:
                # Keep whichever has longer full_text
                if len(b.get("full_text", "")) > len(a.get("full_text", "")):
                    drop.add(idx_a)
                    break  # idx_a is dropped, move on
                else:
                    drop.add(idx_b)
    if drop:
        updates = [u for i, u in enumerate(updates) if i not in drop]
    return updates


_TRACKING_PARAMS = re.compile(
    r"^(utm_[a-z]+|ref|source|fbclid|gclid|mc_eid|campaign|medium|content|term)$",
    re.IGNORECASE,
)


def _normalize_url(url):
    """Normalize a URL by removing tracking query parameters and fragments."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        query = ""
        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=True)
            clean = {k: v for k, v in params.items() if not _TRACKING_PARAMS.match(k)}
            if clean:
                parts = [f"{k}={v}" for k, vs in sorted(clean.items()) for v in vs]
                query = "&".join(parts)
        path = parsed.path.rstrip("/")
        return urlunparse((parsed.scheme, parsed.netloc.lower(), path, parsed.params, query, ""))
    except Exception:
        return url.lower().strip()


def _dedup_by_url_tech(updates):
    """Deduplicate tech updates by normalized URL. Prefer non-HN source."""
    seen = {}
    unique = []
    for u in updates:
        norm = _normalize_url(u.get("url", ""))
        if norm and norm in seen:
            existing_idx = seen[norm]
            existing = unique[existing_idx]
            if existing.get("source_category") == "Hacker News" and u.get("source_category") != "Hacker News":
                unique[existing_idx] = u
            continue
        if norm:
            seen[norm] = len(unique)
        unique.append(u)
    return unique


_CJK_RANGE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
# Latin/digit runs (split boundaries for non-CJK text)
_LATIN_SPLIT = re.compile(r"[\s\-_:,;|/\\()（）\[\]【】「」\"'""''!?！？，。、~]+")


def _title_tokens(title):
    """Tokenize a title for similarity comparison, CJK-aware.

    Strategy:
      - CJK characters → character bigrams (two consecutive chars form one token).
        Bigrams capture meaningful sub-word structure in Chinese, where word
        boundaries are not space-delimited.
      - Latin words / numbers → kept as whole tokens (space/punct delimited).

    Returns a set of tokens, or an empty set if the title is too short.
    """
    if not title:
        return set()
    title = title.lower().strip()
    tokens = set()
    i = 0
    n = len(title)

    while i < n:
        ch = title[i]
        if _CJK_RANGE.match(ch):
            # Collect a run of consecutive CJK chars
            j = i
            while j < n and _CJK_RANGE.match(title[j]):
                j += 1
            cjk_run = title[i:j]
            # Bigrams
            if len(cjk_run) >= 2:
                for k in range(len(cjk_run) - 1):
                    tokens.add(cjk_run[k:k + 2])
            else:
                # Single CJK char — keep it (useful for very short titles)
                tokens.add(cjk_run)
            i = j
        else:
            # Non-CJK: collect until a split boundary or CJK char
            j = i
            while j < n and not _CJK_RANGE.match(title[j]) and not _LATIN_SPLIT.match(title[j]):
                j += 1
            word = title[i:j]
            if word:
                tokens.add(word)
            # Skip boundary chars
            while j < n and _LATIN_SPLIT.match(title[j]):
                j += 1
            i = j if j > i else i + 1

    return tokens


def _title_similarity(t1, t2):
    """Compute Jaccard similarity between two titles (CJK-aware bigrams).

    Falls back to word-level splitting for purely Latin titles. CJK bigrams
    handle Chinese titles where space-based tokenization produces no useful
    word boundaries.
    """
    if not t1 or not t2:
        return 0.0
    tokens1 = _title_tokens(t1)
    tokens2 = _title_tokens(t2)
    if not tokens1 or not tokens2:
        return 0.0
    return len(tokens1 & tokens2) / len(tokens1 | tokens2)


def _dedup_by_title_similarity(updates):
    """Deduplicate non-HN tech updates by title similarity (Jaccard > threshold)."""
    drop = set()
    non_hn = [(i, u) for i, u in enumerate(updates) if u.get("source_category") != "Hacker News"]
    for idx_a in range(len(non_hn)):
        i, a = non_hn[idx_a]
        if i in drop:
            continue
        for idx_b in range(idx_a + 1, len(non_hn)):
            j, b = non_hn[idx_b]
            if j in drop:
                continue
            if _title_similarity(a.get("title", ""), b.get("title", "")) > TITLE_SIM_THRESHOLD:
                if len(b.get("full_text", "")) > len(a.get("full_text", "")):
                    drop.add(i)
                else:
                    drop.add(j)
    if drop:
        updates = [u for i, u in enumerate(updates) if i not in drop]
    return updates


def _load_all_feed_urls(refs_dir, sources_to_run):
    """Collect the set of all currently-monitored feed URLs across sources.

    Used for unified cache pruning — cache entries whose URL is no longer in
    any source's reference file are stale and should be removed.
    """
    all_urls = set()
    if "wechat" in sources_to_run:
        path = refs_dir / "feeds_wechat.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for fd in json.load(f).get("feeds", []):
                        if fd.get("url"):
                            all_urls.add(fd["url"])
            except (json.JSONDecodeError, OSError):
                pass
    if "tech" in sources_to_run:
        path = refs_dir / "feeds_tech.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for cat in data.get("categories", []):
                        for feed in cat.get("feeds", []):
                            if feed.get("url"):
                                all_urls.add(feed["url"])
                    for hn in data.get("hacker_news", []):
                        if hn.get("url"):
                            all_urls.add(hn["url"])
            except (json.JSONDecodeError, OSError):
                pass
    if "podcast" in sources_to_run:
        path = refs_dir / "podcasts.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for p in json.load(f).get("podcasts", []):
                        if p.get("url"):
                            all_urls.add(p["url"])
            except (json.JSONDecodeError, OSError):
                pass
    return all_urls


def _prune_cache(cache, current_urls):
    """Remove cache entries for URLs no longer in the monitored set.

    Returns the count of pruned entries.
    """
    stale = [u for u in cache if u not in current_urls]
    for u in stale:
        del cache[u]
    return len(stale)


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Unified RSS update checker for wechat/tech/podcast sources"
    )
    parser.add_argument("--source", default="all",
                        choices=["all", "wechat", "tech", "podcast"],
                        help="Which source(s) to check (default: all)")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--cache", default=None, help="HTTP cache file path")
    parser.add_argument("--hours", type=int, default=24, help="Time window in hours (default: 24)")
    parser.add_argument("--workers", type=int, default=0,
                        help="Concurrent workers per source (0 = use per-source defaults)")
    parser.add_argument("--category", default=None, help="Filter by category (source-specific)")
    parser.add_argument("--count", type=int, default=0, help="Max feeds to check (0 = all)")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    script_dir = Path(__file__).resolve().parent
    refs_dir = script_dir.parent / "references"
    output_path = Path(args.output)
    cache_path = Path(args.cache) if args.cache else output_path.parent / ".http_cache.json"

    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    cache = load_cache(cache_path)

    sources_to_run = ["wechat", "tech", "podcast"] if args.source == "all" else [args.source]
    all_updates = []
    source_stats = {}
    check_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    for src in sources_to_run:
        print(f"\n--- Checking {src} source ---")
        if src == "wechat":
            w = args.workers if args.workers > 0 else 10
            updates, stats = run_wechat(
                refs_dir / "feeds_wechat.json", cutoff_time, cache,
                workers=w, category=args.category, count=args.count,
            )
        elif src == "tech":
            w = args.workers if args.workers > 0 else 20
            updates, stats = run_tech(
                refs_dir / "feeds_tech.json", cutoff_time, cache,
                workers=w, category=args.category,
            )
        elif src == "podcast":
            w = args.workers if args.workers > 0 else 30
            updates, stats = run_podcast(
                refs_dir / "podcasts.json", cutoff_time, cache,
                workers=w, count=args.count if args.count > 0 else 1000,
            )
        else:
            continue

        all_updates.extend(updates)
        source_stats[src] = {"checked": stats["checked"], "updates": len(updates),
                             **{k: v for k, v in stats.items() if k not in ("checked",)}}
        print(f"  {src}: {len(updates)} updates from {stats['checked']} checked "
              f"({stats['errors']} errors, {stats['not_modified']} not-modified)")
        if stats.get("error_details"):
            print(f"  error details: {stats['error_details']}")
        if stats.get("date_unparsed"):
            print(f"  WARNING: {stats['date_unparsed']} items had unparseable dates (dropped)")
        if stats.get("categories"):
            for cat, cat_stats in stats["categories"].items():
                if cat_stats["updates"] > 0:
                    print(f"    {cat}: {cat_stats['updates']} updates from {cat_stats['checked']} feeds")
        if stats.get("repost_removed"):
            print(f"  Removed {stats['repost_removed']} cross-account reposts (title dedup)")
        if stats.get("short_text_count"):
            print(f"  NOTE: {stats['short_text_count']} articles have short full_text "
                  f"(< {WECHAT_SHORT_TEXT_THRESHOLD} chars) — consider running Step 4 (fetch_articles.py)")

    # Prune stale cache entries (URLs no longer in any monitored source)
    current_urls = _load_all_feed_urls(refs_dir, sources_to_run)
    pruned = _prune_cache(cache, current_urls)
    if pruned > 0:
        print(f"\nCache pruned: removed {pruned} stale entries")

    # Save cache
    save_cache(cache_path, cache)

    # Build output
    output = {
        "metadata": {
            "check_time": check_time,
            "hours": args.hours,
            "sources": source_stats,
            "update_count": len(all_updates),
        },
        "updates": all_updates,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total_updates = len(all_updates)
    total_checked = sum(s["checked"] for s in source_stats.values())
    print(f"\n{'='*60}")
    print(f"Total: {total_updates} updates from {total_checked} sources checked")
    for src, stats in source_stats.items():
        print(f"  {src}: {stats['updates']} updates / {stats['checked']} checked")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
