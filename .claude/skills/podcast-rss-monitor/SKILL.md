---
name: podcast-rss-monitor
description: |
  Monitor Chinese podcast updates and generate daily digest reports with AI-powered summaries.

  Use this skill whenever the user mentions anything related to podcast monitoring, podcast updates, or podcast digests — even casually. This includes:
  - Checking what podcasts updated recently (e.g., "帮我看看昨天的播客", "播客更新", "今天有什么新播客")
  - Generating podcast summary or digest reports (e.g., "播客日报", "播客摘要", "生成播客汇总")
  - Monitoring RSS subscriptions for new episodes (e.g., "RSS更新", "检查播客更新")
  - Any mention of "播客" combined with "更新/摘要/日报/监控/检查/最近"

  Also trigger when users ask about Chinese podcast content, episode summaries, or want to discover new episodes from top-ranked Chinese podcasts.

  Do NOT trigger for: creating/editing podcast audio, managing podcast hosting, non-Chinese podcast tasks, or general podcast recommendations without update context.
---

# Podcast RSS Monitor

Automates checking 1000 top-ranked Chinese podcasts for updates, then generates a daily digest with AI summaries.

## Input Parameters

Users can optionally specify:
- `hours`: Time range (default 24). Use 48 for "前天", 1 for "最近一小时".
- `linkType`: "rss", "xiaoyuzhou", or "all" (default).

## File Structure

```
project_root/
├── podcast-digests/              # Output: final digest reports (permanent)
├── podcast-workspace/            # Intermediate files (gitignored, auto-cleaned)
│   ├── latest_updates.json       # Step 1 output
│   ├── .http_cache.json          # HTTP ETag/If-Modified-Since cache
│   ├── podcast_batch_N.json      # Batch input for sub-agents
│   ├── ai_summaries_batch_N.json # Sub-agent output per batch
│   └── ai_summaries.json         # Merged AI summaries
│
└── .claude/skills/podcast-rss-monitor/
    ├── SKILL.md                  # This file
    ├── scripts/
    │   ├── check_updates.py           # Main update checker
    │   ├── resolve_xiaoyuzhou_urls.py  # Resolve non-XYZ URLs to XYZ episode links
    │   ├── generate_report.py         # Report generator
    │   └── convert_to_json.py         # MD-to-JSON converter (one-time)
    └── references/
        ├── podcasts.json         # 1000 podcast source data
        ├── podcast_rss_list.md   # Raw markdown list
        └── ad_keywords.txt       # Ad filtering keywords
```

All intermediate files go into `{project_root}/podcast-workspace/`. This keeps the skill directory clean and separates temporary data from the skill definition.

---

## Execution Steps

### Step 1: Run Python Script to Check Updates

```bash
mkdir -p "{project_root}/podcast-workspace"
cd "{skill_directory}" && python scripts/check_updates.py \
  --count 1000 --hours 24 --workers 30 \
  --output "{project_root}/podcast-workspace/latest_updates.json" \
  --cache "{project_root}/podcast-workspace/.http_cache.json"
```

If `python` is not found, try `python3` or `uv run python`.

**Parameters**:
- `--count`: How many podcasts to check (default 1000 = all)
- `--hours`: Time window in hours (default 24)
- `--workers`: Concurrency level (default 20, use 30 for speed)
- `--output`: Where to save results
- `--cache`: ETag cache file path

The script already filters ad content using `references/ad_keywords.txt`. No manual ad filtering needed.

**Output** (JSON):
```json
{
  "metadata": {
    "checked_count": 1000,
    "error_count": 5,
    "not_modified_count": 200,
    "error_details": {"HTTP 404": 3, "XML解析失败": 2},
    "hours": 24,
    "update_count": 45,
    "check_time": "2026-03-27 11:30:00 UTC"
  },
  "updates": [
    {
      "podcast_name": "播客名称",
      "rank": 2,
      "episode_title": "单集标题",
      "episode_url": "https://...",
      "pub_date": "2026-03-26 10:00",
      "shownotes": "已过滤广告的 shownotes..."
    }
  ]
}
```

**Result handling**:
- If `update_count` is 0, inform the user and stop — no digest needed.
- If `error_count` > 50 (>5% of total), warn about network issues and suggest retrying.

---

### Step 1.5: Resolve Xiaoyuzhou Episode URLs

Resolve non-Xiaoyuzhou episode URLs to Xiaoyuzhou episode-level links (`xiaoyuzhoufm.com/episode/{eid}`).

```bash
cd "{skill_directory}" && python scripts/resolve_xiaoyuzhou_urls.py \
  --input "{project_root}/podcast-workspace/latest_updates.json"
```

This step:
- Reads `latest_updates.json` from Step 1
- For each non-Xiaoyuzhou episode URL, fetches the podcast's Xiaoyuzhou page
- Matches episode titles to find the correct episode ID
- Replaces URLs with `https://www.xiaoyuzhoufm.com/episode/{eid}`
- Cleans `?utm_source=rss` suffixes from existing Xiaoyuzhou links
- Overwrites `latest_updates.json` with resolved URLs

**Note**: This step requires `xiaoyuzhou_url` field in `podcasts.json` for matching. Podcasts without this field will keep their original URLs.

---

### Step 2: Summarize via Parallel Sub-Agents (max 4 at a time)

Divide updates into **batches of 10**, create one sub-agent per batch.

**CRITICAL**: To avoid API rate limiting, launch **at most 4 sub-agents in parallel**. Process them group by group. Do not launch more than 4 sub-agents in a single message.

#### 2a. Prepare batch files

