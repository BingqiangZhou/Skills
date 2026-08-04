---
name: daily-digest
version: "1.1.3"
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
  ├─ rss-monitor          → collects → workspaces/daily-digests/daily-digests/rss/latest_updates.json
  ├─ github-monitor       → collects → workspaces/daily-digests/daily-digests/github-monitor/latest_updates.json
  └─ tool-update-monitor  → collects → workspaces/daily-digests/daily-digests/tool-update-monitor/latest_updates.json
```

The three collector skills are kept intentionally simple: each only fetches
data and saves it. Summarization and report rendering live here, so the
collection logic stays reusable and the report logic stays centralized.

## Architecture

```
{skill_directory}/                  # This skill's directory (inside the plugin)
  SKILL.md                        # This file — workflow & orchestration
  references/
    subagent-prompts.md           # Per-source summarization prompts (2a/2b/2c)
    editor-prompts.md             # Article & podcast editor prompts (2d)
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
  workspaces/daily-digests/        # Whole pipeline: middleware + reports
    daily-digest/                # This skill's intermediate files
      *_batch_N.json             # Batches for AI summarization
      ai_summaries_batch_N.json  # Sub-agent outputs (per source)
      rss_ai_summaries.json      # Merged RSS one-line summaries
      rss_articles_summaries.json  # Articles track (wechat + tech) split out
      rss_podcasts_summaries.json  # Podcasts track split out
      digest_narrative.json      # Editor narrative (overview + article_topics
                                 #   + podcast_topics + other)
    reports/YYYY-MM-DD/          # Unified report output
      daily-digest_HH-MM.md      # Narrative report (times in CST / UTC+8)
```

The prompt templates for sub-agents live in `references/`. Read the relevant
file before launching that step's sub-agents — SKILL.md tells you when to read
which one.

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the absolute
path to THIS skill's directory, `{project_root}` with the user's current
project (the working directory at run time). The sibling skill directories are
at `{skill_directory}/../rss-monitor`, `/../github-monitor`, and
`/../tool-update-monitor`.

### Step 0: Ensure workspace directories exist

```bash
mkdir -p "{project_root}/workspaces/daily-digests/daily-digest"
mkdir -p "{project_root}/workspaces/daily-digests/reports/$(date +%Y-%m-%d)"
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
  --output "{project_root}/workspaces/daily-digests/daily-digests/rss/latest_updates.json" \
  --cache "{project_root}/workspaces/daily-digests/daily-digests/rss/.http_cache.json"

# [Podcast] Resolve non-Xiaoyuzhou episode URLs to canonical links (in place)
cd "{skill_directory}/../rss-monitor" && python scripts/resolve_xiaoyuzhou_urls.py \
  -i "{project_root}/workspaces/daily-digests/daily-digests/rss/latest_updates.json"

# [WeChat, optional] Enrich short articles — only if metadata.sources.wechat.short_text_count != 0
cd "{skill_directory}/../rss-monitor" && python scripts/fetch_articles.py \
  -i "{project_root}/workspaces/daily-digests/daily-digests/rss/latest_updates.json" \
  -o "{project_root}/workspaces/daily-digests/daily-digests/rss/latest_updates.json" --delay 2.0
```

See the rss-monitor SKILL.md for full flag details. After this step, read
`workspaces/daily-digests/daily-digests/rss/latest_updates.json` and check `metadata.update_count`.

#### 1b. GitHub collection (merged PRs + new issues)

```bash
cd "{skill_directory}/../github-monitor" && python scripts/check_updates.py \
  --hours 24 \
  --output "{project_root}/workspaces/daily-digests/daily-digests/github-monitor/latest_updates.json" \
  --cache  "{project_root}/workspaces/daily-digests/daily-digests/github-monitor/.http_cache.json"
```

`GITHUB_ACCESS_TOKEN` is honored automatically if set. After this step, read
`workspaces/daily-digests/daily-digests/github-monitor/latest_updates.json` and check
`metadata.update_count`.

#### 1c. Tool collection (new releases)

```bash
cd "{skill_directory}/../tool-update-monitor" && python scripts/check_updates.py \
  --hours 168 --workers 6 \
  --output "{project_root}/workspaces/daily-digests/daily-digests/tool-update-monitor/latest_updates.json" \
  --cache "{project_root}/workspaces/daily-digests/daily-digests/tool-update-monitor/.http_cache.json" \
  --state "{project_root}/workspaces/daily-digests/daily-digests/tool-update-monitor/.last_seen.json"
