#!/usr/bin/env python3
"""Generate a Markdown digest report from tool release updates.

Reads latest_updates.json and optional ai_highlights.json, outputs a
category-grouped Markdown report to daily-digests/YYYY-MM-DD/tool-update_HH-MM.md.

Release notes (body) are already human-readable changelogs, so this report
shows them directly (truncated for readability) rather than requiring
per-release AI summaries. The optional ai_highlights.json adds a single
2-3 sentence overview + a one-line note per tool.
"""

import argparse
import json
import sys
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path


# Audience is Chinese readers; display times in CST (matches wechat skill).
CST = timezone(timedelta(hours=8))

# Cap release-note body length in the rendered report. Full body is preserved
# in latest_updates.json for anyone who wants the complete changelog.
BODY_MAX = 1500


def load_json(path):
    """Load a JSON file. Returns None on missing/corrupt (treated as absent)."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def build_highlights_map(highlights_data):
    """Build a tool_id -> one-line-note map from ai_highlights.json.

    Expected shape: {"highlights": "...", "per_tool": {"v2rayn": "...", ...}}
    """
    if not highlights_data or not isinstance(highlights_data, dict):
        return {}, ""
    per_tool = highlights_data.get("per_tool") or {}
    overall = highlights_data.get("highlights") or ""
    if not isinstance(per_tool, dict):
        per_tool = {}
    return per_tool, overall


def fmt_cst(iso_str):
    """Format an ISO timestamp string as 'YYYY-MM-DD HH:MM CST'. Returns '' if
    it can't be parsed (release may have no publish date, e.g. npm or scraped)."""
    if not iso_str:
        return ""
    s = iso_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CST).strftime("%Y-%m-%d %H:%M") + " CST"


def group_by_category(updates, categories_order):
    """Group updates by category, preserving the configured order. Falls back to
    discovery order for any category not in the configured list."""
    groups = OrderedDict()
    for cat in categories_order:
        groups[cat] = []
    for update in updates:
        cat = update.get("category")
        if not cat:
            cat = "其他 / Other"
        if cat not in groups:
            groups[cat] = []
        groups[cat].append(update)
    return groups


def truncate_body(body):
    """Trim release notes to BODY_MAX chars on a paragraph boundary if possible."""
    if not body:
        return ""
    body = body.strip()
    if len(body) <= BODY_MAX:
        return body
    cut = body[:BODY_MAX]
    # Try to break on the last blank line within the window for a clean cut.
    last_break = cut.rfind("\n\n")
    if last_break > BODY_MAX // 2:
        cut = body[:last_break]
    return cut.rstrip() + "\n\n…（节选，完整更新说明见链接）"


def render_release(update, idx, per_tool_map, lines):
    """Append a single release block to `lines`."""
    name = update.get("name", "Unknown")
    version = update.get("version", "?")
    prev = update.get("previous_version")
    published = fmt_cst(update.get("published_at"))
    url = update.get("html_url", "")
    body = truncate_body(update.get("body", ""))
    prerelease = update.get("prerelease", False)
    tid = update.get("tool_id", "")

    title = f"### {idx}. {name} `v{version}`"
    if prerelease:
        title += " *(pre-release)*"
    lines.append(title)
    lines.append("")

    meta_bits = []
    if prev:
        meta_bits.append(f"previous: `v{prev}`")
    if published:
        meta_bits.append(f"published {published}")
    if meta_bits:
        lines.append("> " + " · ".join(meta_bits))
        lines.append("")

    note = per_tool_map.get(tid, "")
    if note:
        lines.append(f"**要点**：{note}")
        lines.append("")

    if body:
        lines.append(body)
        lines.append("")
    elif not note:
        # No body and no AI note (e.g. npm/html sources): show a minimal hint.
        lines.append("*(此版本无内嵌更新说明，详见下方链接)*")
        lines.append("")

    if url:
        lines.append(f"🔗 [{name} release]({url})")
        lines.append("")

    lines.append("---")
    lines.append("")


