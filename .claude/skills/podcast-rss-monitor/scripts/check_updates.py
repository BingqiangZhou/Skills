#!/usr/bin/env python3
"""
播客更新检查脚本 - v2
- RSS 类型：直接请求 RSS feed 解析 XML，支持 If-Modified-Since 增量更新
- 小宇宙类型：请求页面解析最新单集，支持日期过滤
- 并发处理，错误统计
- 广告内容过滤由 Step 2 的子代理完成（见 SKILL.md），本脚本只做抓取与解析
"""

import json
import sys
import re
import time
import random
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from urllib.parse import urlparse
import urllib.request
import urllib.error

from _common import (
    TIMEOUT,
    create_ssl_context,
    fetch_url,
    parse_xiaoyuzhou_next_data as _parse_xiaoyuzhou_next_data,
)

# Named limits instead of inline magic numbers.
SHOWNOTES_MAX = 2000     # shownotes 文本的截取长度
PROGRESS_EVERY = 100     # 每处理多少个播客输出一次进度

def get_utc_now():
    """获取当前 UTC 时间"""
    return datetime.now(timezone.utc)

class _HTMLStripper(HTMLParser):
    """HTML 转纯文本的解析器，基于标准库 html.parser"""
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
        """处理命名实体如 &amp; &nbsp;"""
        if self._skip_depth > 0:
            return
        entities = {'amp': '&', 'lt': '<', 'gt': '>', 'nbsp': '', 'quot': '"',
                     'apos': "'", 'mdash': '—', 'ndash': '–', 'bull': '•'}
        self._pieces.append(entities.get(name, f'&{name};'))

    def handle_charref(self, name):
        """处理数字实体如 &#39; &#x27;"""
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


def strip_html(text):
    """将 HTML 内容转换为纯文本"""
    if not text:
        return ''
    stripper = _HTMLStripper()
    stripper.feed(text)
    return stripper.get_text()

def parse_rss_date(date_str):
    """解析 RSS 日期格式"""
    if not date_str:
        return None
    formats = [
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S GMT',
        '%a, %d %b %Y %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S',
    ]
    date_str = date_str.strip()
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None

def parse_xiaoyuzhou_next_data(html_content):
    """Extract __NEXT_DATA__ from a Xiaoyuzhou page.

    Thin wrapper over _common.parse_xiaoyuzhou_next_data preserving this
    module's existing (buildId, episodes) return shape. Returns (None, None)
    when the page data can't be parsed.
    """
    page_data = _parse_xiaoyuzhou_next_data(html_content)
    if page_data is None:
        return None, None
    build_id = page_data.get('buildId')
    episodes = page_data['props']['pageProps']['podcast']['episodes']
    return build_id, episodes

def parse_iso8601_date(date_str):
    """Parse ISO 8601 date string (e.g. '2024-11-04T04:19:30.561Z').

    Returns a timezone-aware datetime in UTC, or None on failure.
    """
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None

def check_rss_update(podcast, cutoff_time, cache=None):
    """检查 RSS 类型播客的更新"""
    updates = []
    error_info = None
    new_cache_entry = None
    try:
        url = podcast['url']
        etag = None
        last_modified = None

        # 从缓存读取 ETag / Last-Modified
        if cache and url in cache:
            etag = cache[url].get('etag')
            last_modified = cache[url].get('last_modified')

        result = fetch_url(url, etag=etag, last_modified=last_modified)
        if result is None:
            # 304 Not Modified，无需解析
            return None, None, None

        content = result['content']
        if result.get('etag') or result.get('last_modified'):
            new_cache_entry = {'etag': result.get('etag'), 'last_modified': result.get('last_modified')}

        root = ET.fromstring(content)
        items = root.findall('.//item')

        for item in items:
            title_elem = item.find('title')
            title = title_elem.text if title_elem is not None and title_elem.text else ''

            link_elem = item.find('link')
            link = link_elem.text if link_elem is not None and link_elem.text else ''

            pub_date_elem = item.find('pubDate')
            pub_date_str = pub_date_elem.text if pub_date_elem is not None and pub_date_elem.text else ''

            desc_elem = item.find('description')
            description = desc_elem.text if desc_elem is not None and desc_elem.text else ''

            content_elem = item.find('content:encoded', namespaces={'content': 'http://purl.org/rss/1.0/modules/content/'})
            if content_elem is not None and content_elem.text:
                description = content_elem.text

            pub_date = parse_rss_date(pub_date_str)
            if pub_date and pub_date > cutoff_time:
                # 转为纯文本
                plain = strip_html(description)
                updates.append({
                    'podcast_name': podcast['name'],
                    'rank': podcast.get('rank', 0),
                    'episode_title': title,
                    'episode_url': link,
                    'pub_date': pub_date.strftime('%Y-%m-%d %H:%M'),
                    'shownotes': plain[:SHOWNOTES_MAX] if len(plain) > SHOWNOTES_MAX else plain,
                })

    except urllib.error.HTTPError as e:
        error_info = f"HTTP {e.code}"
    except ET.ParseError as e:
        error_info = f"XML解析失败: {e}"
    except urllib.error.URLError as e:
        error_info = f"网络错误: {e.reason}"
    except Exception as e:
        error_info = f"{type(e).__name__}"

    return updates, error_info, new_cache_entry

