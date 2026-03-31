#!/usr/bin/env python3
"""Fetch full article content from WeChat article URLs.

Reads latest_updates.json, attempts to fetch the full article text from
each article's mp.weixin.qq.com URL, and enriches the updates with
full_text content. Falls back to the RSS content:encoded if direct
fetching fails (e.g., WeChat's anti-scraping measures).

For best results, the RSS feed's content:encoded is the primary source.
This script serves as an enrichment layer for articles that need more content.
"""

import argparse
import json
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


class _TextExtractor(HTMLParser):
    """Extract visible text from HTML, skipping scripts and styles."""

    SKIP_TAGS = {"script", "style", "noscript", "header", "footer", "nav"}

    def __init__(self):
        super().__init__()
        self._pieces = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._pieces.append(data)

    def get_text(self):
        text = "".join(self._pieces)
        return re.sub(r"\s+", " ", text).strip()


def create_ssl_context():
    try:
        return ssl.create_default_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def fetch_html(url, timeout=30):
    """Fetch HTML content from a URL. Returns text or None on failure."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        ctx = create_ssl_context()
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def extract_wechat_content(html):
    """Extract article text from a WeChat article HTML page.

    WeChat articles store content in <div id="js_content">.
    If that's not found (anti-scraping page), return None.
    """
    if not html:
        return None

    # Look for js_content div
    marker = 'id="js_content"'
    idx = html.find(marker)
    if idx < 0:
        # Try alternative: rich_media_content
        marker = 'class="rich_media_content"'
        idx = html.find(marker)
        if idx < 0:
            return None

    # Find the content div - extract from the opening tag to closing </div>
    # This is approximate but works for WeChat articles
    start = html.find(">", idx) + 1
    if start <= 0:
        return None

    # Find a reasonable end point (look for the next major section)
    end_markers = ['id="js_pc_close_btn"', 'class="rich_media_tool"', 'id="js_tags"']
    end = len(html)
    for em in end_markers:
        eidx = html.find(em, start)
        if eidx > start:
            end = min(end, eidx)

    content_html = html[start:end]
    extractor = _TextExtractor()
    extractor.feed(content_html)
    text = extractor.get_text()

    return text if len(text) > 100 else None


def main():
    parser = argparse.ArgumentParser(
        description="Fetch full article content for WeChat articles"
    )
    parser.add_argument(
        "-i", "--input", required=True,
        help="Input latest_updates.json path"
    )
    parser.add_argument(
        "-o", "--output", required=True,
        help="Output enriched JSON path"
    )
    parser.add_argument(
        "--min-length", type=int, default=500,
        help="Skip fetching if existing full_text already has this many chars (default: 500)"
    )
    parser.add_argument(
        "--max-articles", type=int, default=0,
        help="Max articles to fetch (0 = all)"
    )
    parser.add_argument(
        "--delay", type=float, default=2.0,
        help="Delay between requests in seconds (default: 2.0)"
    )
    args = parser.parse_args()

    # Load updates
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    updates = data.get("updates", [])
    if not updates:
        print("No updates to enrich.")
        return

    print(f"Processing {len(updates)} articles...")

    fetched_count = 0
    skipped_count = 0
    failed_count = 0

    for i, update in enumerate(updates):
        existing_text = update.get("full_text", "")
        article_url = update.get("article_url", "")
        title = update.get("article_title", "(no title)")

        # Skip if we already have sufficient content from RSS
        if len(existing_text) >= args.min_length:
            skipped_count += 1
            continue

        if not article_url or "mp.weixin.qq.com" not in article_url:
            skipped_count += 1
            continue

        if args.max_articles > 0 and fetched_count >= args.max_articles:
            print(f"Reached max articles limit ({args.max_articles})")
            break

        print(f"  [{i+1}/{len(updates)}] Fetching: {title[:50]}...")

        html = fetch_html(article_url)
        if html:
            full_text = extract_wechat_content(html)
            if full_text and len(full_text) > len(existing_text):
                update["full_text"] = full_text
                update["content_source"] = "mp.weixin.qq.com"
                fetched_count += 1
                print(f"    -> Got {len(full_text)} chars")
            else:
                failed_count += 1
                print(f"    -> Could not extract content (anti-scraping page)")
        else:
            failed_count += 1
            print(f"    -> Fetch failed")

        # Rate limit
        time.sleep(args.delay + random.uniform(0, 1))

    # Save enriched data
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nDone: {fetched_count} enriched, {skipped_count} skipped (already sufficient), {failed_count} failed")


if __name__ == "__main__":
    main()
