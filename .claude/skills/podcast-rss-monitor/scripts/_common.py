"""Shared HTTP/parsing helpers for the podcast-rss-monitor scripts.

Kept dependency-free (Python standard library only) to match the rest of the
skill. Both check_updates.py and resolve_xiaoyuzhou_urls.py import from here so
the SSL context, fetching/retry logic, and __NEXT_DATA__ extraction live in one
place instead of being copy-pasted across scripts.
"""

import json
import re
import ssl
import time
import random
import urllib.request
import urllib.error

# Shared defaults
TIMEOUT = 15
MAX_RETRIES = 2

_CHROME_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)

# Fallback decode order for feed/page bytes.
_DECODE_ORDER = ['utf-8', 'gbk', 'gb2312', 'latin-1']


def create_ssl_context(relaxed=False):
    """创建 SSL context。relaxed=True 时跳过证书验证（仅用于重试）"""
    if relaxed:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def _decode_body(content):
    """Best-effort decode of response bytes using common Chinese encodings."""
    for encoding in _DECODE_ORDER:
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode('utf-8', errors='replace')


def fetch_url(url, etag=None, last_modified=None, accept=None, max_retries=MAX_RETRIES):
    """获取 URL 内容，支持 ETag / If-Modified-Since 增量请求，带重试。

    Args:
        url: target URL.
        etag / last_modified: optional conditional-request headers.
        accept: optional Accept header value. Defaults to a permissive RSS/XML
            value (suitable for feed + HTML pages).
        max_retries: number of retries on transient errors.

    Returns:
        - None when the server responds 304 Not Modified (or raises HTTPError 304).
        - Otherwise a dict: {'content': str, 'etag': str|None, 'last_modified': str|None}.

    Raises:
        urllib.error.HTTPError for non-304/429/5xx terminal statuses.
        urllib.error.URLError / OSError for terminal network failures.
    """
    headers = {
        'User-Agent': _CHROME_UA,
        'Accept': accept or 'application/rss+xml, application/xml, text/xml, */*',
    }
    if etag:
        headers['If-None-Match'] = etag
    if last_modified:
        headers['If-Modified-Since'] = last_modified

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            # SSL 错误重试时使用宽松 context
            use_relaxed = attempt > 0 and last_error and 'SSL' in str(last_error)
            ctx = create_ssl_context(relaxed=use_relaxed)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as response:
                if response.status == 304:
                    return None  # 未修改
                content = response.read()
                return {
                    'content': _decode_body(content),
                    'etag': response.headers.get('ETag'),
                    'last_modified': response.headers.get('Last-Modified'),
                }
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 304:
                return None  # Not Modified，不是错误
            if e.code == 429 and attempt < max_retries:
                wait = int(e.headers.get('Retry-After', 2 * (attempt + 1)))
                time.sleep(wait + random.uniform(0, 1))
                continue
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


def parse_xiaoyuzhou_next_data(html_content):
    """Extract and parse the __NEXT_DATA__ JSON from a Xiaoyuzhou HTML page.

    Returns the parsed page-data dict (props.pageProps.podcast.episodes lives at
    page_data['props']['pageProps']['podcast']['episodes']), or None on failure
    (no script tag found, invalid JSON, or missing structure).
    """
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html_content,
        re.DOTALL,
    )
    if not match:
        return None

    try:
        page_data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None

    try:
        # Validate the expected structure exists.
        page_data['props']['pageProps']['podcast']['episodes']
        return page_data
    except (KeyError, TypeError):
        return None


def extract_xiaoyuzhou_episodes(html_content):
    """Return the episodes array from a Xiaoyuzhou page, or [] on failure.

    Each episode is the raw dict from props.pageProps.podcast.episodes.
    """
    page_data = parse_xiaoyuzhou_next_data(html_content)
    if page_data is None:
        return []
    return page_data['props']['pageProps']['podcast']['episodes']
