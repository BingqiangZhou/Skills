#!/usr/bin/env python3
"""Check GitHub for new activity across one or more repos.

A unified monitor that, per repo in references/repos.json, fetches newly
merged pull requests and/or new issues within a time window, applies an
optional per-repo issue filter, and writes latest_updates.json.

Per-repo config (references/repos.json):
  - owner, repo          (required)
  - name                 (optional, display name)
  - monitor              (optional, list[str]; one/both of "pulls", "issues";
                          default ["issues"])
  - filter               (optional, applies to issues only; see RepoFilter)

Data sources: GitHub REST API.
  - Pulls:  GET /repos/{owner}/{repo}/pulls?state=closed&sort=updated&since=...
  - Issues: GET /repos/{owner}/{repo}/issues?state=open&sort=created&since=...

The API `since` parameter filters on updated_at for both endpoints, so we
re-filter on the client side on the meaningful timestamp for each kind:
  - pulls:  merged_at (must be non-null AND within the window; state=closed
            also includes PRs closed without merging)
  - issues: created_at (must be within the window)

Token: optional. Set GITHUB_ACCESS_TOKEN to raise the rate limit from
60/hour/IP to 5000/hour.

Usage:
    python check_updates.py --hours 24 \\
        --output {project_root}/workspaces/github-monitor/latest_updates.json \\
        --cache  {project_root}/workspaces/github-monitor/.http_cache.json

    # Override the repo list ad hoc (single repo, monitor both pulls+issues,
    # no filter):
    python check_updates.py --hours 24 --owner octocat --repo Hello-World ...
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

# Sibling import: relies on the script being run from the scripts/ directory
# (cwd) or via the `cd {skill_directory} && python scripts/...` convention.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import github_get, github_token  # noqa: E402

GITHUB_API = 'https://api.github.com'

# Audience is Chinese readers; show timestamps in CST.
CST = timezone(timedelta(hours=8))

# Maximum body length kept for AI summarization. Keeps batches small enough to
# avoid sub-agent timeouts (see SKILL.md).
MAX_BODY_CHARS = 3000

# Safety cap on pages to fetch per repo (pagination fallback). With
# per_page=100 this is up to 5000 items — far beyond any realistic window.
MAX_PAGES = 50

# Default repo list when references/repos.json is missing/invalid.
DEFAULT_REPOS = [
    {'owner': '1c7', 'repo': 'chinese-independent-developer',
     'name': '中国独立开发者项目列表', 'monitor': ['pulls']},
    {'owner': 'ruanyf', 'repo': 'weekly',
     'name': '阮一峰 科技爱好者周刊', 'monitor': ['issues']},
]

VALID_TYPES = ('pulls', 'issues')


class _HTMLStripper(HTMLParser):
    """Strip HTML tags, keep the text. Collapses whitespace on demand."""

    def __init__(self):
        super().__init__()
        self._chunks = []

    def handle_data(self, data):
        self._chunks.append(data)

    def get_text(self):
        text = ''.join(self._chunks)
        # Collapse runs of whitespace (incl. newlines) into single spaces.
        text = re.sub(r'\s+', ' ', text).strip()
        return text


def html_to_text(html_or_markdown):
    """Best-effort plain-text extraction from an issue/PR body.

    Bodies are Markdown, but contributors often paste raw HTML (img/a/p tags).
    We strip HTML tags and collapse whitespace. Code blocks and Markdown syntax
    are left mostly intact — the summarizer only needs a rough description, and
    stripping Markdown would lose meaning (e.g. link targets).
    """
    if not html_or_markdown:
        return ''
    try:
        stripper = _HTMLStripper()
        stripper.feed(html_or_markdown)
        text = stripper.get_text()
    except Exception:
        text = html_or_markdown
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def load_repos(repos_path, override_owner=None, override_repo=None):
    """Load the target repo list from references/repos.json.

    Returns a list of repo config dicts. If override_owner/override_repo are
    given, returns a single ad-hoc entry monitoring both pulls and issues with
    no filter. Falls back to DEFAULT_REPOS if the file is missing or invalid.
    """
    if override_owner and override_repo:
        return [{'owner': override_owner, 'repo': override_repo,
                 'monitor': ['pulls', 'issues'], 'filter': None, 'name': None}]

    try:
        with open(repos_path, 'r', encoding='utf-8') as f:
            repos = json.load(f)
        if isinstance(repos, list) and repos:
            return repos
    except (OSError, json.JSONDecodeError):
        pass
    return DEFAULT_REPOS


def monitor_types(repo_cfg):
    """Return the de-duplicated list of activity types to monitor for a repo.

    Valid types are 'pulls' and 'issues'. Invalid entries are dropped. Defaults
    to ['issues'] when absent/empty/all-invalid (preserves the legacy issue
    monitor behavior).
    """
    raw = repo_cfg.get('monitor')
    if not isinstance(raw, list):
        return ['issues']
    seen = []
    for t in raw:
        if t in VALID_TYPES and t not in seen:
            seen.append(t)
    return seen or ['issues']


class RepoFilter:
    """Compiled per-repo issue filter. `matches(issue)` returns True to keep.

    Only applied to issues (PRs use their own merged_at window filter).
    """

    def __init__(self, cfg):
        """cfg is the `filter` dict from repos.json, or None for no filter."""
        cfg = cfg or {}
        self.drop_pull_requests = cfg.get('drop_pull_requests', True)
        self.drop_owner = cfg.get('drop_owner', False)
        # title_allow / title_block are lists of regex strings; compiled once.
        self.title_allow = [re.compile(p, re.IGNORECASE)
                            for p in cfg.get('title_allow', [])]
        self.title_block = [re.compile(p, re.IGNORECASE)
                            for p in cfg.get('title_block', [])]

    def matches(self, issue, owner_login):
        title = issue.get('title') or ''
        if self.drop_pull_requests and issue.get('pull_request') is not None:
            return False
        if self.drop_owner and owner_login:
            user = issue.get('user') or {}
            if (user.get('login') or '').lower() == owner_login.lower():
                return False
        if self.title_allow and not any(p.search(title) for p in self.title_allow):
            return False
        if self.title_block and any(p.search(title) for p in self.title_block):
            return False
        return True


def load_cache(cache_path):
    """Load the HTTP ETag cache. Returns a dict, or {} if missing/corrupt.

    Cache shape: { url: {'etag': str, 'data': <parsed json>, 'ts': float} }
    """
    if not cache_path:
        return {}
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        if isinstance(cache, dict):
            return cache
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_cache(cache_path, cache):
    """Persist the cache. No-op if no path given."""
    if not cache_path:
        return
    try:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f'WARNING: could not write cache {cache_path}: {e}', file=sys.stderr)


def parse_iso(ts):
    """Parse a GitHub ISO-8601 timestamp (e.g. 2026-07-30T12:25:04Z) to aware UTC datetime."""
    if not ts:
        return None
    try:
        return datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except ValueError:
            return None


def _paginate(url, label, owner, repo, cache, per_page):
    """Generic pagination loop for a GitHub list endpoint.

    `label` is 'PRs' or 'issues' (for logs). Returns (items, checked, errors).
    Uses the ETag cache to short-circuit unchanged pages.
    """
    items = []
    checked = 0
    errors = []

    page = 0
    token_note = 'with token' if github_token() else 'WITHOUT token (60/h limit)'
    print(f'Fetching {owner}/{repo} {label} ({token_note})...')

    while url and page < MAX_PAGES:
        page += 1
        cached = cache.get(url, {})
        etag = cached.get('etag')
        try:
            resp = github_get(url, etag=etag)
        except Exception as e:
            msg = f'{owner}/{repo} page {page}: {e}'
            errors.append(msg)
            print(f'  ERROR: {msg}', file=sys.stderr)
            break

        if resp is None:
            # 304 Not Modified — reuse cached page data.
            data = cached.get('data')
            print(f'  page {page}: not modified (cached: {len(data) if isinstance(data, list) else 0} {label})')
            if not isinstance(data, list):
                break
        else:
            data = resp.get('data')
            new_etag = resp.get('etag')
            if isinstance(data, list) and new_etag:
                cache[url] = {'etag': new_etag, 'data': data, 'ts': time.time()}
            rate_rem = resp.get('rate_remaining')
            if rate_rem is not None and rate_rem < 10:
                # Close to the rate limit; pause briefly to avoid a 403.
                print(f'  rate limit low ({rate_rem} remaining); pausing',
                      file=sys.stderr)
                time.sleep(2)
            print(f'  page {page}: {len(data) if isinstance(data, list) else "non-list"} {label}'
                  + (f' (etag {new_etag[:8]}...)' if new_etag else ''))

        if not isinstance(data, list) or not data:
            break

        checked += len(data)
        items.extend(data)

        link = resp.get('link', {}) if resp else {}
        url = link.get('next')
        if not url:
            break

    return items, checked, errors


def fetch_issues(owner, repo, since_iso, cache, per_page=100):
    """Fetch open issues for a repo, sorted by created_at desc, since `since_iso`.

    Note: `since` filters on updated_at, so the client-side created_at re-filter
    in main() is mandatory.
    """
    url = (
        f'{GITHUB_API}/repos/{owner}/{repo}/issues'
        f'?state=open&sort=created&direction=desc'
        f'&since={since_iso}&per_page={per_page}'
    )
    return _paginate(url, 'issues', owner, repo, cache, per_page)


def fetch_merged_prs(owner, repo, since_iso, cache, per_page=100):
    """Fetch closed PRs for a repo, sorted by updated_at desc, since `since_iso`.

    Note: `since` filters on updated_at and state=closed includes PRs closed
    without merging, so the client-side merged_at re-filter in main() is
    mandatory.
    """
    url = (
        f'{GITHUB_API}/repos/{owner}/{repo}/pulls'
        f'?state=closed&sort=updated&direction=desc'
        f'&since={since_iso}&per_page={per_page}'
    )
    return _paginate(url, 'PRs', owner, repo, cache, per_page)


def build_update_issue(issue, repo_key):
    """Project a raw GitHub issue into the flat `update` record we report on."""
    body_raw = issue.get('body') or ''
    body_text = html_to_text(body_raw)
    if len(body_text) > MAX_BODY_CHARS:
        body_text = body_text[:MAX_BODY_CHARS] + '…'

    reactions = issue.get('reactions') or {}
    labels = issue.get('labels') or []
    user = issue.get('user') or {}

    created_at = issue.get('created_at') or ''
    return {
        'type': 'issues',
        'repo': repo_key,
        'item_number': issue.get('number'),
        'item_url': issue.get('html_url') or '',
        'issue_number': issue.get('number'),
        'title': issue.get('title') or '',
        'html_url': issue.get('html_url') or '',
        'created_at': created_at,
        'updated_at': issue.get('updated_at') or '',
        'author': user.get('login') or '',
        'author_url': user.get('html_url') or '',
        'comments': issue.get('comments', 0),
        'reactions_total': reactions.get('total_count', 0),
        'labels': [l.get('name') for l in labels if isinstance(l, dict) and l.get('name')],
        'body_text': body_text,
        'sort_at': created_at,   # used to sort within a repo
    }


def build_update_pr(pr, owner, repo):
    """Project a raw GitHub PR into the flat `update` record we report on."""
    body_raw = pr.get('body') or ''
    body_text = html_to_text(body_raw)
    if len(body_text) > MAX_BODY_CHARS:
        body_text = body_text[:MAX_BODY_CHARS] + '…'

    reactions = pr.get('reactions') or {}
    labels = pr.get('labels') or []
    user = pr.get('user') or {}
    base = pr.get('base') or {}

    merged_at = pr.get('merged_at') or ''
    return {
        'type': 'pulls',
        'repo': f'{owner}/{repo}',
        'owner': owner,
        'repo_name': repo,
        'item_number': pr.get('number'),
        'item_url': pr.get('html_url') or '',
        'pr_number': pr.get('number'),
        'title': pr.get('title') or '',
        'html_url': pr.get('html_url') or '',
        'merged_at': merged_at,
        'closed_at': pr.get('closed_at') or '',
        'updated_at': pr.get('updated_at') or '',
        'created_at': pr.get('created_at') or '',
        'author': user.get('login') or '',
        'author_url': user.get('html_url') or '',
        'base_branch': base.get('ref') or '',
        'merge_commit_sha': pr.get('merge_commit_sha') or '',
        'additions': pr.get('additions', 0),
        'deletions': pr.get('deletions', 0),
        'changed_files': pr.get('changed_files', 0),
        'comments': pr.get('comments', 0),
        'reactions_total': reactions.get('total_count', 0),
        'labels': [l.get('name') for l in labels if isinstance(l, dict) and l.get('name')],
        'body_text': body_text,
        'sort_at': merged_at,   # used to sort within a repo
    }


def keep_issue(issue, cutoff, repo_filter, owner):
    """Issue time-window + per-repo filter gate. Returns True to keep."""
    created = parse_iso(issue.get('created_at'))
    if created is None or created < cutoff:
        return False
    return repo_filter.matches(issue, owner)


def keep_pr(pr, cutoff):
    """PR merged_at window gate. Returns True to keep.

    The API `since` filters on updated_at (so old-but-recently-commented PRs
    leak in), and state=closed includes closed-without-merge PRs. We keep a PR
    only if it was actually merged AND the merge happened within the window.
    """
    merged_at = parse_iso(pr.get('merged_at'))
    if merged_at is None:
        return False
    return merged_at >= cutoff


def main():
    parser = argparse.ArgumentParser(
        description='Check GitHub for new activity (merged PRs + new issues) across one or more repos')
    parser.add_argument('--hours', type=int, default=24,
                        help='Time window in hours (default: 24)')
    parser.add_argument('--output', required=True,
                        help='Output latest_updates.json path')
    parser.add_argument('--cache', default=None,
                        help='HTTP ETag cache file path')
    parser.add_argument('--repos', default=None,
                        help='references/repos.json path (default: sibling references/)')
    parser.add_argument('--owner', default=None,
                        help='Ad-hoc single repo: owner (overrides --repos)')
    parser.add_argument('--repo', default=None,
                        help='Ad-hoc single repo: repo name (overrides --repos)')
    parser.add_argument('--per-page', type=int, default=100,
                        help='GitHub API page size (default: 100)')
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    repos_path = args.repos or str(script_dir.parent / 'references' / 'repos.json')
    repos = load_repos(repos_path, args.owner, args.repo)

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=args.hours)
    since_iso = cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')

    cache = load_cache(args.cache)

    all_updates = []
    seen_keys = set()           # dedup within this run by item html_url
    total_checked = 0
    total_filtered_out = 0
    total_errors = []
    per_repo_stats = []         # [{repo, name, checked, kept, filtered_out}]
    repo_acc = {}               # repo_key -> accumulators (across pulls+issues)

    def repo_bucket(repo_key, display_name):
        b = repo_acc.get(repo_key)
        if b is None:
            b = {'name': display_name, 'checked': 0, 'kept': 0, 'filtered_out': 0}
            repo_acc[repo_key] = b
        return b

    for repo_cfg in repos:
        owner = repo_cfg.get('owner')
        repo = repo_cfg.get('repo')
        if not owner or not repo:
            continue
        repo_key = f'{owner}/{repo}'
        display_name = repo_cfg.get('name') or repo_key
        types = monitor_types(repo_cfg)
        # Issue filter applies only to issues; PRs are filtered by merged_at.
        repo_filter = RepoFilter(repo_cfg.get('filter'))
        bucket = repo_bucket(repo_key, display_name)

        for kind in types:
            if kind == 'issues':
                items, checked, errors = fetch_issues(
                    owner, repo, since_iso, cache, per_page=args.per_page)
            else:  # 'pulls'
                items, checked, errors = fetch_merged_prs(
                    owner, repo, since_iso, cache, per_page=args.per_page)
            total_checked += checked
            total_errors.extend(errors)
            bucket['checked'] += checked

            kept = 0
            filtered = 0
            for item in items:
                if kind == 'issues':
                    if not keep_issue(item, cutoff, repo_filter, owner):
                        filtered += 1
                        continue
                    record = build_update_issue(item, repo_key)
                else:
                    if not keep_pr(item, cutoff):
                        filtered += 1
                        continue
                    record = build_update_pr(item, owner, repo)

                dedup_key = record.get('html_url') or (record['type'], record.get('item_number'))
                if dedup_key in seen_keys:
                    filtered += 1
                    continue
                seen_keys.add(dedup_key)
                all_updates.append(record)
                kept += 1
            total_filtered_out += filtered
            bucket['kept'] += kept
            bucket['filtered_out'] += filtered

        print(f'{repo_key}: {bucket["kept"]} kept, '
              f'{bucket["filtered_out"]} filtered out '
              f'(monitor={types})\n')

    # Group by repo, newest-first within each group. A reverse sort on
    # (repo, sort_at) yields repo-grouping + reverse-chronological order.
    all_updates.sort(key=lambda u: (u.get('repo', ''), u.get('sort_at', '')),
                     reverse=True)

    save_cache(args.cache, cache)

    # Build per_repo stats in repo-iteration order.
    for repo_cfg in repos:
        owner = repo_cfg.get('owner')
        repo = repo_cfg.get('repo')
        if not owner or not repo:
            continue
        repo_key = f'{owner}/{repo}'
        b = repo_acc.get(repo_key)
        if b is None:
            continue
        per_repo_stats.append({
            'repo': repo_key,
            'name': b['name'],
            'checked': b['checked'],
            'kept': b['kept'],
            'filtered_out': b['filtered_out'],
        })

    output = {
        'metadata': {
            'checked_count': total_checked,
            'filtered_out_count': total_filtered_out,
            'error_count': len(total_errors),
            'error_details': total_errors,
            'hours': args.hours,
            'update_count': len(all_updates),
            'check_time': now_utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'repos': [f"{c.get('owner')}/{c.get('repo')}" for c in repos if c.get('owner') and c.get('repo')],
            'per_repo': per_repo_stats,
        },
        'updates': all_updates,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    pr_count = sum(1 for u in all_updates if u['type'] == 'pulls')
    issue_count = len(all_updates) - pr_count
    print(f'Wrote {len(all_updates)} updates to {out_path} '
          f'(PRs={pr_count}, issues={issue_count})')
    print(f'  checked={total_checked}  filtered_out={total_filtered_out}  '
          f'errors={len(total_errors)}')
    if total_errors:
        print('  Errors:')
        for e in total_errors:
            print(f'    - {e}')


if __name__ == '__main__':
    main()
