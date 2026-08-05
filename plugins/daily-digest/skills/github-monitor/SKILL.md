---
name: github-monitor
version: "1.0.6"
description: |
  Collect and save new GitHub repository activity — newly merged pull requests
  and/or new issues — to latest_updates.json. This is the COLLECTION layer
  only: a single unified monitor driven by references/repos.json. It does NOT
  summarize or render a report — use the daily-digest skill for that.

  Each repo declares which activity types to watch (`monitor: ["pulls"]`,
  `["issues"]`, or both) and may carry an optional per-repo issue filter.

  Default config watches two repos:
  - `1c7/chinese-independent-developer` (中国独立开发者项目列表) for newly
    **merged PRs** — a curated list where new projects are added by merging
    community PRs, so "newly merged PR" effectively means "newly added project".
  - `ruanyf/weekly` (阮一峰 科技爱好者周刊) for new **issues**, filtered to keep
    only community **open-source project self-recommendations**
    (【开源自荐】 / 【自荐】 / 【开源项目】…).

  Use this skill whenever the user wants to CHECK or COLLECT raw GitHub
  activity without a report. This includes:
  - Checking chinese-independent-developer / 独立开发者项目列表 / 1c7 仓库 for new
    entries (新项目 / 新收录 / 最近合并的 PR)
  - Checking 阮一峰周刊 / ruanyf weekly for new self-recommended open-source projects
  - Checking what pull requests were recently merged into a GitHub repo
  - Checking what new issues were opened on a GitHub repo
  - Any mention of GitHub combined with PR / pull request / 合并 / merged /
    issue / 检查 / 抓取 / 获取 / 最近 / 更新（不含"日报/摘要"）

  Do NOT trigger for: generating a digest/report/summary (use daily-digest),
  monitoring GitHub *releases* / *tags* / *versions* (use tool-update-monitor),
  RSS / podcast / WeChat updates, or general "open a PR" / "create a PR" requests.
---

# GitHub Monitor Skill (Collection Layer)

