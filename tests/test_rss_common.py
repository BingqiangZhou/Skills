"""rss-monitor: _common.py pure helpers (dates, Retry-After, durations, HTML)."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_loader import rss_common  # noqa: E402

CST = timezone(timedelta(hours=8))


class ParseRssDateTests(unittest.TestCase):
    def test_naive_pinned_to_cst(self):
        dt = rss_common().parse_rss_date("Fri, 14 Aug 2026 08:00:00")
        self.assertEqual(dt.utcoffset(), timedelta(hours=8))

    def test_explicit_offset_preserved(self):
        for raw, want in [("Fri, 14 Aug 2026 08:00:00 +0000", timedelta(0)),
                          ("Fri, 14 Aug 2026 08:00:00 +0800", timedelta(hours=8))]:
            dt = rss_common().parse_rss_date(raw)
            self.assertEqual(dt.utcoffset(), want, raw)

    def test_gmt_suffix_stripped_to_cst(self):
        dt = rss_common().parse_rss_date("Fri, 14 Aug 2026 08:00:00 GMT")
        self.assertEqual(dt.utcoffset(), timedelta(hours=8))

    def test_iso_naive_pinned_to_cst(self):
        dt = rss_common().parse_iso8601_date("2024-11-04T04:19:30")
        self.assertEqual(dt.utcoffset(), timedelta(hours=8))

    def test_iso_z_preserved(self):
        dt = rss_common().parse_iso8601_date("2024-11-04T04:19:30.561Z")
        self.assertEqual(dt.utcoffset(), timedelta(0))

    def test_garbage_returns_none(self):
        self.assertIsNone(rss_common().parse_rss_date("not a date"))
        self.assertIsNone(rss_common().parse_rss_date(""))


class FmtCstTests(unittest.TestCase):
    def test_utc_converted(self):
        dt = datetime(2026, 8, 14, 2, 30, tzinfo=timezone.utc)
        self.assertEqual(rss_common().fmt_cst(dt), "2026-08-14 10:30")

    def test_none_empty(self):
        self.assertEqual(rss_common().fmt_cst(None), "")

    def test_custom_format(self):
        dt = datetime(2026, 8, 14, 2, 30, tzinfo=timezone.utc)
        self.assertEqual(rss_common().fmt_cst(dt, "%H:%M"), "10:30")


class RetryAfterTests(unittest.TestCase):
    def setUp(self):
        self.f = rss_common()._retry_after_seconds

    def test_numeric(self):
        self.assertEqual(self.f("30", 2), 30)

    def test_capped_at_60(self):
        self.assertEqual(self.f("99999", 2), 60)

    def test_http_date_future_capped(self):
        now = datetime.now(timezone.utc)
        future = (now + timedelta(seconds=300)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT")
        self.assertEqual(self.f(future, 2), 60)

    def test_http_date_past_is_zero(self):
        now = datetime.now(timezone.utc)
        past = (now - timedelta(seconds=100)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT")
        self.assertEqual(self.f(past, 2), 0)

    def test_garbage_and_missing_use_default(self):
        self.assertEqual(self.f("junk", 7), 7)
        self.assertEqual(self.f(None, 5), 5)


class ParseDurationTests(unittest.TestCase):
    def setUp(self):
        self.f = rss_common().parse_duration

    def test_forms(self):
        self.assertEqual(self.f("3725"), 3725)
        self.assertEqual(self.f("62:05"), 3725)
        self.assertEqual(self.f("01:02:05"), 3725)

    def test_invalid(self):
        self.assertIsNone(self.f(""))
        self.assertIsNone(self.f("abc"))
        self.assertIsNone(self.f("1:2:3:4"))


class StripHtmlTests(unittest.TestCase):
    def test_tags_and_entities(self):
        self.assertEqual(
            rss_common().strip_html("<p>Hello&nbsp;&amp; welcome</p>"),
            "Hello & welcome")

    def test_script_skipped(self):
        self.assertEqual(
            rss_common().strip_html("a<script>var x=1;</script>b"), "ab")

    def test_empty(self):
        self.assertEqual(rss_common().strip_html(""), "")


class ParseRssItemsTests(unittest.TestCase):
    def test_atom_prefers_rel_alternate(self):
        c = rss_common()
        atom = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
                '<entry><title>E</title>'
                '<link rel="enclosure" href="https://cdn.example/a.mp3"/>'
                '<link rel="alternate" href="https://example.com/ep1"/>'
                '<published>2026-08-13T10:00:00Z</published></entry></feed>')
        items = c.parse_rss_items(atom)
        self.assertEqual(items[0]["link"], "https://example.com/ep1")

    def test_atom_without_rel_keeps_first_href(self):
        c = rss_common()
        atom = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
                '<entry><title>E</title>'
                '<link href="https://example.com/ep1"/></entry></feed>')
        items = c.parse_rss_items(atom)
        self.assertEqual(items[0]["link"], "https://example.com/ep1")

    def test_hybrid_feed_not_double_counted(self):
        c = rss_common()
        hybrid = ('<?xml version="1.0"?>'
                  '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
                  '<channel><item><title>RSS item</title></item>'
                  '<atom:entry><atom:title>Atom entry</atom:title></atom:entry>'
                  '</channel></rss>')
        items = c.parse_rss_items(hybrid)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "RSS item")


class CacheIoTests(unittest.TestCase):
    def test_atomic_save_and_load_roundtrip(self):
        import tempfile
        c = rss_common()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "cache.json"
            c.save_json_atomic(p, {"etag": "x"})
            self.assertEqual(c.load_cache(p), {"etag": "x"})
            self.assertFalse(list(p.parent.glob("*.tmp")))

    def test_load_cache_corrupt_returns_empty(self):
        import tempfile
        c = rss_common()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cache.json"
            p.write_bytes(b'{"broken": ')  # truncated JSON
            self.assertEqual(c.load_cache(p), {})


if __name__ == "__main__":
    unittest.main()
