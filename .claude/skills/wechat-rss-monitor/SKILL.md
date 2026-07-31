---
name: wechat-rss-monitor
description: |
  Monitor WeChat Official Account RSS feeds (via Wechat2RSS) for new articles
  and generate daily digest reports with AI-powered summaries.

  Use this skill whenever the user mentions anything related to WeChat article
  monitoring, WeChat RSS feeds, or WeChat article digests. This includes:
  - Checking what WeChat public accounts published recently
  - Generating WeChat article summary or digest reports
  - Monitoring wechat2rss for new articles
  - Any mention of 公众号/微信 combined with 更新/摘要/日报/监控/检查/最近
  - Questions about what's new on WeChat public accounts
  - Requests to check specific WeChat account categories (security, dev, etc.)

  Do NOT trigger for: creating/editing WeChat articles, managing WeChat
  accounts, non-Chinese content tasks, or general WeChat usage questions
  without update/monitoring context.
---

# WeChat RSS Monitor Skill

Monitor ~395 WeChat Official Account RSS feeds via Wechat2RSS
(https://wechat2rss.xlab.app/list/all) and generate AI-summarized daily
digest reports. Feeds are organized into 4 categories: Security (安全),
Development (开发), Other (其他), and User Submitted (用户提交).

## Architecture

```
.claude/skills/wechat-rss-monitor/
  SKILL.md                  # This file
  scripts/
    fetch_feed_list.py      # Fetch & parse feed list from GitHub
    check_updates.py        # Check RSS feeds for new articles
    fetch_articles.py       # [optional] Enrich articles with full content from URLs
    merge_summaries.py      # Merge per-batch AI summaries into one file
    generate_report.py      # Generate Markdown digest report
  references/
    feeds.json              # Cached feed list (~395 feeds, refreshed weekly)
                            # NOTE: feeds.json records its own metadata.fetch_time,
                            # which is the source of truth for cache freshness.

{project_root}/
  workspaces/wechat/         # Runtime intermediate files
    .http_cache.json
    latest_updates.json
    wechat_batch_N.json     # Batches for AI summarization
    ai_summaries_batch_N.json
    ai_summaries.json
  daily-digests/YYYY-MM-DD/ # Unified output directory
    wechat_HH-MM.md         # (times shown in CST / UTC+8)
```

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the path to
this `.claude/skills/wechat-rss-monitor/` directory and `{project_root}`
with the project root (`E:/Projects/AI/OpenClaw_Skills`).

### Step 0: Ensure workspace directories exist

```bash
mkdir -p "{project_root}/workspaces/wechat"
mkdir -p "{project_root}/daily-digests/$(date +%Y-%m-%d)"
```

### Step 1: Fetch/update the feed list

Only run if `references/feeds.json` is missing or older than 7 days. Freshness
is determined from `feeds.json`'s own `metadata.fetch_time` (the source of
truth), so no separate cache file is required.

```bash
cd "{skill_directory}" && python scripts/fetch_feed_list.py \
  --output "{skill_directory}/references/feeds.json"
```

Options:
- `--force` to force refresh regardless of cache age
- `--cache PATH` legacy sidecar cache file (optional; output freshness is
  read from feeds.json directly)

### Step 2: Check RSS feeds for new articles

```bash
cd "{skill_directory}" && python scripts/check_updates.py \
  --hours 24 --workers 10 \
  --output "{project_root}/workspaces/wechat/latest_updates.json" \
  --cache "{project_root}/workspaces/wechat/.http_cache.json"
```

Options:
- `--hours N` — time window in hours (default: 24)
- `--workers N` — concurrent workers (default: 10, all hit same domain)
- `--category NAME` — filter by category (安全/开发/其他/用户提交)
- `--count N` — limit number of feeds to check (0 = all)

After the script finishes, read the output JSON and check
`metadata.update_count`. If it is 0, stop and inform the user that no
new articles were found.

Watch the console output for a `date_unparsed_count` warning. A non-zero
count means some items had dates that could not be parsed and were dropped
— a high value usually signals wechat2rss changed its date format and
needs investigation (inspect `pub_date_raw` in the output JSON).

Each update now contains a `full_text` field with the complete article
text extracted from the RSS feed's `content:encoded`. For most articles
this is already sufficient for AI summarization.

### Step 2.5 (Optional): Enrich articles with full content

Rarely needed — the RSS `content:encoded` from wechat2rss is usually comprehensive. Only run if many articles have `full_text` under 200 characters. Attempts to fetch from mp.weixin.qq.com often fail due to anti-scraping measures.

```bash
cd "{skill_directory}" && python scripts/fetch_articles.py \
  -i "{project_root}/workspaces/wechat/latest_updates.json" \
  -o "{project_root}/workspaces/wechat/latest_updates.json" \
  --delay 2.0
```

Options:
- `--min-length N` — skip articles whose `full_text` already exceeds N chars (default: 500)
- `--delay N` — seconds between requests (default: 2.0, respect rate limits)
- `--max-articles N` — limit how many articles to fetch (default: 0 = all needing enrichment)

