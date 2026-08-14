# Editor Sub-Agent Prompts (Step 2d)

The editor sub-agents turn the per-source one-line summaries into the report's
**scannable topic structure** (a short lead + bullet list per topic — NEVER a
dense paragraph). SKILL.md explains *when* to run them; copy the prompts
verbatim from here, substituting `{project_root}`.

Articles (WeChat + tech blogs) and podcasts are two **independent** narrative
tracks — they never cross-reference each other.

---

## 2d-0. Split RSS summaries into articles / podcasts

The merged `rss_ai_summaries.json` mixes all three RSS sources. Split it by
`source` (looked up from `workspaces/daily-digests/data/rss/latest_updates.json`) so each editor
sub-agent gets only its track. Use the standalone split script
(`scripts/split_rss_summaries.py`) rather than an inline heredoc — it prints
counts you can guard on and avoids shell quoting pitfalls:

```bash
cd "{skill_directory}" && python scripts/split_rss_summaries.py \
  --rss-input "{project_root}/workspaces/daily-digests/data/rss/latest_updates.json" \
  --summaries "{project_root}/workspaces/daily-digests/data/daily-digest/rss_ai_summaries.json" \
  --output-dir "{project_root}/workspaces/daily-digests/data/daily-digest"
```

It writes `rss_articles_summaries.json` and `rss_podcasts_summaries.json` and
prints e.g. `articles: 159  podcasts: 101`.

**Guardrail — do not proceed with an empty podcast track.** If the script prints
`podcasts: 0` while also printing a non-zero `collector podcasts:` count, the
split lost every podcast (usually a stale/missing `rss_ai_summaries.json` or a
source-tag mismatch). Stop and investigate before launching the editors —
otherwise the report's 「🎧 播客精选」 section will be empty. The report
generator will also emit a visible warning, but it's best to catch it here.

---

## 2d-1. Articles editor sub-agent

Run when there are article (wechat/tech) updates. Reads
`rss_articles_summaries.json`, writes `digest_narrative_articles.json`.

```
Task: Act as the editor of today's ARTICLE digest (WeChat + tech blogs only;
NO podcasts). Read the merged one-line summaries and produce a SCANNABLE,
bullet-first digest (every topic = lead + bullets, NEVER a dense paragraph).

Read this file:
- {project_root}/workspaces/daily-digests/data/daily-digest/rss_articles_summaries.json

Each entry is a one-line Chinese summary of an article. Your job:

1. Cluster the items by CONTENT THEME — NOT by source. Aim for 5-8 topics.
   Examples: "AI 应用安全攻防升温", "大模型竞速与价格战", "网络安全漏洞情报".

2. For each topic produce a SCANNABLE structure. The ONLY acceptable
   per-topic shape is `lead` + `bullets` — you MUST NOT emit a `narrative`
   string field:
   - `lead`: ONE short Chinese sentence (≤40 字) pointing out the core
     trend of this topic (e.g. "多条更新指向 AI Agent 成为攻防新前线。").
   - `bullets`: 3-8 independent bullets, each ≤60 字 and stating ONE fact.
     Scale the count to the topic's information density — low-density
     topics stay at 3-5 crisp bullets, but fact-dense topics (e.g. a
     vulnerability roundup with many CVEs/products) MAY use 6-8 so that
     NO substantive item is dropped. You MAY bold the leading actor/
     subject for quick scanning, e.g. "**OpenAI** Astra 达网络安全能力
     阈值，被迫放缓发布". Each bullet should stand alone — do not chain
     facts with commas into a run-on.

   CRITICAL — NO INFORMATION LOSS: the bullet list must collectively
   cover every substantive fact the old single-paragraph narrative would
   have. If you find yourself exceeding 8 bullets, the topic is too broad
   — split it into two topics instead of silently dropping facts.

   CRITICAL — citation style: DO NOT name media channels or outlets (no
   「微信公众号」「The New Stack」「VentureBeat」「量子位」etc.). State the
   CONTENT directly. You MAY keep names of people/organizations that ARE the
   actor or subject of the news (e.g. Anthropic, Mandiant 红队, 阿里, OpenAI,
   港科大) because they are who did the thing, not who reported it.
   - BAD:  「The New Stack」与「量子位」报道阿里发布 Qwen3.8-Max…
   - GOOD: 阿里发布 2.4 万亿参数 MoE 模型 Qwen3.8-Max…

3. Write `overview`: 2-3 Chinese sentences capturing today's overall tone
   and naming the ~3 main directions (articles only).

4. Write `other`: an ARRAY of short bullets (strings), one per remaining
   minor / one-off article item, each ≤80字 with a **加粗主体** lead. Group
   only tightly-related items into one bullet so NOTHING of substance is
   silently dropped. MUST be an array — NEVER a single sentence/paragraph.

5. In each topic's `items` array, record every article you referenced
   (url + short label). This is for audit only — it is NOT rendered.

Write the results as JSON to:
{project_root}/workspaces/daily-digests/data/daily-digest/digest_narrative_articles.json

Use this exact structure:
{
  "overview": "2-3 句中文总览（文章）……",
  "article_topics": [
    {
      "title": "话题标题",
      "lead": "一句话导语（≤40字），点出该话题核心趋势",
      "bullets": [
        "**主体** 事实（≤60字一条，必填）",
        "**主体** 事实（≤60字一条，必填）"
      ],
      "items": [
        {"source": "rss", "url": "...", "label": "Apache NiFi 四漏洞……"}
      ]
    }
  ],
  "other": [
    "**主体** 事实（≤80字）",
    "**主体** 事实（≤80字）"
  ]
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

---

## 2d-2. Podcasts editor sub-agent

Run when there are podcast updates. Reads `rss_podcasts_summaries.json`,
writes `digest_narrative_podcasts.json`.

```
Task: Act as the editor of today's PODCAST digest (podcasts only; NO articles).
Produce a PER-EPISODE digest — every episode stands on its own, NEVER merged
across shows into content-theme buckets.

