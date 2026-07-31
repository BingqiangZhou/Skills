---
name: tool-update-monitor
description: |
  Monitor developer tools and operating systems for new releases and generate a
  digest report of what's new since the last check. Covers: ZCode, VS Code,
  Claude Code, OpenAI Codex CLI, OpenClaw, Hermes Agent, OpenCode, Homebrew,
  Warp, Clash Verge Rev, v2rayN, Windows 11, macOS. Sources are GitHub Releases,
  the npm registry, Microsoft's Windows release-health page, Apple's support
  page, and (for ZCode) the official changelog page.

  Use this skill whenever the user mentions tool/CLI/app/OS version updates,
  checking for new releases, or wants a "what changed" digest for these
  tools. This includes:
  - Checking whether any monitored tool released a new version
  - Generating a tool update / release digest report
  - Any mention of 工具更新 / 版本更新 / 工具日报 / 检查更新 / 新版本 / 工具有没有更新
  - System/OS updates: Windows 11 / 系统更新 / macOS / 苹果系统更新 / 补丁 / Patch Tuesday
  - Questions like "zcode/vscode/claude code/codex/openclaw/hermes/opencode/
    homebrew/warp/clash verge/v2rayn/windows/macOS 最近有更新吗"
  - "What's new in my tools", "any tool updates lately"

  Do NOT trigger for: installing/upgrading the tools themselves, configuring
  them, general product news/blog posts, comparing tool features, or
  questions about a single tool's usage that aren't about version updates.
---

# Tool Update Monitor Skill

Monitor **13 developer tools and operating systems** for new releases and
produce a digest of what changed since the last check. Detection is
**last-seen version based**: the first run records a baseline; subsequent
runs only surface versions newer than what was last recorded, so the reader
sees "truly new" releases rather than everything published in a fixed window.

Tools are grouped into four categories:

- **AI 编码代理 / AI Coding Agents** — ZCode, VS Code, Claude Code, OpenAI
  Codex CLI, OpenClaw, Hermes Agent, OpenCode
- **系统工具 / System Tools** — Homebrew, Warp
- **系统更新 / OS Updates** — Windows 11, macOS Tahoe
- **网络代理 / Network Proxies** — Clash Verge Rev, v2rayN

Source types (dispatched in `check_updates.py`): GitHub Releases API
(9 tools), npm registry (`@anthropic-ai/claude-code`), HTML changelog
regex scrape (ZCode, macOS), and a dedicated Windows table scraper for
Microsoft's release-health page. Add/remove tools in
`references/tools.json` — no code changes needed for new same-type sources.

## Architecture

```
.claude/skills/tool-update-monitor/
  SKILL.md                  # This file
  scripts/
    check_updates.py        # Fetch all sources; compute NEW vs last-seen
    generate_report.py      # Build the Markdown digest
  references/
    tools.json              # Curated tool list (edit to add/remove tools)

{project_root}/
  workspaces/tool-update-monitor/   # Runtime intermediate files (gitignored)
    .http_cache.json               # ETag/If-Modified-Since per source URL
    .last_seen.json                # { tool_id: { version, published_at, checked_at } }
    latest_updates.json            # This run's results (input to report)
    ai_highlights.json             # Single AI pass output (optional)
  daily-digests/YYYY-MM-DD/        # Unified output directory
    tool-update_HH-MM.md           # (times shown in CST / UTC+8)
```

## How "new" detection works

- **Baseline run** (no `.last_seen.json`, or `--force-baseline`): records the
  current version of every tool and reports **zero** new updates. The report
  is a short "baseline established" note. This avoids spamming 11 "new"
  entries on first use.
- **Normal run**: for each tool, fetch its latest release(s); any version
  newer than the recorded one is "new". After computing, the latest version
  is written back to `.last_seen.json`. So if a tool ships 3 releases since
  you last checked, all 3 surface in one digest.
- **ETag/If-Modified-Since** caching skips unchanged endpoints (304) quickly;
  a 304 tool reuses its previously known version and contributes no new
  updates.

## Input Parameters

- `--hours N` — time window shown in the report header (default: 168 = 1
  week). Informational only; actual detection is version-based, not time-based.
- `--category NAME` — substring filter on category (e.g. `网络代理`).
- `--tool ID` — check a single tool id (e.g. `v2rayn`).
- `--force-baseline` — re-record current versions as baseline; report 0 new.

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the path to
this `.claude/skills/tool-update-monitor/` directory and `{project_root}`
with the project root (`E:/Projects/AI/OpenClaw_Skills`).

### Step 0: Ensure workspace directories exist

```bash
mkdir -p "{project_root}/workspaces/tool-update-monitor"
mkdir -p "{project_root}/daily-digests/$(date +%Y-%m-%d)"
```

### Step 1: Check all tools for new releases

```bash
cd "{skill_directory}" && python scripts/check_updates.py \
  --hours 168 --workers 6 \
  --output "{project_root}/workspaces/tool-update-monitor/latest_updates.json" \
  --cache "{project_root}/workspaces/tool-update-monitor/.http_cache.json" \
  --state "{project_root}/workspaces/tool-update-monitor/.last_seen.json"
```

