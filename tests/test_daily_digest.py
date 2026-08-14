"""daily-digest: report merge/version math, batch prep, narrative normalizers."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_loader import gur, mn, pb, narrative_mod  # noqa: E402


class PodcastEpisodeLinkTests(unittest.TestCase):
    def test_title_linked_when_url_present(self):
        n = narrative_mod()
        lines = []
        n.render_podcast_episodes(
            lines, "🎧 播客精选",
            [{"show": "秀", "title": "第 1 期", "summary": "内容",
              "url": "https://www.xiaoyuzhoufm.com/episode/e1"}],
            [], [], missing_placeholder=None)
        text = "\n".join(lines)
        self.assertIn("[第 1 期](https://www.xiaoyuzhoufm.com/episode/e1)：内容", text)

    def test_title_with_brackets_still_links(self):
        n = narrative_mod()
        lines = []
        n.render_podcast_episodes(
            lines, "🎧 播客精选",
            [{"show": "秀", "title": "聊聊 [AI] 与Agent", "summary": "内容",
              "url": "https://x/e(1)"}],
            [], [], missing_placeholder=None)
        text = "\n".join(lines)
        self.assertIn("\\[AI\\]", text)
        self.assertIn("%28", text)

    def test_no_url_renders_plain_title(self):
        n = narrative_mod()
        lines = []
        n.render_podcast_episodes(
            lines, "🎧 播客精选",
            [{"show": "秀", "title": "第 2 期", "summary": "内容", "url": ""}],
            [], [], missing_placeholder=None)
        text = "\n".join(lines)
        self.assertIn("- **秀** · 第 2 期：内容", text)


class MergeToolUpdatesTests(unittest.TestCase):
    def test_numeric_version_not_lexicographic(self):
        updates = [
            {"tool_id": "x", "name": "X", "version": "0.9.0",
             "published_at": "OLD", "html_url": "http://old", "body": "old"},
            {"tool_id": "x", "name": "X", "version": "0.10.0",
             "published_at": "NEW", "html_url": "http://new", "body": "new"},
        ]
        merged = gur()._merge_tool_updates(updates)
        self.assertEqual(merged[0]["version"], "0.10.0")
        self.assertEqual(merged[0]["html_url"], "http://new")
        self.assertEqual(merged[0]["versions"], ["0.9.0", "0.10.0"])

    def test_single_entry_passthrough(self):
        m = gur()._merge_tool_updates([{"tool_id": "y", "version": "1.0"}])
        self.assertEqual(m[0]["version"], "1.0")
        self.assertNotIn("versions", m[0])

    def test_version_key(self):
        m = gur()
        self.assertGreater(m._version_key("0.10.0"), m._version_key("0.9.0"))
        self.assertEqual(m._version_key(None), (0,))


class BuildGithubSummaryMapTests(unittest.TestCase):
    def test_null_shapes_do_not_crash(self):
        m = gur()
        self.assertEqual(m.build_github_summary_map(None), {})
        self.assertEqual(m.build_github_summary_map({"summaries": None}), {})
        self.assertEqual(
            m.build_github_summary_map(
                {"summaries": ["not-a-dict", {"item_url": "u", "ai_summary": "s"}]}),
            {"u": "s"})


class FallbackOverviewTests(unittest.TestCase):
    def test_expected_podcast_count_null_updates(self):
        m = gur()
        self.assertEqual(m._expected_podcast_count({"updates": None}), 0)
        self.assertEqual(
            m._expected_podcast_count(
                {"updates": [{"source": "podcast"}, {"source": "wechat"}]}), 1)


class PrepareBatchesTests(unittest.TestCase):
    def test_rss_grouping_and_truncation(self):
        m = pb()
        updates = [
            {"source": "wechat", "url": "a", "title": "t", "account_name": "A",
             "full_text": "x" * 3000},
            {"source": "podcast", "url": "b", "title": "t2", "podcast_name": "P",
             "shownotes": "s"},
        ]
        batches = m.prepare_rss_batches(updates, batch_size=10)
        flat = [e for b in batches for e in b]
        by_url = {e["url"]: e for e in flat}
        self.assertEqual(by_url["a"]["full_text"], "x" * 1500)
        self.assertEqual(by_url["b"]["podcast_name"], "P")

    def test_github_flat_batches(self):
        updates = [{"type": "pulls", "item_number": 1, "html_url": "u",
                    "title": "t", "author": "a", "repo": "r/b",
                    "body_text": "hello"}]
        batches = pb().prepare_github_batches(updates, batch_size=10)
        self.assertEqual(batches[0][0]["item_type"], "pulls")

    def test_clean_stale_batches(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for i in range(3):
                (d / f"github_batch_{i}.json").write_text("[]", encoding="utf-8")
                (d / f"github_ai_summaries_batch_{i}.json").write_text("{}", encoding="utf-8")
            (d / "rss_batch_0.json").write_text("[]", encoding="utf-8")
            removed = pb().clean_stale_batches(d, "github")
            self.assertEqual(removed, 6)
            self.assertTrue((d / "rss_batch_0.json").exists())
            self.assertFalse(any(d.glob("github_batch_*.json")))

    def test_clean_stale_batches_missing_dir(self):
        self.assertEqual(pb().clean_stale_batches(Path("Z:/nope/never"), "x"), 0)


class MergeNarrativesNormalizers(unittest.TestCase):
    def test_topics_keep_lead_bullets(self):
        out = mn()._normalize_topics([
            {"title": "T", "lead": "L", "bullets": ["b1", "b2"], "items": []}])
        self.assertEqual(out[0]["lead"], "L")
        self.assertEqual(out[0]["bullets"], ["b1", "b2"])
        self.assertNotIn("items", out[0])

    def test_topics_narrative_only_flagged_unscannable(self):
        out = mn()._normalize_topics([{"title": "T", "narrative": "dense text"}])
        self.assertEqual(out[0]["bullets"], [])
        self.assertEqual(out[0]["narrative"], "dense text")

    def test_podcast_episodes_shape(self):
        out = mn()._normalize_podcast_episodes([
            {"show": "S", "title": "E1", "summary": "一句话", "url": "u"},
            "not-a-dict"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["show"], "S")

    def test_str_list_coercion(self):
        self.assertEqual(mn()._normalize_str_list(["a", 1, None]), ["a"])
        # A bare string is wrapped into a one-element list (accepted shape).
        self.assertEqual(mn()._normalize_str_list("string"), ["string"])


if __name__ == "__main__":
    unittest.main()
