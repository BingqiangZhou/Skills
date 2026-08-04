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
    ## 🔥 今日重点                      ← RSS topic narratives
    ## 📌 其他动态                      ← RSS minor-items roundup (optional)
    ## 🔧 GitHub 动态                   ← compact list of PRs/issues
    ## 🛠 工具更新                       ← compact list of releases

The underlying structured data always stays in
``workspaces/<source>/latest_updates.json`` for on-demand follow-up.

If no RSS narrative payload is available (editor step skipped/failed), the
overview degrades to a best-effort string built from whatever highlights are
on hand, but GitHub/tool lists are always rendered from their collector data.
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

# ---------------------------------------------------------------------------
# Shared limits
# ---------------------------------------------------------------------------
FALLBACK_CHARS = 200            # max chars of description shown when no AI summary
BODY_MAX = 1500                 # tool release-note body cap in the rendered report
SHOWNOTES_FALLBACK_BASE = 500   # podcast shownotes fallback base
SHOWNOTES_FALLBACK_MAX = 150    # podcast shownotes fallback final length
SECURITY_SUBGROUP_THRESHOLD = 8  # split the 安全 category into sub-groups above this

# RSS source display order + labels
SOURCE_ORDER = ["wechat", "tech", "podcast"]
SOURCE_LABELS = {
    "wechat": "微信公众号",
    "tech": "科技博客",
    "podcast": "播客",
}

# Tech category display order
TECH_CATEGORY_ORDER = ["AI/ML", "芯片硬件", "云计算", "开源", "网络安全", "综合科技"]

# Wechat category display order
WECHAT_CATEGORY_ORDER = ["安全", "开发", "其他", "用户提交"]

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


# ===========================================================================
# RSS summary map + renderers
# ===========================================================================

def build_rss_summary_map(summaries_data):
    """Build a {url: {"ai_summary": str, "category": str}} lookup."""
    if not summaries_data:
        return {}
    result = {}
    summaries = summaries_data.get("summaries")
    if isinstance(summaries, list):
        for item in summaries:
            if not isinstance(item, dict):
                continue
            url = (item.get("url") or item.get("article_url")
                   or item.get("episode_url") or "")
            text = item.get("ai_summary") or item.get("summary") or ""
            category = item.get("category", "")
            if url:
                result[url.strip()] = {
                    "ai_summary": text.strip() if isinstance(text, str) else text,
                    "category": category,
                }
        return result
    # Flat {url: text} dict (podcast legacy shape)
    for key, value in summaries_data.items():
        if isinstance(key, str) and isinstance(value, str):
            result[key.strip()] = {"ai_summary": value.strip(), "category": ""}
    return result


def _security_subgroup(update):
    """Return the sub-group label for a 安全 update, based on title + account."""
    haystack = f"{update.get('account_name', '')} {update.get('title', '')}"
    for label, pattern in SECURITY_SUBGROUPS:
        if pattern is None:
            return label
        if pattern.search(haystack):
            return label
    return SECURITY_SUBGROUPS[-1][0]


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
        if url:
            lines.append(f"**文章**: [{title}]({url})")
        else:
            lines.append(f"**文章**: {title}")
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


def render_wechat(updates, summary_map, lines, index):
    """Render the wechat subsection. Returns updated index."""
    lines.append(f"### {SOURCE_LABELS['wechat']} ({len(updates)} 篇)")
    lines.append("")
    lines.append("---")
    lines.append("")

    groups = OrderedDict()
    for cat in WECHAT_CATEGORY_ORDER:
        groups[cat] = []
    for u in updates:
        cat = u.get("category", "其他")
        groups.setdefault(cat, [])
        groups[cat].append(u)

    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue
        lines.append(f"#### {cat} ({len(cat_updates)} 篇)")
        lines.append("")
        lines.append("---")
        lines.append("")
        if cat == "安全" and len(cat_updates) > SECURITY_SUBGROUP_THRESHOLD:
            sub_buckets = OrderedDict((label, []) for label, _ in SECURITY_SUBGROUPS)
            for u in cat_updates:
                sub_buckets[_security_subgroup(u)].append(u)
            for sub_label, sub_updates in sub_buckets.items():
                if not sub_updates:
                    continue
                lines.append(f"##### [{sub_label}] ({len(sub_updates)} 篇)")
                lines.append("")
                index = _render_wechat_articles(sub_updates, summary_map, lines, index)
        else:
            index = _render_wechat_articles(cat_updates, summary_map, lines, index)
    return index