```

`GITHUB_ACCESS_TOKEN` is honored automatically (same env var as the GitHub
monitor above). With a token the 9 GitHub-Releases-sourced tools use the
5000/hour authenticated rate limit; without one concurrent checks can hit
`HTTP 403` rate-limit errors. Token is optional but recommended.

After this step, read `workspaces/daily-digests/daily-digests/tool-update-monitor/latest_updates.json`.
If `metadata.baseline_run` is true or `metadata.update_count` is 0, there are
no new releases — the tool section of the report will be skipped/empty.

### Step 2: AI Summarization (parallel sub-agents)

If ALL three sources have zero updates, stop and inform the user that nothing
new was found today.

**Avoid two common sub-agent failure modes:**
1. **Oversized input**: WeChat `full_text`, podcast `shownotes`, and GitHub
   `body_text` can be thousands of chars per item. `prepare_batches.py`
   truncates text fields so each batch stays small.
2. **Broken output JSON**: sub-agents that write JSON via bash
   `heredoc`/`echo`/`cat` produce malformed files. The prompts force
   `json.dump()`, and the sibling `merge_summaries.py` scripts tolerate any
   corrupt batches instead of crashing.

**Launch at most 4 sub-agents in parallel** (API rate limiting).

#### 2a. RSS summarization

Prepare batches (truncates `full_text`/`shownotes` to 1500 chars, groups by
source):

```bash
cd "{skill_directory}" && python scripts/prepare_batches.py \
  --source rss \
  --input "{project_root}/workspaces/daily-digests/daily-digests/rss/latest_updates.json" \
  --output-dir "{project_root}/workspaces/daily-digests/daily-digest" \
  --prefix "rss"
```

Launch one sub-agent per batch (groups of 4). **Read
`references/subagent-prompts.md` § "2a" for the exact prompt template.** Each
sub-agent reads a `rss_batch_{N}.json` and writes
`rss_ai_summaries_batch_{N}.json`.

Then merge the batches:

```bash
cd "{skill_directory}/../rss-monitor" && python scripts/merge_summaries.py \
  --batch-dir "{project_root}/workspaces/daily-digests/daily-digest" \
  --pattern "rss_ai_summaries_batch_*.json" \
  -o "{project_root}/workspaces/daily-digests/daily-digest/rss_ai_summaries.json"
```

#### 2b. GitHub summarization

```bash
cd "{skill_directory}" && python scripts/prepare_batches.py \
  --source github \
  --input "{project_root}/workspaces/daily-digests/daily-digests/github-monitor/latest_updates.json" \
  --output-dir "{project_root}/workspaces/daily-digests/daily-digest" \
  --prefix "github"
```

Launch one sub-agent per batch (groups of 4). **Read
`references/subagent-prompts.md` § "2b" for the exact prompt template.** Each
sub-agent reads a `github_batch_{N}.json` and writes
`github_ai_summaries_batch_{N}.json`.

Then merge:

```bash
cd "{skill_directory}/../github-monitor" && python scripts/merge_summaries.py \
  --batch-dir "{project_root}/workspaces/daily-digests/daily-digest" \
  --pattern "github_ai_summaries_batch_*.json" \
  -o "{project_root}/workspaces/daily-digests/daily-digest/github_ai_summaries.json"
