"""rss-monitor: check_updates.py logic (dedup, cache pruning, URL cleanup)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_loader import rss_check  # noqa: E402


class TitleSimilarityTests(unittest.TestCase):
    def test_identical(self):
        sim = rss_check()._title_similarity("大模型价格战再起", "大模型价格战再起")
        self.assertGreater(sim, 0.99)

    def test_different(self):
        sim = rss_check()._title_similarity("大模型价格战再起", "Windows 11 更新补丁")
        self.assertLess(sim, 0.2)

    def test_empty(self):
        self.assertEqual(rss_check()._title_similarity("", "x"), 0.0)

    def test_token_cache_is_consistent(self):
        m = rss_check()
        m._TITLE_TOKEN_CACHE.clear()
        a = m._title_similarity("AI Agent 安全攻防指南", "AI Agent 安全攻防指南（续）")
        self.assertGreater(a, 0.5)
        # Cache keys are the raw title strings (tokenization lowercases).
        self.assertIn("AI Agent 安全攻防指南", m._TITLE_TOKEN_CACHE)


class WechatTitleDedupTests(unittest.TestCase):
    def test_cross_account_repost_dedup_keeps_longer_text(self):
        m = rss_check()
        updates = [
            {"account_name": "A", "title": "DeepSeek 发布新模型 R2",
             "full_text": "short", "url": "u1"},
            {"account_name": "B", "title": "DeepSeek 发布新模型 R2",
             "full_text": "much longer full text wins", "url": "u2"},
        ]
        out = m._dedup_wechat_by_title(updates)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["url"], "u2")

    def test_same_account_not_deduped_here(self):
        m = rss_check()
        updates = [
            {"account_name": "A", "title": "同题", "full_text": "x", "url": "u1"},
            {"account_name": "A", "title": "同题", "full_text": "y", "url": "u2"},
        ]
        self.assertEqual(len(m._dedup_wechat_by_title(updates)), 2)


class CachePruneTests(unittest.TestCase):
    """The subset-run regression: pruning must never delete another source's
    still-monitored entries, and must skip when a refs file is broken."""

    def _refs(self, td):
        (td / "feeds_wechat.json").write_text(json.dumps(
            {"feeds": [{"url": "http://wx.example/feed"}]}), encoding="utf-8")
        (td / "feeds_tech.json").write_text(json.dumps(
            {"categories": [{"feeds": [{"url": "http://tech.example/feed"}]}],
             "hacker_news": []}), encoding="utf-8")

    def test_all_refs_present_prunes_only_stale(self):
        m = rss_check()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            self._refs(td)
            (td / "podcasts.json").write_text(json.dumps(
                {"podcasts": [{"url": "http://pod.example/feed"}]}), encoding="utf-8")
            cache = {"http://wx.example/feed": {}, "http://tech.example/feed": {},
                     "http://pod.example/feed": {}, "http://gone.example/feed": {}}
            keep, unloaded = m._load_all_feed_urls(td, ["wechat", "tech", "podcast"])
            self.assertEqual(unloaded, [])
            self.assertEqual(m._prune_cache(cache, keep), 1)
            self.assertNotIn("http://gone.example/feed", cache)
            self.assertIn("http://pod.example/feed", cache)
            self.assertIn("http://tech.example/feed", cache)

    def test_missing_refs_reported_unloaded(self):
        m = rss_check()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            self._refs(td)  # no podcasts.json
            _, unloaded = m._load_all_feed_urls(td, ["wechat", "tech", "podcast"])
            self.assertEqual(unloaded, ["podcast"])


class NormalizeUrlTests(unittest.TestCase):
    def test_tracking_params_stripped(self):
        m = rss_check()
        self.assertEqual(
            m._normalize_url("http://a.com/x/?utm_source=rss&ref=tw&id=2#frag"),
            "http://a.com/x?id=2")

    def test_case_normalized_path_kept(self):
        self.assertEqual(rss_check()._normalize_url("HTTP://A.COM/x/"),
                         "http://a.com/x")


if __name__ == "__main__":
    unittest.main()
