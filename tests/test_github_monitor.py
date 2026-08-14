"""github-monitor: cache keys, pagination (via fake github_get), filters."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from script_loader import gm_check, gm_common  # noqa: E402


class StableUrlTests(unittest.TestCase):
    def test_since_stripped_page_pinned(self):
        m = gm_check()
        u1 = ("https://api.github.com/repos/a/b/issues?state=open&sort=created"
              "&direction=desc&since=2026-08-13T10:00:00Z&per_page=100")
        u2 = ("https://api.github.com/repos/a/b/issues?state=open&sort=created"
              "&direction=desc&since=2026-08-14T09:12:34Z&per_page=100")
        self.assertEqual(m._stable_url(u1, 1), m._stable_url(u2, 1))
        self.assertNotRegex(m._stable_url(u1, 1), r"(?:^|&)since=")

    def test_page_number_in_key(self):
        m = gm_check()
        u = ("https://api.github.com/repos/a/b/pulls?state=closed&per_page=100"
             "&since=2026-08-13T10:00:00Z")
        k1 = m._stable_url(u, 1)
        k2 = m._stable_url(u, 2)
        self.assertNotEqual(k1, k2)
        self.assertRegex(k2, r"[?&]page=2")
        self.assertNotRegex(k1, r"[?&]page=")

    def test_param_order_normalized(self):
        m = gm_check()
        a = ("https://api.github.com/repos/a/b/pulls?state=closed&page=2&per_page=100")
        b = ("https://api.github.com/repos/a/b/pulls?per_page=100&state=closed&page=2")
        self.assertEqual(m._stable_url(a, 2), m._stable_url(b, 2))


class FakePaginationTests(unittest.TestCase):
    """_paginate over a fake github_get: 200 pages via Link, then a full 304
    walk of the cached pages."""

    def setUp(self):
        self.m = gm_check()
        self._orig = self.m.github_get
        self._orig_token = self.m.github_token

    def tearDown(self):
        self.m.github_get = self._orig
        self.m.github_token = self._orig_token

    def _canon(self, url):
        """Canonical lookup key: the implementation's own stable form for the
        URL's page — resilient to param order and to the synthesized next-page
        URL differing from the Link-form URL."""
        from urllib.parse import parse_qsl
        page = int(dict(parse_qsl(url.split("?", 1)[1] if "?" in url else "")).get("page", 1))
        return self.m._stable_url(url, page)

    def _install(self, pages, cache):
        """pages: {canonical_url: (data_list, etag, next_url_or_None)}"""
        self.m.github_token = lambda: "fake-token"

        def fake_get(url, etag=None, **kw):
            data, etag_val, next_url = pages[self._canon(url)]
            if etag and etag_val and etag == etag_val:
                return None  # 304
            return {"data": data, "etag": etag_val, "link":
                    {"next": next_url} if next_url else {},
                    "rate_remaining": 5000, "rate_reset": 0}
        self.m.github_get = fake_get

    def test_200_pagination_follows_link(self):
        m = self.m
        p1 = ("https://api.github.com/repos/a/b/pulls?state=closed&per_page=100")
        p2 = ("https://api.github.com/repos/a/b/pulls?state=closed&page=2&per_page=100")
        cache = {}
        self._install({self._canon(p1): ([{"n": 1}, {"n": 2}], "E1", p2),
                       self._canon(p2): ([{"n": 3}], "E2", None)}, cache)
        items, checked, errors = m._paginate(p1, "PRs", "a", "b", cache, per_page=2)
        self.assertEqual(errors, [])
        self.assertEqual([i["n"] for i in items], [1, 2, 3])
        self.assertEqual(checked, 3)

    def test_304_walks_cached_pages(self):
        """Second run: page 1 304 → reconstruct page 2 → 304 → partial page
        ends the walk (the truncation-at-page-1 regression)."""
        m = self.m
        p1 = ("https://api.github.com/repos/a/b/pulls?state=closed&per_page=100")
        p2 = ("https://api.github.com/repos/a/b/pulls?state=closed&page=2&per_page=100")
        payload = {self._canon(p1): ([{"n": 1}, {"n": 2}], "E1", p2),
                   self._canon(p2): ([{"n": 3}], "E2", None)}
        cache = {}
        self._install(payload, cache)
        m._paginate(p1, "PRs", "a", "b", cache, per_page=2)
        # Run 2 with the same (warm) cache: every conditional GET 304s.
        items, checked, errors = m._paginate(p1, "PRs", "a", "b", cache, per_page=2)
        self.assertEqual(errors, [])
        self.assertEqual([i["n"] for i in items], [1, 2, 3], "304 run must not truncate")


class KeepFilterTests(unittest.TestCase):
    def setUp(self):
        self.m = gm_check()
        self.cutoff = datetime(2026, 8, 13, tzinfo=timezone.utc)

    def test_pr_requires_merge_in_window(self):
        new = {"merged_at": "2026-08-14T00:00:00Z"}
        old = {"merged_at": "2026-08-01T00:00:00Z"}
        unmerged = {"merged_at": None}
        self.assertTrue(self.m.keep_pr(new, self.cutoff))
        self.assertFalse(self.m.keep_pr(old, self.cutoff))
        self.assertFalse(self.m.keep_pr(unmerged, self.cutoff))

    def test_issue_window_and_title_filter(self):
        f = self.m.RepoFilter({"title_allow": ["【开源自荐】", "【自荐】"]})
        ok = {"title": "【开源自荐】我的项目", "created_at": "2026-08-14T00:00:00Z"}
        blocked = {"title": "周报讨论", "created_at": "2026-08-14T00:00:00Z"}
        owner_issue = {"title": "【开源自荐】official",
                       "created_at": "2026-08-14T00:00:00Z",
                       "user": {"login": "someone"}}
        self.assertTrue(self.m.keep_issue(ok, self.cutoff, f, "someone"))
        self.assertFalse(self.m.keep_issue(blocked, self.cutoff, f, "someone"))
        self.assertFalse(
            self.m.keep_issue(owner_issue, self.cutoff,
                              self.m.RepoFilter({"drop_owner": True}), "someone"))

    def test_pull_request_entries_dropped_by_default(self):
        f = self.m.RepoFilter({})
        pr_like = {"title": "x", "pull_request": {"url": "..."}}
        self.assertFalse(f.matches(pr_like, "owner"))


class LoadReposTests(unittest.TestCase):
    def test_valid_file(self):
        m = gm_check()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "repos.json"
            p.write_text(json.dumps([{"owner": "a", "repo": "b"}]), encoding="utf-8")
            self.assertEqual(m.load_repos(str(p)), [{"owner": "a", "repo": "b"}])

    def test_missing_falls_back(self):
        m = gm_check()
        repos = m.load_repos("Z:/no/such/path/repos.json")
        self.assertTrue(repos)  # defaults

    def test_adhoc_override(self):
        m = gm_check()
        repos = m.load_repos("unused", override_owner="o", override_repo="r")
        self.assertEqual(repos[0]["monitor"], ["pulls", "issues"])


class GmCommonRetryAfterTests(unittest.TestCase):
    def test_http_date_no_crash(self):
        f = gm_common()._retry_after_seconds
        now = datetime.now(timezone.utc)
        d = (now + timedelta(seconds=300)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertEqual(f(d, 2), 60)


if __name__ == "__main__":
    unittest.main()
