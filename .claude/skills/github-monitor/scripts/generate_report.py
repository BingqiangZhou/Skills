#!/usr/bin/env python3
"""Generate a Markdown digest report from monitored GitHub activity.

Reads latest_updates.json (produced by check_updates.py, mixing merged PRs and
new issues, each tagged with a `type` field) and optional AI summaries, then
outputs a Markdown report grouped by repository. Within each repo, items are
already sorted newest-first by check_updates.py; PRs and issues interleave on
a single timeline.
"""

import argparse
import json
import sys
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# The audience of these reports is Chinese readers; display times in CST.
CST = timezone(timedelta(hours=8))


def load_json(path):
    """Load a JSON file, return None on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def build_summary_map(summaries_data):
    """Build a lookup map keyed by item_url for fast lookup.

    Accepts the generic `item_url` key and the legacy `pr_url` / `issue_url`
    keys for backward compatibility.
    """
    if not summaries_data or "summaries" not in summaries_data:
        return {}
    result = {}
    for item in summaries_data["summaries"]:
        url = (item.get("item_url")
               or item.get("pr_url")
               or item.get("issue_url")
               or "")
        if url:
            result[url] = item.get("ai_summary", "")
    return result


def parse_iso(ts):
    """Parse a GitHub ISO-8601 timestamp to aware UTC datetime, or None."""
    if not ts:
        return None
    try:
        return datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except ValueError:
            return None


def fmt_cst(ts):
    """Format a GitHub timestamp as 'YYYY-MM-DD HH:MM' in CST, or '' if unparseable."""
    dt = parse_iso(ts)
    if dt is None:
        return ''
    return dt.astimezone(CST).strftime('%Y-%m-%d %H:%M')


def fmt_diff(additions, deletions, changed_files):
    """Format diff stats as '+N / -M / F files', or '' if all zero."""
    if not any([additions, deletions, changed_files]):
        return ''
    return f"+{additions} / -{deletions} / {changed_files} files"


def repo_display_name(repo_key, per_repo):
    """Look up a human-friendly name for a repo from metadata.per_repo, else the key."""
    for entry in per_repo or []:
        if entry.get('repo') == repo_key and entry.get('name'):
            return entry['name']
    return repo_key


def render_pr(update, index, summary_map, lines):
    """Append a single merged-PR block to `lines`."""
    title = update.get('title') or '(无标题)'
    url = update.get('html_url') or ''
    number = update.get('pr_number')
    author = update.get('author') or ''
    author_url = update.get('author_url') or ''
    base_branch = update.get('base_branch') or ''
    merged = fmt_cst(update.get('merged_at'))
    comments = update.get('comments', 0)
    reactions = update.get('reactions_total', 0)
    additions = update.get('additions', 0)
    deletions = update.get('deletions', 0)
    changed_files = update.get('changed_files', 0)
    labels = update.get('labels') or []
    body_text = update.get('body_text') or ''

    ai_summary = summary_map.get(url, '')

    # Heading includes the PR number for quick reference.
    heading = f"### {index}. {title}"
    if number:
        heading += f" (#{number})"
    lines.append(heading)
    lines.append("")

    meta_bits = []
    if merged:
        meta_bits.append(f"**合并**: {merged}")
    if author:
        if author_url:
            meta_bits.append(f"**作者**: [@{author}]({author_url})")
        else:
            meta_bits.append(f"**作者**: @{author}")
    if base_branch:
        meta_bits.append(f"**分支**: `{base_branch}`")
    diff = fmt_diff(additions, deletions, changed_files)
    if diff:
        meta_bits.append(f"**改动**: {diff}")
    if comments:
        meta_bits.append(f"💬 {comments}")
    if reactions:
        meta_bits.append(f"👍 {reactions}")
    if meta_bits:
        lines.append(" | ".join(meta_bits))
        lines.append("")

    if labels:
        lines.append(" ".join(f"`#{l}`" for l in labels))
        lines.append("")

    if url:
        lines.append(f"**PR**: {url}")
        lines.append("")
    _append_summary(lines, ai_summary, body_text)

    lines.append("---")
    lines.append("")


def render_issue(update, index, summary_map, lines):
    """Append a single issue block to `lines`."""
    title = update.get('title') or '(无标题)'
    url = update.get('html_url') or ''
    author = update.get('author') or ''
    author_url = update.get('author_url') or ''
    comments = update.get('comments', 0)
    reactions = update.get('reactions_total', 0)
    created = fmt_cst(update.get('created_at'))
    body_text = update.get('body_text') or ''
    labels = update.get('labels') or []

    ai_summary = summary_map.get(url, '')

    lines.append(f"### {index}. {title}")
    lines.append("")
    meta_bits = []
    if created:
        meta_bits.append(f"**发布**: {created}")
    if author:
        if author_url:
            meta_bits.append(f"**作者**: [@{author}]({author_url})")
        else:
            meta_bits.append(f"**作者**: @{author}")
    if comments:
        meta_bits.append(f"💬 {comments}")
    if reactions:
        meta_bits.append(f"👍 {reactions}")
    if meta_bits:
        lines.append(" | ".join(meta_bits))
        lines.append("")
    if labels:
        lines.append("**标签**: " + "、".join(f"`{l}`" for l in labels))
        lines.append("")
    if url:
        lines.append(f"**Issue**: {url}")
        lines.append("")
    _append_summary(lines, ai_summary, body_text)

    lines.append("---")
    lines.append("")


def _append_summary(lines, ai_summary, body_text):
    """Append the AI summary, or fall back to a body excerpt."""
    if ai_summary:
        lines.append(f"**AI 摘要**: {ai_summary}")
        lines.append("")
    elif body_text:
        fallback = body_text[:200] + ("..." if len(body_text) > 200 else "")
        lines.append(f"**简介**: {fallback}")
        lines.append("")


def render_item(update, index, summary_map, lines):
    """Dispatch to the PR or issue renderer based on the `type` field."""
    if update.get('type') == 'pulls':
        render_pr(update, index, summary_map, lines)
    else:
        render_issue(update, index, summary_map, lines)


def group_by_repo(updates):
    """Group updates by repo, preserving first-seen order (updates are already newest-first)."""
    groups = OrderedDict()
    for update in updates:
        repo = update.get('repo', 'unknown/unknown')
        groups.setdefault(repo, []).append(update)
    return groups


def generate_report(updates_data, summary_map):
    """Generate the full Markdown report."""
    meta = updates_data.get("metadata", {})
    updates = updates_data.get("updates", [])

    now = datetime.now(CST)
    report_time = now.strftime("%Y-%m-%d %H:%M")

    pr_count = sum(1 for u in updates if u.get('type') == 'pulls')
    issue_count = len(updates) - pr_count

    lines = []
    lines.append(f"# GitHub 动态更新 - {report_time} (CST)")
    lines.append("")
    repos = meta.get("repos") or []
    hours = meta.get("hours", 24)
    update_count = meta.get("update_count", len(updates))
    filtered_out = meta.get("filtered_out_count", 0)
    checked = meta.get("checked_count", 0)
    per_repo = meta.get("per_repo") or []
    repos_str = " / ".join(repos) if repos else "(unknown)"
    lines.append(f"> 仓库: {repos_str} | 时间窗: {hours} 小时 | "
                 f"检查 {checked} 条 | 过滤 {filtered_out} 条 | "
                 f"保留 {update_count} 条（PR {pr_count} / issue {issue_count}）")
    lines.append("")
    lines.append("---")
    lines.append("")

    if update_count == 0:
        lines.append("本轮时间窗内没有新的 GitHub 动态（无新合并 PR，无新 issue）。")
        lines.append("")

    index = 0
    groups = group_by_repo(updates)
    multi_repo = len(groups) > 1
    for repo_key, repo_updates in groups.items():
        name = repo_display_name(repo_key, per_repo)
        if multi_repo:
            header = f"{name}" if name == repo_key else f"{name}（`{repo_key}`）"
            lines.append(f"## {header}（{len(repo_updates)} 条）")
            lines.append("")
            lines.append("---")
            lines.append("")
        for update in repo_updates:
            index += 1
            render_item(update, index, summary_map, lines)

    lines.append(f"*Generated at {report_time} CST (UTC+8)*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate GitHub activity digest report (merged PRs + new issues)")
    parser.add_argument("-i", "--input", required=True,
                        help="Input latest_updates.json path")
    parser.add_argument("-s", "--summaries", default=None,
                        help="AI summaries JSON path")
    parser.add_argument("-o", "--output", required=True,
                        help="Output Markdown file path")
    args = parser.parse_args()

    updates_data = load_json(args.input)
    if not updates_data:
        print(f"Error: Cannot load updates from {args.input}", file=sys.stderr)
        sys.exit(1)

    update_count = updates_data.get("metadata", {}).get("update_count", 0)

    summary_map = {}
    if args.summaries:
        summaries_data = load_json(args.summaries)
        if summaries_data:
            summary_map = build_summary_map(summaries_data)
            print(f"Loaded {len(summary_map)} AI summaries")

    report = generate_report(updates_data, summary_map)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"Report written to {output_path} ({update_count} items)")


if __name__ == "__main__":
    main()