After the script finishes, read the output JSON and check `metadata`:

- If `metadata.baseline_run` is `true`, this was the first run. Proceed to
  Step 3 to write the short baseline report — **skip Step 2** (no updates
  to summarize).
- If `metadata.update_count` is `0` (and not a baseline run), there's nothing
  new. Proceed to Step 3 to write the "no new versions" report, then inform
  the user.
- If `metadata.error_count / checked_count > 0.2`, the script already printed
  a WARNING. Inspect `metadata.error_details` and mention notable failures to
  the user (e.g. GitHub rate-limiting, or ZCode's HTML page being JS-rendered).

Watch for the **scraped sources** in particular:

- **ZCode** is scraped from an HTML changelog that may be JavaScript-rendered.
  If it shows up under errors as "scrape failed (JS-rendered)", that's expected
  occasionally — report it as a known limitation but continue with the other
  tools. Do not block the run.
- **Windows 11** and **macOS** are scraped from table/heading-based pages
  (Microsoft release-health and Apple support). If Microsoft or Apple changes
  their page layout, the scraper may report "table not found" or "version not
  found" — these are page-layout changes, not crashes. The Windows scraper
  tracks the build number (e.g. `26200.8973`) of the configured channel
  (`win_version` in tools.json, default `25H2`); a new monthly cumulative
  update surfaces as "new". macOS tracks the latest point release (e.g. `26.6`)
  and has no release date from its source.
- **macOS**: the entry tracks **macOS Tahoe (26.x)**, the current GA version.
  macOS 27 Golden Gate is in beta (fall 2026) and lives on a separate page, so
  it's excluded. When 27 goes GA, update the `tools.json` entry's `url` and
  `version_regex` to the macOS 27 page.

### Step 2: AI highlights (only if `update_count > 0`, optional)

Release notes are already human-readable changelogs, so there is **no**
per-release summarization. Instead, make **one** sub-agent pass over the
whole digest to add a 2–3 sentence overview and a one-line note per updated
tool. Skip this step entirely if sub-agents are unavailable (e.g. Claude.ai)
or if `update_count` is 0 — `generate_report.py` falls back to raw notes.

Sub-agent prompt template (replace `{project_root}` with the real path):

```
Task: Read tool release updates and write a brief Chinese-language highlights
note.

Read the file {project_root}/workspaces/tool-update-monitor/latest_updates.json.

Look at the `updates` array. Each entry has a tool name, version,
previous_version, published_at, and a `body` (release notes). Write:

1. `highlights`: 2-3 Chinese sentences. Which releases matter most? Any
   breaking changes or notable features? A brief "should you upgrade" hint.
2. `per_tool`: a map of tool_id -> ONE Chinese sentence (under 60 chars)
   summarizing that tool's update.

Write the results as JSON to:
{project_root}/workspaces/tool-update-monitor/ai_highlights.json

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

If the sub-agent fails or is skipped, just omit `ai_highlights.json`; the
report will render release notes without highlights.

### Step 3: Generate the digest report

```bash
cd "{skill_directory}" && python scripts/generate_report.py \
  -i "{project_root}/workspaces/tool-update-monitor/latest_updates.json" \
  -s "{project_root}/workspaces/tool-update-monitor/ai_highlights.json" \
  -o "{project_root}/daily-digests/YYYY-MM-DD/tool-update_HH-MM.md"
```

Replace `YYYY-MM-DD` with today's date (CST) and `HH-MM` with the current
time. The `-s` flag is optional — if `ai_highlights.json` doesn't exist,
drop the flag and the report uses raw release notes only. Report timestamps
are shown in CST (UTC+8).

## Performance Notes

- First (baseline) scan: ~5-10s (11 endpoints, 6 workers, mostly 304-friendly).
- Subsequent scans with ETag cache: ~3-6s (most endpoints return 304).
- GitHub unauthenticated API is rate-limited to ~60 req/hour per IP; with
  ETag caching, repeated checks within an hour are well within limits. If you
  hit rate limits, the affected tool appears under `HTTP 403 / rate limit` in
  `error_details` and the rest of the run continues.
- AI highlights (1 sub-agent): ~10-20s when run.
- Total end-to-end: ~10-30s (no baseline) or ~5-10s (baseline).

## Completion

After generating the report, inform the user:

- Whether this was a baseline run (first check, recorded current versions) or
  a normal run, and how many new releases were found.
- Which tools/categories had updates (with counts), or "no new versions since
  last check".
- The path to the generated report file.
- A brief highlight: name the 1-2 most notable new releases (the AI
  highlights note helps here; otherwise pick from the highest-version jumps).
- Note any tools that failed to check (especially ZCode's HTML scrape or
  GitHub rate limits) so the user knows the coverage isn't 100%.
- Mention they can filter next time: `--category 网络代理` or `--tool v2rayn`,
  or force a fresh baseline with `--force-baseline`.
