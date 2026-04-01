#!/usr/bin/env python3
"""Generate a Markdown tech daily digest report.

Reads latest_updates.json, optional AI summaries, and trend insight,
outputs a category-grouped Markdown report with trend insight section.
"""

import argparse
import json
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path


# Category display order
CATEGORY_ORDER = ["AI/ML", "芯片硬件", "云计算", "开源", "网络安全", "综合科技"]


def load_json(path):
    """Load a JSON file, return empty dict/list on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def build_summary_map(summaries_data):
    """Build a lookup map from AI summaries data.

    Returns a dict keyed by url for fast lookup.
    """
    if not summaries_data or "summaries" not in summaries_data:
        return {}

    result = {}
    for item in summaries_data["summaries"]:
        url = item.get("url", "")
        if url:
            result[url] = {
                "ai_summary": item.get("ai_summary", ""),
                "category": item.get("category", ""),
            }
    return result


def group_by_category(updates, summary_map):
    """Group updates by category, using AI-assigned category when available.

    Returns (ordered_groups, hn_items) where groups is OrderedDict of
    category -> items and hn_items is a list of Hacker News items.
    """
    groups = OrderedDict()
    for cat in CATEGORY_ORDER:
        groups[cat] = []
    groups["其他"] = []

    hn_items = []

    for update in updates:
        source_cat = update.get("source_category", "")

        if source_cat == "Hacker News":
            hn_items.append(update)
            continue

        # Check if AI assigned a different category
        url = update.get("url", "")
        ai_info = summary_map.get(url, {})
        ai_cat = ai_info.get("category", "")

        # Use AI category if it's in our known categories, otherwise use source category
        final_cat = ai_cat if ai_cat in groups else source_cat

        if final_cat not in groups:
            final_cat = "其他"

        groups[final_cat].append(update)

    return groups, hn_items


def generate_report(updates_data, summary_map, trend_insight):
    """Generate the full Markdown report."""
    meta = updates_data.get("metadata", {})
    updates = updates_data.get("updates", [])

    now = datetime.now(timezone.utc)
    report_date = now.strftime("%Y-%m-%d")
    report_time = now.strftime("%Y-%m-%d %H:%M")

    lines = []

    # Header
    lines.append(f"# AI 科技日报 - {report_date}")
    lines.append("")
    checked = meta.get("checked_count", 0)
    hours = meta.get("hours", 24)
    update_count = meta.get("update_count", len(updates))
    lines.append(f"> 共检查 {checked} 个信息源，时间范围 {hours} 小时，发现 {update_count} 条更新")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Trend insight section
    if trend_insight:
        insight_text = trend_insight.get("trend_insight", "")
        if insight_text:
            lines.append("## 今日趋势洞察")
            lines.append("")
            lines.append(insight_text)
            lines.append("")
            lines.append("---")
            lines.append("")

    # Group by category
    groups, hn_items = group_by_category(updates, summary_map)
    article_index = 0

    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue

        lines.append(f"## {cat} ({len(cat_updates)} 条)")
        lines.append("")

        for update in cat_updates:
            article_index += 1
            source_name = update.get("source_name", "Unknown")
            title = update.get("title", "(no title)")
            url = update.get("url", "")
            pub_date = update.get("published", "")
            description = update.get("description", "")

            # Look up AI summary by URL
            ai_info = summary_map.get(url, {})
            ai_summary = ai_info.get("ai_summary", "")

            lines.append(f"### {article_index}. {title}")
            lines.append("")
            lines.append(f"**来源**: {source_name} | **发布时间**: {pub_date}")
            lines.append("")
            if url:
                lines.append(f"**链接**: {url}")
                lines.append("")
            if ai_summary:
                lines.append(f"**AI 摘要**: {ai_summary}")
                lines.append("")
            elif description:
                fallback = description[:200] + ("..." if len(description) > 200 else "")
                lines.append(f"**摘要**: {fallback}")
                lines.append("")

            lines.append("---")
            lines.append("")

    # Hacker News section
    if hn_items:
        lines.append("## Hacker News 热门 (" + str(len(hn_items)) + " 条)")
        lines.append("")

        for item in hn_items:
            article_index += 1
            title = item.get("title", "(no title)")
            url = item.get("url", "")
            points = item.get("hn_points")
            comments = item.get("hn_comments")

            # Look up AI summary
            ai_info = summary_map.get(url, {})
            ai_summary = ai_info.get("ai_summary", "")

            # Build stats string
            stats_parts = []
            if points is not None:
                stats_parts.append(f"points: {points}")
            if comments is not None:
                stats_parts.append(f"comments: {comments}")
            stats_str = ", ".join(stats_parts)

            lines.append(f"### {article_index}. {title}")
            lines.append("")
            if stats_str:
                lines.append(f"**热度**: {stats_str}")
                lines.append("")
            if url:
                lines.append(f"**链接**: {url}")
                lines.append("")
            if ai_summary:
                lines.append(f"**AI 摘要**: {ai_summary}")
                lines.append("")

            lines.append("---")
            lines.append("")

    # Footer
    lines.append(f"*报告生成时间: {report_time} UTC*")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate tech daily digest report")
    parser.add_argument("-i", "--input", required=True, help="Input latest_updates.json path")
    parser.add_argument("-s", "--summaries", default=None, help="AI summaries JSON path")
    parser.add_argument("--insight", default=None, help="Trend insight JSON path")
    parser.add_argument("-o", "--output", required=True, help="Output Markdown file path")
    args = parser.parse_args()

    # Load updates
    updates_data = load_json(args.input)
    if not updates_data:
        print(f"Error: Cannot load updates from {args.input}", file=sys.stderr)
        sys.exit(1)

    update_count = updates_data.get("metadata", {}).get("update_count", 0)
    if update_count == 0:
        print("No updates found. Nothing to report.")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# AI 科技日报 - {now}\n\n")
            f.write(f"> 未在检查的时间窗口内发现新文章。\n")
        return

    # Load AI summaries (optional)
    summary_map = {}
    if args.summaries:
        summaries_data = load_json(args.summaries)
        if summaries_data:
            summary_map = build_summary_map(summaries_data)
            print(f"Loaded {len(summary_map)} AI summaries")

    # Load trend insight (optional)
    trend_insight = None
    if args.insight:
        trend_insight = load_json(args.insight)
        if trend_insight:
            print("Loaded trend insight")

    # Generate report
    report = generate_report(updates_data, summary_map, trend_insight)

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"Report written to {output_path} ({update_count} articles)")


if __name__ == "__main__":
    main()