def _check_xiaoyuzhou_fallback(podcast, content):
    """旧版正则解析：当 __NEXT_DATA__ 不可用时作为后备"""
    updates = []
    episode_pattern = r'-\s*!\[Image[^\]]*\]\([^)]+\)\s*\n+([^\n]+)\s*\n+(.*?)(?=-\s*!\[Image|$)'
    matches = re.findall(episode_pattern, content, re.DOTALL)

    if matches:
        title, desc = matches[0]
        title = title.strip()
        desc = desc.strip()

        episode_url_match = re.search(r'href="([^"]*episode[^"]*)"', content)
        episode_url = episode_url_match.group(1) if episode_url_match else podcast['url']

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
            'podcast_name': podcast['name'],
            'rank': podcast.get('rank', 0),
            'episode_title': title,
            'episode_url': episode_url,
            'pub_date': pub_date.strftime('%Y-%m-%d %H:%M') if pub_date else '未知（小宇宙最新一集）',
            'shownotes': plain[:SHOWNOTES_MAX] if len(plain) > SHOWNOTES_MAX else plain,
        })

    return updates

def check_xiaoyuzhou_update(podcast, cutoff_time, cache=None):
    """检查小宇宙类型播客的更新（通过 __NEXT_DATA__ JSON 解析，支持缓存）"""
    updates = []
    error_info = None
    new_cache_entry = None
    try:
        url = podcast['url']
        etag = None
        last_modified = None

        # 从缓存读取 ETag
        if cache and url in cache:
            etag = cache[url].get('etag')
            last_modified = cache[url].get('last_modified')

        result = fetch_url(url, etag=etag, last_modified=last_modified)
        if result is None:
            # 304 Not Modified — return None for `updates` so the main loop
            # (which checks `if updates is None`) counts this as not_modified.
            # (Matches the RSS path's convention in check_rss_update.)
            return None, None, None

        content = result['content']
        if result.get('etag') or result.get('last_modified'):
            new_cache_entry = {
                'etag': result.get('etag'),
                'last_modified': result.get('last_modified')
            }

        # 优先使用 __NEXT_DATA__ 结构化数据
        build_id, episodes = parse_xiaoyuzhou_next_data(content)

        if episodes is not None:
            for ep in episodes:
                title = ep.get('title', '').strip()
                if not title:
                    continue

                eid = ep.get('eid', '')
                pub_date_str = ep.get('pubDate', '')
                pub_date = parse_iso8601_date(pub_date_str)

                # 日期过滤：跳过早于截止时间的单集
                if pub_date and pub_date <= cutoff_time:
                    continue

                # 用 description 作为 shownotes，转为纯文本
                description = ep.get('description', '') or ''

                # 构建单集链接
                if eid:
                    episode_url = f'https://www.xiaoyuzhoufm.com/episode/{eid}'
                else:
                    episode_url = url

                plain = strip_html(description)
                updates.append({
                    'podcast_name': podcast['name'],
                    'rank': podcast.get('rank', 0),
                    'episode_title': title,
                    'episode_url': episode_url,
                    'pub_date': pub_date.strftime('%Y-%m-%d %H:%M') if pub_date else '未知（小宇宙）',
                    'shownotes': plain[:SHOWNOTES_MAX] if len(plain) > SHOWNOTES_MAX else plain,
                })
        else:
            # 后备：旧版正则解析
            updates = _check_xiaoyuzhou_fallback(podcast, content)

    except urllib.error.HTTPError as e:
        error_info = f"HTTP {e.code}"
    except Exception as e:
        error_info = f"{type(e).__name__}"

    return updates, error_info, new_cache_entry

def load_cache(cache_path):
    """加载上次请求的缓存（ETag / Last-Modified）"""
    if cache_path and cache_path.exists():
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def save_cache(cache_path, cache_data):
    """保存缓存"""
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False)

