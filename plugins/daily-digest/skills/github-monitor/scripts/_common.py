"""Shared HTTP helpers for the github-monitor scripts.

Kept dependency-free (Python standard library only) to match the rest of the
skill. Provides SSL context creation, a retrying fetch_url with ETag /
If-Modified-Since conditional requests, and a github_get helper that targets
the GitHub REST API (Accept header, optional Bearer token, Link-header
pagination, X-RateLimit awareness).
"""

import json
import os
import re
import ssl
import sys
import time
import random
import email.utils
import urllib.request
import urllib.error
from datetime import datetime, timezone

# Shared defaults
TIMEOUT = 15
MAX_RETRIES = 2

_CHROME_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)

# Fallback decode order for response bytes.
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


def _retry_after_seconds(value, default):
    """Parse a Retry-After header (delay-seconds or HTTP-date) into seconds.

    The int-only parse raised ValueError on the RFC 7231 HTTP-date form.
    Caps the wait at 60s so a hostile/buggy server cannot stall the run.
    Returns `default` when the value is missing or unparseable.
    """
    if value is None:
        return default
    value = value.strip()
    try:
        return min(max(int(value), 0), 60)
    except ValueError:
        pass
    try:
        target = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return default
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    delta = (target - datetime.now(timezone.utc)).total_seconds()
    if delta <= 0:
        return 0
    return min(delta, 60)


def fetch_url(url, etag=None, last_modified=None, accept=None, max_retries=MAX_RETRIES):
    """获取 URL 内容，支持 ETag / If-Modified-Since 增量请求，带重试。

    Args:
        url: target URL.
        etag / last_modified: optional conditional-request headers.
        accept: optional Accept header value. Defaults to a permissive value
            suitable for feed + HTML pages.
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
        'Accept': accept or 'application/vnd.github+json, application/json, */*',
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
                wait = _retry_after_seconds(e.headers.get('Retry-After'),
                                            2 * (attempt + 1))
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


def github_token():
    """Return a GitHub token from the environment, or None.

    Reads GITHUB_ACCESS_TOKEN. Without a token the GitHub REST API allows
    ~60 requests/hour per IP; with one it is 5000/hour.
    """
    return os.environ.get('GITHUB_ACCESS_TOKEN')


def parse_link_header(link_header):
    """Parse an HTTP `Link` header into a dict like {'next': url, 'last': url}.

    Returns {} if the header is missing or malformed.
    """
    if not link_header:
        return {}
    result = {}
    # Each part looks like: <https://api.github.com/...?page=2>; rel="next"
    for part in link_header.split(','):
        m = re.search(r'<([^>]+)>;\s*rel="([^"]+)"', part)
        if m:
            result[m.group(2)] = m.group(1)
    return result


def github_get(url, etag=None, max_retries=MAX_RETRIES):
    """GET a GitHub REST API URL with auth, conditional requests, and rate-limit handling.

    Sets the GitHub JSON Accept header and, if a token is present in the
    environment (GITHUB_ACCESS_TOKEN), an Authorization: Bearer header.
    Retries on 429 (honoring Retry-After) and 5xx, same backoff policy as
    fetch_url. GitHub signals primary rate-limit exhaustion with 403 +
    X-RateLimit-Remaining: 0 (not 429): when the reset epoch is <= 5 minutes
    away we sleep until it and retry; otherwise we raise a 403 whose message
    says when the limit resets and recommends GITHUB_ACCESS_TOKEN.

    Returns:
        - None on 304 Not Modified.
        - Otherwise a dict:
            {'data': parsed-json|None, 'content': raw-str, 'etag': str|None,
             'link': {rel: url}, 'rate_remaining': int|None, 'rate_reset': int|None}

    Raises:
        urllib.error.HTTPError for terminal non-304 statuses (after retries).
        urllib.error.URLError / OSError for terminal network failures.
    """
    headers = {
        'User-Agent': _CHROME_UA,
        'Accept': 'application/vnd.github+json',
    }
    token = github_token()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if etag:
        headers['If-None-Match'] = etag

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            use_relaxed = attempt > 0 and last_error and 'SSL' in str(last_error)
            ctx = create_ssl_context(relaxed=use_relaxed)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as response:
                if response.status == 304:
                    return None
                raw = response.read()
                content = _decode_body(raw)
                link_header = response.headers.get('Link')
                rate_remaining = response.headers.get('X-RateLimit-Remaining')
                rate_reset = response.headers.get('X-RateLimit-Reset')

                # Parse JSON body when possible; tolerate non-JSON (e.g. error pages).
                data = None
                try:
                    data = json.loads(content)
                except (json.JSONDecodeError, ValueError):
                    data = None

                return {
                    'data': data,
                    'content': content,
                    'etag': response.headers.get('ETag'),
                    'link': parse_link_header(link_header),
                    'rate_remaining': int(rate_remaining) if rate_remaining else None,
                    'rate_reset': int(rate_reset) if rate_reset else None,
                }
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 304:
                return None
            if e.code == 429 and attempt < max_retries:
                wait = _retry_after_seconds(e.headers.get('Retry-After'),
                                            2 * (attempt + 1))
                time.sleep(wait + random.uniform(0, 1))
                continue
            if e.code == 403 and e.headers.get('X-RateLimit-Remaining') == '0':
                # Primary rate limit exhausted. Sleep until the reset epoch
                # when it is close; otherwise surface a clear error instead
                # of stalling the run for most of an hour.
                try:
                    delay = int(e.headers.get('X-RateLimit-Reset', 0)) - int(time.time())
                except (TypeError, ValueError):
                    delay = -1
                if 0 <= delay <= 300 and attempt < max_retries:
                    print(f'  GitHub rate limit hit (403); sleeping {delay}s '
                          f'until reset', file=sys.stderr)
                    time.sleep(delay + 2)
                    continue
                raise urllib.error.HTTPError(
                    url, 403,
                    f'GitHub rate limit exhausted (resets in {max(delay, 0)}s); '
                    f'set GITHUB_ACCESS_TOKEN to raise it to 5000/hour',
                    e.headers, None) from e
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
