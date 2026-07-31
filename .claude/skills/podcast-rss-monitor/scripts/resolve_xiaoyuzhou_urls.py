#!/usr/bin/env python3
"""
解析非小宇宙的单集链接为小宇宙单集链接。

读取 latest_updates.json，对 episode_url 不是 xiaoyuzhoufm.com/episode/ 的条目，
通过播客的小宇宙主页获取最近单集列表，按标题匹配后替换为小宇宙单集链接。
"""

import json
import re
import sys
import time
import random
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from _common import fetch_url as _fetch_url_raw, extract_xiaoyuzhou_episodes


def fetch_url(url):
    """获取 URL 的 HTML 文本（用于小宇宙页面抓取）。

    Thin wrapper over _common.fetch_url: drops ETag/Last-Modified support (the
    resolver always wants fresh page content) and returns the decoded body
    string directly, or None on 304.
    """
    result = _fetch_url_raw(url, accept='text/html, */*')
    if result is None:
        return None
    return result['content']


def parse_xiaoyuzhou_episodes(html_content):
    """从小宇宙页面解析出最近单集列表，返回 [(title, eid), ...]"""
    result = []
    for ep in extract_xiaoyuzhou_episodes(html_content):
        title = ep.get('title', '').strip()
        eid = ep.get('eid', '').strip()
        if title and eid:
            result.append((title, eid))
    return result


def normalize_title(title):
    """归一化标题用于匹配：去首尾空格、全角转半角、统一空白"""
    title = title.strip()
    title = title.replace('\u3000', ' ')  # 全角空格
    title = re.sub(r'\s+', ' ', title)
    return title


def match_episode(target_title, episodes):
    """从单集列表中匹配标题，返回 eid 或 None

    匹配策略：归一化后精确匹配 → 包含匹配
    """
    target = normalize_title(target_title)

    # 精确匹配
    for title, eid in episodes:
        if normalize_title(title) == target:
            return eid

    # 包含匹配（target 包含 title 或 title 包含 target）
    for title, eid in episodes:
        nt = normalize_title(title)
        if target in nt or nt in target:
            return eid

    return None


def load_podcasts_map():
    """加载播客名 -> 小宇宙播客页面 URL 映射"""
    script_dir = Path(__file__).parent
    podcasts_file = script_dir.parent / 'references' / 'podcasts.json'
    with open(podcasts_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return {p['name']: p.get('xiaoyuzhou_url', '') for p in data.get('podcasts', []) if p.get('xiaoyuzhou_url')}


def resolve_one_podcast(podcast_name, indices, episode_titles, xyz_url):
    """解析单个播客的小宇宙单集链接。

    线程安全：只读取共享输入，返回结果（idx -> eid|None）由主线程统一回写。
    Returns (podcast_name, resolved_pairs, failed_count)
      resolved_pairs: [(idx, eid_or_None), ...]  (eid None 表示未匹配，计入 failed)
      failed_count:   无法解析/未匹配的更新数（含无 xyz_url、无单集数据、抛异常）
    """
    if not xyz_url:
        print(f'  [跳过] {podcast_name}: 无小宇宙页面链接')
        return podcast_name, [(idx, None) for idx in indices], len(indices)

    try:
        html = fetch_url(xyz_url)
        episodes = parse_xiaoyuzhou_episodes(html)

        if not episodes:
            print(f'  [失败] {podcast_name}: 小宇宙页面无单集数据')
            return podcast_name, [(idx, None) for idx in indices], len(indices)

        pairs = []
        for idx in indices:
            eid = match_episode(episode_titles[idx], episodes)
            if not eid:
                print(f'  [未匹配] {podcast_name}: "{episode_titles[idx][:30]}" 在 {len(episodes)} 个单集中未找到')
            pairs.append((idx, eid))

        # 请求间抖动延迟，降低对同一域名并发的限流风险
        time.sleep(random.uniform(0.2, 0.5))
        return podcast_name, pairs, 0

    except Exception as e:
        print(f'  [错误] {podcast_name}: {type(e).__name__}: {e}')
        return podcast_name, [(idx, None) for idx in indices], len(indices)


def resolve_updates(input_path, output_path=None):
    """解析所有非小宇宙单集链接"""
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    updates = data['updates']
    podcasts_map = load_podcasts_map()

    # 找出需要解析的更新（非小宇宙单集链接）
    needs_resolve = []
    for i, update in enumerate(updates):
        url = update.get('episode_url', '')
        if 'xiaoyuzhoufm.com/episode/' not in url:
            needs_resolve.append(i)

    if not needs_resolve:
        print(f'所有 {len(updates)} 个更新已有小宇宙单集链接，无需解析')
        return

    print(f'共 {len(updates)} 个更新，其中 {len(needs_resolve)} 个需要解析小宇宙链接')

    # 按播客名分组去重
    podcast_indices = defaultdict(list)  # podcast_name -> [update_index, ...]
    for idx in needs_resolve:
        name = updates[idx]['podcast_name']
        podcast_indices[name].append(idx)

    # 标题快照（只读，供 worker 读取，避免并发访问 updates 列表）
    episode_titles = [u.get('episode_title', '') for u in updates]

    # 按播客并发解析。目标都是 xiaoyuzhoufm.com 同一域名，受控并发 + 抖动延迟防限流。
    # 各 worker 只返回 (idx, eid)，主线程统一回写 updates，无共享可变状态。
    resolved = 0
    failed = 0
    num_workers = min(len(podcast_indices), 4)
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [
            executor.submit(resolve_one_podcast, name, indices, episode_titles, podcasts_map.get(name))
            for name, indices in podcast_indices.items()
        ]
        for future in as_completed(futures):
            _, pairs, group_failed = future.result()
            failed += group_failed
            for idx, eid in pairs:
                if eid:
                    updates[idx]['episode_url'] = f'https://www.xiaoyuzhoufm.com/episode/{eid}'
                    resolved += 1
                else:
                    failed += 1

    # 清理已有小宇宙链接的 ?utm_source=rss 后缀
    cleaned = 0
    for update in updates:
        url = update.get('episode_url', '')
        if 'xiaoyuzhoufm.com/episode/' in url and '?utm_source=' in url:
            update['episode_url'] = url.split('?utm_source=')[0]
            cleaned += 1

    print(f'解析完成: 解析 {resolved} 个，失败 {failed} 个，清理后缀 {cleaned} 个')

    # 保存
    out = output_path or input_path
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'已保存: {out}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='解析非小宇宙单集链接为小宇宙单集链接')
    parser.add_argument('--input', '-i', type=str, help='输入 latest_updates.json 路径')
    parser.add_argument('--output', '-o', type=str, help='输出文件路径（默认覆盖输入文件）')
    args = parser.parse_args()

    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')

    input_path = args.input or str(Path.cwd() / 'workspaces' / 'podcast' / 'latest_updates.json')
    resolve_updates(input_path, args.output)