def render_tech(updates, summary_map, lines, index, trend_insight=None):
    """Render the tech subsection. Returns updated index."""
    tech_items = [u for u in updates if u.get("source_category") != "Hacker News"]
    hn_items = [u for u in updates if u.get("source_category") == "Hacker News"]

    lines.append(f"### {SOURCE_LABELS['tech']} ({len(tech_items)} 篇)")
    lines.append("")

    if trend_insight:
        insight_text = trend_insight.get("trend_insight", "")
        if insight_text:
            lines.append("#### 今日趋势洞察")
            lines.append("")
            lines.append(insight_text)
            lines.append("")
            lines.append("---")
            lines.append("")

    groups = OrderedDict()
    for cat in TECH_CATEGORY_ORDER:
        groups[cat] = []
    groups["其他"] = []

    for u in tech_items:
        source_cat = u.get("source_category", "其他")
        url = u.get("url", "")
        ai_cat = summary_map.get(url, {}).get("category", "")
        final_cat = ai_cat if ai_cat in groups else source_cat
        if final_cat not in groups:
            final_cat = "其他"
        groups[final_cat].append(u)

    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue
        lines.append(f"#### {cat} ({len(cat_updates)} 篇)")
        lines.append("")
        for u in cat_updates:
            index += 1
            source_name = u.get("source_name", "Unknown")
            title = u.get("title", "(no title)")
            url = u.get("url", "")
            pub_date = u.get("pub_date", "")
            full_text = u.get("full_text", "")
            ai_summary = summary_map.get(url, {}).get("ai_summary", "")

            if url:
                lines.append(f"##### {index}. [{title}]({url})")
            else:
                lines.append(f"##### {index}. {title}")
            lines.append("")
            lines.append(f"**来源**: {source_name}")
            lines.append("")
            if ai_summary:
                lines.append(f"**AI 摘要**: {ai_summary}")
                lines.append("")
            elif full_text:
                lines.append(f"**摘要**: {_fallback_text(full_text)}")
                lines.append("")
            lines.append("---")
            lines.append("")

    if hn_items:
        lines.append(f"#### Hacker News 热门 ({len(hn_items)} 条)")
        lines.append("")
        for item in hn_items:
            index += 1
            title = item.get("title", "(no title)")
            url = item.get("url", "")
            ai_summary = summary_map.get(url, {}).get("ai_summary", "")

            if url:
                lines.append(f"##### {index}. [{title}]({url})")
            else:
                lines.append(f"##### {index}. {title}")
            lines.append("")
            if ai_summary:
                lines.append(f"**AI 摘要**: {ai_summary}")
                lines.append("")
            lines.append("---")
            lines.append("")
    return index


