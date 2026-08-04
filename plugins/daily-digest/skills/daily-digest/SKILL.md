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
      rss_ai_summaries.json      # Merged RSS one-line summaries
      rss_articles_summaries.json  # Articles track (wechat + tech) split out
      rss_podcasts_summaries.json  # Podcasts track split out
      digest_narrative.json      # Editor narrative (overview + article_topics
                                 #   + podcast_topics + other)
  daily-digests/YYYY-MM-DD/        # Unified output directory
    daily-digest_HH-MM.md          # Narrative report (times in CST / UTC+8)
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

`GITHUB_ACCESS_TOKEN` is honored automatically (same env var as the GitHub
monitor above). With a token the 9 GitHub-Releases-sourced tools use the
5000/hour authenticated rate limit; without one concurrent checks can hit
`HTTP 403` rate-limit errors. Token is optional but recommended.

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

#### 2d. RSS editor narrative — articles & podcasts separately

The unified report renders each source by a style that fits its volume:

- **RSS** is high-volume (hundreds of items) and benefits from synthesis →
  rendered as a **narrative** produced by editor sub-agents. Within RSS,
  **articles** (WeChat + tech blogs) and **podcasts** are summarized as two
  INDEPENDENT narrative tracks — they never cross-reference each other.
- **GitHub** and **工具** are low-volume and inherently structured → rendered
  directly as a compact list by `generate_unified_report.py` from their
  `latest_updates.json` (+ their one-line AI summaries / key notes). They do
  NOT take part in the narrative.

##### 2d-0. Split RSS summaries into articles / podcasts

The merged `rss_ai_summaries.json` mixes all three RSS sources. Split it by
`source` (looked up from `workspaces/rss/latest_updates.json`) so each editor
sub-agent gets only its track. Run this inline Python:

```bash
python - <<'PY'
import json
from pathlib import Path

base = Path("{project_root}/workspaces")
lu = json.load(open(base / "rss/latest_updates.json", encoding="utf-8"))
url2src = {u.get("url", "").strip(): u.get("source", "")
           for u in lu.get("updates", [])}
summ = json.load(open(base / "daily-digest/rss_ai_summaries.json", encoding="utf-8"))

articles, podcasts = [], []
for e in summ.get("summaries", []):
    url = (e.get("url") or "").strip()
    rec = {"url": url, "ai_summary": e.get("ai_summary", ""),
           "category": e.get("category", ""), "source": url2src.get(url, "")}
    (podcasts if rec["source"] == "podcast" else articles).append(rec)

json.dump({"summaries": articles},
          open(base / "daily-digest/rss_articles_summaries.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
json.dump({"summaries": podcasts},
          open(base / "daily-digest/rss_podcasts_summaries.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(f"articles: {len(articles)}  podcasts: {len(podcasts)}")
PY
```

##### 2d-1. Articles editor sub-agent

Run when there are article (wechat/tech) updates. **Sub-Agent Prompt Template**:

```
Task: Act as the editor of today's ARTICLE digest (WeChat + tech blogs only;
NO podcasts). Read the merged one-line summaries and produce a NARRATIVE report.

Read this file:
- {project_root}/workspaces/daily-digest/rss_articles_summaries.json

Each entry is a one-line Chinese summary of an article. Your job:

1. Cluster the items by CONTENT THEME — NOT by source. Aim for 5-8 topics.
   Examples: "AI 应用安全攻防升温", "大模型竞速与价格战", "网络安全漏洞情报".

2. For each topic write a 2-4 sentence Chinese narrative that weaves the
   relevant items together. Prefer synthesis ("多条更新指向同一趋势…") over
   item-by-item listing.

   CRITICAL — citation style: DO NOT name media channels or outlets (no
   「微信公众号」「The New Stack」「VentureBeat」「量子位」etc.). State the
   CONTENT directly. You MAY keep names of people/organizations that ARE the
   actor or subject of the news (e.g. Anthropic, Mandiant 红队, 阿里, OpenAI,
   港科大) because they are who did the thing, not who reported it.
   - BAD:  「The New Stack」与「量子位」报道阿里发布 Qwen3.8-Max…
   - GOOD: 阿里发布 2.4 万亿参数 MoE 模型 Qwen3.8-Max…

3. Write `overview`: 2-3 Chinese sentences capturing today's overall tone
   and naming the ~3 main directions (articles only).

4. Write `other`: 1-2 Chinese sentences gathering remaining minor / one-off
   article items so NOTHING of substance is silently dropped.

5. In each topic's `items` array, record every article you referenced
   (url + short label). This is for audit only — it is NOT rendered.

Write the results as JSON to:
{project_root}/workspaces/daily-digest/digest_narrative_articles.json

Use this exact structure:
{
  "overview": "2-3 句中文总览（文章）……",
  "article_topics": [
    {
      "title": "话题标题",
      "narrative": "2-4 句中文叙事，直接讲内容，不提媒体来源……",
      "items": [
        {"source": "rss", "url": "...", "label": "Apache NiFi 四漏洞……"}
      ]
    }
  ],
  "other": "1-2 句：零碎文章内容归拢……"
}

CRITICAL - Coverage rule: every ARTICLE with real substance must appear in
some article_topics narrative (and its items[]) or be mentioned in `other`.

CRITICAL - Encoding rules to avoid broken JSON:
- You MUST write the file using Python json.dump(), NOT bash heredoc/echo/cat.
- Do NOT use Chinese smart quotes in the text — use straight quotes (").
- Use ensure_ascii=False and encoding="utf-8" when writing.
- Write to the articles-only file shown above (NOT the shared
  digest_narrative.json — the main flow merges the two tracks afterwards).
```

