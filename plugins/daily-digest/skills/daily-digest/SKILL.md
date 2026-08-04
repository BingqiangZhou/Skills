---
name: daily-digest
version: "1.0.0"
description: |
  Orchestrate the three collector skills (rss-monitor, github-monitor,
  tool-update-monitor) into a single run and produce ONE unified daily digest
  report with AI-powered summaries.

  This is the summary/report layer. The sibling skills only collect raw data;
  THIS skill runs them, summarizes the results, and renders a unified Markdown
  report covering RSS (WeChat / tech blogs / podcasts), GitHub activity
  (merged PRs / new issues), and tool/OS release updates.

  Use this skill whenever the user wants a daily digest / summary / report
  across their information sources. Triggers include:
  - 每日摘要 / 生成日报 / 信息汇总 / 今日摘要 / daily digest / daily report
  - 看看今天有什么更新 / 今天有什么新内容 / 汇总一下最近的更新
  - 生成 RSS + GitHub + 工具的统一日报
  - "跑一下所有监控" / "全部检查一遍并出日报"
  - Any request that spans more than one source type, or asks for a digest
    rather than raw data collection

  Do NOT trigger for: collecting a single source only (use rss-monitor /
  github-monitor / tool-update-monitor directly), installing/upgrading tools,
  creating/editing articles, or general questions unrelated to digest/report
  generation.
---

# Daily Digest Skill

This is the **orchestration + report layer** of the `daily-digest` plugin. It
coordinates the three sibling collector skills and turns their raw output into
one unified AI-summarized Markdown digest.

```
daily-digest (this skill — orchestrate + summarize + report)
  │
  ├─ rss-monitor          → collects → workspaces/rss/latest_updates.json
  ├─ github-monitor       → collects → workspaces/github-monitor/latest_updates.json
  └─ tool-update-monitor  → collects → workspaces/tool-update-monitor/latest_updates.json
```

The three collector skills are kept intentionally simple: each only fetches
data and saves it. Summarization and report rendering live here, so the
collection logic stays reusable and the report logic stays centralized.

## Architecture

```
{skill_directory}/                  # This skill's directory (inside the plugin)
  SKILL.md                        # This file
  scripts/
    prepare_batches.py            # Split latest_updates.json into sub-agent batches
    generate_unified_report.py    # Render the unified daily digest Markdown

# Sibling skills' scripts are reused (not copied) from:
{skill_directory}/../rss-monitor/scripts/
  check_updates.py, fetch_feed_list.py, fetch_articles.py,
  resolve_xiaoyuzhou_urls.py, merge_summaries.py
{skill_directory}/../github-monitor/scripts/
  check_updates.py, merge_summaries.py
{skill_directory}/../tool-update-monitor/scripts/
  check_updates.py

{project_root}/                    # The user's current project (cwd at run time)
  workspaces/
    daily-digest/                # This skill's intermediate files
      *_batch_N.json             # Batches for AI summarization
      ai_summaries_batch_N.json  # Sub-agent outputs (per source)
      digest_highlights.json     # Cross-source highlights (optional)
  daily-digests/YYYY-MM-DD/        # Unified output directory
    daily-digest_HH-MM.md          # (times shown in CST / UTC+8)
```

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the absolute
path to THIS skill's directory, `{project_root}` with the user's current
project (the working directory at run time). The sibling skill directories are
at `{skill_directory}/../rss-monitor`, `/../github-monitor`, and
`/../tool-update-monitor`.

### Step 0: Ensure workspace directories exist

```bash
mkdir -p "{project_root}/workspaces/daily-digest"
mkdir -p "{project_root}/daily-digests/$(date +%Y-%m-%d)"
```

### Step 1: Trigger collection across all sources

Run each sibling collector's `check_updates.py`. Each writes to its own
workspace under `{project_root}/workspaces/<source>/latest_updates.json`. If a
source is already freshly collected (same day) you MAY skip re-running it —
read the existing `latest_updates.json` and check `metadata.generated_at`.

