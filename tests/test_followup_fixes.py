"""Follow-up fixes: TUM enrichment caching, resolver counting/cleanup,
markdown escaping, undated-item stats."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_loader import (  # noqa: E402
    rss_check, rss_resolver, tum_check, load_script,
)

compact = lambda: load_script("dd_compact_t",
                              "plugins/daily-digest/skills/daily-digest/scripts/compact_lists.py")


class TumEnrichmentCacheTests(unittest.TestCase):
    """Secondary (enrichment) URLs must record validators AND the extracted
    text so a 304 replay doesn't degrade to the un-enriched body."""

    PAGE = "<html><body><h1>Updates</h1><p>Real notes here</p></body></html>"

    def setUp(self):
        self.m = tum_check()
        self._orig = self.m.fetch_url_with_retry
        self.calls = []

    def tearDown(self):
        self.m.fetch_url_with_retry = self._orig

    def _install(self, responses):
        def fake(url, cache=None, **kw):
            etag, body = responses[url]
            entry = (cache or {}).get(url) or {}
            self.calls.append(url)
            if etag and entry.get("etag") == etag:
                return None, 304, entry  # conditional hit
            return body, 200, {"etag": etag}
        self.m.fetch_url_with_retry = fake

    def test_url_only_body_cached_across_runs(self):
        m = self.m
        url = "https://code.visualstudio.com/updates/v1_99"
        body = url  # URL-only release body (the VS Code pattern)
        self._install({url: ("W/\"etag1\"", self.PAGE)})
        cache = {}
        out1 = m._enrich_url_only_body(body, cache)
        self.assertIn("Real notes here", out1)
        self.assertEqual(cache[url]["text"], out1)
        # Run 2: server 304s — cached text must be reused, not the bare URL.
        out2 = m._enrich_url_only_body(body, cache)
        self.assertEqual(out1, out2)
        self.assertEqual(self.calls, [url, url])

    def test_github_notes_cached_across_runs(self):
        m = self.m
        url = "https://api.github.com/repos/a/b/releases/tags/v1.2.3"
        self._install({url: ("E1", json.dumps({"body": "release notes"}))})
        cache = {}
        notes1 = m._fetch_github_release_body("a/b", "1.2.3", cache)
        self.assertEqual(notes1, "release notes")
        self.assertEqual(cache[url]["notes"], "release notes")
        notes2 = m._fetch_github_release_body("a/b", "1.2.3", cache)
        self.assertEqual(notes2, "release notes")
        self.assertEqual(self.calls, [url, url])


class ResolverTests(unittest.TestCase):
    def test_clean_utm_suffixes(self):
        m = rss_resolver()
        updates = [
            {"source": "podcast", "url": "https://www.xiaoyuzhoufm.com/episode/e1?utm_source=rss"},
            {"source": "podcast", "url": "https://www.xiaoyuzhoufm.com/episode/e2"},
            {"source": "wechat", "url": "https://x/?utm_source=rss"},
        ]
        cleaned = m._clean_utm_suffixes(updates)
        self.assertEqual(cleaned, 1)
        self.assertEqual(updates[0]["url"], "https://www.xiaoyuzhoufm.com/episode/e1")
        self.assertEqual(updates[2]["url"], "https://x/?utm_source=rss")

    def test_no_double_count_and_early_return_cleans(self):
        """resolve_updates: failures counted once; the nothing-to-resolve
        early return still cleans UTM suffixes (and saves)."""
        m = rss_resolver()
        data = {
            "metadata": {},
            "updates": [
                {"source": "podcast", "podcast_name": "P1",
                 "title": "t1", "url": "https://other.example/ep1"},
                {"source": "podcast", "podcast_name": "P1",
                 "title": "t2", "url": "https://other.example/ep2"},
                {"source": "podcast", "podcast_name": "P2",
                 "title": "t3", "url": "https://www.xiaoyuzhoufm.com/episode/e3?utm_source=rss"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.json"
            src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            m.load_podcasts_map = lambda: {}
            m.resolve_one_podcast = (
                lambda name, indices, titles, xyz:
                (name, [(i, None) for i in indices], 0))
            m.resolve_updates(str(src))
            out = json.loads(src.read_text(encoding="utf-8"))
            # Early-return path must still have cleaned the utm suffix.
            self.assertEqual(out["updates"][2]["url"],
                             "https://www.xiaoyuzhoufm.com/episode/e3")


class UndatedItemsTests(unittest.TestCase):
    FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>dated</title><link>http://a/1</link>
      <pubDate>Fri, 14 Aug 2026 08:00:00</pubDate></item>
    <item><title>undated</title><link>http://a/2</link><pubDate>garbage</pubDate></item>
    </channel></rss>"""

    def test_tech_feed_counts_date_unparsed(self):
        m = rss_check()
        orig = m.fetch_url
        m.fetch_url = lambda url, **kw: {"content": self.FEED,
                                         "etag": "E", "last_modified": None}
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            feed_info = {"name": "f", "url": "http://a/feed", "category": "c",
                         "language": "en"}
            _, articles, err, _cache, date_unparsed = m._check_tech_feed(
                feed_info, cutoff, {})
        finally:
            m.fetch_url = orig
        self.assertIsNone(err)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "dated")
        self.assertEqual(date_unparsed, 1)


class MarkdownLinkTests(unittest.TestCase):
    def test_brackets_and_parens_escaped(self):
        md = compact().md_link("feat: add [core] module (v2)", "http://x/a(1)")
        self.assertNotIn("[core]", md.replace("\\[core\\]", ""))
        self.assertIn("\\[core\\]", md)
        self.assertIn("%28", md)
        self.assertNotIn("a(1)", md)

    def test_no_url_returns_text(self):
        self.assertEqual(compact().md_link("plain", ""), "plain")


if __name__ == "__main__":
    unittest.main()
