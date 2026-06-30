#!/usr/bin/env python3
"""Generate a Markdown digest report from Wechat2RSS article updates.

Reads latest_updates.json and optional AI summaries, outputs a
category-grouped Markdown report.
"""

import argparse
import json
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path


# Category display order
CATEGORY_ORDER = ["安全", "开发", "其他", "用户提交"]

# The audience of these reports is Chinese readers; display times in CST.
CST = timezone(timedelta(hours=8))

# Security (安全) is by far the largest category (~326 feeds). To keep the
# report readable we further split it into sub-groups by keyword. Order matters:
# the first matching sub-group wins, so put the most specific terms first.
# Each entry is (sub-group label, compiled regex of keywords matched against
# the account name + title).
SECURITY_SUBGROUPS = [
    ("漏洞情报 / CVE",
     re.compile(r"CVE-?\d|漏洞|0day|0-day|exp\b|漏洞预警|威胁情报|advisory", re.IGNORECASE)),
    ("应急响应 / 攻防",
     re.compile(r"应急响应|攻防|红队|蓝队|渗透|黑客|攻击|钓鱼|勒索|ransom|apt\b|蓝军", re.IGNORECASE)),
    ("逆向 / 二进制",
     re.compile(r"逆向|二进制|exploit|malware|样本分析|脱壳|rootkit|kernel", re.IGNORECASE)),
    ("移动 / 物联网",
     re.compile(r"android|ios|apk|移动安全|物联网|iot|车联网|智能设备", re.IGNORECASE)),
    ("安全工具 / 开发",
     re.compile(r"工具|平台|引擎|检测|扫描|waf|ids|防火墙|态势感知|soc\b", re.IGNORECASE)),
    ("安全资讯 / 其他", None),  # fallback: everything else in 安全
]


def security_subgroup(update):
    """Return the sub-group label for a 安全 update, based on title + account."""
    haystack = f"{update.get('account_name', '')} {update.get('article_title', '')}"
    for label, pattern in SECURITY_SUBGROUPS:
        if pattern is None:
            return label
        if pattern.search(haystack):
            return label
    return SECURITY_SUBGROUPS[-1][0]


def load_json(path):
    """Load a JSON file, return empty dict/list on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def build_summary_map(summaries_data):
    """Build a lookup map from AI summaries data.

    Returns a dict keyed by article_url for fast lookup.
    """
    if not summaries_data or "summaries" not in summaries_data:
        return {}
    result = {}
    for item in summaries_data["summaries"]:
        url = item.get("article_url", "")
        if url:
            result[url] = item.get("ai_summary", "")
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


def render_article(update, article_index, summary_map, lines):
    """Append a single article block to `lines`."""
    account_name = update.get("account_name", "Unknown")
    article_title = update.get("article_title", "(no title)")
    article_url = update.get("article_url", "")
    pub_date = update.get("pub_date", "")
    summary_text = update.get("summary_text", "")

    # Look up AI summary by URL (primary key)
    ai_summary = summary_map.get(article_url, "")

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


def generate_report(updates_data, summary_map):
    """Generate the full Markdown report."""
    meta = updates_data.get("metadata", {})
    updates = updates_data.get("updates", [])

    now = datetime.now(CST)
    report_time = now.strftime("%Y-%m-%d %H:%M")

    lines = []

    # Header
    lines.append(f"# WeChat Official Account Update Digest - {report_time} (CST)")
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

        if cat == "安全" and len(cat_updates) > 8:
            # Sub-group the large security category for readability.
            sub_buckets = OrderedDict((label, []) for label, _ in SECURITY_SUBGROUPS)
            for update in cat_updates:
                sub_buckets[security_subgroup(update)].append(update)

            for sub_label, sub_updates in sub_buckets.items():
                if not sub_updates:
                    continue
                lines.append(f"### [{sub_label}] ({len(sub_updates)})")
                lines.append("")
                for update in sub_updates:
                    article_index += 1
                    render_article(update, article_index, summary_map, lines)
        else:
            for update in cat_updates:
                article_index += 1
                render_article(update, article_index, summary_map, lines)

    # Footer
    lines.append(f"*Generated at {report_time} CST (UTC+8)*")

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
        now = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# WeChat Official Account Update Digest - {now} (CST)\n\n")
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
