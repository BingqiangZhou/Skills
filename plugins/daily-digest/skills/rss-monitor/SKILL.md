---
name: rss-monitor
description: |
  Monitor RSS information sources (WeChat Official Accounts, tech blogs,
  Chinese podcasts) and generate a unified daily digest report with
  AI-powered summaries.

  Use this skill whenever the user mentions monitoring RSS feeds, WeChat
  articles, tech news, or podcasts for updates/digests. Triggers include:
  - 公众号/微信 + 更新/摘要/日报/监控/检查/最近
  - 科技/AI + 日报/新闻/动态/科技日报
  - 播客 + 更新/摘要/日报/监控
  - "看看最近有什么更新" / "信息源日报" / "RSS监控"
  - Questions about what's new across WeChat / tech blogs / podcasts
  - Any combination of the above sources

  Do NOT trigger for: creating/editing articles or podcast audio, managing
  accounts, non-Chinese content tasks, or general usage questions without
  update/monitoring context.
---

# RSS Monitor Skill

Monitor multiple RSS information sources and generate AI-summarized daily
digest reports in a single unified Markdown file. Three sources are supported:

| Source | Feeds | Content |
|--------|-------|---------|
| **WeChat** (微信公众号) | ~395 via Wechat2RSS | Security (安全), Dev (开发), Other (其他), User-submitted (用户提交) |
| **Tech** (科技博客) | ~24 RSS + 2 Hacker News | AI/ML, 芯片硬件, 云计算, 开源, 网络安全, 综合科技 |
| **Podcast** (播客) | ~1000 (RSS + Xiaoyuzhou) | Chinese podcasts ranked by xyzrank.com |

## Architecture

```
{skill_directory}/                   # This skill's directory (inside the plugin)
  SKILL.md                         # This file
  scripts/
    _common.py                     # Shared HTTP/RSS/HTML/cache helpers
    check_updates.py               # Unified checker: --source wechat|tech|podcast|all
    merge_summaries.py             # Merge per-batch AI summaries into one file
    generate_report.py             # Generate unified Markdown digest (by source section)
    fetch_feed_list.py             # [WeChat] Fetch feed list from GitHub (weekly cache)
    fetch_articles.py              # [WeChat, optional] Enrich short articles with full content
    resolve_xiaoyuzhou_urls.py     # [Podcast] Resolve non-XYZ episode URLs to XYZ links
  references/
    feeds_wechat.json              # WeChat feed list (~395, refreshed weekly)
    feeds_tech.json                # Tech blog feed list (24 RSS + 2 HN, static)
    podcasts.json                  # Podcast list (~1000, from xyzrank.com)

{project_root}/                    # The user's current project (cwd at run time)
  workspaces/rss/                  # Runtime intermediate files
    .http_cache.json
    latest_updates.json
    *_batch_N.json                 # Batches for AI summarization (per source)
    ai_summaries_batch_N.json
    ai_summaries.json
    trend_insight.json             # [optional] Tech trend insight
  daily-digests/YYYY-MM-DD/        # Unified output directory
    rss_HH-MM.md                   # (times shown in CST / UTC+8)
```

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the absolute
path to this skill's directory and `{project_root}` with the user's current
project (the working directory at run time).

### Step 0: Ensure workspace directories exist

```bash
mkdir -p "{project_root}/workspaces/rss"
mkdir -p "{project_root}/daily-digests/$(date +%Y-%m-%d)"
```

### Step 1: [WeChat] Refresh the feed list (only if stale)

Only run if `references/feeds_wechat.json` is missing or older than 7 days.
Freshness is determined from `feeds_wechat.json`'s own `metadata.fetch_time`.

```bash
cd "{skill_directory}" && python scripts/fetch_feed_list.py \
  --output "{skill_directory}/references/feeds_wechat.json"
```

Options: `--force` to force refresh regardless of cache age.

### Step 2: Check all sources for new content