def main():
    import argparse
    parser = argparse.ArgumentParser(description='检查播客更新')
    parser.add_argument('--count', type=int, default=1000, help='检查的播客数量（默认1000，全部）')
    parser.add_argument('--hours', type=int, default=24, help='时间范围（小时）')
    parser.add_argument('--workers', type=int, default=20, help='并发数')
    parser.add_argument('--link-type', choices=['rss', 'xiaoyuzhou', 'all'], default='all', help='链接类型')
    parser.add_argument('--output', '-o', type=str, help='输出文件路径')
    parser.add_argument('--cache', type=str, help='缓存文件路径（用于增量更新）')
    args = parser.parse_args()

    # 设置 stdout 编码
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')

    # 读取播客列表
    script_dir = Path(__file__).parent
    json_path = script_dir.parent / 'references' / 'podcasts.json'

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    podcasts = data['podcasts']

    # 过滤链接类型
    if args.link_type != 'all':
        podcasts = [p for p in podcasts if p.get('link_type') == args.link_type]

    # 限制数量
    podcasts = podcasts[:args.count]

    # 计算截止时间
    cutoff_time = get_utc_now() - timedelta(hours=args.hours)

    # 加载缓存：缓存路径默认从 --output 所在目录派生（与其他技能一致）；
    # 若未指定 --output（仅打印到 stdout），则回退到项目根的统一 workspaces 目录。
    if args.cache:
        cache_path = Path(args.cache)
    elif args.output:
        cache_path = Path(args.output).parent / '.http_cache.json'
    else:
        cache_path = Path.cwd() / 'workspaces' / 'podcast' / '.http_cache.json'
    cache = load_cache(cache_path)

    print(f"检查 {len(podcasts)} 个播客的更新（时间范围: {args.hours}小时）...", file=sys.stderr)
    if cache:
        print(f"已加载缓存: {len(cache)} 条记录", file=sys.stderr)

    all_updates = []
    checked = 0
    errors = 0
    error_details = {}
    not_modified = 0
    # Seed the new cache only with URLs we still monitor, so entries for feeds
    # no longer in podcasts.json get pruned instead of accumulating forever.
    current_urls = {p['url'] for p in podcasts}
    new_cache = {u: cache[u] for u in current_urls if u in cache}

    def process_podcast(podcast):
        if podcast.get('link_type') == 'rss':
            return check_rss_update(podcast, cutoff_time, cache)
        elif podcast.get('link_type') == 'xiaoyuzhou':
            return check_xiaoyuzhou_update(podcast, cutoff_time, cache)
        return [], None, None

    def process_domain_group(podcast_group):
        """处理同一域名下的一组播客（串行），避免触发限流"""
        results = []
        for p in podcast_group:
            results.append((p, process_podcast(p)))
            # 同域名请求间加小延迟，降低限流风险
            time.sleep(random.uniform(0.1, 0.3))
        return results

    # 按域名分组，同域名串行，不同域名并行
    domain_groups = defaultdict(list)
    for p in podcasts:
        try:
            domain = urlparse(p['url']).netloc
        except Exception:
            domain = 'unknown'
        domain_groups[domain].append(p)

    print(f"域名分组: {len(domain_groups)} 个域名", file=sys.stderr)
    top_domains = sorted(domain_groups.items(), key=lambda x: -len(x[1]))[:5]
    for d, ps in top_domains:
        print(f"  {d}: {len(ps)} 个播客", file=sys.stderr)

    # 每个域名分配一个 worker，域名内串行处理
    num_workers = min(len(domain_groups), args.workers)
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
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
                        new_cache[podcast['url']] = cache_entry
            except Exception as e:
                # The whole domain group failed (single root cause). Count every
                # podcast in the group as checked + errored so the checked_count
                # denominator (used by the ">5% error" heuristic) stays accurate.
                group_size = len(domain_groups[domain])
                checked += group_size
                errors += group_size
                err_name = type(e).__name__
                error_details[err_name] = error_details.get(err_name, 0) + 1

            # 进度显示
            if checked % PROGRESS_EVERY == 0:
                print(f"  进度: {checked}/{len(podcasts)} (更新: {len(all_updates)}, 错误: {errors})", file=sys.stderr)

    # 保存缓存
    save_cache(cache_path, new_cache)

    # 按排名排序
    all_updates.sort(key=lambda x: x.get('rank', 9999))

    # 输出 JSON 结果
    result = {
        'metadata': {
            'checked_count': checked,
            'error_count': errors,
            'not_modified_count': not_modified,
            'error_details': error_details,
            'hours': args.hours,
            'update_count': len(all_updates),
            'check_time': get_utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')
        },
        'updates': all_updates
    }

    output_json = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output_json)
        print(f"结果已保存到: {args.output}", file=sys.stderr)
    else:
        print(output_json)

    print(f"完成! 检查 {checked} 个，未变化 {not_modified} 个，错误 {errors} 个，发现 {len(all_updates)} 个更新", file=sys.stderr)
    if error_details:
        print(f"错误分类: {dict(error_details)}", file=sys.stderr)

if __name__ == '__main__':
    main()
