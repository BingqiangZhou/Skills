---
name: github-monitor
description: |
  Monitor GitHub repositories for new activity — newly merged pull requests and/or
  new issues — and generate a daily digest report with AI-powered summaries. A
  single unified monitor driven by references/repos.json: each repo declares
  which activity types to watch (`monitor: ["pulls"]`, `["issues"]`, or both)
  and may carry an optional per-repo issue filter.

  Default config watches two repos:
  - `1c7/chinese-independent-developer` (中国独立开发者项目列表) for newly
    **merged PRs** — a curated list where new projects are added by merging
    community PRs, so "newly merged PR" effectively means "newly added project".
  - `ruanyf/weekly` (阮一峰 科技爱好者周刊) for new **issues**, filtered to keep
    only community **open-source project self-recommendations**
    (【开源自荐】 / 【自荐】 / 【开源项目】…).

  Use this skill whenever the user wants to check for or summarize GitHub
  activity. This includes:
  - Monitoring chinese-independent-developer / 独立开发者项目列表 / 1c7 仓库 for new
    entries (新项目 / 新收录 / 最近合并的 PR)
  - Monitoring 阮一峰周刊 / ruanyf weekly for new self-recommended open-source projects
  - Checking what pull requests were recently merged into a GitHub repo
  - Checking what new issues were opened on a GitHub repo
  - Any mention of GitHub combined with PR / pull request / 合并 / merged /
    issue / 日报 / 监控 / 检查 / 最近 / 更新
  - Questions like "1c7 仓库最近合并了哪些 PR", "独立开发者列表有什么新项目",
    or "watch this repo's issues and summarize them"

  Do NOT trigger for: monitoring GitHub *releases* / *tags* / *versions* (use
  tool-update-monitor), RSS / podcast / WeChat updates, or general "open a PR" /
  "create a PR" requests.
---

# GitHub Monitor Skill

