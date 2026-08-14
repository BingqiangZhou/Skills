"""tool-update-monitor: version math, baseline deadlock guard, Windows KB."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_loader import tum_check  # noqa: E402


class VersionTupleTests(unittest.TestCase):
    def test_numeric_comparison(self):
        m = tum_check()
        self.assertGreater(m.version_tuple("0.10.0"), m.version_tuple("0.9.0"))
        self.assertLess(m.version_tuple("1.2"), m.version_tuple("1.2.1"))
        self.assertEqual(m.version_tuple(""), (0,))

    def test_nonnumeric_stops(self):
        self.assertEqual(tum_check().version_tuple("0.2026.stable_04"),
                         (0, 2026))

    def test_normalize_strips_v(self):
        m = tum_check()
        self.assertEqual(m.normalize_version("v2.5.1"), "2.5.1")
        self.assertEqual(m.normalize_version("2026.7.1-2"), "2026.7.1")


class PickLatestTests(unittest.TestCase):
    def test_numeric_max_wins(self):
        m = tum_check()
        tool = {"compare_by": "semver"}
        rels = [{"version": "1.0.9", "published_at": "2026-07-02T00:00:00Z"},
                {"version": "1.1.0", "published_at": "2026-07-01T00:00:00Z"}]
        self.assertEqual(m.pick_latest_release(tool, rels)["version"], "1.1.0")

    def test_truncated_tuple_tie_broken_by_published_at(self):
        # Suffix versions ("2026.7.1-2", "2026.7.2-beta.1") truncate to the
        # same (2026, 7) tuple; the later published_at wins the tie — this is
        # what keeps beta streams from looking "new" every run.
        m = tum_check()
        tool = {"compare_by": "semver"}
        rels = [{"version": "2026.7.1-2", "published_at": "2026-07-20T00:00:00Z"},
                {"version": "2026.7.2-beta.1", "published_at": "2026-07-19T00:00:00Z"}]
        self.assertEqual(m.pick_latest_release(tool, rels)["version"], "2026.7.1-2")

    def test_date_mode(self):
        m = tum_check()
        tool = {"compare_by": "date"}
        rels = [{"version": "a", "published_at": "2026-07-01T00:00:00Z"},
                {"version": "b", "published_at": "2026-08-01T00:00:00Z"}]
        self.assertEqual(m.pick_latest_release(tool, rels)["version"], "b")


class ComputeNewReleasesTests(unittest.TestCase):
    def test_semver_newer_only(self):
        m = tum_check()
        tool = {"id": "t", "compare_by": "semver"}
        rels = [{"version": "1.0.0", "published_at": "2026-07-01T00:00:00Z"},
                {"version": "1.1.0", "published_at": "2026-07-02T00:00:00Z"},
                {"version": "0.9.0", "published_at": "2026-07-03T00:00:00Z"}]
        prev = {"version": "1.0.0"}
        new = m._compute_new_releases(tool, rels, prev)
        self.assertEqual([r["version"] for r in new], ["1.1.0"])


class BaselineDeadlockGuardTests(unittest.TestCase):
    """304 + empty state must force one ETag-free refetch (the deadlock)."""

    URL = "https://api.github.com/repos/a/b/releases?per_page=10"

    def setUp(self):
        self.m = tum_check()
        self.tool = {"id": "fake", "name": "Fake", "category": "t",
                     "source_type": "github", "repo": "a/b"}
        fresh = [{"version": "1.2.3", "published_at": "2026-08-01T00:00:00Z",
                  "body": "n", "html_url": "http://x"}]
        calls = []

        def fetcher(t, cache):
            entry = cache.get(self.URL, {}) if isinstance(cache, dict) else {}
            calls.append(bool(entry.get("etag")))
            if entry.get("etag"):
                return None, "not_modified"
            return {"releases": [dict(r) for r in fresh]}, {"etag": "new"}

        self.calls = calls
        self.m._FETCHERS["github"] = fetcher
        self.m._url_for_tool = lambda t: self.URL

    def test_304_with_empty_state_refetches(self):
        cache = {self.URL: {"etag": "old"}}
        r = self.m.check_one_tool(self.tool, cache, {})
        self.assertEqual(self.calls, [True, False])  # conditional, then forced
        self.assertTrue(r["ok"])
        self.assertEqual(r["latest"]["version"], "1.2.3")

    def test_304_with_snapshot_uses_snapshot(self):
        cache = {self.URL: {"etag": "old"}}
        state = {"fake": {"version": "1.0.0",
                          "releases": [{"version": "1.2.3",
                                        "published_at": "2026-08-01T00:00:00Z"}]}}
        r = self.m.check_one_tool(self.tool, cache, state)
        self.assertEqual(self.calls, [True])  # single 304, no refetch
        self.assertEqual([x["version"] for x in r["new_releases"]], ["1.2.3"])


class WindowsKbTests(unittest.TestCase):
    HTML = """
    <table>
    <tr><th>Version</th><th>Servicing option</th><th>Availability date</th>
    <th>Latest update</th><th>Latest revision date</th><th>Latest build</th></tr>
    <tr><td>25H2</td><td>GA</td><td>2025-10-01</td>
    <td><a href="https://support.microsoft.com/help/5062777">2026-07 D</a></td>
    <td>2026-07-09</td><td>26200.8973</td></tr>
    </table>
    """

    def test_kb_from_href(self):
        m = tum_check()
        tool = {"id": "windows", "source_type": "windows",
                "url": "https://example.invalid/x", "win_version": "25H2"}
        orig = m.fetch_url_with_retry
        m.fetch_url_with_retry = lambda url, cache=None, **kw: (self.HTML, 200, {})
        try:
            result, ret = m.fetch_windows(tool, {})
        finally:
            m.fetch_url_with_retry = orig
        rel = result["releases"][0]
        self.assertEqual(rel["version"], "26200.8973")
        self.assertEqual(rel["kb"], "KB5062777")
        self.assertIn("KB5062777", rel["name"])


class ParseIsoTests(unittest.TestCase):
    def test_z_and_offset_and_naive(self):
        m = tum_check()
        self.assertIsNotNone(m.parse_iso_datetime("2026-07-30T12:45:22Z"))
        self.assertIsNotNone(m.parse_iso_datetime("2026-07-30T12:45:22+00:00"))
        self.assertEqual(m.parse_iso_datetime("junk"), None)
        self.assertEqual(m.parse_iso_datetime(""), None)


if __name__ == "__main__":
    unittest.main()
