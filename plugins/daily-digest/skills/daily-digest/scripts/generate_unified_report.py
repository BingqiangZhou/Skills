#!/usr/bin/env python3
"""Render the unified daily digest Markdown report.

The report mixes two presentation styles, tuned to each source's nature:

- **RSS** is high-volume (hundreds of items) and benefits from synthesis, so
  it is rendered as a **narrative**: an editor sub-agent clusters the
  per-item summaries into 5-8 cross-cutting topic narratives plus an overview
  and an "其他动态" roundup. No per-item blocks for RSS.
- **GitHub** and **工具** are low-volume and inherently structured ("which
  repo PR merged", "which tool released which version"), so they are rendered
  as a **compact list** — one tight block per item with its AI summary/key
  note (when available) and a link.

Output shape:

    # 每日信息摘要 - 2026-08-04 09:30 (CST)
    > RSS: 42 条 | GitHub: 8 条 | 工具: 3 个新版本

    ## 📊 今日概览                      ← editor overview (RSS-driven)
    ## 🔥 今日重点                      ← article topic narratives
    ## 🎧 播客精选                      ← podcast topic narratives (separate track)
    ## 📌 其他动态                      ← RSS minor-items roundup (optional)
    ## 🔧 GitHub 动态                   ← compact list of PRs/issues
    ## 🛠 工具更新                       ← compact list of releases

The underlying structured data always stays in
``workspaces/daily-digests/data/<source>/latest_updates.json`` for on-demand follow-up.

If no RSS narrative payload is available (editor step skipped/failed), the
overview degrades to a best-effort string built from whatever highlights are
on hand, but GitHub/tool lists are always rendered from their collector data.
"""

import argparse
import json
import sys
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path


# The audience of these reports is Chinese readers; display times in CST.
CST = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# Shared limits
# ---------------------------------------------------------------------------
FALLBACK_CHARS = 200            # max chars of description shown when no AI summary


# ===========================================================================
# Generic helpers
# ===========================================================================