#### 1a. RSS collection (WeChat + tech blogs + podcasts)

Refresh the WeChat feed list first if stale (>7 days), then check all sources,
resolve Xiaoyuzhou URLs, and optionally enrich short WeChat articles:

```bash
# [WeChat] Refresh feed list if references/feeds_wechat.json is missing or >7 days old
cd "{skill_directory}/../rss-monitor" && python scripts/fetch_feed_list.py \
  --output "{skill_directory}/../rss-monitor/references/feeds_wechat.json"

# Check all RSS sources
cd "{skill_directory}/../rss-monitor" && python scripts/check_updates.py \
  --source all --hours 24 \
  --output "{project_root}/workspaces/rss/latest_updates.json" \
  --cache "{project_root}/workspaces/rss/.http_cache.json"

# [Podcast] Resolve non-Xiaoyuzhou episode URLs to canonical links (in place)
cd "{skill_directory}/../rss-monitor" && python scripts/resolve_xiaoyuzhou_urls.py \
  -i "{project_root}/workspaces/rss/latest_updates.json"

# [WeChat, optional] Enrich short articles — only if metadata.sources.wechat.short_text_count != 0
cd "{skill_directory}/../rss-monitor" && python scripts/fetch_articles.py \
  -i "{project_root}/workspaces/rss/latest_updates.json" \
  -o "{project_root}/workspaces/rss/latest_updates.json" --delay 2.0
```

See the rss-monitor SKILL.md for full flag details. After this step, read
`workspaces/rss/latest_updates.json` and check `metadata.update_count`.

#### 1b. GitHub collection (merged PRs + new issues)

```bash
cd "{skill_directory}/../github-monitor" && python scripts/check_updates.py \
  --hours 24 \
  --output "{project_root}/workspaces/github-monitor/latest_updates.json" \
  --cache  "{project_root}/workspaces/github-monitor/.http_cache.json"
```

`GITHUB_ACCESS_TOKEN` is honored automatically if set. After this step, read
`workspaces/github-monitor/latest_updates.json` and check
`metadata.update_count`.

#### 1c. Tool collection (new releases)

```bash
cd "{skill_directory}/../tool-update-monitor" && python scripts/check_updates.py \
  --hours 168 --workers 6 \
  --output "{project_root}/workspaces/tool-update-monitor/latest_updates.json" \
  --cache "{project_root}/workspaces/tool-update-monitor/.http_cache.json" \
  --state "{project_root}/workspaces/tool-update-monitor/.last_seen.json"
```

After this step, read `workspaces/tool-update-monitor/latest_updates.json`.
If `metadata.baseline_run` is true or `metadata.update_count` is 0, there are
no new releases — the tool section of the report will be skipped/empty.

### Step 2: AI Summarization (parallel sub-agents)

If ALL three sources have zero updates, stop and inform the user that nothing
new was found today.

**CRITICAL — avoid two common sub-agent failure modes:**
1. **Oversized input**: WeChat `full_text`, podcast `shownotes`, and GitHub
   `body_text` can be thousands of chars per item. `prepare_batches.py`
   truncates text fields so each batch stays small.
2. **Broken output JSON**: sub-agents that write JSON via bash
   `heredoc`/`echo`/`cat` produce malformed files (Chinese smart quotes,
   encoding errors). The prompt below forces `json.dump()`; and the sibling
   `merge_summaries.py` scripts tolerate any corrupt batches instead of
   crashing.

**Launch at most 4 sub-agents in parallel** (API rate limiting).

#### 2a. RSS summarization

Prepare batches (this truncates `full_text`/`shownotes` to 1500 chars and
groups by source):

```bash
cd "{skill_directory}" && python scripts/prepare_batches.py \
  --source rss \
  --input "{project_root}/workspaces/rss/latest_updates.json" \
  --output-dir "{project_root}/workspaces/daily-digest" \
  --prefix "rss"
```

