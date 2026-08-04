# Reflection Prompts (Step 2)

These prompts turn the day's facts into the journal entry. SKILL.md explains
*when* to run each one; apply them to whatever the agent gathered in Step 1
(git, files, conversation context, or a user answer) — there is no fixed input
file.

The body of the entry is **中文**; the coda (§C) is **中英双语**. The division
of labor:

- **§A Reflection** — author the `## 🌤 今日主线` (and `## 🔍 细节与回响`)
  sections. Run when the day had meaningful activity.
- **§B Silent-day branch** — a minimal `## 🌤 今日主线` for a day with nothing
  to report. Run when nothing meaningful happened, then skip straight to §C.
- **§C Bilingual coda** — author `## ✨ 尾声`. **Always** runs, silent day or
  not.

---

## §A. Reflection (body, 中文)

Your input is whatever you gathered in Step 1 — a glance at git, recent files,
the current conversation, or a user's answer. It is not a structured file; you
hold it in mind.

Your job, in **中文**:

1. **Cluster the day's activity into 3-6 work threads.** A thread is a line of
   effort, not a single event. Group related things together — e.g.
   "发布 v1.2.0 的收尾工作", "给 agent-journal 铺骨架", "修一个老接口的边界 bug".
   Prefer synthesis over item-by-item listing.

2. **For each thread write one factual sentence** stating what was done, drawn
   strictly from what actually happened. Then, **only where honest**, add one
   short observation — a snag that slowed things down, a small surprise, a
   thread that connects to earlier days. If there is nothing honest to
   observe, omit it; do not manufacture insight.

   CRITICAL — honesty: do NOT invent work that did not happen. If a thread is
   thin (one commit, one line), say so plainly. The entry's value is
   truthfulness, not volume.

3. **Decide on `## 🔍 细节与回响`.** If, across the day, 1-3 interesting points
   emerged that don't fit a thread (a curious file, a reverted change, a
   pairing of unrelated work, a moment from the conversation), write them here
   in a few short 中文 lines. If nothing stands out, **omit the section
   entirely** — do not pad.

Hand the resulting threads + details off to Step 3 for rendering under
`## 🌤 今日主线` and `## 🔍 细节与回响`. When git is your source, use commit
subjects as the grain (nobody wants to read hashes); only surface a hash when
referring to a specific change that matters.

---

## §B. Silent-day branch (body, 中文)

Run this instead of §A when the day genuinely had nothing meaningful to report
— no commits, no file changes, no real conversation activity, and the user
didn't name anything either.

Write **1-2 gentle 中文 sentences** acknowledging the quiet day, for the
`## 🌤 今日主线` section. Rules:
- No guilt, no productivity sermon. A fallow day is a real kind of day.
- Do **not** invent activity. Then omit `## 🔍 细节与回响` and go straight to
  the coda (§C), which still runs.

Example texture (do not copy verbatim — write your own):
> 今天代码仓库里没有新的足迹 —— 是一个安静的日子，像窗外无风时湖面。

---

## §C. Bilingual coda (尾声, 中英双语)

**Always run this**, silent day or not. It authors the `## ✨ 尾声` section of
the entry — the one section that is never omitted.

You are writing **two independent short pieces**:

### 中文 (3-6 sentences)

Author a short 中文 piece. It should:
- Lead with **imagery**, not advice. 自然意象、光影、时令、器物、城市与夜的边
  界，都可以入笔。
- Echo the day's texture **loosely** — if the day was about releases and
  tidying, you may let "收束 / 落定 / 归档" color the imagery; if it was a
  silent day, let "空 / 静 / 留白" color it. The link should be felt, not
  stated ("今天我做了 X，所以…"). Never explain the metaphor.
- Be allowed to lean philosophical, or to stay small and concrete. Either is
  fine; pompous is not.

### English (3-6 sentences)

Author a short English piece. It must:
- **Echo the 中文 in mood, not in words.** This is the central rule. The two
  pieces are **companions, not translations**. Each must read as a complete
  piece on its own; a reader of either should not feel they are missing half.
- Be free to **switch the image entirely.** If the 中文 piece rested on a
  metaphor that does not survive English (a pun, a four-character idiom, a
  culturally specific season word), choose a fresh English image that carries
  the same emotional weather. Do not force a literal rendering that has gone
  stale — "river of stars" is fine if it shines; if it doesn't, find another.
- Hold the **same emotional register**: if the 中文 is quiet and inward, the
  English is quiet and inward; if one is wry, the other is wry.

### Anti-examples (avoid these)

- ❌ 鸡汤 / motivational sermon: 「加油！明天会更好」 / "Keep going, tomorrow
  is another day" / "Believe in yourself." These flatten the entry.
- ❌ Literal back-translation: the English merely re-saying the Chinese word by
  word. If you can't find a living English image, pick a new one — see above.
- ❌ Resolving the metaphor: 「星河喻指今天的众多提交」 / "The river of stars
  stands for today's many commits." Never explain. Once explained, a metaphor
  dies.
- ❌ Skipping the coda on a silent day. **The coda always runs.** An empty day
  is not exempt — 空无 itself can be the subject. If nothing happened, let the
  quiet be the material, in both languages.

### Output placement

Hand the two pieces to Step 3 to render as:

```markdown
## ✨ 尾声
### 中文
<the 中文 piece>

### English
<the English piece>
```

Order is fixed: 中文 first, English second. The two sub-headings are always
both present.
