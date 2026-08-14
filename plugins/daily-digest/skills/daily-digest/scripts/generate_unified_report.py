#!/usr/bin/env python3
"""Render the unified daily digest Markdown report.

The report mixes two presentation styles, tuned to each source's nature:

- **RSS** is high-volume (hundreds of items) and benefits from synthesis, so
  it is rendered as a **narrative**: an editor sub-agent clusters the
  per-item summaries into 5-8 cross-cutting topic narratives plus an overview
  and an "其他动态" roundup. No per-item blocks for RSS.
  → normalization + rendering live in ``narrative.py``.
- **GitHub** and **工具** are low-volume and inherently structured ("which
  repo PR merged", "which tool released which version"), so they are rendered
  as a **compact list** — one tight block per item with its AI summary/key
  note (when available) and a link.
  → rendering lives in ``compact_lists.py``.

This file keeps the report assembly and the CLI; shared helpers (atomic JSON
writes, CST time formatting) live in ``io_utils.py``.

Output shape:

    # 每日信息摘要 - 2026-08-04 09:30 (CST)
    > RSS: 42 条 | GitHub: 8 条 | 工具: 3 个新版本

    ## 📊 今日概览                      ← editor overview (RSS-driven)
    ## 🔥 今日重点                      ← article topic narratives
    ## 🎧 播客精选                      ← per-episode roundup (grouped by show)
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
import os
import sys
from datetime import datetime
from pathlib import Path

import io_utils
import narrative
import compact_lists
from io_utils import CST, fmt_cst  # noqa: F401  (fmt_cst re-exported)
from narrative import build_narrative, fallback_overview
from compact_lists import (
    build_github_summary_map,
    build_tool_highlights,
    render_github_list,
    render_tool_list,
)

# Backward-compat aliases for the pre-split private names — tests and any
# external callers keep working.
_normalize_topics = narrative.normalize_topics
_normalize_str_list = narrative.normalize_str_list
_normalize_podcast_episodes = narrative.normalize_podcast_episodes
_fallback_overview = fallback_overview
_expected_podcast_count = narrative.expected_podcast_count
_merge_tool_updates = compact_lists.merge_tool_updates
_version_key = compact_lists.version_key
_fallback_text = compact_lists.fallback_text


def load_json(path):
    """Load a JSON file, return None on missing/corrupt.

    UnicodeDecodeError is caught too: it is a ValueError (NOT an OSError),
    so a sub-agent writing GBK used to escape this loader as a traceback.
    """
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError,
            UnicodeDecodeError, OSError):
        return None


def generate_unified_report(rss_data, github_data, github_summary_map,
                            tool_data, tool_per_tool, tool_overall,
                            narrative_data, fallback_overview,
                            narrative_tuple=None):
    """Assemble the unified Markdown report.

    Parameters
    ----------
    rss_data, github_data, tool_data : dict | None
        Collector outputs (``latest_updates.json``); None omits the section.
    github_summary_map : dict
        {item_url: ai_summary} for the GitHub compact list.
    tool_per_tool : dict, tool_overall : str
        Per-tool AI notes and the overall highlights string.
    narrative_data : dict | None
        The editor sub-agents' merged ``digest_narrative.json``.
        Drives the overview + 今日重点 + 其他动态 sections. When absent the
        overview degrades to ``fallback_overview``.
    fallback_overview : str
        Best-effort overview string used only when no narrative payload.
    narrative_tuple : tuple | None
        Precomputed ``build_narrative(narrative_data)`` result — callers that
        also need the normalized tracks (e.g. main's coverage summary) pass
        it to avoid normalizing the payload twice.
    """
    now = datetime.now(CST)
    report_time = now.strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append(f"# 每日信息摘要 - {report_time} (CST)")
    lines.append("")

    # Source-count summary line (computed from collector metadata). For tools,
    # count DISTINCT tool_ids (the collector emits one entry per tool, but older
    # output could carry several per tool — collapse to a tool count).
    rss_count = len(rss_data.get("updates") or []) if rss_data else 0
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
    if narrative_tuple is None:
        narrative_tuple = build_narrative(narrative_data)
    (overview, article_topics, podcast_topics,
     podcast_episodes, podcast_other, other) = narrative_tuple
    has_narrative = bool(overview or article_topics or podcast_topics
                         or podcast_episodes or other)
    expected_podcasts = narrative.expected_podcast_count(rss_data)

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

    # Article section: if the collector saw non-podcast items but no article
    # narrative came back, surface it.
    expected_articles = (len(rss_data.get("updates") or []) if rss_data else 0) \
        - expected_podcasts
    article_placeholder = None
    if not article_topics and expected_articles > 0:
        article_placeholder = (
            f"> ⚠️ 本轮采集到 {expected_articles} 条文章/微信公众号更新，"
            "但未生成文章叙事（文章主编步骤缺失或失败），可重跑 daily-digest 补齐。"
        )

    # Podcast section: NEVER silently drop. If the collector saw podcasts but
    # the editor produced no podcast narrative (neither the new per-episode
    # track nor the legacy topic track), emit the heading + a clear warning so
    # a missing podcast track is obvious (not invisible).
    podcast_placeholder = None
    has_podcast = bool(podcast_episodes or podcast_topics)
    if not has_podcast and expected_podcasts > 0:
        podcast_placeholder = (
            f"> ⚠️ 本轮采集到 {expected_podcasts} 条播客，"
            "但未生成播客叙事（播客主编步骤缺失或失败），可重跑 daily-digest 补齐。"
        )
        print(f"WARNING: expected {expected_podcasts} podcasts but "
              f"podcast narrative is empty; emitting placeholder in report.",
              file=sys.stderr)

    narrative.render_topic_list(lines, "🔥 今日重点", article_topics,
                                missing_placeholder=article_placeholder)
    narrative.render_podcast_episodes(lines, "🎧 播客精选",
                                      podcast_episodes, podcast_other,
                                      podcast_topics,
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

    io_utils.setup_win32_stdout()

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

    # --- Deprecated cross-source highlights (fallback overview only) ---
    digest_highlights_text = ""
    if args.digest_highlights:
        dh = load_json(args.digest_highlights)
        if dh:
            digest_highlights_text = dh.get("highlights", "") or ""
            if digest_highlights_text:
                print("Loaded cross-source digest highlights (fallback only)")

    fallback_overview = narrative.fallback_overview(
        digest_highlights_text, tool_overall, rss_insight)
    if not narrative_data and fallback_overview:
        print("Using fallback overview (no narrative payload).")

    # Normalize the narrative payload ONCE — both the report assembly and
    # the coverage summary below need the tracks.
    narr_tuple = build_narrative(narrative_data)

    report = generate_unified_report(
        rss_data, github_data, github_summary_map,
        tool_data, tool_per_tool, tool_overall,
        narrative_data, fallback_overview, narrative_tuple=narr_tuple,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_name(output_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(report)
    os.replace(tmp, output_path)

    # Summary of what was rendered
    rendered = []
    if rss_data is not None:
        rendered.append(f"RSS({len(rss_data.get('updates') or [])})")
    if github_data is not None:
        rendered.append(f"GitHub({github_data.get('metadata', {}).get('update_count', 0)})")
    if tool_data is not None:
        rendered.append(f"Tool({tool_data.get('metadata', {}).get('update_count', 0)})")
    mode = "narrative" if narrative_data else "fallback"
    # Podcast narrative coverage — surfaces a missing track at a glance.
    if rss_data is not None:
        _, _, pod_topics, pod_eps, _, _ = narr_tuple
        expected = narrative.expected_podcast_count(rss_data)
        if expected > 0:
            if pod_eps:
                rendered.append(f"Podcasts({len(pod_eps)} eps / {expected})")
            else:
                rendered.append(f"Podcasts({len(pod_topics)} topics / {expected} eps)")
    print(f"Unified report written to {output_path} "
          f"({', '.join(rendered)}, {mode})")


if __name__ == "__main__":
    main()
