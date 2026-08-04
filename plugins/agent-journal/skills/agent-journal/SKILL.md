---
name: agent-journal
version: "1.0.0"
description: |
  Review what was done today and produce a daily journal entry that ends with a
  bilingual (Chinese + English) poetic coda. The agent gathers the day's facts
  itself — from git, files, the current conversation, or by asking the user —
  and authors the whole entry (中文回顾 + 中英双语诗意收尾). There is no
  collection script; the agent decides how to reconstruct the day.

  This skill is the reflection + render layer, single-agent by design: the
  voice should be one voice. Intended for scheduled self-reflection of agents
  like OpenClaw / Hermes.

  Use this skill whenever the user (or an agent framework) wants a daily
  self-reflection / journal entry about today's work. Triggers include:
  - 每日回顾 / 今日回顾 / 写今日手记 / 今天做了什么 / 写日记 / agent journal
  - 固定时间回顾今日工作 / 给今天写一段总结 / 每日自省 / reflection
  - "帮我回顾一下今天" / "为今天写一段手记"

  Do NOT trigger for: 生成内容监控日报（use daily-digest）, 发布版本（use
  release）, 写外部文章/文档, 或单纯查 git log（just run git log directly).
---

# Agent Journal Skill

This skill turns **the day itself** into one Markdown journal entry. There is
no collection script and no fixed data source — the agent reconstructs what
happened today, reflects on it in 中文, and ends with a **中英双语诗意收尾**.

```
agent-journal (this skill — gather → reflect → author → write)
  │
  └─ the agent gathers the day's facts itself, then writes the entry
     (git / files / conversation context / asking the user — its choice)
```

The agent is trusted to decide how much to gather and from where. That keeps
this skill honest about its purpose: a reflection, not a log dump. The prompts
in `references/reflection-prompts.md` govern *how to write*; this file governs
*what to do*.

## Architecture

```
{skill_directory}/                  # This skill's directory (inside the plugin)
  SKILL.md                        # This file — workflow & steps
  references/
    reflection-prompts.md         # §A reflection / §B silent-day / §C bilingual coda prompts

{project_root}/                    # The user's current project (cwd at run time)
  journals/YYYY-MM-DD/            # Output directory
    journal_HH-MM.md              # The journal entry (times in CST / UTC+8)
```

No `workspaces/`, no intermediate JSON. The agent goes straight from
reconstruction to the finished Markdown file.

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the absolute
path to THIS skill's directory, `{project_root}` with the user's current
project (the working directory at run time).

### Step 0: Ensure the output directory exists

```bash
mkdir -p "{project_root}/journals/$(date +%Y-%m-%d)"
```

### Step 1: Reconstruct today (agent, self-directed)

Gather the day's facts yourself. **You decide how**, based on what kind of day
this is. You are not required to use any particular source — pick what is
honest and available. Common options, in roughly decreasing reliability:

- **Current conversation context** — the most truthful source when this skill
  is triggered mid-session: what was the user actually working on with you
  today? What got built, fixed, decided, abandoned?
- **Git history** — if a repo is relevant, a quick look tells you what landed:
  ```bash
  git -C "{project_root}" log --since=00:00 --oneline --no-merges
  git -C "{project_root}" status --porcelain
  ```
  Use this to corroborate or to reconstruct a day you weren't present for. Do
  not dump commit hashes into the entry — use subjects.
- **Recent files** — newly created or heavily edited files are a hint at the
  day's shape when git isn't available (non-repo dir, uncommitted work).
- **Ask the user** — when signals are thin or unclear, a single short question
  ("今天主要在忙什么？") is better than guessing. Do not interrogate.

Decide a scope for "today" (since 00:00 CST by default) and stay inside it.
If after honest effort **nothing meaningful happened**, that is a valid result
— note it and use the silent-day branch (§B) for the body.

### Step 2: Reflect + author (agent)

**Read `references/reflection-prompts.md` in full before this step.** It
contains the three prompt templates. Apply them to whatever you gathered:

1. **Reflection (§A)** — cluster the day into 3-6 work threads in **中文**.
   For each thread write one factual sentence plus, where honest, one
   observation (a snag, a small surprise, a through-line). Never invent work.
2. **Silent-day branch (§B)** — only when the day genuinely had nothing to
   report. Write 1-2 gentle 中文 sentences acknowledging the quiet, then go
   straight to the coda.
3. **Bilingual coda (§C)** — always, even on a silent day. Author **two
   independent short pieces**: first 中文 (3-6 sentences), then English (3-6
   sentences). They echo each other in mood but are NOT literal translations.

### Step 3: Write the journal entry

Author the Markdown entry directly to
`{project_root}/journals/YYYY-MM-DD/journal_HH-MM.md` (replace `YYYY-MM-DD`
with today's date in CST and `HH-MM` with the current time). Use this
structure:

```
# 今日手记 - <YYYY-MM-DD HH:MM> (CST)
> <one-line overview the agent chooses to write>

## 🌤 今日主线      ← 3-6 work threads in 中文 (from §A), or §B's 1-2 sentences if silent
## 🔍 细节与回响    ← interesting points / snags / observations (中文; omit if none)
## ✨ 尾声          ← bilingual poetic coda — ALWAYS present, even on a silent day
   ### 中文
   <3-6 sentences of 中文 imagery>
   ### English
   <3-6 sentences of English>
```

Notes:
- The `> <overview>` header line is whatever one-line frame the agent chooses —
  a commit/file count ("提交 N 笔 | 改动 M 文件"), a plain sentence of mood, or
  on a silent day simply `> 今日无痕迹`. Pick what fits; do not force a count
  when there is none.
- `## 🔍 细节与回响` may be omitted entirely if the day had nothing notable.
- `## ✨ 尾声` is the one section that is **never** omitted.

## Performance Notes

- This skill has no collection cost — gathering is a few git calls or a glance
  at conversation context, typically seconds.
- Reflection + authoring is the only AI cost; no sub-agents are used (one
  voice).
- A silent day skips clustering and goes straight to §B + the coda, so it is
  the cheapest run.

## Completion

After writing the entry, inform the user:
- The path to the generated journal entry.
- A one-line summary of the day (a highlight or two, or that it was a silent
  day).
- The mood of the bilingual coda in one phrase (e.g. "尾声偏静水意象"), so the
  user knows the texture without opening the file.
- That they can review an earlier day by saying so ("回顾 8 月 1 日"), or widen
  scope ("把另外那个 repo 也算进来").