### Step 3: AI Summarization (parallel sub-agents)

Divide updates into **batches of 10**, create one sub-agent per batch.

**CRITICAL — avoid two common sub-agent failure modes:**
1. **Oversized input**: WeChat `full_text` can be thousands of chars per
   article. A 10-article batch with full text easily exceeds 50KB and causes
   sub-agent timeouts/context errors. Truncate `full_text` when preparing
   batches (Step 3a does this for you).
2. **Broken output JSON**: sub-agents that write JSON via bash
   `heredoc`/`echo`/`cat` produce malformed files (Chinese smart quotes,
   encoding errors). The prompt below forces `json.dump()`; and
   `merge_summaries.py` tolerates any corrupt batches instead of crashing.

**Launch at most 4 sub-agents in parallel** (API rate limiting). Process
batches group by group; do not launch more than 4 Agent tool calls in a
single message.

#### 3a. Prepare batch files (automated)

This creates the batch files deterministically and truncates `full_text` to
the first 1500 chars (enough for a one-sentence summary) so each batch stays
small. Run from the project root:

```bash
cd "{project_root}"
python -c "
import json
with open('workspaces/wechat/latest_updates.json','r',encoding='utf-8') as f:
    data=json.load(f)
updates=data['updates']
for i in range(0,len(updates),10):
    batch=[{'account_name':u['account_name'],'article_title':u['article_title'],
            'article_url':u['article_url'],
            'full_text':u.get('full_text','')[:1500]} for u in updates[i:i+10]]
    n=i//10
    with open(f'workspaces/wechat/wechat_batch_{n}.json','w',encoding='utf-8') as f:
        json.dump(batch,f,ensure_ascii=False)
print(f'Saved {(len(updates)+9)//10} batches')
"
```

#### 3b. Launch sub-agents (groups of 4)

Total batches = `ceil(update_count / 10)`. Divide into groups of 4. For
example, 12 batches → groups [0-3], [4-7], [8-11]. Launch each group in one
message (up to 4 Agent tool calls), wait for the group to finish, then
launch the next.

**Sub-Agent Prompt Template** (replace `{N}` with the batch index, and
`{project_root}` with the real path):

```
Task: Summarize WeChat articles into one Chinese sentence each.

Read the file {project_root}/workspaces/wechat/wechat_batch_{N}.json.

For each article, use the `full_text` field to write ONE concise Chinese
sentence (under 100 characters) capturing the key point or takeaway, so the
reader can decide whether to read the full article.

Write the results as JSON to:
{project_root}/workspaces/wechat/ai_summaries_batch_{N}.json

Use this exact structure:
{
  "summaries": [
    {
      "article_url": "the article_url from the input",
      "account_name": "...",
      "article_title": "...",
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

#### 3c. Merge batch summaries

After all sub-agents complete, merge the batch files into one
`ai_summaries.json`. `merge_summaries.py` skips any missing/corrupt batch
files with a warning (instead of crashing) and de-duplicates by article_url:

```bash
cd "{skill_directory}" && python scripts/merge_summaries.py \
  --batch-dir "{project_root}/workspaces/wechat" \
  -o "{project_root}/workspaces/wechat/ai_summaries.json"
```

If no batch files were produced (all sub-agents failed) it exits cleanly
and writes nothing — Step 4 will simply fall back to the raw description
text, so the pipeline never breaks.

If sub-agents are not available at all (e.g., in Claude.ai), skip Step 3
entirely. The report generator uses the raw description text as fallback.

### Step 4: Generate the digest report

```bash
cd "{skill_directory}" && python scripts/generate_report.py \
  -i "{project_root}/workspaces/wechat/latest_updates.json" \
  -s "{project_root}/workspaces/wechat/ai_summaries.json" \
  -o "{project_root}/daily-digests/YYYY-MM-DD/wechat_HH-MM.md"
```

Replace `YYYY-MM-DD` with today's date (CST) and `HH-MM` with current time.
Report timestamps are shown in CST (UTC+8) for the Chinese-reading audience.

Report grouping: articles are grouped by category (安全/开发/其他/用户提交).
Because 安全 dominates (~326 feeds), when it has more than 8 updates it is
further split into sub-groups (漏洞情报/CVE, 应急响应/攻防, 逆向/二进制,
移动/物联网, 安全工具/开发, 安全资讯/其他) by keyword for readability.

## Performance Notes

- Feed list fetch: ~1s (weekly cached)
- Full RSS scan (395 feeds, 10 workers): ~60-90s
- Subsequent scans (ETag cached): ~15-20s
- AI summarization (4 sub-agents): ~60s
- Total end-to-end: ~2-3 minutes

## Completion

After generating the report, inform the user:
- How many articles were found (and how many were skipped as trivial)
- Which categories had updates (with counts)
- The path to the generated report file
- A brief highlight: mention the 2-3 most interesting/important articles by title
- Suggest they can use `--category` to filter by specific category next time