##### 2d-2. Podcasts editor sub-agent

Run when there are podcast updates. **Sub-Agent Prompt Template**:

```
Task: Act as the editor of today's PODCAST digest (podcasts only; NO articles).
Read the one-line summaries and produce a NARRATIVE report.

Read this file:
- {project_root}/workspaces/daily-digest/rss_podcasts_summaries.json

Each entry is a one-line Chinese summary of a podcast episode. Your job:

1. Cluster the episodes by CONTENT THEME — aim for 4-6 topics. Group by what
   the episodes are ABOUT (e.g. "AI 与创业对谈", "财经与市场", "文化与历史",
   "心理健康与生活"). Lifestyle / personal / true-crime episodes should be
   bundled very briefly into one "其他" note WITHOUT elaborating sensitive
   details — just acknowledge they exist.

2. For each topic write a 2-4 sentence Chinese narrative weaving the relevant
   episodes together. You MAY name the podcast/show when it identifies the
   conversation (e.g. 「某播客对谈……」), but focus on the CONTENT, not on
   who published it.

3. In each topic's `items` array, record every episode you referenced
   (url + short label). This is for audit only — it is NOT rendered.

Write the results as JSON to:
{project_root}/workspaces/daily-digest/digest_narrative_podcasts.json

Use this exact structure:
{
  "podcast_topics": [
    {
      "title": "话题标题",
      "narrative": "2-4 句中文叙事……",
      "items": [
        {"source": "rss", "url": "...", "label": "节目名 · 主题……"}
      ]
    }
  ]
}

CRITICAL - Encoding rules to avoid broken JSON:
- You MUST write the file using Python json.dump(), NOT bash heredoc/echo/cat.
- Do NOT use Chinese smart quotes in the text — use straight quotes (").
- Use ensure_ascii=False and encoding="utf-8" when writing.
- Write to the podcasts-only file shown above (NOT the shared
  digest_narrative.json — the main flow merges the two tracks afterwards).
```

Run 2d-1 and 2d-2 as two parallel sub-agents. They write to SEPARATE files
(`digest_narrative_articles.json` and `digest_narrative_podcasts.json`) to
avoid any write race. The main flow then merges them into the single
`digest_narrative.json` consumed by Step 3:

```bash
python - <<'PY'
import json
from pathlib import Path
base = Path("{project_root}/workspaces/daily-digest")
merged = {"overview": "", "article_topics": [], "podcast_topics": [], "other": ""}
for name in ("digest_narrative_articles.json", "digest_narrative_podcasts.json"):
    p = base / name
    if p.exists():
        merged.update(json.load(open(p, encoding="utf-8")))
json.dump(merged, open(base / "digest_narrative.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("merged ->", base / "digest_narrative.json")
PY
```

Note: the legacy `digest_highlights.json` (3-5 one-line highlights) is no
longer produced. If one from an older run is present, the report generator
will still use it as a *fallback overview* when `digest_narrative.json` is
missing — but you should produce `digest_narrative.json` going forward.

### Step 3: Generate the unified digest report

```bash
cd "{skill_directory}" && python scripts/generate_unified_report.py \
  --rss-input   "{project_root}/workspaces/rss/latest_updates.json" \
  --digest-narrative "{project_root}/workspaces/daily-digest/digest_narrative.json" \
  --github-input "{project_root}/workspaces/github-monitor/latest_updates.json" \
  --github-summaries "{project_root}/workspaces/daily-digest/github_ai_summaries.json" \
  --tool-input "{project_root}/workspaces/tool-update-monitor/latest_updates.json" \
  --tool-highlights "{project_root}/workspaces/daily-digest/tool_ai_highlights.json" \
  -o "{project_root}/daily-digests/YYYY-MM-DD/daily-digest_HH-MM.md"
```

Replace `YYYY-MM-DD` with today's date (CST) and `HH-MM` with current time.
All flags except `-o` are optional:

- `--rss-input` + `--digest-narrative` drive the **narrative** part (overview
  + 今日重点 + 其他动态). Omit `--digest-narrative` only if Step 2d was
  skipped/failed (the overview then degrades to a fallback string).
- `--github-input` + `--github-summaries` drive the **GitHub compact list**
  (one tight block per PR/issue, with its one-line AI summary when present).
- `--tool-input` + `--tool-highlights` drive the **tool compact list**
  (one tight block per release, with the per-tool key note when present).
- Optional fallback flags (only consulted when `--digest-narrative` is
  missing or empty): `--digest-highlights` (legacy), `--tool-highlights`, and
  `--rss-insight` are stitched into a best-effort overview so the pipeline
  never hard-fails on a summarization gap.

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

Articles and podcasts are two independent narrative tracks — they never
cross-reference. RSS has no per-item blocks (it's high-volume → synthesized).
GitHub and tools are listed in full because they're low-volume and structured.
The full structured data for every source remains in
`workspaces/<source>/latest_updates.json` for on-demand follow-up — if the
user wants detail on a specific RSS item, they can ask the agent to search
the workspace data.

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