A unified collector for GitHub repository activity across one or more repos.
For each repo in `references/repos.json`, it fetches newly merged pull requests
and/or new issues within a time window (per the repo's `monitor` setting),
optionally applies a per-repo issue filter, and **saves** the result to
`latest_updates.json`. This skill is **collection only** — it does not
summarize or render a report. To turn the collected data into a unified
AI-summarized daily digest, run the **daily-digest** skill.

The default `repos.json` ships two entries:
- `1c7/chinese-independent-developer` — watch **merged PRs** (new project listings).
- `ruanyf/weekly` — watch **issues**, filtered to community open-source
  self-recommendations. Add more repos to watch additional projects; each can
  monitor pulls, issues, or both, and carry its own issue filter.

## Architecture

```
{skill_directory}/             # This skill's directory (inside the plugin)
  SKILL.md                  # This file
  scripts/
    _common.py              # GitHub HTTP helpers (ETag, rate-limit, auth)
    check_updates.py        # Fetch merged PRs + new issues, apply filters
    merge_summaries.py      # Merge per-batch AI summaries (reused by daily-digest)
  references/
    repos.json              # Target repos + per-repo monitor/filter rules

{project_root}/                # The user's current project (cwd at run time)
  workspaces/daily-digests/data/github-monitor/          # Runtime intermediate files (gitignored)
    .http_cache.json                  # GitHub API ETag cache
    latest_updates.json               # ← THIS skill's output (input to daily-digest)
```

> NOTE: This skill is **collection only** — it does NOT summarize or render a
> report. Summarization and report generation are handled by the daily-digest
> skill, which reuses this skill's `merge_summaries.py`.

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the absolute
path to this skill's directory and `{project_root}` with the user's current
project (the working directory at run time).

### Step 0: Ensure workspace directories exist

```bash
mkdir -p "{project_root}/workspaces/daily-digests/data/github-monitor"
```

### Step 1: Check for new activity (merged PRs + new issues)

```bash
cd "{skill_directory}" && python scripts/check_updates.py \
  --hours 24 \
  --output "{project_root}/workspaces/daily-digests/data/github-monitor/latest_updates.json" \
  --cache  "{project_root}/workspaces/daily-digests/data/github-monitor/.http_cache.json"
```

Options:
- `--hours N` — time window in hours (default: 24)
- `--repos PATH` — override `references/repos.json`
- `--owner OWNER --repo REPO` — monitor a single ad-hoc repo **monitoring both
  pulls and issues with no filter** (overrides `--repos`). Use this for a
  one-off "watch this repo" task.
- `--per-page N` — GitHub API page size (default: 100)

**GitHub token (optional but recommended):** the unauthenticated API allows
only ~60 requests/hour per IP. Set `GITHUB_ACCESS_TOKEN` in the environment to
raise it to 5000/hour:

```bash
export GITHUB_ACCESS_TOKEN="ghp_xxx"
```

The script honors the token automatically — no flag needed.

After the script finishes, read `latest_updates.json` and check
`metadata.update_count`. If it is `0`, stop and inform the user that no new
activity was found in the window. `metadata.per_repo` shows per-repo
checked/kept/filtered counts — a useful sanity check. Each `updates[]` entry
carries a `type` field (`"pulls"` or `"issues"`); the console log reports the
PR/issue split. `metadata.filtered_out_count` is the total dropped (by filters,
the time window, unmerged PRs, or dedup).

## Configuring repos (references/repos.json)

`repos.json` is a list of repo entries. A minimal entry reports every new
issue in the window (the default `monitor` is `["issues"]`):

```json
[
  { "owner": "octocat", "repo": "Hello-World" }
]
```

Each entry may carry:

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `monitor` | string[] | `["issues"]` | Which activity to watch: `"pulls"`, `"issues"`, or both |
| `name` | string | `{owner}/{repo}` | Display name in the report |
| `filter` | object | — | Issue-only filter (see below); ignored for PRs |

An entry's optional `filter` object controls which **issues** are kept. All
filter fields are optional:

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `drop_pull_requests` | bool | `true` | Drop PRs (the issues endpoint also returns them) |
| `drop_owner` | bool | `false` | Drop issues authored by the repo owner |
| `title_allow` | string[] | `[]` | Regexes; the title must match at least one to be kept |
| `title_block` | string[] | `[]` | Regexes; a matching title is dropped (applied after `title_allow`) |

To monitor a repo once without editing the file, pass `--owner` / `--repo` on
the command line (monitors both pulls and issues, no filter).

## Filtering logic (how an item is kept)

For each repo, `check_updates.py` applies these rules per activity type:

**Pulls** — a PR is kept only when ALL hold:
1. It was fetched via `state=closed`.
2. Its `merged_at` is non-null — i.e. it was actually merged, not just closed
   without merging. (The pulls API returns both.)
3. Its `merged_at` falls within the `--hours` window. (The GitHub API `since`
   parameter filters on `updated_at`, NOT `merged_at`, so this client-side
   `merged_at` re-filter is mandatory to keep old-but-recently-commented PRs out.)

**Issues** — an issue is kept only when ALL hold:
1. `created_at` is within the `--hours` window. (The GitHub API `since`
   parameter filters on `updated_at`, so this client-side `created_at`
   re-filter is mandatory to keep old-but-recently-commented issues out.)
2. It passes the per-repo `filter` (or, if the repo has no filter, every issue
   in the window is kept).
3. It is not a duplicate of an already-seen item (by `html_url`/`number`),
   deduplicated within the single run across both types.

## Performance Notes

| Stage | Time |
|-------|------|
| Activity fetch (per repo, 1 page, ETag cached) | ~1-2s |

A 24-hour window typically yields a single API page (`per_page=100`) per repo;
the script paginates automatically if a repo exceeds that. Many repos share
the same rate-limit budget, so set `GITHUB_ACCESS_TOKEN` when monitoring
several. A 24h window on `1c7/chinese-independent-developer` typically yields
0–3 merged PRs; `ruanyf/weekly` yields a handful of self-recommendation issues.

## Completion

After collection, inform the user:
- How many items were found (total, broken down by PR vs issue, and per repo),
  and how many were filtered out
- The output path: `workspaces/daily-digests/data/github-monitor/latest_updates.json`
- To generate a unified AI-summarized daily digest, run the **daily-digest**
  skill — it consumes this file (plus the RSS and tool-update data) and
  produces `workspaces/daily-digests/reports/YYYY-MM-DD/daily-digest_HH-MM.md`.
- Note whether a GitHub token was used (if `update_count` is suspiciously low
  and no token was set, the API rate limit may be the cause — suggest setting
  `GITHUB_ACCESS_TOKEN`).