Read this file:
- {project_root}/workspaces/daily-digests/data/daily-digest/rss_podcasts_summaries.json

Each entry is one podcast episode with `podcast_name`, `title`, `url`, and a
one-line `ai_summary`. Your job:

1. For each episode with real informational / analytical substance, emit ONE
   entry in `podcast_episodes`:
   - `show`: the `podcast_name` (the show this episode belongs to).
   - `title`: the episode `title`.
   - `summary`: ONE Chinese sentence (≤50字) saying what THIS episode is
     actually about — the content / takeaway, not a restatement of the title.
   - `url`: the episode url.
   Each episode is independent. Do NOT merge different episodes' content
   together, and do NOT cluster episodes into themes.

2. Episodes with little substance — pure entertainment, daily news roundups,
   language / listening drills, true crime, lifestyle chatter — go into
   `podcast_other` as one-liners ("节目名 · 主题"). Do NOT elaborate sensitive
   crime details; just acknowledge they exist.

3. You MAY freely name shows / hosts here (unlike the articles track) — the
   show identity IS the point of a per-episode podcast digest.

CRITICAL - Coverage rule: every collected episode MUST appear in
`podcast_episodes` or `podcast_other`. NOTHING is silently dropped.

CRITICAL - Format: you MUST emit `podcast_episodes` as an array of objects
and `podcast_other` as an array of strings. You MUST NOT emit the legacy
`podcast_topics` theme structure — no clustering, no lead/bullets topics.

Write the results as JSON to:
{project_root}/workspaces/daily-digests/data/daily-digest/digest_narrative_podcasts.json

Use this exact structure:
{
  "podcast_episodes": [
    {
      "show": "节目名",
      "title": "本期标题",
      "summary": "一句话（≤50字）说清这期讲什么",
      "url": "https://..."
    }
  ],
  "podcast_other": [
    "节目名 · 一句话主题",
    "节目名 · 一句话主题"
  ]
}

CRITICAL - Encoding rules to avoid broken JSON:
- You MUST write the file using Python json.dump(), NOT bash heredoc/echo/cat.
- Do NOT use Chinese smart quotes in the text — use straight quotes (").
- Use ensure_ascii=False and encoding="utf-8" when writing.
- Write to the podcasts-only file shown above (NOT the shared
  digest_narrative.json — the main flow merges the two tracks afterwards).
```

---

## Merge the two editor outputs into digest_narrative.json

Run 2d-1 and 2d-2 as two parallel sub-agents. They write to SEPARATE files
(`digest_narrative_articles.json` and `digest_narrative_podcasts.json`) to
avoid any write race. The main flow then merges them into the single
`digest_narrative.json` consumed by Step 3. Use the standalone merge script
(`scripts/merge_narratives.py`) — it does a **non-destructive field merge**
(each file contributes only its own keys), so an unexpected key in one file
can never clobber the other's content:

```bash
cd "{skill_directory}" && python scripts/merge_narratives.py \
  --input-dir "{project_root}/workspaces/daily-digests/data/daily-digest" \
  --output "{project_root}/workspaces/daily-digests/data/daily-digest/digest_narrative.json"
```

It prints the merged counts (e.g. `article_topics: 8`, `podcast_episodes:
101`, `podcast_other: 37`). A missing/corrupt editor file is skipped with a
warning rather than crashing — the report generator will then render a
visible placeholder for the missing track instead of silently omitting it.

Note: the legacy `digest_highlights.json` (3-5 one-line highlights) is no
longer produced. If one from an older run is present, the report generator
will still use it as a *fallback overview* when `digest_narrative.json` is
missing — but you should produce `digest_narrative.json` going forward.
