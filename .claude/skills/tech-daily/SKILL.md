---
name: tech-daily
description: |
  Monitor tech news from curated RSS feeds (English + Chinese) and Hacker News,
  generating AI-powered daily digest reports with trend insights.

  Use this skill whenever the user mentions anything related to tech news
  monitoring, AI tech daily reports, or technology RSS digest generation. This includes:
  - Generating AI tech daily reports or digests
  - Monitoring tech news RSS feeds
  - Checking Hacker News for trending topics
  - Any mention of 科技日报/AI日报/科技新闻/tech news combined with 日报/摘要/监控/检查/生成
  - Questions about what's new in AI/tech
  - Requests to check specific tech categories (AI/ML, cloud, security, etc.)

  Do NOT trigger for: WeChat article monitoring (use wechat-rss-monitor),
  podcast monitoring (use podcast-rss-monitor), or general coding questions
  without tech news context.
---

# Tech Daily Report Skill

Monitor ~25 curated tech RSS feeds (English + Chinese sources) and Hacker News
(via hnrss.org) for new articles, then generate AI-summarized daily digest
reports with trend insights.

Sources cover: AI/ML, chips/hardware, cloud computing, open source,
cybersecurity, and general tech. Output is a Chinese-language Markdown report.

## Architecture

```
.claude/skills/tech-daily/
  SKILL.md                  # This file
  scripts/
    check_updates.py        # Fetch RSS feeds + HN, filter, deduplicate
    generate_report.py      # Generate categorized Markdown report
  references/
    feeds.json              # Curated feed list (~25 RSS + 2 HN feeds)

{project_root}/
  tech-daily-workspace/     # Runtime intermediate files
    .http_cache.json
    latest_updates.json
    tech_daily_batch_N.json      # Batches for AI summarization
    ai_summaries_batch_N.json
    ai_summaries.json
    trend_insight.json
  daily-digests/YYYY-MM-DD/ # Unified output directory
    tech-daily_HH-MM.md
```

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the path to
this `.claude/skills/tech-daily/` directory and `{project_root}`
with the project root (`E:/Projects/AI/OpenClaw_Skills`).

### Step 0: Ensure workspace directories exist

```bash
mkdir -p "{project_root}/tech-daily-workspace"
mkdir -p "{project_root}/daily-digests/$(date +%Y-%m-%d)"
```

### Step 1: Check RSS feeds and HN for new articles

```bash
cd "{skill_directory}" && python scripts/check_updates.py \
  --hours 24 --workers 20 \
  --output "{project_root}/tech-daily-workspace/latest_updates.json" \
  --cache "{project_root}/tech-daily-workspace/.http_cache.json"
```

Options:
- `--hours N` — time window in hours (default: 24)
- `--workers N` — concurrent workers (default: 20)
- `--category NAME` — filter by category (AI/ML, 芯片硬件, 云计算, 开源, 网络安全, 综合科技, Hacker News)

After the script finishes, read the output JSON and check
`metadata.update_count`. If it is 0, stop and inform the user that no
new articles were found.

### Step 2: AI Summarization (parallel sub-agents)

Split the updates into batches of ~10 articles each. Launch up to 4
sub-agents concurrently using the Agent tool.

For each batch:

1. Write a batch file to `tech-daily-workspace/tech_daily_batch_N.json`
   containing the batch's updates array from `latest_updates.json`.

2. Launch a sub-agent with this prompt:

```
You are summarizing tech news articles for a Chinese-language daily digest.
Read the file {project_root}/tech-daily-workspace/tech_daily_batch_{N}.json

For each article, use the `full_text` field (or `description` as fallback)
to write a ONE-SENTENCE Chinese summary (under 100 characters) that captures
the key point or takeaway. The summary should be informative and help the
reader decide whether to read the full article.

Also, if the article's `source_category` doesn't seem accurate based on the
content, suggest a better category from: AI/ML, 芯片硬件, 云计算, 开源,
网络安全, 综合科技. Otherwise keep the original category.

Output a JSON file at {project_root}/tech-daily-workspace/ai_summaries_batch_{N}.json
with this exact structure:
{
  "summaries": [
    {
      "url": "the url from the input",
      "ai_summary": "one-sentence Chinese summary",
      "category": "category name"
    }
  ]
}

CRITICAL - Encoding rules to avoid broken JSON:
- MUST use Python json.dump() to write the JSON file, NOT bash heredoc/echo/cat
- Do NOT use Chinese smart quotes (\u201c \u201d) in ai_summary text — use
  straight quotes (") or avoid quotes altogether
- Use ensure_ascii=False and encoding="utf-8" when writing
- Example: with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
```

3. After all sub-agents complete, manually merge the batch summary files
   into a single `tech-daily-workspace/ai_summaries.json`:
   - Read each `ai_summaries_batch_N.json` file
   - Combine all `summaries` arrays into one list
   - Write the merged result using Python json.dump() (same encoding rules)

If sub-agents are not available, skip this step. The report generator will
use the raw description text as fallback.

### Step 3: Trend Insight (single sub-agent)

Launch one sub-agent to analyze all summaries and generate trend insights:

```
You are analyzing tech news trends for a Chinese-language daily digest.
Read the file {project_root}/tech-daily-workspace/ai_summaries.json

Analyze all the AI summaries and identify 3-5 key tech trends, patterns,
or themes from today's news. Write 3-5 sentences in Chinese that capture
the most important developments and their connections.

Focus on:
- Emerging patterns across multiple articles
- Significant product launches, breakthroughs, or announcements
- Cross-domain connections (e.g., how AI relates to security or cloud)
- Industry shifts or notable developments

Output a JSON file at {project_root}/tech-daily-workspace/trend_insight.json
with this exact structure:
{
  "trend_insight": "1. [trend 1] 2. [trend 2] 3. [trend 3] ..."
}

CRITICAL - Encoding rules:
- MUST use Python json.dump() to write the JSON file
- Use ensure_ascii=False and encoding="utf-8" when writing
```

### Step 4: Generate the digest report

```bash
cd "{skill_directory}" && python scripts/generate_report.py \
  -i "{project_root}/tech-daily-workspace/latest_updates.json" \
  -s "{project_root}/tech-daily-workspace/ai_summaries.json" \
  --insight "{project_root}/tech-daily-workspace/trend_insight.json" \
  -o "{project_root}/daily-digests/YYYY-MM-DD/tech-daily_HH-MM.md"
```

Replace `YYYY-MM-DD` with today's date and `HH-MM` with current time.

If AI summaries or trend insight are not available, omit the corresponding
flags (`-s` or `--insight`).

## Performance Notes

- Full RSS scan (~25 feeds + 2 HN, 20 workers): ~30-60s
- Subsequent scans (ETag cached): ~10-15s
- AI summarization (4 sub-agents): ~60s
- Trend insight (1 sub-agent): ~30s
- Total end-to-end: ~2-3 minutes

## Completion

After generating the report, inform the user:
- How many articles were found (with category breakdown)
- The path to the generated report file
- A brief highlight: mention the 2-3 most interesting/important articles
- Suggest they can use `--category` to filter by specific category next time
