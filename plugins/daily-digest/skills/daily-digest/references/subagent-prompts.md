# Sub-Agent Prompt Templates (Step 2a / 2b / 2c)

These are the exact prompts handed to the per-source summarization sub-agents.
SKILL.md tells you *when* to run each; copy the prompt verbatim from here,
substituting `{project_root}`, `{batch_file}`, and `{N}`.

All three prompts share a critical encoding rule (json.dump, no smart quotes,
ensure_ascii=False) — never let a sub-agent write JSON via bash heredoc/echo/cat.

---

## 2a. RSS summarization (one sub-agent per batch)

Run after `prepare_batches.py --source rss`. One sub-agent per `rss_batch_{N}.json`.

```
Task: Summarize RSS articles into one Chinese sentence each.

Read the file {project_root}/workspaces/daily-digests/data/daily-digest/{batch_file}.

For each item, use the `full_text` field to write ONE concise Chinese
sentence (under 100 characters) capturing the key point or takeaway, so the
reader can decide whether to read the full article.

Write the results as JSON to:
{project_root}/workspaces/daily-digests/data/daily-digest/rss_ai_summaries_batch_{N}.json

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

---

## 2b. GitHub summarization (one sub-agent per batch)

Run after `prepare_batches.py --source github`. One sub-agent per `github_batch_{N}.json`.

```
Task: Summarize GitHub activity (merged pull requests and/or new issues) into
one Chinese sentence each.

Read the file {project_root}/workspaces/daily-digests/data/daily-digest/github_batch_{N}.json.

Each entry is either a newly merged GitHub pull request (item_type="pulls") or a
new GitHub issue (item_type="issues"). For each entry, use the `title` and
`body_text` fields to write ONE concise Chinese sentence (under 100 characters)
capturing the key point or purpose:
- For a PR: what it changed or added (project name, what it does, notable detail).
- For an issue: what the author is announcing or asking about.
The goal is that the reader can decide whether to look into it.

Write the results as JSON to:
{project_root}/workspaces/daily-digests/data/daily-digest/github_ai_summaries_batch_{N}.json

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

---

## 2c. Tool highlights (single sub-agent)

Only run if the tool source has `update_count > 0`. Release notes are already
human-readable, so there is no per-release summarization — just one overview pass.

```
Task: Read tool release updates and write a brief Chinese-language highlights
note.

Read the file {project_root}/workspaces/daily-digests/data/tool-update-monitor/latest_updates.json.

Look at the `updates` array. Each entry has a tool name, version,
previous_version, published_at, and a `body` (release notes). Write:

1. `highlights`: 2-3 Chinese sentences. Which releases matter most? Any
   breaking changes or notable features? A brief "should you upgrade" hint.
2. `per_tool`: a map of tool_id -> ONE Chinese sentence (under 60 chars)
   summarizing that tool's update.

Write the results as JSON to:
{project_root}/workspaces/daily-digests/data/daily-digest/tool_ai_highlights.json

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