def render_baseline(updates_data, lines):
    """Render a baseline-establishment report. Lists every tool + its current
    version so the reader knows what got recorded — framed as baseline, not 'new'."""
    # The baseline tools come through as updates only if we chose to emit them;
    # by design check_updates.py emits NO updates on baseline, so we reconstruct
    # the tool list from .last_seen.json passed via the updates_data's companion.
    # Simpler: we read last-seen from a side input. But to keep generate_report
    # single-input, we emit a short note here; the rich baseline detail is in
    # the console output + the state file.
    meta = updates_data.get("metadata", {})
    checked = meta.get("checked_count", 0)
    errors = meta.get("error_count", 0)
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    lines.append(f"# 工具更新日报（基线建立）- {now} CST")
    lines.append("")
    lines.append(f"> 首次运行：已记录 {checked} 个工具的当前版本作为基线。")
    lines.append("> 本次不报告「新版本」——下次运行起，仅出现新版本时才会报告。")
    if errors:
        lines.append(f"> ⚠️ {errors} 个工具获取失败，详见控制台输出与 `.last_seen.json`。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*基线详情见 `workspaces/tool-update-monitor/.last_seen.json`。*")
    return


def generate_report(updates_data, per_tool_map, overall_highlight):
    """Generate the full Markdown report string."""
    meta = updates_data.get("metadata", {})
    updates = updates_data.get("updates", [])
    categories_order = meta.get("categories_order") or []
    now = datetime.now(CST)
    report_time = now.strftime("%Y-%m-%d %H:%M")

    lines = []

    # Baseline run: dedicated short report.
    if meta.get("baseline_run"):
        render_baseline(updates_data, lines)
        return "\n".join(lines)

    # Normal report.
    checked = meta.get("checked_count", 0)
    errors = meta.get("error_count", 0)
    update_count = meta.get("update_count", len(updates))
    hours = meta.get("hours", 168)

    lines.append(f"# 工具更新日报 - {report_time} CST")
    lines.append("")
    summary = (f"> 检查 {checked} 个工具（约 {hours}h 窗口），"
               f"发现 {update_count} 个新版本，{errors} 个工具出错。")
    lines.append(summary)
    lines.append("")

    if overall_highlight:
        lines.append(f"**AI 速览**：{overall_highlight}")
        lines.append("")

    lines.append("---")
    lines.append("")

    if update_count == 0:
        lines.append("最近无新版本发布。下次有更新时再报告。")
        lines.append("")
        lines.append(f"*Generated at {report_time} CST (UTC+8)*")
        return "\n".join(lines)

    # Group by category.
    groups = group_by_category(updates, categories_order)
    idx = 0
    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue
        lines.append(f"## {cat}（{len(cat_updates)} 个新版本）")
        lines.append("")
        lines.append("---")
        lines.append("")
        for update in cat_updates:
            idx += 1
            render_release(update, idx, per_tool_map, lines)

    lines.append(f"*Generated at {report_time} CST (UTC+8)*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate tool-update-monitor digest report"
    )
    parser.add_argument("-i", "--input", required=True,
                        help="Input latest_updates.json path")
    parser.add_argument("-s", "--summaries", default=None,
                        help="Optional ai_highlights.json path")
    parser.add_argument("-o", "--output", required=True,
                        help="Output Markdown file path")
    args = parser.parse_args()

    updates_data = load_json(args.input)
    if not updates_data:
        print(f"Error: Cannot load updates from {args.input}", file=sys.stderr)
        sys.exit(1)

    # AI highlights are optional. Missing file / no sub-agents -> empty maps.
    per_tool_map, overall_highlight = ({}, "")
    if args.summaries:
        hd = load_json(args.summaries)
        if hd:
            per_tool_map, overall_highlight = build_highlights_map(hd)
            print(f"Loaded AI highlights for {len(per_tool_map)} tool(s).")

    report = generate_report(updates_data, per_tool_map, overall_highlight)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    meta = updates_data.get("metadata", {})
    baseline = meta.get("baseline_run", False)
    n = meta.get("update_count", 0)
    if baseline:
        print(f"Baseline report written to {output_path}")
    else:
        print(f"Report written to {output_path} ({n} release(s))")


if __name__ == "__main__":
    main()