```bash
cd "{project_root}"
python -c "
import json
with open('podcast-workspace/latest_updates.json','r',encoding='utf-8') as f: data=json.load(f)
for i in range(0,len(data['updates']),10):
    batch=[{'podcast_name':u['podcast_name'],'episode_title':u['episode_title'],'episode_url':u['episode_url'],'shownotes':u.get('shownotes','')[:800]} for u in data['updates'][i:i+10]]
    with open(f'podcast-workspace/podcast_batch_{i//10}.json','w',encoding='utf-8') as f: json.dump(batch,f,ensure_ascii=False)
print(f'Saved {(len(data[\"updates\"])+9)//10} batches')
"
```

#### 2b. Launch sub-agents (groups of 4)

Calculate total batches: `total = ceil(update_count / 10)`

Divide batches into groups of 4. For example, if there are 12 batches:
- Group 1: batches 0, 1, 2, 3
- Group 2: batches 4, 5, 6, 7
- Group 3: batches 8, 9, 10, 11

Launch each group in a single message (up to 4 Agent tool calls), wait for all agents in that group to complete, then launch the next group.

**Sub-Agent Prompt Template** (replace `{N}` with batch index):

```
Task: Summarize podcast shownotes into one sentence

Read {project_root}/podcast-workspace/podcast_batch_{N}.json and summarize each podcast episode's shownotes into ONE concise Chinese sentence (30-50 characters).

Summary format:
本期节目[讨论了/讲述了/分享了][主题]，[主要内容]。

After summarizing all episodes, write the results as JSON to:
{project_root}/podcast-workspace/ai_summaries_batch_{N}.json

Use this exact format (key = episode_url, value = summary):
{
  "https://episode-url-1": "本期节目一句话总结...",
  "https://episode-url-2": "本期节目一句话总结..."
}

IMPORTANT: You MUST write the file using the Write tool. The file must be valid JSON.

Requirements:
1. Each summary must be exactly ONE sentence
2. 30-50 Chinese characters
3. Capture the main topic and key points
4. Use natural Chinese expression
```

**Key**: Each sub-agent writes to its OWN batch file (`ai_summaries_batch_{N}.json`), not a shared file. This prevents race conditions.

After each group completes, launch the next group. Repeat until all batches are processed.

#### 2c. Merge all batch results

```bash
cd "{project_root}"
python -c "
import json, glob
merged = {}
for f in sorted(glob.glob('podcast-workspace/ai_summaries_batch_*.json')):
    with open(f,'r',encoding='utf-8') as fh: merged.update(json.load(fh))
with open('podcast-workspace/ai_summaries.json','w',encoding='utf-8') as fh: json.dump(merged,fh,ensure_ascii=False,indent=2)
print(f'Merged {len(merged)} summaries')
"
```

**Fallback**: If sub-agents are unavailable, skip to Step 3 and use truncation-based summaries:

```bash
cd "{skill_directory}" && python scripts/generate_report.py \
  -i "{project_root}/podcast-workspace/latest_updates.json" \
  -o "{project_root}/podcast-digests/YYYY-MM-DD_HH-mm.md"
```

---

### Step 3: Generate Final Report

```bash
cd "{skill_directory}" && python scripts/generate_report.py \
  -i "{project_root}/podcast-workspace/latest_updates.json" \
  -s "{project_root}/podcast-workspace/ai_summaries.json" \
  -o "{project_root}/podcast-digests/YYYY-MM-DD_HH-mm.md"
```

**Directory**: `{project_root}/podcast-digests/`

**Filename**: `YYYY-MM-DD_HH-mm.md`

Create the directory if it doesn't exist. Always write to the project root `podcast-digests/` (not inside `.claude/`).

---

## Architecture

```
Main Agent
  |
  Step 1: python check_updates.py
          |- Concurrent workers (30)
          |- Checks all 1000 podcasts
          |- Built-in ad filtering (dual-tier: high/low confidence)
          |- HTTP cache (ETag/If-Modified-Since) for incremental updates
          |- Error statistics with classification
          |- Output: podcast-workspace/latest_updates.json
  |
  Step 1.5: python resolve_xiaoyuzhou_urls.py
          |- Resolves non-XYZ URLs to XYZ episode links
          |- Title matching (exact → substring)
          |- Cleans utm_source suffixes
          |- Updates: podcast-workspace/latest_updates.json (in-place)
  |
  Step 2: Prepare batches -> N parallel sub-agents (max 4 concurrent)
          |- Each sub-agent reads podcast-workspace/podcast_batch_N.json
          |- Each sub-agent writes to podcast-workspace/ai_summaries_batch_N.json
          |- Process in groups of 4 to avoid rate limiting
          |- Merge all results -> podcast-workspace/ai_summaries.json
          |- Fallback: skip to Step 3 (truncation-based summaries)
  |
  Step 3: python generate_report.py
          |- Merges AI summaries with update data
          |- Fallback to truncation for missing summaries
          |- Output: podcast-digests/YYYY-MM-DD_HH-mm.md
```

---

## Performance

| Metric | Value |
|--------|-------|
| 1000 podcasts (full) | ~2 min |
| 1000 podcasts (cached) | ~30 sec |
| Sub-agent summarization | ~1 min per group of 4 |

---

## Usage Examples

```
User: 帮我看看昨天的播客更新
User: 播客日报
User: 最近一天有什么播客更新
User: 帮我监控播客更新
User: 检查一下排名靠前的播客有什么新内容
User: podcast update
```

## Completion Message

```
播客监控完成！
- 检查播客: {total} 个 (RSS: {rss}, 小宇宙: {xy})
- 发现更新: {count} 个
- 错误/跳过: {errors} 个
- 输出文件: ./podcast-digests/YYYY-MM-DD_HH-mm.md
```