```bash
cd "{skill_directory}" && python scripts/check_updates.py \
  --source all --hours 24 \
  --output "{project_root}/workspaces/rss/latest_updates.json" \
  --cache "{project_root}/workspaces/rss/.http_cache.json"
```

Options:
- `--source wechat|tech|podcast|all` — which source(s) to check (default: all)
- `--hours N` — time window in hours (default: 24)
- `--category NAME` — filter by category (source-specific)
- `--count N` — limit number of feeds to check (0 = all)
- `--workers N` — concurrent workers per source (0 = per-source defaults)

After the script finishes, read the output JSON and check
`metadata.update_count`. If it is 0, stop and inform the user that no new
content was found.

Watch for these console signals:
- `date_unparsed` warnings — a high count may signal a feed source changed
  its date format and needs investigation.
- `Removed N cross-account reposts` — wechat title dedup removed reposted
  articles (normal, reduces noise).
- `NOTE: N articles have short full_text` — see Step 4 below.

### Step 3: [Podcast] Resolve Xiaoyuzhou episode URLs

Resolves non-Xiaoyuzhou podcast episode URLs to canonical
`xiaoyuzhoufm.com/episode/{eid}` links. Only affects podcast-source updates.

```bash
cd "{skill_directory}" && python scripts/resolve_xiaoyuzhou_urls.py \
  -i "{project_root}/workspaces/rss/latest_updates.json"
```

(This overwrites the input file in place.)

### Step 4: [WeChat, Optional] Enrich articles with full content

Rarely needed — the RSS `content:encoded` from wechat2rss is usually
comprehensive. Check `metadata.sources.wechat.short_text_count` in the output
JSON — if it is non-zero, run this step to fetch full article content.

```bash
cd "{skill_directory}" && python scripts/fetch_articles.py \
  -i "{project_root}/workspaces/rss/latest_updates.json" \
  -o "{project_root}/workspaces/rss/latest_updates.json" \
  --delay 2.0
```

Options:
- `--min-length N` — skip articles whose `full_text` already exceeds N chars (default: 500)
- `--delay N` — seconds between requests (default: 2.0)
- `--max-articles N` — limit how many articles to fetch (default: 0 = all)

### Step 5: AI Summarization (parallel sub-agents)

Divide updates into **batches of 10 per source**, create one sub-agent per
batch.

**CRITICAL — avoid two common sub-agent failure modes:**
1. **Oversized input**: WeChat `full_text` and podcast `shownotes` can be
   thousands of chars per article. Truncate text fields when preparing batches
   (Step 5a does this for you).
2. **Broken output JSON**: sub-agents that write JSON via bash
   `heredoc`/`echo`/`cat` produce malformed files (Chinese smart quotes,
   encoding errors). The prompt below forces `json.dump()`; and
   `merge_summaries.py` tolerates any corrupt batches instead of crashing.

**Launch at most 4 sub-agents in parallel** (API rate limiting).

#### 5a. Prepare batch files (automated)

Creates batch files per source, truncating text fields so each batch stays
small. Run from the project root:

```bash
cd "{project_root}"
python -c "
import json
with open('workspaces/rss/latest_updates.json','r',encoding='utf-8') as f:
    data=json.load(f)
updates=data['updates']
# Group by source
from collections import defaultdict
by_source=defaultdict(list)
for u in updates:
    by_source[u.get('source','other')].append(u)
batch_idx=0
for source, items in by_source.items():
    for i in range(0,len(items),10):
        chunk=items[i:i+10]
        batch=[]
        for u in chunk:
            text=u.get('full_text','') or u.get('shownotes','')
            entry={'source':source,'url':u.get('url',''),'title':u.get('title','')}
            if source=='wechat':
                entry['account_name']=u.get('account_name','')
            elif source=='podcast':
                entry['podcast_name']=u.get('podcast_name','')
            entry['full_text']=text[:1500]
            batch.append(entry)
        with open(f'workspaces/rss/{source}_batch_{batch_idx}.json','w',encoding='utf-8') as f:
            json.dump(batch,f,ensure_ascii=False)
        batch_idx+=1
print(f'Saved {batch_idx} batches across {len(by_source)} sources')
"
```