def _format_duration(seconds):
    """Format seconds into '1h 2m' / '30m'. Returns None if invalid."""
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
    """Render the podcast subsection. Returns updated index."""
    lines.append(f"### {SOURCE_LABELS['podcast']} ({len(updates)} 集)")
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

        ai_summary = (summary_map.get(url, {}).get("ai_summary", "")
                      or summary_map.get(title, {}).get("ai_summary", ""))
        if not ai_summary:
            ai_summary = _podcast_fallback_summary(shownotes)

        display_url = _clean_episode_url(url)

        lines.append(f"#### {index}. {podcast_name} (排名 {rank})")
        lines.append("")
        if display_url:
            lines.append(f"**单集**: [{title}]({display_url})")
        else:
            lines.append(f"**单集**: {title}")
        lines.append("")
        if duration:
            lines.append(f"**时长**: ⏱ {duration}")
            lines.append("")
        lines.append(f"**摘要**: {ai_summary}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return index


def render_rss_section(updates_data, summary_map, trend_insight, lines, index):
    """Render the whole RSS top-level section. Returns updated index."""
    updates = updates_data.get("updates", [])
    meta = updates_data.get("metadata", {})
    hours = meta.get("hours", 24)

    # Source breakdown line
    source_parts = []
    for src in SOURCE_ORDER:
        src_updates = [u for u in updates if u.get("source") == src]
        if src_updates:
            source_parts.append(f"{SOURCE_LABELS[src]}: {len(src_updates)}")
    source_line = " | ".join(source_parts) if source_parts else ""

    lines.append(f"## 📰 RSS 信息源 ({len(updates)} 篇)")
    lines.append("")
    detail = f"时间范围 {hours} 小时"
    if source_line:
        detail += f" | {source_line}"
    lines.append(f"> {detail}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not updates:
        lines.append("本轮时间窗内未发现新的 RSS 内容。")
        lines.append("")
        lines.append("---")
        lines.append("")
        return index

    # Group updates by source, preserving SOURCE_ORDER
    by_source = OrderedDict()
    for src in SOURCE_ORDER:
        by_source[src] = []
    for u in updates:
        src = u.get("source", "")
        by_source.setdefault(src, []).append(u)

    for src in SOURCE_ORDER:
        src_updates = by_source.get(src, [])
        if not src_updates:
            continue
        if src == "wechat":
            index = render_wechat(src_updates, summary_map, lines, index)
        elif src == "tech":
            index = render_tech(src_updates, summary_map, lines, index,
                                trend_insight=trend_insight)
        elif src == "podcast":
            index = render_podcast(src_updates, summary_map, lines, index)
    return index


# ===========================================================================
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


def fmt_diff(additions, deletions, changed_files):
    """Format diff stats as '+N / -M / F files', or '' if all zero."""
    if not any([additions, deletions, changed_files]):
        return ''
    return f"+{additions} / -{deletions} / {changed_files} files"


def repo_display_name(repo_key, per_repo):
    """Look up a human-friendly name for a repo from metadata.per_repo."""
    for entry in per_repo or []:
        if entry.get('repo') == repo_key and entry.get('name'):
            return entry['name']
    return repo_key


def _append_summary(lines, ai_summary, body_text):
    """Append the AI summary, or fall back to a body excerpt."""
    if ai_summary:
        lines.append(f"**AI 摘要**: {ai_summary}")
        lines.append("")
    elif body_text:
        fallback = body_text[:FALLBACK_CHARS] + ("..." if len(body_text) > FALLBACK_CHARS else "")
        lines.append(f"**简介**: {fallback}")
        lines.append("")


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


def render_github_item(update, index, summary_map, lines):
    """Dispatch to the PR or issue renderer based on the `type` field."""
    if update.get('type') == 'pulls':
        render_pr(update, index, summary_map, lines)
    else:
        render_issue(update, index, summary_map, lines)


def render_github_section(updates_data, summary_map, lines, index):
    """Render the GitHub top-level section. Returns updated index."""
    meta = updates_data.get("metadata", {})
    updates = updates_data.get("updates", [])
    hours = meta.get("hours", 24)
    update_count = meta.get("update_count", len(updates))
    filtered_out = meta.get("filtered_out_count", 0)
    checked = meta.get("checked_count", 0)
    per_repo = meta.get("per_repo") or []
    repos = meta.get("repos") or []

    pr_count = sum(1 for u in updates if u.get('type') == 'pulls')
    issue_count = len(updates) - pr_count

    lines.append(f"## 🔧 GitHub 动态 ({update_count} 条)")
    lines.append("")
    repos_str = " / ".join(repos) if repos else ""
    header_parts = [f"时间窗 {hours} 小时", f"检查 {checked} 条",
                    f"过滤 {filtered_out} 条",
                    f"PR {pr_count} / issue {issue_count}"]
    if repos_str:
        header_parts.append(f"仓库: {repos_str}")
    lines.append("> " + " | ".join(header_parts))
    lines.append("")
    lines.append("---")
    lines.append("")

    if not updates:
        lines.append("本轮时间窗内没有新的 GitHub 动态（无新合并 PR，无新 issue）。")
        lines.append("")
        lines.append("---")
        lines.append("")
        return index

    # Group by repo, preserving first-seen order (updates are already newest-first)
    groups = OrderedDict()
    for update in updates:
        repo = update.get('repo', 'unknown/unknown')
        groups.setdefault(repo, []).append(update)

    multi_repo = len(groups) > 1
    for repo_key, repo_updates in groups.items():
        name = repo_display_name(repo_key, per_repo)
        if multi_repo:
            header = f"{name}" if name == repo_key else f"{name}（`{repo_key}`）"
            lines.append(f"### {header}（{len(repo_updates)} 条）")
            lines.append("")
            lines.append("---")
            lines.append("")
        for update in repo_updates:
            index += 1
            render_github_item(update, index, summary_map, lines)
    return index


# ===========================================================================
# Tool renderers
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


def truncate_body(body):
    """Trim release notes to BODY_MAX chars on a paragraph boundary if possible."""
    if not body:
        return ""
    body = body.strip()
    if len(body) <= BODY_MAX:
        return body
    cut = body[:BODY_MAX]
    last_break = cut.rfind("\n\n")
    if last_break > BODY_MAX // 2:
        cut = body[:last_break]
    return cut.rstrip() + "\n\n…（节选，完整更新说明见链接）"


def render_tool_release(update, idx, per_tool_map, lines):
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
        lines.append("*(此版本无内嵌更新说明，详见下方链接)*")
        lines.append("")

    if url:
        lines.append(f"🔗 [{name} release]({url})")
        lines.append("")

    lines.append("---")
    lines.append("")


def render_tool_section(updates_data, per_tool_map, overall_highlight, lines, index):
    """Render the tool-update top-level section. Returns updated index."""
    meta = updates_data.get("metadata", {})
    updates = updates_data.get("updates", [])
    categories_order = meta.get("categories_order") or []
    checked = meta.get("checked_count", 0)
    errors = meta.get("error_count", 0)
    update_count = meta.get("update_count", len(updates))
    hours = meta.get("hours", 168)

    # Baseline run: render a short baseline note instead of releases.
    if meta.get("baseline_run"):
        lines.append("## 🛠 工具更新（基线建立）")
        lines.append("")
        lines.append(f"> 首次运行：已记录 {checked} 个工具的当前版本作为基线，本次不报告新版本。")
        if errors:
            lines.append(f"> ⚠️ {errors} 个工具获取失败，详见采集输出。")
        lines.append("")
        lines.append("---")
        lines.append("")
        return index

    lines.append(f"## 🛠 工具更新 ({update_count} 个新版本)")
    lines.append("")
    lines.append(f"> 检查 {checked} 个工具（约 {hours}h 窗口），"
                 f"{errors} 个工具出错。")
    lines.append("")
    if overall_highlight:
        lines.append(f"**AI 速览**：{overall_highlight}")
        lines.append("")
    lines.append("---")
    lines.append("")

    if not updates:
        lines.append("最近无新版本发布。")
        lines.append("")
        lines.append("---")
        lines.append("")
        return index

    # Group by category, preserving configured order.
    groups = OrderedDict()
    for cat in categories_order:
        groups[cat] = []
    for update in updates:
        cat = update.get("category") or "其他 / Other"
        groups.setdefault(cat, []).append(update)

    for cat, cat_updates in groups.items():
        if not cat_updates:
            continue
        lines.append(f"### {cat}（{len(cat_updates)} 个新版本）")
        lines.append("")
        lines.append("---")
        lines.append("")
        for update in cat_updates:
            index += 1
            render_tool_release(update, index, per_tool_map, lines)
    return index


# ===========================================================================
# Narrative rendering (the unified report's actual body)
# ===========================================================================

def _normalize_topics(raw_topics):
    """Turn a raw topics list into [{title, narrative}] (drops items[])."""
    out = []
    if not isinstance(raw_topics, list):
        return out
    for t in raw_topics:
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "").strip()
        narrative = (t.get("narrative") or "").strip()
        if title or narrative:
            out.append({"title": title, "narrative": narrative})
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
    is empty. Tolerates a missing/partial payload by returning empty values.
    """
    if not isinstance(narrative_data, dict):
        return "", [], [], ""

    overview = (narrative_data.get("overview") or "").strip()
    other = (narrative_data.get("other") or "").strip()

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


def _render_topic_list(lines, heading, topics):
    """Render a numbered list of topic narratives under ``heading``.

    No-op when ``topics`` is empty.
    """
    if not topics:
        return
    lines.append(f"## {heading}")
    lines.append("")
    for i, topic in enumerate(topics, 1):
        title = topic.get("title") or f"话题 {i}"
        narrative = topic.get("narrative") or ""
        lines.append(f"### {i}. {title}")
        lines.append("")
        lines.append(narrative if narrative else "*(暂无叙述)*")
        lines.append("")
        lines.append("---")
        lines.append("")


def render_narrative_body(overview, article_topics, podcast_topics, other):
    """Render the narrative body (everything between the header and footer).

    Always emits 今日概览. Then 今日重点 (article topics), 播客精选 (podcast
    topics), and 其他动态 (only when non-empty). Article and podcast topics
    are kept fully separate — they never cross-reference.
    """
    lines = []

    lines.append("## 📊 今日概览")
    lines.append("")
    lines.append(overview if overview else "*(本轮未生成概览)*")
    lines.append("")
    lines.append("---")
    lines.append("")

    _render_topic_list(lines, "🔥 今日重点", article_topics)
    _render_topic_list(lines, "🎧 播客精选", podcast_topics)

    if other:
        lines.append("## 📌 其他动态")
        lines.append("")
        lines.append(other)
        lines.append("")
        lines.append("---")
        lines.append("")

    return lines


# ===========================================================================
# Compact list renderers for GitHub / tool (low-volume, structured sources)
# ===========================================================================

def _compact_github_item(update, index, summary_map, lines):
    """Append one GitHub PR/issue as a tight block: title + meta + summary.

    Kept denser than the legacy render_pr/render_issue: meta on one line, AI
    summary (or short body excerpt) right under it, link last.
    """
    item_type = update.get("type", "issues")
    number = update.get("item_number")
    title = update.get("title") or "(无标题)"
    url = update.get("html_url") or ""
    repo = update.get("repo", "")
    author = update.get("author") or ""
    comments = update.get("comments", 0)
    reactions = update.get("reactions_total", 0)
    body_text = update.get("body_text") or ""
    ai_summary = summary_map.get(url, "")

    # Numbered title — link if we have a URL.
    prefix = f"- **{index}.** "
    if url:
        lines.append(f"{prefix}[{title}]({url})")
    else:
        lines.append(f"{prefix}{title}")

    # One-line meta: repo · type #N · author · 💬/👍
    meta_bits = []
    if repo:
        meta_bits.append(f"`{repo}`")
    if number:
        meta_bits.append(f"{'PR' if item_type == 'pulls' else 'Issue'} #{number}")
    if author:
        meta_bits.append(f"@{author}")
    extras = []
    if comments:
        extras.append(f"💬 {comments}")
    if reactions:
        extras.append(f"👍 {reactions}")
    if extras:
        meta_bits.append(" ".join(extras))
    if meta_bits:
        lines.append(f"  {' · '.join(meta_bits)}")

    # Summary: prefer AI one-liner, else a short body excerpt.
    if ai_summary:
        lines.append(f"  {ai_summary.strip()}")
    elif body_text:
        lines.append(f"  {_fallback_text(body_text)}")

    lines.append("")


def _compact_tool_release(update, index, per_tool_map, lines):
    """Append one tool release as a tight block: name `vX` + meta + key note.

    Release notes bodies can be long, so in the compact view we show only the
    AI per-tool key note (when available) + a link, not the full body.
    """
    name = update.get("name", "Unknown")
    version = update.get("version", "?")
    prev = update.get("previous_version")
    published = fmt_cst(update.get("published_at"))
    url = update.get("html_url", "")
    prerelease = update.get("prerelease", False)
    tid = update.get("tool_id", "")

    ver_label = f"`v{version}`"
    if prerelease:
        ver_label += " *(pre-release)*"
    lines.append(f"- **{index}.** {name} {ver_label}")

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
    if url:
        lines.append(f"  🔗 [{name} release]({url})")

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


def render_tool_list(tool_data, per_tool_map, overall_highlight, lines):
    """Render the tool section as a compact list grouped by category.

    Returns the number of releases rendered.
    """
    meta = tool_data.get("metadata", {})
    updates = tool_data.get("updates", [])
    categories_order = meta.get("categories_order") or []
    checked = meta.get("checked_count", 0)
    errors = meta.get("error_count", 0)
    update_count = meta.get("update_count", len(updates))
    hours = meta.get("hours", 168)

    lines.append("## 🛠 工具更新（{}）".format(
        "基线建立" if meta.get("baseline_run") else f"{update_count} 个新版本"))
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

    # Source-count summary line (computed from collector metadata)
    rss_count = len(rss_data.get("updates", [])) if rss_data else 0
    gh_count = (github_data.get("metadata", {}).get("update_count", 0)
                if github_data else 0)
    tool_meta = tool_data.get("metadata", {}) if tool_data else {}
    tool_count = 0 if tool_meta.get("baseline_run") else tool_meta.get("update_count", 0)

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

    _render_topic_list(lines, "🔥 今日重点", article_topics)
    _render_topic_list(lines, "🎧 播客精选", podcast_topics)

    if other:
        lines.append("## 📌 其他动态")
        lines.append("")
        lines.append(other)
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
    print(f"Unified report written to {output_path} "
          f"({', '.join(rendered)}, {mode})")


if __name__ == "__main__":
    main()