This prints how many batches were saved. Launch one sub-agent per batch
(groups of 4). **Sub-Agent Prompt Template** (replace `{batch_file}` and
`{N}`):

```
Task: Summarize RSS articles into one Chinese sentence each.

Read the file {project_root}/workspaces/daily-digest/{batch_file}.

For each item, use the `full_text` field to write ONE concise Chinese
sentence (under 100 characters) capturing the key point or takeaway, so the
reader can decide whether to read the full article.

Write the results as JSON to:
{project_root}/workspaces/daily-digest/rss_ai_summaries_batch_{N}.json

Use this exact structure:
{
  "summaries": [
    {
      "url": "the url from the input",
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

Then merge the batches with the sibling merge script (it tolerates corrupt /
missing files and de-duplicates by URL):

```bash
cd "{skill_directory}/../rss-monitor" && python scripts/merge_summaries.py \
  --batch-dir "{project_root}/workspaces/daily-digest" \
  --pattern "rss_ai_summaries_batch_*.json" \
  -o "{project_root}/workspaces/daily-digest/rss_ai_summaries.json"
```

#### 2b. GitHub summarization

```bash
cd "{skill_directory}" && python scripts/prepare_batches.py \
  --source github \
  --input "{project_root}/workspaces/github-monitor/latest_updates.json" \
  --output-dir "{project_root}/workspaces/daily-digest" \
  --prefix "github"
```

Launch one sub-agent per batch (groups of 4). **Sub-Agent Prompt Template**
(replace `{N}`):

```
Task: Summarize GitHub activity (merged pull requests and/or new issues) into
one Chinese sentence each.

Read the file {project_root}/workspaces/daily-digest/github_batch_{N}.json.

Each entry is either a newly merged GitHub pull request (item_type="pulls") or a
new GitHub issue (item_type="issues"). For each entry, use the `title` and
`body_text` fields to write ONE concise Chinese sentence (under 100 characters)
capturing the key point or purpose:
- For a PR: what it changed or added (project name, what it does, notable detail).
- For an issue: what the author is announcing or asking about.
The goal is that the reader can decide whether to look into it.

Write the results as JSON to:
{project_root}/workspaces/daily-digest/github_ai_summaries_batch_{N}.json

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

Then merge:

```bash
cd "{skill_directory}/../github-monitor" && python scripts/merge_summaries.py \
  --batch-dir "{project_root}/workspaces/daily-digest" \
  --pattern "github_ai_summaries_batch_*.json" \
  -o "{project_root}/workspaces/daily-digest/github_ai_summaries.json"
```

#### 2c. Tool highlights (single sub-agent)

Only run if the tool source has `update_count > 0`. Release notes are already
human-readable, so there is no per-release summarization — just one overview
pass. **Sub-Agent Prompt Template**:

```
Task: Read tool release updates and write a brief Chinese-language highlights
note.

Read the file {project_root}/workspaces/tool-update-monitor/latest_updates.json.

Look at the `updates` array. Each entry has a tool name, version,
previous_version, published_at, and a `body` (release notes). Write:

1. `highlights`: 2-3 Chinese sentences. Which releases matter most? Any
   breaking changes or notable features? A brief "should you upgrade" hint.
2. `per_tool`: a map of tool_id -> ONE Chinese sentence (under 60 chars)
   summarizing that tool's update.

Write the results as JSON to:
{project_root}/workspaces/daily-digest/tool_ai_highlights.json

Use this exact structure:
{
  "highlights": "2-3 句中文总览……",
  "per_tool": {
    "v2rayn": "一句话要点",
    "claude-code": "一句话要点"
  }
}

CRITICAL - Encoding rules to avoid broken JSON:
- You MUST write the file using Python json.dump(), NOT bash heredoc/echo/cat.
- Do NOT use Chinese smart quotes (\u201c \u201d) in the text — use straight
  quotes (") or avoid quotes altogether.
- Use ensure_ascii=False and encoding="utf-8" when writing.
- Example:
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
```