#### 5b. Launch sub-agents (groups of 4)

Total batches = count from Step 5a output. Divide into groups of 4. For
example, 12 batches → groups [0-3], [4-7], [8-11]. Launch each group in one
message (up to 4 Agent tool calls), wait for the group to finish, then
launch the next.

**Sub-Agent Prompt Template** (replace `{batch_file}` and `{N}`):

```
Task: Summarize RSS articles into one Chinese sentence each.

Read the file {project_root}/workspaces/rss/{batch_file}.

For each item, use the `full_text` field to write ONE concise Chinese
sentence (under 100 characters) capturing the key point or takeaway, so the
reader can decide whether to read the full article.

Write the results as JSON to:
{project_root}/workspaces/rss/ai_summaries_batch_{N}.json

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

#### 5c. Merge batch summaries

After all sub-agents complete, merge the batch files into one
`ai_summaries.json`. `merge_summaries.py` skips any missing/corrupt batch
files with a warning (instead of crashing) and de-duplicates by URL:

```bash
cd "{skill_directory}" && python scripts/merge_summaries.py \
  --batch-dir "{project_root}/workspaces/rss" \
  -o "{project_root}/workspaces/rss/ai_summaries.json"
```

If no batch files were produced (all sub-agents failed) it exits cleanly
and writes nothing — Step 6 will simply fall back to the raw description
text, so the pipeline never breaks.

### Step 5.5: [Tech, Optional] Trend Insight

For the tech source, optionally generate a trend insight via a single
sub-agent that reads `ai_summaries.json`, identifies 3-5 trends, and writes
`trend_insight.json`:

```
Task: Identify 3-5 key tech trends from today's AI summaries.

Read {project_root}/workspaces/rss/ai_summaries.json.

Identify 3-5 notable trends or themes across the articles. Write them as
a numbered list in Chinese.

Write to {project_root}/workspaces/rss/trend_insight.json:
{
  "trend_insight": "1. ... 2. ... 3. ..."
}

Use Python json.dump() with ensure_ascii=False, encoding="utf-8".
```

### Step 6: Generate the unified digest report

```bash
cd "{skill_directory}" && python scripts/generate_report.py \
  -i "{project_root}/workspaces/rss/latest_updates.json" \
  -s "{project_root}/workspaces/rss/ai_summaries.json" \
  --insight "{project_root}/workspaces/rss/trend_insight.json" \
  -o "{project_root}/daily-digests/YYYY-MM-DD/rss_HH-MM.md"
```

Replace `YYYY-MM-DD` with today's date (CST) and `HH-MM` with current time.
Omit `--insight` if no trend insight was generated. Report timestamps are
shown in CST (UTC+8).

Report structure: the report is organized by source section (微信公众号 →
科技博客 → 播客), each preserving its original grouping logic:
- **WeChat**: grouped by category (安全/开发/其他/用户提交); 安全 split into
  sub-groups (漏洞情报/CVE, 应急响应/攻防, etc.) when >8 updates.
- **Tech**: grouped by category (AI/ML, 芯片硬件, etc.) + Hacker News section.
- **Podcast**: flat list sorted by rank.

## Performance Notes

- WeChat feed list fetch: ~1s (weekly cached)
- Full scan (395 wechat + 26 tech + 1000 podcast, concurrent): ~2-4 min
- Subsequent scans (ETag cached): ~30-60s
- AI summarization (4 sub-agents): ~60s
- Total end-to-end: ~3-5 minutes

## Completion

After generating the report, inform the user:
- How many items were found total, and per source (wechat/tech/podcast)
- The path to the generated report file
- A brief highlight: mention the 2-3 most interesting items by title
- Suggest they can use `--source wechat|tech|podcast` to check only one source,
  or `--category NAME` to filter by specific category
