---
name: tool-update-monitor
version: "1.4.0"
description: |
  Collect and save new releases of developer tools and operating systems to
  latest_updates.json. This is the COLLECTION layer only: it detects new
  versions and writes them out. It does NOT summarize or render a report —
  use the daily-digest skill for that.

  Covers: ZCode, VS Code, Claude Code, OpenAI Codex CLI, OpenClaw, Hermes
  Agent, OpenCode, Homebrew, Warp, Clash Verge Rev, v2rayN, Windows 11,
  macOS. Sources are GitHub Releases, the npm registry, Microsoft's Windows
  release-health page, Apple's support page, and (for ZCode) the official
  changelog page.

  Use this skill whenever the user wants to CHECK or COLLECT raw tool/CLI/
  app/OS version updates without a report. This includes:
  - Checking whether any monitored tool released a new version
  - Any mention of 工具更新 / 版本检查 / 检查更新 / 新版本 / 工具有没有更新（不含"日报/摘要"）
  - System/OS update checks: Windows 11 / 系统更新检查 / macOS / 苹果系统更新 / 补丁 / Patch Tuesday
  - Questions like "zcode/vscode/claude code/codex/openclaw/hermes/opencode/
    homebrew/warp/clash verge/v2rayn/windows/macOS 最近有更新吗"

  Do NOT trigger for: generating a digest/report/summary (use daily-digest),
  installing/upgrading the tools themselves, configuring them, general product
  news/blog posts, comparing tool features, or questions about a single tool's
  usage that aren't about version updates.
---

# Tool Update Monitor Skill (Collection Layer)

Collect new releases of **13 developer tools and operating systems** and
**save** them to `latest_updates.json`. This skill is **collection only** — it
does not summarize or render a report. To turn the collected data into a
unified AI-summarized daily digest, run the **daily-digest** skill.

Detection is **last-seen version based**: the first run records a baseline;
subsequent runs only surface versions newer than what was last recorded, so the
reader sees "truly new" releases rather than everything published in a fixed window.

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
{skill_directory}/                # This skill's directory (inside the plugin)
  SKILL.md                  # This file
  scripts/
    check_updates.py        # Fetch all sources; compute NEW vs last-seen
  references/
    tools.json              # Curated tool list (edit to add/remove tools)

{project_root}/                    # The user's current project (cwd at run time)
  workspaces/daily-digests/data/tool-update-monitor/   # Runtime intermediate files (gitignored)
    .http_cache.json               # ETag/If-Modified-Since per source URL
    .last_seen.json                # { tool_id: { version, published_at, checked_at } }
    latest_updates.json            # ← THIS skill's output (input to daily-digest)
```

## How "new" detection works

- **Baseline run** (no `.last_seen.json`, or `--force-baseline`): records the
  current version of every tool and reports **zero** new updates. This avoids
  spamming 13 "new" entries on first use.
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
- `--workers N` — concurrent workers (default: 6). Lower it if you hit GitHub
  rate limits (the 9 GitHub-Releases-sourced tools share the same token pool).

## Execution Steps

Follow these steps in order. Replace `{skill_directory}` with the absolute
path to this skill's directory and `{project_root}` with the user's current
project (the working directory at run time).

### Step 0: Ensure workspace directories exist

```bash
mkdir -p "{project_root}/workspaces/daily-digests/data/tool-update-monitor"
```

### Step 1: Check all tools for new releases

```bash
cd "{skill_directory}" && python scripts/check_updates.py \
  --hours 168 --workers 6 \
  --output "{project_root}/workspaces/daily-digests/data/tool-update-monitor/latest_updates.json" \
  --cache "{project_root}/workspaces/daily-digests/data/tool-update-monitor/.http_cache.json" \
  --state "{project_root}/workspaces/daily-digests/data/tool-update-monitor/.last_seen.json"
```

After the script finishes, read the output JSON and check `metadata`:

- If `metadata.baseline_run` is `true`, this was the first run — the baseline
  was recorded and there are no new updates to summarize.
- If `metadata.update_count` is `0` (and not a baseline run), there's nothing
  new.
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

## Performance Notes

- First (baseline) scan: ~5-10s (13 endpoints, 6 workers, mostly 304-friendly).
- Subsequent scans with ETag cache: ~3-6s (most endpoints return 304).
- **GitHub API authentication**: set `GITHUB_ACCESS_TOKEN` in the environment
  (same env var as github-monitor). With a token, the 9 GitHub-Releases-sourced
  tools use the 5000/hour authenticated rate limit; without one they fall back
  to the 60/hour anonymous limit, where concurrent checks (`--workers 6`) can
  trigger `HTTP 403 / rate limit` errors. Token is optional but strongly
  recommended.

## Completion

After collection, inform the user:

- Whether this was a baseline run (first check, recorded current versions) or
  a normal run, and how many new releases were found.
- Which tools/categories had updates (with counts), or "no new versions since
  last check".
- The output path: `workspaces/daily-digests/data/tool-update-monitor/latest_updates.json`
- To generate a unified AI-summarized daily digest, run the **daily-digest**
  skill — it consumes this file (plus the RSS and GitHub data) and produces
  `workspaces/daily-digests/reports/YYYY-MM-DD/daily-digest_HH-MM.md`.
- Note any tools that failed to check (especially ZCode's HTML scrape or
  GitHub rate limits) so the user knows the coverage isn't 100%.
- Mention they can filter next time: `--category 网络代理` or `--tool v2rayn`,
  or force a fresh baseline with `--force-baseline`.
