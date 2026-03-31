#!/usr/bin/env python3
"""Generate a Markdown digest report from Wechat2RSS article updates.

Reads latest_updates.json and optional AI summaries, outputs a
category-grouped Markdown report.
"""

import argparse
import json
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path


# Category display order
CATEGORY_ORDER = ["安全", "开发", "其他", "用户提交"]


def load_json(path):
    """Load a JSON file, return empty dict/list on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def build_summary_map(summaries_data):
    """Build a lookup map from AI summaries data.

    Key: (account_name, article_title), Value: summary text
    """
    if not summaries_data or "summaries" not in summaries_data:
        return {}
    result = {}
    for item in summaries_data["summaries"]:
        key = (item.get("account_name", ""), item.get("article_title", ""))
        result[key] = item.get("ai_summary", "")
    return result


def group_by_category(updates):
    """Group updates by category, preserving CATEGORY_ORDER."""
    groups = OrderedDict()
    for cat in CATEGORY_ORDER:
        groups[cat] = []
    for update in updates:
        cat = update.get("category", "其他")
        if cat not in groups:
            groups[cat] = []
        groups[cat].append(update)
    return groups


def generate_report(updates_data, summary_map):
    """Generate the full Markdown report."""
    meta = updates_data.get("metadata", {})
    updates = updates_data.get("updates", [])

    now = datetime.now(timezone.utc)
    report_time = now.strftime("%Y-%m-%d %H:%M")

    lines = []

    # Header
    lines.append(f"# WeChat Official Account Update Digest - {report_time}")
    lines.append("")
    checked = meta.get("checked_count", 0)
    hours = meta.get("hours", 24)
    update_count = meta.get("update_count", len(updates))
    lines.append(f"> Checked {checked} accounts, time range {hours} hours, found {update_count} updates")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Group by category
    groups = group_by_category(updates)
    article_index = 0

    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue

        lines.append(f"## {cat} ({len(cat_updates)} updates)")
        lines.append("")
        lines.append("---")
        lines.append("")

        for update in cat_updates:
            article_index += 1
            account_name = update.get("account_name", "Unknown")
            article_title = update.get("article_title", "(no title)")
            article_url = update.get("article_url", "")
            pub_date = update.get("pub_date", "")
            summary_text = update.get("summary_text", "")

            # Look up AI summary
            key = (account_name, article_title)
            ai_summary = summary_map.get(key, "")

            lines.append(f"### {article_index}. {account_name}")
            lines.append("")
            lines.append(f"**Article**: {article_title}")
            lines.append("")
            if article_url:
                lines.append(f"**Link**: {article_url}")
                lines.append("")
            if pub_date:
                lines.append(f"**Published**: {pub_date}")
                lines.append("")
            if ai_summary:
                lines.append(f"**AI Summary**: {ai_summary}")
                lines.append("")
            elif summary_text:
                # Show first 200 chars of description as fallback
                fallback = summary_text[:200] + ("..." if len(summary_text) > 200 else "")
                lines.append(f"**Summary**: {fallback}")
                lines.append("")

            lines.append("---")
            lines.append("")

    # Footer
    lines.append(f"*Generated at {report_time} UTC*")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate WeChat RSS digest report")
    parser.add_argument("-i", "--input", required=True, help="Input latest_updates.json path")
    parser.add_argument("-s", "--summaries", default=None, help="AI summaries JSON path")
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
        # Write a minimal report
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# WeChat Official Account Update Digest - {now}\n\n")
            f.write(f"> No new articles found in the checked time window.\n")
        return

    # Load AI summaries (optional)
    summary_map = {}
    if args.summaries:
        summaries_data = load_json(args.summaries)
        if summaries_data:
            summary_map = build_summary_map(summaries_data)
            print(f"Loaded {len(summary_map)} AI summaries")

    # Generate report
    report = generate_report(updates_data, summary_map)

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"Report written to {output_path} ({update_count} articles)")


if __name__ == "__main__":
    main()
