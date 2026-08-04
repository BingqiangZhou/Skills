#!/usr/bin/env python3
"""Generate a unified Markdown digest report from all RSS sources.

Reads latest_updates.json (containing wechat + tech + podcast updates mixed,
each carrying a ``source`` field) and optional AI summaries, outputs a single
Markdown report organized by source section:

    # RSS 信息源日报 - 2026-08-04 09:30 (CST)
    ## 微信公众号 (N 篇)     ← wechat grouping (category + security sub-groups)
    ## 科技博客 (N 篇)       ← tech grouping (6 categories + Hacker News)
    ## 播客 (N 集)           ← podcast flat list by rank

Each source section delegates to a dedicated renderer that preserves the
original skill's grouping logic.
"""

import argparse
import json
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path


# The audience of these reports is Chinese readers; display times in CST.
CST = timezone(timedelta(hours=8))

# Source section display order
SOURCE_ORDER = ["wechat", "tech", "podcast"]

SOURCE_LABELS = {
    "wechat": "微信公众号",
    "tech": "科技博客",
    "podcast": "播客",
}

# Named limits
FALLBACK_CHARS = 200        # max chars of description shown when no AI summary
SECURITY_SUBGROUP_THRESHOLD = 8  # split the 安全 category into sub-groups above this count
SHOWNOTES_FALLBACK_BASE = 500   # podcast shownotes fallback base
SHOWNOTES_FALLBACK_MAX = 150    # podcast shownotes fallback final length

# Tech category display order
TECH_CATEGORY_ORDER = ["AI/ML", "芯片硬件", "云计算", "开源", "网络安全", "综合科技"]

# Security (安全) sub-group keywords for the wechat source.
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
    ("安全资讯 / 其他", None),  # fallback
]

# Wechat category display order
WECHAT_CATEGORY_ORDER = ["安全", "开发", "其他", "用户提交"]


# ===========================================================================
# Shared helpers
# ===========================================================================

