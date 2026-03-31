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
    fetch_articles.py       # Enrich articles with full content from URLs
    generate_report.py      # Generate Markdown digest report
  references/
    feeds.json              # Cached feed list (~395 feeds, refreshed weekly)

{project_root}/
  wechat-workspace/         # Runtime intermediate files
    .feed_list_cache.json
    .http_cache.json
    latest_updates.json
    wechat_batch_N.json     # Batches for AI summarization
    ai_summaries_batch_N.json
    ai_summaries.json
  wechat-digests/           # Final output reports
    YYYY-MM-DD_HH-MM.md
```

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the path to
this `.claude/skills/wechat-rss-monitor/` directory and `{project_root}`
with the project root (`E:/Projects/AI/OpenClaw_Skills`).

### Step 0: Ensure workspace directories exist

```bash
mkdir -p "{project_root}/wechat-workspace"
mkdir -p "{project_root}/wechat-digests"
```

### Step 1: Fetch/update the feed list

Only run if `references/feeds.json` is missing or older than 7 days.

```bash
cd "{skill_directory}" && python scripts/fetch_feed_list.py \
  --output "{skill_directory}/references/feeds.json" \
  --cache "{project_root}/wechat-workspace/.feed_list_cache.json"
```

Options:
- `--force` to force refresh regardless of cache age

### Step 2: Check RSS feeds for new articles

```bash
cd "{skill_directory}" && python scripts/check_updates.py \
  --hours 24 --workers 10 \
  --output "{project_root}/wechat-workspace/latest_updates.json" \
  --cache "{project_root}/wechat-workspace/.http_cache.json"
```

Options:
- `--hours N` — time window in hours (default: 24)
- `--workers N` — concurrent workers (default: 10, all hit same domain)
- `--category NAME` — filter by category (安全/开发/其他/用户提交)
- `--count N` — limit number of feeds to check (0 = all)

After the script finishes, read the output JSON and check
`metadata.update_count`. If it is 0, stop and inform the user that no
new articles were found.

Each update now contains a `full_text` field with the complete article
text extracted from the RSS feed's `content:encoded`. For most articles
this is already sufficient for AI summarization.

### Step 2.5 (Optional): Enrich articles with full content

Rarely needed — the RSS `content:encoded` from wechat2rss is usually comprehensive. Only run if many articles have `full_text` under 200 characters. Attempts to fetch from mp.weixin.qq.com often fail due to anti-scraping measures.

```bash
cd "{skill_directory}" && python scripts/fetch_articles.py \
  -i "{project_root}/wechat-workspace/latest_updates.json" \
  -o "{project_root}/wechat-workspace/latest_updates.json" \
  --delay 2.0
```

Options:
- `--min-length N` — skip articles whose `full_text` already exceeds N chars (default: 500)
- `--delay N` — seconds between requests (default: 2.0, respect rate limits)
- `--max-articles N` — limit how many articles to fetch (default: 0 = all needing enrichment)

### Step 3: AI Summarization (parallel sub-agents)

Split the updates into batches of ~10 articles each. Launch up to 4
sub-agents concurrently using the Agent tool.

For each batch:

1. Write a batch file to `wechat-workspace/wechat_batch_N.json` containing
   the batch's updates array from `latest_updates.json`.

2. Launch a sub-agent with this prompt:

```
You are summarizing WeChat articles for a daily digest. Read the file
{project_root}/wechat-workspace/wechat_batch_{N}.json

For each article, use the `full_text` field (which contains the complete
article content extracted from RSS content:encoded) to write a ONE-SENTENCE
Chinese summary (under 100 characters) that captures the key point or
takeaway. The summary should be informative and help the reader decide
whether to read the full article.

If `full_text` is empty or very short, fall back to the `summary_text` field.

Output a JSON file at {project_root}/wechat-workspace/ai_summaries_batch_{N}.json
with this exact structure:
{
  "summaries": [
    {
      "article_url": "the article_url from the input",
      "account_name": "...",
      "article_title": "...",
      "ai_summary": "one-sentence Chinese summary"
    }
  ]
}
```

3. After all sub-agents complete, merge all batch summary files into a
   single `wechat-workspace/ai_summaries.json`:
```json
{
  "summaries": [/* all entries from all batches */]
}
```

If sub-agents are not available (e.g., in Claude.ai), skip this step.
The report generator will use the raw description text as fallback.

### Step 4: Generate the digest report

```bash
cd "{skill_directory}" && python scripts/generate_report.py \
  -i "{project_root}/wechat-workspace/latest_updates.json" \
  -s "{project_root}/wechat-workspace/ai_summaries.json" \
  -o "{project_root}/wechat-digests/{timestamp}.md"
```

The `{timestamp}` should be in `YYYY-MM-DD_HH-MM` format based on current time.

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