#### 2d. Cross-source highlights (single sub-agent, optional)

For the unified digest's value-add, make one sub-agent pass over the merged
results to identify 3-5 cross-source highlights. Only run when at least two
sources have updates. **Sub-Agent Prompt Template**:

```
Task: Identify 3-5 cross-source highlights from today's updates.

Read these files (whichever exist):
- {project_root}/workspaces/daily-digest/rss_ai_summaries.json
- {project_root}/workspaces/daily-digest/github_ai_summaries.json
- {project_root}/workspaces/daily-digest/tool_ai_highlights.json

Across all sources, pick the 3-5 most interesting or noteworthy items/trends.
Write a numbered list in Chinese, each item ONE sentence (under 100 chars).
Prefer cross-cutting themes (e.g. an AI trend appearing in both RSS and
GitHub) over isolated items.

Write the results as JSON to:
{project_root}/workspaces/daily-digest/digest_highlights.json

Use this exact structure:
{
  "highlights": "1. ... 2. ... 3. ..."
}

CRITICAL - Encoding rules to avoid broken JSON:
- You MUST write the file using Python json.dump(), NOT bash heredoc/echo/cat.
- Do NOT use Chinese smart quotes (\u201c \u201d) in the text — use straight
  quotes (") or avoid quotes altogether.
- Use ensure_ascii=False and encoding="utf-8" when writing.
- Example:
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
```

### Step 3: Generate the unified digest report

```bash
cd "{skill_directory}" && python scripts/generate_unified_report.py \
  --rss-input   "{project_root}/workspaces/rss/latest_updates.json" \
  --rss-summaries "{project_root}/workspaces/daily-digest/rss_ai_summaries.json" \
  --github-input "{project_root}/workspaces/github-monitor/latest_updates.json" \
  --github-summaries "{project_root}/workspaces/daily-digest/github_ai_summaries.json" \
  --tool-input "{project_root}/workspaces/tool-update-monitor/latest_updates.json" \
  --tool-highlights "{project_root}/workspaces/daily-digest/tool_ai_highlights.json" \
  --digest-highlights "{project_root}/workspaces/daily-digest/digest_highlights.json" \
  -o "{project_root}/daily-digests/YYYY-MM-DD/daily-digest_HH-MM.md"
```

Replace `YYYY-MM-DD` with today's date (CST) and `HH-MM` with current time.
All flags except `-o` are optional — omit the ones for sources you did not
collect or summarize (e.g. omit `--tool-highlights` on a baseline run, or
omit `--digest-highlights` if Step 2d was skipped). A source with zero updates
is rendered as a short "no new updates" line rather than a full section.

Report structure: a single Markdown file with three top-level sections in a
fixed order — RSS 信息源 → GitHub 动态 → 工具更新 — each delegating to the
same grouping logic as the original per-source reports (WeChat category +
security sub-groups, tech categories + Hacker News, podcast by rank; GitHub
by repo with interleaved PR/issues; tools by category).

## Performance Notes

- Collection dominates runtime: RSS full scan ~2-4 min, GitHub ~1-2s/repo,
  tools ~5-10s. Reusing already-collected same-day `latest_updates.json`
  skips re-collection and cuts the run to the summarization + render time.
- AI summarization: RSS + GitHub batches at 4 sub-agents in parallel ~60s
  each; tool highlights ~10-20s; cross-source highlights ~10-20s.
- Total end-to-end (cold): ~4-6 minutes. Warm (data already collected):
  ~1-2 minutes.

## Completion

After generating the report, inform the user:
- Per-source counts (RSS / GitHub / tools), and whether any source had zero
  updates or was skipped
- The path to the generated unified report file
- A brief highlight: the 2-3 most interesting items across all sources (draw
  from the cross-source highlights if generated, otherwise pick the top items)
- Remind them they can re-run to refresh a single source by invoking that
  collector skill directly, then re-running this skill (it reuses cached data)