A unified monitor for GitHub repository activity across one or more repos.
For each repo in `references/repos.json`, it fetches newly merged pull requests
and/or new issues within a time window (per the repo's `monitor` setting),
optionally applies a per-repo issue filter, and generates an AI-summarized daily
digest grouped by repo. PRs and issues appear together on one timeline, each
rendered with the fields that matter for its type (PR: merge time / branch /
diff stats; issue: creation time / labels).

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
    merge_summaries.py      # Merge per-batch AI summaries into one file
    generate_report.py      # Generate Markdown digest report
  references/
    repos.json              # Target repos + per-repo monitor/filter rules

{project_root}/                # The user's current project (cwd at run time)
  workspaces/github-monitor/          # Runtime intermediate files (gitignored)
    .http_cache.json                  # GitHub API ETag cache
    latest_updates.json
    github-monitor_batch_N.json       # Batches for AI summarization
    ai_summaries_batch_N.json
    ai_summaries.json
  daily-digests/YYYY-MM-DD/           # Unified output directory
    github-monitor_HH-MM.md           # (times shown in CST / UTC+8)
```

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the absolute
path to this skill's directory and `{project_root}` with the user's current
project (the working directory at run time).

### Step 0: Ensure workspace directories exist

```bash
mkdir -p "{project_root}/workspaces/github-monitor"
mkdir -p "{project_root}/daily-digests/$(date +%Y-%m-%d)"
```

### Step 1: Check for new activity (merged PRs + new issues)

```bash
cd "{skill_directory}" && python scripts/check_updates.py \
  --hours 24 \
  --output "{project_root}/workspaces/github-monitor/latest_updates.json" \
  --cache  "{project_root}/workspaces/github-monitor/.http_cache.json"
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

### Step 2: AI Summarization (parallel sub-agents)

Divide updates into **batches of 10**, create one sub-agent per batch.

**CRITICAL — avoid two common sub-agent failure modes:**
1. **Oversized input**: an item `body_text` can be thousands of chars. A
   10-item batch with full text easily exceeds 50KB and causes sub-agent
   timeouts/context errors. Truncate `body_text` when preparing batches
   (Step 2a does this — `check_updates.py` already caps body at 3000 chars,
   and Step 2a further truncates to 1500 for summarization).
2. **Broken output JSON**: sub-agents that write JSON via bash
   `heredoc`/`echo`/`cat` produce malformed files (Chinese smart quotes,
   encoding errors). The prompt below forces `json.dump()`; and
   `merge_summaries.py` tolerates any corrupt batches instead of crashing.

**Launch at most 4 sub-agents in parallel** (API rate limiting). Process
batches group by group; do not launch more than 4 Agent tool calls in a
single message.

#### 2a. Prepare batch files (automated)

This creates the batch files deterministically and truncates `body_text` to
the first 1500 chars so each batch stays small. Each entry is type-aware
(`item_type` = `"pulls"` or `"issues"`). Run from the project root:

```bash
cd "{project_root}"
python -c "
import json
with open('workspaces/github-monitor/latest_updates.json','r',encoding='utf-8') as f:
    data=json.load(f)
updates=data['updates']
for i in range(0,len(updates),10):
    batch=[{'item_type':u.get('type',''),'item_number':u.get('item_number'),
            'item_url':u.get('html_url'),'title':u['title'],
            'author':u.get('author',''),'repo':u.get('repo',''),
            'body_text':u.get('body_text','')[:1500]} for u in updates[i:i+10]]
    n=i//10
    with open(f'workspaces/github-monitor/github-monitor_batch_{n}.json','w',encoding='utf-8') as f:
        json.dump(batch,f,ensure_ascii=False)
print(f'Saved {(len(updates)+9)//10} batches')
"
```

#### 2b. Launch sub-agents (groups of 4)

Total batches = `ceil(update_count / 10)`. Divide into groups of 4. For
example, 12 batches → groups [0-3], [4-7], [8-11]. Launch each group in one
message (up to 4 Agent tool calls), wait for the group to finish, then
launch the next.

**Sub-Agent Prompt Template** (replace `{N}` with the batch index, and
`{project_root}` with the real path):

```
Task: Summarize GitHub activity (merged pull requests and/or new issues) into
one Chinese sentence each.

Read the file {project_root}/workspaces/github-monitor/github-monitor_batch_{N}.json.

Each entry is either a newly merged GitHub pull request (item_type="pulls") or a
new GitHub issue (item_type="issues"). For each entry, use the `title` and
`body_text` fields to write ONE concise Chinese sentence (under 100 characters)
capturing the key point or purpose:
- For a PR: what it changed or added (project name, what it does, notable detail).
- For an issue: what the author is announcing or asking about.
The goal is that the reader can decide whether to look into it.

Write the results as JSON to:
{project_root}/workspaces/github-monitor/ai_summaries_batch_{N}.json

Use this exact structure:
{
  "summaries": [
    {
      "item_url": "the item_url from the input",
      "item_number": 12345,
      "item_type": "pulls",
      "title": "...",
      "ai_summary": "一句话中文摘要"
    }
  ]
}

CRITICAL - Encoding rules to avoid broken JSON:
- You MUST write the file using Python json.dump(), NOT bash heredoc/echo/cat.
- Do NOT use Chinese smart quotes (\u201c \u201d) in the ai_summary text —
  use straight quotes (") or avoid quotes altogether.
- Use ensure_ascii=False and encoding="utf-8" when writing.
- Example:
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
```

#### 2c. Merge batch summaries

After all sub-agents complete, merge the batch files into one
`ai_summaries.json`. `merge_summaries.py` keys on `item_url` (falling back to
legacy `pr_url`/`issue_url`), skips any missing/corrupt batch files with a
warning (instead of crashing), and de-duplicates by URL:

```bash
cd "{skill_directory}" && python scripts/merge_summaries.py \
  --batch-dir "{project_root}/workspaces/github-monitor" \
  --pattern "ai_summaries_batch_*.json" \
  -o "{project_root}/workspaces/github-monitor/ai_summaries.json"
```

If no batch files were produced (all sub-agents failed) it exits cleanly
and writes nothing — Step 3 will simply fall back to the raw body text, so
the pipeline never breaks.

If sub-agents are not available at all (e.g., in Claude.ai), skip Step 2
entirely. The report generator uses the raw body text as fallback.

### Step 3: Generate the digest report

```bash
cd "{skill_directory}" && python scripts/generate_report.py \
  -i "{project_root}/workspaces/github-monitor/latest_updates.json" \
  -s "{project_root}/workspaces/github-monitor/ai_summaries.json" \
  -o "{project_root}/daily-digests/YYYY-MM-DD/github-monitor_HH-MM.md"
```

Replace `YYYY-MM-DD` with today's date (CST) and `HH-MM` with current time.
Report timestamps are shown in CST (UTC+8) for the Chinese-reading audience.

Report grouping: items are grouped by repository (one section per repo when
there are multiple), newest-first within each section, PRs and issues
interleaved on the timeline.

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
| AI summarization (4 sub-agents) | ~60s |
| Total end-to-end (2 default repos) | ~1-2 minutes |

A 24-hour window typically yields a single API page (`per_page=100`) per repo;
the script paginates automatically if a repo exceeds that. Many repos share
the same rate-limit budget, so set `GITHUB_ACCESS_TOKEN` when monitoring
several. A 24h window on `1c7/chinese-independent-developer` typically yields
0–3 merged PRs; `ruanyf/weekly` yields a handful of self-recommendation issues.

## Completion

After generating the report, inform the user:
- How many items were found (total, broken down by PR vs issue, and per repo),
  and how many were filtered out
- The path to the generated report file
- A brief highlight: mention the 2–3 most interesting merged PRs / issues by title
- Note whether a GitHub token was used (if `update_count` is suspiciously low
  and no token was set, the API rate limit may be the cause — suggest setting
  `GITHUB_ACCESS_TOKEN`)