def load_json(path):
    """Load a JSON file, return None on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def build_summary_map(summaries_data):
    """Build a {url: summary_info} lookup from the merged summaries data.

    summary_info is a dict: {"ai_summary": str, "category": str}.
    The category field is used by the tech renderer for AI-assigned category
    override (preserved from the original tech-daily skill).

    Handles the output of merge_summaries.py (list of {url, ai_summary}) and
    the podcast flat-dict format ({url: text}).
    """
    if not summaries_data:
        return {}

    result = {}

    # Shape from merge_summaries.py: {"summaries": [{"url":..., "ai_summary":...}]}
    summaries = summaries_data.get("summaries")
    if isinstance(summaries, list):
        for item in summaries:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("article_url") or item.get("episode_url") or ""
            text = item.get("ai_summary") or item.get("summary") or ""
            category = item.get("category", "")
            if url:
                result[url.strip()] = {
                    "ai_summary": text.strip() if isinstance(text, str) else text,
                    "category": category,
                }
        return result

    # Shape from podcast: flat {url: text}
    for key, value in summaries_data.items():
        if isinstance(key, str) and isinstance(value, str):
            result[key.strip()] = {"ai_summary": value.strip(), "category": ""}

    return result


def _fallback_text(text, max_chars=FALLBACK_CHARS):
    """Truncate text to max_chars with ellipsis."""
    if not text:
        return ""
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


# ===========================================================================
# Wechat renderer
# ===========================================================================

def _security_subgroup(update):
    """Return the sub-group label for a 安全 update, based on title + account."""
    haystack = f"{update.get('account_name', '')} {update.get('title', '')}"
    for label, pattern in SECURITY_SUBGROUPS:
        if pattern is None:
            return label
        if pattern.search(haystack):
            return label
    return SECURITY_SUBGROUPS[-1][0]


def render_wechat(updates, summary_map, lines, index):
    """Render wechat source section. Returns updated article index."""
    lines.append(f"## {SOURCE_LABELS['wechat']} ({len(updates)} 篇)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Group by category
    groups = OrderedDict()
    for cat in WECHAT_CATEGORY_ORDER:
        groups[cat] = []
    for u in updates:
        cat = u.get("category", "其他")
        if cat not in groups:
            groups[cat] = []
        groups[cat].append(u)

    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue

        lines.append(f"### {cat} ({len(cat_updates)} 篇)")
        lines.append("")
        lines.append("---")
        lines.append("")

        if cat == "安全" and len(cat_updates) > SECURITY_SUBGROUP_THRESHOLD:
            # Sub-group the large security category
            sub_buckets = OrderedDict((label, []) for label, _ in SECURITY_SUBGROUPS)
            for u in cat_updates:
                sub_buckets[_security_subgroup(u)].append(u)

            for sub_label, sub_updates in sub_buckets.items():
                if not sub_updates:
                    continue
                lines.append(f"#### [{sub_label}] ({len(sub_updates)} 篇)")
                lines.append("")
                index = _render_wechat_articles(sub_updates, summary_map, lines, index)
        else:
            index = _render_wechat_articles(cat_updates, summary_map, lines, index)

    return index


def _render_wechat_articles(updates, summary_map, lines, index):
    """Render a list of wechat articles. Returns updated index."""
    for u in updates:
        index += 1
        account = u.get("account_name", "Unknown")
        title = u.get("title", "(no title)")
        url = u.get("url", "")
        pub_date = u.get("pub_date", "")
        full_text = u.get("full_text", "")

        ai_summary = summary_map.get(url, {}).get("ai_summary", "")

        lines.append(f"##### {index}. {account}")
        lines.append("")
        lines.append(f"**文章**: {title}")
        lines.append("")
        if url:
            lines.append(f"**链接**: {url}")
            lines.append("")
        if pub_date:
            lines.append(f"**发布时间**: {pub_date}")
            lines.append("")
        if ai_summary:
            lines.append(f"**AI 摘要**: {ai_summary}")
            lines.append("")
        elif full_text:
            lines.append(f"**摘要**: {_fallback_text(full_text)}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return index


# ===========================================================================
# Tech renderer
# ===========================================================================

def render_tech(updates, summary_map, lines, index, trend_insight=None):
    """Render tech source section. Returns updated article index."""
    # Separate HN items
    tech_items = [u for u in updates if u.get("source_category") != "Hacker News"]
    hn_items = [u for u in updates if u.get("source_category") == "Hacker News"]

    lines.append(f"## {SOURCE_LABELS['tech']} ({len(tech_items)} 篇)")
    lines.append("")

    # Optional trend insight (tech-specific feature)
    if trend_insight:
        insight_text = trend_insight.get("trend_insight", "")
        if insight_text:
            lines.append("### 今日趋势洞察")
            lines.append("")
            lines.append(insight_text)
            lines.append("")
            lines.append("---")
            lines.append("")

    # Group by category
    groups = OrderedDict()
    for cat in TECH_CATEGORY_ORDER:
        groups[cat] = []
    groups["其他"] = []

    for u in tech_items:
        source_cat = u.get("source_category", "其他")

        # Check if AI assigned a different category (preserved from tech-daily)
        url = u.get("url", "")
        ai_info = summary_map.get(url, {})
        ai_cat = ai_info.get("category", "")

        # Use AI category if it's a known category, otherwise use source category
        final_cat = ai_cat if ai_cat in groups else source_cat
        if final_cat not in groups:
            final_cat = "其他"

        groups[final_cat].append(u)

    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue

        lines.append(f"### {cat} ({len(cat_updates)} 篇)")
        lines.append("")

        for u in cat_updates:
            index += 1
            source_name = u.get("source_name", "Unknown")
            title = u.get("title", "(no title)")
            url = u.get("url", "")
            pub_date = u.get("pub_date", "")
            full_text = u.get("full_text", "")
            ai_summary = summary_map.get(url, {}).get("ai_summary", "")

            lines.append(f"#### {index}. {title}")
            lines.append("")
            lines.append(f"**来源**: {source_name} | **发布时间**: {pub_date}")
            lines.append("")
            if url:
                lines.append(f"**链接**: {url}")
                lines.append("")
            if ai_summary:
                lines.append(f"**AI 摘要**: {ai_summary}")
                lines.append("")
            elif full_text:
                lines.append(f"**摘要**: {_fallback_text(full_text)}")
                lines.append("")

            lines.append("---")
            lines.append("")

    # Hacker News section
    if hn_items:
        lines.append(f"### Hacker News 热门 ({len(hn_items)} 条)")
        lines.append("")

        for item in hn_items:
            index += 1
            title = item.get("title", "(no title)")
            url = item.get("url", "")
            points = item.get("hn_points")
            comments = item.get("hn_comments")
            ai_summary = summary_map.get(url, {}).get("ai_summary", "")

            stats_parts = []
            if points is not None:
                stats_parts.append(f"points: {points}")
            if comments is not None:
                stats_parts.append(f"comments: {comments}")
            stats_str = ", ".join(stats_parts)

            lines.append(f"#### {index}. {title}")
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

    return index


# ===========================================================================
# Podcast renderer
# ===========================================================================

def _format_duration(seconds):
    """Format seconds into a human-readable duration string.

    Examples: 3725 -> "1h 2m", 1800 -> "30m", 90 -> "2m".
    Returns None if seconds is None or invalid.
    """
    if not seconds or seconds <= 0:
        return None
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"
    return f"{minutes}m"


def _podcast_fallback_summary(shownotes):
    """Generate a truncation-based fallback summary for podcast shownotes."""
    if not shownotes:
        return "内容暂无"
    text = shownotes
    if len(text) > SHOWNOTES_FALLBACK_BASE:
        text = text[:SHOWNOTES_FALLBACK_BASE]
    if len(text) > SHOWNOTES_FALLBACK_MAX:
        return text[:SHOWNOTES_FALLBACK_MAX] + "..."
    return text


def _clean_episode_url(url):
    """Strip ?utm_source=rss suffix from episode URLs."""
    if url and "?utm_source=" in url:
        return url.split("?utm_source=")[0]
    return url or ""


def render_podcast(updates, summary_map, lines, index):
    """Render podcast source section. Returns updated article index."""
    lines.append(f"## {SOURCE_LABELS['podcast']} ({len(updates)} 集)")
    lines.append("")
    lines.append("---")
    lines.append("")

    for u in updates:
        index += 1
        podcast_name = u.get("podcast_name", "未知")
        rank = u.get("rank", 0)
        title = u.get("title", "未知标题")
        url = u.get("url", "")
        pub_date = u.get("pub_date", "未知")
        shownotes = u.get("shownotes", "")
        duration = _format_duration(u.get("duration"))

        # Try AI summary by URL, then by title as fallback
        ai_summary = (summary_map.get(url, {}).get("ai_summary", "")
                      or summary_map.get(title, {}).get("ai_summary", ""))
        if not ai_summary:
            ai_summary = _podcast_fallback_summary(shownotes)

        display_url = _clean_episode_url(url)

        lines.append(f"### {index}. {podcast_name} (排名 {rank})")
        lines.append("")
        lines.append(f"**单集**: {title}")
        lines.append("")
        # Duration and pub_date on one line for compactness
        meta_parts = [f"**发布时间**: {pub_date}"]
        if duration:
            meta_parts.append(f"**时长**: ⏱ {duration}")
        lines.append(" | ".join(meta_parts))
        lines.append("")
        lines.append(f"**链接**: {display_url}")
        lines.append("")
        lines.append(f"**摘要**: {ai_summary}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return index


# ===========================================================================
# Main report generation
# ===========================================================================

def generate_report(updates_data, summary_map, trend_insight=None):
    """Generate the full unified Markdown report."""
    meta = updates_data.get("metadata", {})
    updates = updates_data.get("updates", [])

    now = datetime.now(CST)
    report_time = now.strftime("%Y-%m-%d %H:%M")

    lines = []

    # Header
    lines.append(f"# RSS 信息源日报 - {report_time} (CST)")
    lines.append("")

    total_checked = sum(s.get("checked", 0) for s in meta.get("sources", {}).values())
    hours = meta.get("hours", 24)
    update_count = meta.get("update_count", len(updates))

    # Source breakdown line
    source_parts = []
    for src in SOURCE_ORDER:
        src_updates = [u for u in updates if u.get("source") == src]
        if src_updates:
            source_parts.append(f"{SOURCE_LABELS[src]}: {len(src_updates)}")
    source_line = " | ".join(source_parts) if source_parts else ""

    lines.append(f"> 共检查 {total_checked} 个信息源，时间范围 {hours} 小时，发现 {update_count} 条更新")
    if source_line:
        lines.append(f"> {source_line}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Group updates by source
    by_source = OrderedDict()
    for src in SOURCE_ORDER:
        by_source[src] = []
    for u in updates:
        src = u.get("source", "")
        if src not in by_source:
            by_source[src] = []
        by_source[src].append(u)

    article_index = 0

    # Render each source section
    for src in SOURCE_ORDER:
        src_updates = by_source.get(src, [])
        if not src_updates:
            continue

        if src == "wechat":
            article_index = render_wechat(src_updates, summary_map, lines, article_index)
        elif src == "tech":
            article_index = render_tech(src_updates, summary_map, lines, article_index,
                                        trend_insight=trend_insight)
        elif src == "podcast":
            article_index = render_podcast(src_updates, summary_map, lines, article_index)

    # Footer
    lines.append(f"*报告生成时间: {report_time} CST (UTC+8)*")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate unified RSS digest report"
    )
    parser.add_argument("-i", "--input", required=True, help="Input latest_updates.json path")
    parser.add_argument("-s", "--summaries", default=None, help="AI summaries JSON path")
    parser.add_argument("--insight", default=None, help="Trend insight JSON path (tech)")
    parser.add_argument("-o", "--output", required=True, help="Output Markdown file path")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    # Load updates
    updates_data = load_json(args.input)
    if not updates_data:
        print(f"Error: Cannot load updates from {args.input}", file=sys.stderr)
        sys.exit(1)

    update_count = updates_data.get("metadata", {}).get("update_count", 0)
    if update_count == 0:
        print("No updates found. Nothing to report.")
        now = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# RSS 信息源日报 - {now} (CST)\n\n")
            f.write("> 未在检查的时间窗口内发现新内容。\n")
        return

    # Load AI summaries (optional)
    summary_map = {}
    if args.summaries:
        summaries_data = load_json(args.summaries)
        if summaries_data:
            summary_map = build_summary_map(summaries_data)
            print(f"Loaded {len(summary_map)} AI summaries")

    # Load trend insight (optional, tech-specific)
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
