#!/usr/bin/env python3
"""
Resolve non-Xiaoyuzhou episode URLs to Xiaoyuzhou episode links.

Reads latest_updates.json, and for podcast updates whose url is NOT already a
xiaoyuzhoufm.com/episode/ link, fetches the podcast's Xiaoyuzhou page to get
recent episodes, matches by title, and rewrites the url to the canonical
xiaoyuzhoufm.com/episode/{eid} link.

Podcast-specific optional step — only relevant for the podcast source.
"""

import json
import re
import sys
import time
import random
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from _common import (fetch_url as _fetch_url_raw, extract_xiaoyuzhou_episodes,
                     save_json_atomic)

# Minimum length for substring-based title matching. Titles shorter than this
# are only matched exactly, to prevent false positives like "第50期" matching
# "第500期" (the former is a substring of the latter).
MIN_SUBSTRATE_LEN = 8


def fetch_url(url):
    """Fetch HTML text from a URL (for Xiaoyuzhou page scraping).

    Thin wrapper over _common.fetch_url: drops ETag/Last-Modified support (the
    resolver always wants fresh page content) and returns the decoded body
    string directly, or None on 304.
    """
    result = _fetch_url_raw(url, accept="text/html, */*")
    if result is None:
        return None
    return result["content"]


def parse_xiaoyuzhou_episodes(html_content):
    """Parse recent episodes from a Xiaoyuzhou page. Returns [(title, eid), ...]."""
    result = []
    for ep in extract_xiaoyuzhou_episodes(html_content):
        title = ep.get("title", "").strip()
        eid = ep.get("eid", "").strip()
        if title and eid:
            result.append((title, eid))
    return result


def normalize_title(title):
    """Normalize a title for matching: strip, full-width space → space, collapse whitespace."""
    title = title.strip()
    title = title.replace("\u3000", " ")
    title = re.sub(r"\s+", " ", title)
    return title


def match_episode(target_title, episodes):
    """Match a title against an episode list. Returns eid or None.

    Strategy: exact match after normalization → bidirectional substring containment.

    Substring matching requires the shorter string to be at least
    MIN_SUBSTRATE_LEN chars to avoid false positives (e.g. "第50期" being a
    substring of "第500期"). Titles shorter than this are only matched exactly.
    """
    target = normalize_title(target_title)

    # Exact match
    for title, eid in episodes:
        if normalize_title(title) == target:
            return eid

    # Substring containment (with minimum length guard)
    for title, eid in episodes:
        nt = normalize_title(title)
        if not nt or not target:
            continue
        shorter = target if len(target) <= len(nt) else nt
        if len(shorter) < MIN_SUBSTRATE_LEN:
            continue  # too short for reliable substring match
        if target in nt or nt in target:
            return eid

    return None