```

#### 2c. Tool highlights (single sub-agent)

Only run if the tool source has `update_count > 0`. Release notes are already
human-readable, so there is no per-release summarization — just one overview
pass. **Read `references/subagent-prompts.md` § "2c" for the prompt.** The
sub-agent reads `latest_updates.json` and writes `tool_ai_highlights.json`.

#### 2d. RSS editor narrative — articles & podcasts separately

The report renders RSS as two **independent** narrative tracks (articles and
podcasts never cross-reference); GitHub and tools are rendered directly as
compact lists by Step 3, so they do NOT take part in the narrative.

**Read `references/editor-prompts.md` in full before running this step** — it
contains the split script, both editor prompts, and the merge script. The
flow is:

1. **Split** `rss_ai_summaries.json` into `rss_articles_summaries.json` and
   `rss_podcasts_summaries.json` (inline Python in §2d-0).
2. **Two parallel sub-agents** (§2d-1 articles editor, §2d-2 podcasts editor):
   - Articles editor → `digest_narrative_articles.json`
     (`overview` + `article_topics` + `other`; **no media-channel names**, only
     the actors/subjects of the news; clusters 5-8 content themes).
   - Podcasts editor → `digest_narrative_podcasts.json`
     (`podcast_topics`; clusters 4-6 themes; bundles lifestyle/crime episodes
     briefly without sensitive detail).
3. **Merge** both into `digest_narrative.json` (inline Python in §merge).

### Step 3: Generate the unified digest report

```bash
cd "{skill_directory}" && python scripts/generate_unified_report.py \
  --rss-input   "{project_root}/workspaces/daily-digests/daily-digests/rss/latest_updates.json" \
  --digest-narrative "{project_root}/workspaces/daily-digests/daily-digest/digest_narrative.json" \
  --github-input "{project_root}/workspaces/daily-digests/daily-digests/github-monitor/latest_updates.json" \
  --github-summaries "{project_root}/workspaces/daily-digests/daily-digest/github_ai_summaries.json" \
  --tool-input "{project_root}/workspaces/daily-digests/daily-digests/tool-update-monitor/latest_updates.json" \
  --tool-highlights "{project_root}/workspaces/daily-digests/daily-digest/tool_ai_highlights.json" \
  -o "{project_root}/workspaces/daily-digests/reports/YYYY-MM-DD/daily-digest_HH-MM.md"
```

Replace `YYYY-MM-DD` with today's date (CST) and `HH-MM` with current time.
All flags except `-o` are optional:

- `--rss-input` + `--digest-narrative` drive the **narrative** part (overview
  + 今日重点 + 播客精选 + 其他动态). Omit `--digest-narrative` only if Step 2d
  was skipped/failed (the overview then degrades to a fallback string).
- `--github-input` + `--github-summaries` drive the **GitHub compact list**.
- `--tool-input` + `--tool-highlights` drive the **tool compact list**.
- Optional fallback flags (only consulted when `--digest-narrative` is missing):
  `--digest-highlights` (legacy), `--tool-highlights`, `--rss-insight` are
  stitched into a best-effort overview so the pipeline never hard-fails.

Report structure — narrative for RSS (articles & podcasts separate), compact
lists for GitHub & tools:

```
# 每日信息摘要 - <time> (CST)
> RSS: N 条 | GitHub: M 条 | 工具: K 个新版本

## 📊 今日概览          ← articles editor overview (or fallback)
## 🔥 今日重点          ← 5-8 ARTICLE topic narratives (no media-channel names)
## 🎧 播客精选          ← 4-6 PODCAST topic narratives (independent track)
## 📌 其他动态          ← minor articles roundup (omitted if empty)
## 🔧 GitHub 动态       ← compact list of PRs/issues (grouped by repo)
## 🛠 工具更新           ← compact list of releases (grouped by category)
```

RSS has no per-item blocks (it's high-volume → synthesized). GitHub and tools
are listed in full because they're low-volume and structured. The full
structured data for every source remains in
`workspaces/<source>/latest_updates.json` for on-demand follow-up.

## Performance Notes

- Collection dominates runtime: RSS full scan ~2-4 min, GitHub ~1-2s/repo,
  tools ~5-10s. Reusing already-collected same-day `latest_updates.json`
  skips re-collection and cuts the run to the summarization + render time.
- AI summarization: RSS + GitHub batches at 4 sub-agents in parallel ~60s
  each; tool highlights ~10-20s; editor narratives ~60-120s each.
- Total end-to-end (cold): ~4-6 minutes. Warm (data already collected):
  ~1-2 minutes.

## Completion

After generating the report, inform the user:
- Per-source counts (RSS / GitHub / tools), and whether any source had zero
  updates or was skipped
- The path to the generated unified report file
- A brief highlight: the 2-3 most interesting items across all sources (draw
  from the narrative overview if generated, otherwise pick the top items)
- Remind them they can re-run to refresh a single source by invoking that
  collector skill directly, then re-running this skill (it reuses cached data)