def load_json(path):
    """Load a JSON file, return None on missing/corrupt."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _fallback_text(text, max_chars=FALLBACK_CHARS):
    """Truncate text to max_chars with ellipsis."""
    if not text:
        return ""
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def parse_iso(ts):
    """Parse an ISO-8601 timestamp to aware UTC datetime, or None."""
    if not ts:
        return None
    try:
        return datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        except ValueError:
            return None


def fmt_cst(ts):
    """Format an ISO timestamp as 'YYYY-MM-DD HH:MM' in CST, or '' if unparseable."""
    dt = parse_iso(ts)
    if dt is None:
        return ''
    return dt.astimezone(CST).strftime('%Y-%m-%d %H:%M')


# GitHub renderers
# ===========================================================================

def build_github_summary_map(summaries_data):
    """Build a {item_url: ai_summary} lookup."""
    if not summaries_data or "summaries" not in summaries_data:
        return {}
    result = {}
    for item in summaries_data["summaries"]:
        url = (item.get("item_url") or item.get("pr_url")
               or item.get("issue_url") or "")
        if url:
            result[url] = item.get("ai_summary", "")
    return result


def repo_display_name(repo_key, per_repo):
    """Look up a human-friendly name for a repo from metadata.per_repo."""
    for entry in per_repo or []:
        if entry.get('repo') == repo_key and entry.get('name'):
            return entry['name']
    return repo_key


# ===========================================================================
# Tool helpers
# ===========================================================================

def build_tool_highlights(highlights_data):
    """Build (tool_id->note map, overall string) from ai_highlights.json."""
    if not highlights_data or not isinstance(highlights_data, dict):
        return {}, ""
    per_tool = highlights_data.get("per_tool") or {}
    overall = highlights_data.get("highlights") or ""
    if not isinstance(per_tool, dict):
        per_tool = {}
    return per_tool, overall


# ===========================================================================
# Narrative rendering (the unified report's actual body)
# ===========================================================================

def _normalize_topics(raw_topics):
    """Turn a raw topics list into [{title, lead, bullets, narrative}].

    Drops the audit-only ``items[]``. Carries the scannable fields
    (``lead``, ``bullets``) to the renderer; keeps the legacy ``narrative``
    string so older editor output (one dense paragraph, no bullets) still
    renders as a paragraph.
    """
    out = []
    if not isinstance(raw_topics, list):
        return out
    for t in raw_topics:
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "").strip()
        lead = (t.get("lead") or "").strip()
        narrative = (t.get("narrative") or "").strip()
        raw_bullets = t.get("bullets")
        bullets = []
        if isinstance(raw_bullets, list):
            for b in raw_bullets:
                if isinstance(b, str):
                    b = b.strip()
                    if b:
                        bullets.append(b)
        if title or lead or bullets or narrative:
            out.append({
                "title": title,
                "lead": lead,
                "bullets": bullets,
                "narrative": narrative,
            })
    return out


def build_narrative(narrative_data):
    """Normalize the editor sub-agent's digest_narrative.json output.

    Supports two payload shapes:

    - **Split** (articles + podcasts independent): top-level ``overview``,
      ``other``, plus ``article_topics`` and ``podcast_topics`` lists.
    - **Legacy/flat**: top-level ``overview``, ``other``, ``topics`` list
      (kept for backward compatibility).

    Returns ``(overview, article_topics, podcast_topics, other)``. For the
    legacy shape, all topics land in ``article_topics`` and ``podcast_topics``
    is empty. ``other`` is a list of bullet strings when the editor complied
    with the scannable format, else a legacy paragraph string (or "").
    Tolerates a missing/partial payload by returning empty values.
    """
    if not isinstance(narrative_data, dict):
        return "", [], [], ""

    overview = (narrative_data.get("overview") or "").strip()
    raw_other = narrative_data.get("other")
    if isinstance(raw_other, list):
        # Scannable format: a list of bullet strings.
        other = [b.strip() for b in raw_other
                 if isinstance(b, str) and b.strip()]
    elif isinstance(raw_other, str):
        other = raw_other.strip()
    else:
        other = ""

    if "article_topics" in narrative_data or "podcast_topics" in narrative_data:
        article_topics = _normalize_topics(narrative_data.get("article_topics"))
        podcast_topics = _normalize_topics(narrative_data.get("podcast_topics"))
    else:
        article_topics = _normalize_topics(narrative_data.get("topics"))
        podcast_topics = []
    return overview, article_topics, podcast_topics, other


def _fallback_overview(digest_highlights_text, tool_overall, rss_insight):
    """Best-effort overview string when no narrative payload is available.

    Pulls together any cross-source highlights, tool highlights, and the RSS
    trend insight so the degraded report still says *something* useful.
    """
    snippets = []
    if digest_highlights_text:
        snippets.append(digest_highlights_text.strip())
    if tool_overall:
        snippets.append(f"工具：{tool_overall.strip()}")
    if rss_insight:
        insight_text = (rss_insight.get("trend_insight") or "").strip()
        if insight_text:
            snippets.append(insight_text)
    return "\n\n".join(snippets)


def _expected_podcast_count(rss_data):
    """Count ``source == "podcast"`` items in the RSS collector output.

    Used to tell whether the podcast narrative *should* have been produced, so
    the report can warn loudly instead of silently omitting the section when
    the editor sub-agent failed or produced empty output.
    """
    if not rss_data:
        return 0
    return sum(1 for u in rss_data.get("updates", [])
               if u.get("source") == "podcast")


def _render_topic_list(lines, heading, topics, missing_placeholder=None):
    """Render a numbered list of topic narratives under ``heading``.

    When ``topics`` is non-empty, renders the list as before. When empty:

    - If ``missing_placeholder`` is None (default): no-op (back-compat for
      callers that genuinely want silence when there's nothing to say).
    - If ``missing_placeholder`` is a string: still emit the ``## heading``
      followed by the placeholder block, so the section is never silently
      dropped — the reader always knows it was expected.
    """
    if topics:
        lines.append(f"## {heading}")
        lines.append("")
        for i, topic in enumerate(topics, 1):
            title = topic.get("title") or f"话题 {i}"
            lead = topic.get("lead") or ""
            bullets = topic.get("bullets") or []
            narrative = topic.get("narrative") or ""
            lines.append(f"### {i}. {title}")
            lines.append("")
            if bullets:
                # Scannable format: short lead + bullet list.
                if lead:
                    lines.append(lead)
                    lines.append("")
                for b in bullets:
                    lines.append(f"- {b}")
            elif narrative:
                # Backward compat: older editor output (one dense paragraph,
                # no bullets) renders as before.
                lines.append(narrative)
            else:
                lines.append("*(暂无叙述)*")
            lines.append("")
            lines.append("---")
            lines.append("")
        return

    if missing_placeholder is None:
        return
    lines.append(f"## {heading}")
    lines.append("")
    lines.append(missing_placeholder)
    lines.append("")
    lines.append("---")
    lines.append("")


# ===========================================================================
# Compact list renderers for GitHub / tool (low-volume, structured sources)
# ===========================================================================

def _compact_github_item(update, index, summary_map, lines):
    """Append one GitHub PR/issue as a tight block: title + summary.

    No meta line — the title link already carries the repo + number, and for
    the self-promotion issues / project-list PRs that dominate this feed the
    author / reactions are noise rather than signal. The block is just the
    linked title followed by the AI summary (or a short body excerpt).
    """
    title = update.get("title") or "(无标题)"
    url = update.get("html_url") or ""
    body_text = update.get("body_text") or ""
    ai_summary = summary_map.get(url, "")

    # Numbered title — link if we have a URL.
    prefix = f"- **{index}.** "
    if url:
        lines.append(f"{prefix}[{title}]({url})")
    else:
        lines.append(f"{prefix}{title}")

    # Summary: prefer AI one-liner, else a short body excerpt.
    if ai_summary:
        lines.append(f"  {ai_summary.strip()}")
    elif body_text:
        lines.append(f"  {_fallback_text(body_text)}")

    lines.append("")


def _compact_tool_release(update, index, per_tool_map, lines):
    """Append one tool release as a tight block: name `vX` + meta + key note.

    Release notes bodies can be long, so in the compact view we show only the
    AI per-tool key note (when available); the tool name itself links to the
    release (no separate link line).

    When the entry carries a ``versions`` array (the collector collapses a
    tool's multiple new versions into one entry), the version is rendered as a
    range ``v{oldest} → v{newest}`` rather than a single ``v{version}``.
    """
    name = update.get("name", "Unknown")
    version = update.get("version", "?")
    versions = update.get("versions") or []
    prev = update.get("previous_version")
    published = fmt_cst(update.get("published_at"))
    url = update.get("html_url", "")
    prerelease = update.get("prerelease", False)
    tid = update.get("tool_id", "")

    if len(versions) > 1:
        ver_label = f"`v{versions[0]}` → `v{versions[-1]}`"
    else:
        ver_label = f"`v{version}`"
    if prerelease:
        ver_label += " *(pre-release)*"
    # Tool name links directly to the release; no separate 🔗 line.
    name_label = f"[{name}]({url})" if url else name
    lines.append(f"- **{index}.** {name_label} {ver_label}")

    meta_bits = []
    if prev:
        meta_bits.append(f"from `v{prev}`")
    if published:
        meta_bits.append(published)
    if meta_bits:
        lines.append(f"  {' · '.join(meta_bits)}")

    note = per_tool_map.get(tid, "")
    if note:
        lines.append(f"  {note.strip()}")

    lines.append("")


def render_github_list(github_data, summary_map, lines):
    """Render the GitHub section as a compact list (no repo subsections).

    Items are grouped by repo with a bold repo label, but stay list-shaped.
    Returns the number of items rendered.
    """
    meta = github_data.get("metadata", {})
    updates = github_data.get("updates", [])
    update_count = meta.get("update_count", len(updates))
    per_repo = meta.get("per_repo") or []
    hours = meta.get("hours", 24)

    pr_count = sum(1 for u in updates if u.get("type") == "pulls")
    issue_count = len(updates) - pr_count

    lines.append(f"## 🔧 GitHub 动态（{update_count} 条）")
    lines.append("")
    lines.append(f"> 时间窗 {hours}h · PR {pr_count} / Issue {issue_count}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not updates:
        lines.append("本轮无新的 GitHub 动态。")
        lines.append("")
        return 0

    # Group by repo, preserving first-seen order (updates are newest-first).
    groups = OrderedDict()
    for u in updates:
        groups.setdefault(u.get("repo", "unknown/unknown"), []).append(u)

    multi_repo = len(groups) > 1
    idx = 0
    for repo_key, repo_updates in groups.items():
        if multi_repo:
            name = repo_display_name(repo_key, per_repo)
            label = name if name == repo_key else f"{name}（`{repo_key}`）"
            lines.append(f"**{label}**（{len(repo_updates)}）")
            lines.append("")
        for u in repo_updates:
            idx += 1
            _compact_github_item(u, idx, summary_map, lines)
    return idx


def _merge_tool_updates(updates):
    """Collapse multiple entries for the same tool into one.

    The collector already dedupes a tool's new versions into a single entry
    carrying a ``versions`` array, but this also tolerates older collector
    output where one tool appears as several entries (e.g. multiple beta tags
    for the same version). All entries for a tool are merged into the one with
    the highest ``version``; a ``versions`` array (oldest→newest) is added when
    more than one distinct version is present. Order is preserved by each
    tool's first appearance in ``updates``.
    """
    by_tool = OrderedDict()
    for u in updates:
        tid = u.get("tool_id") or u.get("name") or ""
        if tid not in by_tool:
            by_tool[tid] = {
                "entries": [],
                "versions": [],
            }
        by_tool[tid]["entries"].append(u)
        v = u.get("version")
        if v and v not in by_tool[tid]["versions"]:
            by_tool[tid]["versions"].append(v)

    merged = []
    for tid, info in by_tool.items():
        entries = info["entries"]
        # Representative = highest version (ties → last seen, which keeps the
        # newest published_at from collector ordering).
        best = entries[0]
        for u in entries[1:]:
            if (u.get("version", "") > best.get("version", "")):
                best = u
        merged_entry = dict(best)
        versions = info["versions"]
        if len(versions) > 1:
            # Collector supplies versions oldest→newest; entries are
            # newest→oldest, so sort defensively by version tuple.
            try:
                versions = sorted(versions, key=_version_key)
            except Exception:
                pass
            merged_entry["versions"] = versions
            merged_entry["version"] = versions[-1]
        else:
            merged_entry["version"] = best.get("version")
        merged.append(merged_entry)
    return merged


def _version_key(v):
    """Comparison key for a dotted version string (ints; non-numeric → 0)."""
    if not v:
        return (0,)
    parts = []
    for p in str(v).split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def render_tool_list(tool_data, per_tool_map, overall_highlight, lines):
    """Render the tool section as a compact list grouped by category.

    Returns the number of tools rendered.
    """
    meta = tool_data.get("metadata", {})
    updates = tool_data.get("updates", [])
    categories_order = meta.get("categories_order") or []
    checked = meta.get("checked_count", 0)
    errors = meta.get("error_count", 0)
    hours = meta.get("hours", 168)

    # Collapse multiple entries per tool into one (with a versions range).
    updates = _merge_tool_updates(updates)
    tool_count = len(updates)

    lines.append("## 🛠 工具更新（{}）".format(
        "基线建立" if meta.get("baseline_run") else f"{tool_count} 个新版本"))
    lines.append("")

    if meta.get("baseline_run"):
        lines.append(f"> 首次运行：已记录 {checked} 个工具当前版本作为基线，本次不报告新版本。")
        if errors:
            lines.append(f"> ⚠️ {errors} 个工具获取失败。")
        lines.append("")
        lines.append("---")
        lines.append("")
        return 0

    lines.append(f"> 检查 {checked} 个工具（约 {hours}h）· {errors} 个出错")
    lines.append("")
    if overall_highlight:
        lines.append(f"**速览**：{overall_highlight}")
        lines.append("")
    lines.append("---")
    lines.append("")

    if not updates:
        lines.append("最近无新版本发布。")
        lines.append("")
        return 0

    groups = OrderedDict()
    for cat in categories_order:
        groups[cat] = []
    for u in updates:
        cat = u.get("category") or "其他 / Other"
        groups.setdefault(cat, []).append(u)

    idx = 0
    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue
        lines.append(f"**{cat}**（{len(cat_updates)}）")
        lines.append("")
        for u in cat_updates:
            idx += 1
            _compact_tool_release(u, idx, per_tool_map, lines)
    return idx

def generate_unified_report(rss_data, github_data, github_summary_map,
                            tool_data, tool_per_tool, tool_overall,
                            narrative_data, fallback_overview):
    """Assemble the unified Markdown report.

    Parameters
    ----------
    rss_data, github_data, tool_data : dict | None
        Collector outputs. RSS feeds the narrative (when available); GitHub
        and tool feed compact list sections rendered from their data.
    github_summary_map : dict
        ``{item_url: ai_summary}`` for GitHub items.
    tool_per_tool, tool_overall : dict, str
        Per-tool key-note map + overall highlight string for tools.
    narrative_data : dict | None
        The editor sub-agent's ``digest_narrative.json`` (RSS-only now).
        Drives the overview + 今日重点 + 其他动态 sections. When absent the
        overview degrades to ``fallback_overview``.
    fallback_overview : str
        Best-effort overview string used only when no narrative payload.
    """
    now = datetime.now(CST)
    report_time = now.strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append(f"# 每日信息摘要 - {report_time} (CST)")
    lines.append("")

    # Source-count summary line (computed from collector metadata). For tools,
    # count DISTINCT tool_ids (the collector emits one entry per tool, but older
    # output could carry several per tool — collapse to a tool count).
    rss_count = len(rss_data.get("updates", [])) if rss_data else 0
    gh_count = (github_data.get("metadata", {}).get("update_count", 0)
                if github_data else 0)
    tool_meta = tool_data.get("metadata", {}) if tool_data else {}
    if tool_meta.get("baseline_run"):
        tool_count = 0
    else:
        tool_updates = tool_data.get("updates", []) if tool_data else []
        seen = {u.get("tool_id") or u.get("name") for u in tool_updates}
        tool_count = len(seen)

    parts = []
    if rss_data is not None:
        parts.append(f"RSS: {rss_count} 条")
    if github_data is not None:
        parts.append(f"GitHub: {gh_count} 条")
    if tool_data is not None:
        parts.append(f"工具: {tool_count} 个新版本")
    if parts:
        lines.append("> " + " | ".join(parts))
        lines.append("")

    # --- Overview + 今日重点(文章) + 播客精选 + 其他动态 (or fallback) ---
    overview, article_topics, podcast_topics, other = build_narrative(narrative_data)
    has_narrative = bool(overview or article_topics or podcast_topics or other)
    expected_podcasts = _expected_podcast_count(rss_data)

    lines.append("## 📊 今日概览")
    lines.append("")
    if has_narrative and overview:
        lines.append(overview)
    elif fallback_overview:
        lines.append(fallback_overview)
    else:
        lines.append("*(本轮未生成概览)*")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not has_narrative:
        lines.append("> ⚠️ 本次未生成 RSS 话题叙事（主编步骤缺失或失败），"
                     "如需可重跑 daily-digest 补齐。")
        lines.append("")

    # Article topics never silently vanish either: if the collector had
    # non-podcast items but no article narrative came back, surface it.
    expected_articles = (len(rss_data.get("updates", [])) if rss_data else 0) \
        - expected_podcasts
    article_placeholder = None
    if not article_topics and expected_articles > 0:
        article_placeholder = (
            f"> ⚠️ 本轮采集到 {expected_articles} 条文章/微信公众号更新，"
            "但未生成文章叙事（文章主编步骤缺失或失败），可重跑 daily-digest 补齐。"
        )

    # Podcast section: NEVER silently drop. If the collector saw podcasts but
    # the editor produced no podcast_topics, emit the heading + a clear warning
    # so a missing podcast track is obvious (not invisible).
    podcast_placeholder = None
    if not podcast_topics and expected_podcasts > 0:
        podcast_placeholder = (
            f"> ⚠️ 本轮采集到 {expected_podcasts} 条播客，"
            "但未生成播客叙事（播客主编步骤缺失或失败），可重跑 daily-digest 补齐。"
        )
        print(f"WARNING: expected {expected_podcasts} podcasts but "
              f"podcast_topics is empty; emitting placeholder in report.",
              file=sys.stderr)

    _render_topic_list(lines, "🔥 今日重点", article_topics,
                       missing_placeholder=article_placeholder)
    _render_topic_list(lines, "🎧 播客精选", podcast_topics,
                       missing_placeholder=podcast_placeholder)

    if other:
        lines.append("## 📌 其他动态")
        lines.append("")
        if isinstance(other, list):
            # Scannable format: one bullet per minor item.
            for b in other:
                lines.append(f"- {b}")
        else:
            # Backward compat: older editor output (a single dense
            # paragraph) renders as before.
            lines.append(str(other))
        lines.append("")
        lines.append("---")
        lines.append("")

    # --- GitHub: compact list (low-volume, structured) ---
    if github_data is not None:
        render_github_list(github_data, github_summary_map, lines)

    # --- Tool: compact list (low-volume, structured) ---
    if tool_data is not None:
        render_tool_list(tool_data, tool_per_tool, tool_overall, lines)

    lines.append(f"*报告生成时间: {report_time} CST (UTC+8)*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate the unified daily digest report (narrative-first)"
    )
    parser.add_argument("--rss-input", default=None,
                        help="RSS latest_updates.json path")
    parser.add_argument("--github-input", default=None,
                        help="GitHub latest_updates.json path")
    parser.add_argument("--github-summaries", default=None,
                        help="GitHub ai_summaries.json path (for compact-list notes)")
    parser.add_argument("--tool-input", default=None,
                        help="Tool latest_updates.json path")
    parser.add_argument("--tool-highlights", default=None,
                        help="Tool ai_highlights.json path (for compact-list notes)")
    parser.add_argument("--digest-narrative", default=None,
                        help="digest_narrative.json path (editor sub-agent output)")
    parser.add_argument("--digest-highlights", default=None,
                        help="(deprecated fallback) cross-source digest_highlights.json")
    parser.add_argument("--rss-insight", default=None,
                        help="(fallback) RSS trend_insight.json")
    parser.add_argument("-o", "--output", required=True,
                        help="Output Markdown file path")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    provided = [args.rss_input, args.github_input, args.tool_input]
    if not any(provided):
        print("Error: provide at least one of --rss-input / --github-input / "
              "--tool-input", file=sys.stderr)
        sys.exit(1)

    # --- RSS (counts + fallback insight) ---
    rss_data = None
    rss_insight = None
    if args.rss_input:
        rss_data = load_json(args.rss_input)
        if not rss_data:
            print(f"WARNING: cannot load RSS input {args.rss_input}.",
                  file=sys.stderr)
            rss_data = None
        elif args.rss_insight:
            rss_insight = load_json(args.rss_insight)
            if rss_insight:
                print("Loaded RSS trend insight (fallback only)")

    # --- GitHub (counts + summary map for compact list) ---
    github_data = None
    github_summary_map = {}
    if args.github_input:
        github_data = load_json(args.github_input)
        if not github_data:
            print(f"WARNING: cannot load GitHub input {args.github_input}.",
                  file=sys.stderr)
            github_data = None
        elif args.github_summaries:
            sd = load_json(args.github_summaries)
            if sd:
                github_summary_map = build_github_summary_map(sd)
                print(f"Loaded {len(github_summary_map)} GitHub AI summaries")

    # --- Tool (counts + per-tool notes for compact list) ---
    tool_data = None
    tool_per_tool, tool_overall = ({}, "")
    if args.tool_input:
        tool_data = load_json(args.tool_input)
        if not tool_data:
            print(f"WARNING: cannot load tool input {args.tool_input}.",
                  file=sys.stderr)
            tool_data = None
        elif args.tool_highlights:
            hd = load_json(args.tool_highlights)
            if hd:
                tool_per_tool, tool_overall = build_tool_highlights(hd)
                print(f"Loaded AI highlights for {len(tool_per_tool)} tool(s)")

    # --- Narrative payload (primary, RSS-driven) ---
    narrative_data = None
    if args.digest_narrative:
        narrative_data = load_json(args.digest_narrative)
        if narrative_data:
            print("Loaded digest narrative")
        else:
            print("WARNING: cannot load digest narrative; will fall back.",
                  file=sys.stderr)

    # --- Legacy highlights (fallback overview only) ---
    digest_highlights_text = ""
    if args.digest_highlights:
        dh = load_json(args.digest_highlights)
        if dh:
            digest_highlights_text = dh.get("highlights", "") or ""
            if digest_highlights_text:
                print("Loaded cross-source digest highlights (fallback only)")

    fallback_overview = _fallback_overview(
        digest_highlights_text, tool_overall, rss_insight)
    if not narrative_data and fallback_overview:
        print("Using fallback overview (no narrative payload).")

    report = generate_unified_report(
        rss_data, github_data, github_summary_map,
        tool_data, tool_per_tool, tool_overall,
        narrative_data, fallback_overview,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    # Summary of what was rendered
    rendered = []
    if rss_data is not None:
        rendered.append(f"RSS({len(rss_data.get('updates', []))})")
    if github_data is not None:
        rendered.append(f"GitHub({github_data.get('metadata', {}).get('update_count', 0)})")
    if tool_data is not None:
        rendered.append(f"Tool({tool_data.get('metadata', {}).get('update_count', 0)})")
    mode = "narrative" if narrative_data else "fallback"
    # Podcast narrative coverage — surfaces a missing track at a glance.
    if rss_data is not None:
        _, _, pod_topics, _ = build_narrative(narrative_data)
        expected = _expected_podcast_count(rss_data)
        if expected > 0:
            rendered.append(f"Podcasts({len(pod_topics)} topics / {expected} eps)")
    print(f"Unified report written to {output_path} "
          f"({', '.join(rendered)}, {mode})")


if __name__ == "__main__":
    main()
