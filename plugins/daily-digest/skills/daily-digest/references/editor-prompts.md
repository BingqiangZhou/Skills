# Editor Sub-Agent Prompts (Step 2d)

The editor sub-agents turn the per-source one-line summaries into the report's
**narrative**. SKILL.md explains *when* to run them; copy the prompts verbatim
from here, substituting `{project_root}`.

Articles (WeChat + tech blogs) and podcasts are two **independent** narrative
tracks — they never cross-reference each other.

---

## 2d-0. Split RSS summaries into articles / podcasts

The merged `rss_ai_summaries.json` mixes all three RSS sources. Split it by
`source` (looked up from `workspaces/daily-digests/daily-digests/rss/latest_updates.json`) so each editor
sub-agent gets only its track:

```bash
python - <<'PY'
import json
from pathlib import Path

base = Path("{project_root}/workspaces/daily-digests/daily-digests")
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

---

## 2d-1. Articles editor sub-agent

Run when there are article (wechat/tech) updates. Reads
`rss_articles_summaries.json`, writes `digest_narrative_articles.json`.

```
Task: Act as the editor of today's ARTICLE digest (WeChat + tech blogs only;
NO podcasts). Read the merged one-line summaries and produce a NARRATIVE report.

Read this file:
- {project_root}/workspaces/daily-digests/daily-digest/rss_articles_summaries.json

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
{project_root}/workspaces/daily-digests/daily-digest/digest_narrative_articles.json

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

---

## 2d-2. Podcasts editor sub-agent

Run when there are podcast updates. Reads `rss_podcasts_summaries.json`,
writes `digest_narrative_podcasts.json`.

```
Task: Act as the editor of today's PODCAST digest (podcasts only; NO articles).
Read the one-line summaries and produce a NARRATIVE report.

Read this file:
- {project_root}/workspaces/daily-digests/daily-digest/rss_podcasts_summaries.json

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
{project_root}/workspaces/daily-digests/daily-digest/digest_narrative_podcasts.json

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

---

## Merge the two editor outputs into digest_narrative.json

Run 2d-1 and 2d-2 as two parallel sub-agents. They write to SEPARATE files
(`digest_narrative_articles.json` and `digest_narrative_podcasts.json`) to
avoid any write race. The main flow then merges them into the single
`digest_narrative.json` consumed by Step 3:

```bash
python - <<'PY'
import json
from pathlib import Path
base = Path("{project_root}/workspaces/daily-digests/daily-digest")
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
