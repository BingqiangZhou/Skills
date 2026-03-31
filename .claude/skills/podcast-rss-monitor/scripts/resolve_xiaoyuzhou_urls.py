#!/usr/bin/env python3
"""
解析非小宇宙的单集链接为小宇宙单集链接。

读取 latest_updates.json，对 episode_url 不是 xiaoyuzhoufm.com/episode/ 的条目，
通过播客的小宇宙主页获取最近单集列表，按标题匹配后替换为小宇宙单集链接。
"""

import json
import re
import ssl
import sys
import time
import random
import urllib.request
import urllib.error
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

TIMEOUT = 15
MAX_RETRIES = 2


def _create_ssl_context(relaxed=False):
    if relaxed:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def fetch_url(url, max_retries=MAX_RETRIES):
    """获取 URL 内容"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html, */*'
    }
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            use_relaxed = attempt > 0 and last_error and 'SSL' in str(last_error)
            ctx = _create_ssl_context(relaxed=use_relaxed)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as response:
                content = response.read()
                for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                    try:
                        return content.decode(encoding)
                    except (UnicodeDecodeError, LookupError):
                        continue
                return content.decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            last_error = e
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
    raise last_error


def parse_xiaoyuzhou_episodes(html_content):
    """从小宇宙页面解析出最近单集列表，返回 [(title, eid), ...]"""
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html_content,
        re.DOTALL
    )
    if not match:
        return []

    try:
        page_data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return []

    try:
        episodes = page_data['props']['pageProps']['podcast']['episodes']
    except (KeyError, TypeError):
        return []

    result = []
    for ep in episodes:
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

    # 按播客获取小宇宙单集列表
    resolved = 0
    failed = 0
    for podcast_name, indices in podcast_indices.items():
        xyz_url = podcasts_map.get(podcast_name)
        if not xyz_url:
            print(f'  [跳过] {podcast_name}: 无小宇宙页面链接')
            failed += len(indices)
            continue

        try:
            html = fetch_url(xyz_url)
            episodes = parse_xiaoyuzhou_episodes(html)

            if not episodes:
                print(f'  [失败] {podcast_name}: 小宇宙页面无单集数据')
                failed += len(indices)
                continue

            for idx in indices:
                title = updates[idx]['episode_title']
                eid = match_episode(title, episodes)
                if eid:
                    updates[idx]['episode_url'] = f'https://www.xiaoyuzhoufm.com/episode/{eid}'
                    resolved += 1
                else:
                    print(f'  [未匹配] {podcast_name}: "{title[:30]}" 在 {len(episodes)} 个单集中未找到')
                    failed += 1

            # 请求间延迟，避免限流
            time.sleep(random.uniform(0.2, 0.5))

        except Exception as e:
            print(f'  [错误] {podcast_name}: {type(e).__name__}: {e}')
            failed += len(indices)

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

    input_path = args.input or str(Path.cwd() / 'podcast-workspace' / 'latest_updates.json')
    resolve_updates(input_path, args.output)