def load_podcasts_map():
    """Load podcast name → Xiaoyuzhou podcast page URL map."""
    script_dir = Path(__file__).parent
    podcasts_file = script_dir.parent / "references" / "podcasts.json"
    with open(podcasts_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {p["name"]: p.get("xiaoyuzhou_url", "")
            for p in data.get("podcasts", []) if p.get("xiaoyuzhou_url")}


def resolve_one_podcast(podcast_name, indices, episode_titles, xyz_url):
    """Resolve Xiaoyuzhou episode URLs for a single podcast.

    Thread-safe: only reads shared input, returns results for the main thread
    to write back.
    Returns (podcast_name, resolved_pairs, failed_count).
    """
    if not xyz_url:
        print(f"  [跳过] {podcast_name}: 无小宇宙页面链接")
        # group_failed stays 0 — the caller counts each unresolved pair
        # itself; counting them here too double-reported every failure.
        return podcast_name, [(idx, None) for idx in indices], 0

    try:
        # Space requests on the shared xiaoyuzhoufm.com domain BEFORE each
        # request; sleeping after a worker's only request just stalled the
        # pool for the next podcast.
        time.sleep(random.uniform(0.2, 0.5))
        html = fetch_url(xyz_url)
        episodes = parse_xiaoyuzhou_episodes(html)

        if not episodes:
            print(f"  [失败] {podcast_name}: 小宇宙页面无单集数据")
            return podcast_name, [(idx, None) for idx in indices], 0

        pairs = []
        for idx in indices:
            eid = match_episode(episode_titles[idx], episodes)
            if not eid:
                print(f"  [未匹配] {podcast_name}: \"{episode_titles[idx][:30]}\" "
                      f"在 {len(episodes)} 个单集中未找到")
            pairs.append((idx, eid))

        return podcast_name, pairs, 0

    except Exception as e:
        print(f"  [错误] {podcast_name}: {type(e).__name__}: {e}")
        return podcast_name, [(idx, None) for idx in indices], 0


def _clean_utm_suffixes(updates):
    """Strip ?utm_source=… from already-Xiaoyuzhou episode URLs. Returns count.

    Runs on EVERY exit path — the early "nothing to resolve" return used to
    skip it, so the runs where all URLs were already canonical (exactly the
    case the cleaning exists for) never cleaned anything.
    """
    cleaned = 0
    for update in updates:
        if update.get("source") != "podcast":
            continue
        url = update.get("url", "")
        if "xiaoyuzhoufm.com/episode/" in url and "?utm_source=" in url:
            update["url"] = url.split("?utm_source=")[0]
            cleaned += 1
    return cleaned


def resolve_updates(input_path, output_path=None):
    """Resolve all non-Xiaoyuzhou episode URLs."""
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    updates = data["updates"]
    podcasts_map = load_podcasts_map()

    # Find podcast updates that need resolution (only podcast-source items
    # with non-Xiaoyuzhou URLs).
    needs_resolve = []
    for i, update in enumerate(updates):
        if update.get("source") != "podcast":
            continue
        url = update.get("url", "")
        if "xiaoyuzhoufm.com/episode/" not in url:
            needs_resolve.append(i)

    out = output_path or input_path

    if not needs_resolve:
        podcast_count = sum(1 for u in updates if u.get("source") == "podcast")
        cleaned = _clean_utm_suffixes(updates)
        note = f"；清理后缀 {cleaned} 个" if cleaned else ""
        print(f"所有 {podcast_count} 个播客更新已有小宇宙单集链接，无需解析{note}")
        if cleaned:
            save_json_atomic(out, data)
            print(f"已保存: {out}")
        return

    print(f"共 {len(updates)} 个更新，其中 {len(needs_resolve)} 个播客更新需要解析小宇宙链接")

    # Group by podcast name to deduplicate
    podcast_indices = defaultdict(list)
    for idx in needs_resolve:
        name = updates[idx]["podcast_name"]
        podcast_indices[name].append(idx)

    # Title snapshot (read-only for workers)
    episode_titles = [u.get("title", "") for u in updates]

    resolved = 0
    failed = 0
    num_workers = min(len(podcast_indices), 4)
    with ThreadPoolExecutor(max_workers=max(num_workers, 1)) as executor:
        futures = [
            executor.submit(resolve_one_podcast, name, indices,
                            episode_titles, podcasts_map.get(name))
            for name, indices in podcast_indices.items()
        ]
        for future in as_completed(futures):
            _, pairs, group_failed = future.result()
            failed += group_failed
            for idx, eid in pairs:
                if eid:
                    updates[idx]["url"] = f"https://www.xiaoyuzhoufm.com/episode/{eid}"
                    resolved += 1
                else:
                    failed += 1

    cleaned = _clean_utm_suffixes(updates)

    print(f"解析完成: 解析 {resolved} 个，失败 {failed} 个，清理后缀 {cleaned} 个")

    save_json_atomic(out, data)
    print(f"已保存: {out}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="解析非小宇宙单集链接为小宇宙单集链接")
    parser.add_argument("--input", "-i", type=str, help="输入 latest_updates.json 路径")
    parser.add_argument("--output", "-o", type=str, help="输出文件路径（默认覆盖输入文件）")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    input_path = args.input or str(Path.cwd() / "workspaces" / "daily-digests" / "data" / "rss" / "latest_updates.json")
    resolve_updates(input_path, args.output)
