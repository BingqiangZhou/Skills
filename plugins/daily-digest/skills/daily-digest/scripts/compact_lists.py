"""Compact-list track: render the GitHub and tool sections of the report.

Split out of generate_unified_report.py. GitHub and tool updates are
low-volume and inherently structured ("which repo PR merged", "which tool
released which version"), so unlike RSS they are NOT synthesized into a
narrative — each item renders as one tight block with its link and key note.
"""

from collections import OrderedDict

from io_utils import fmt_cst

# Max chars of body text shown when no AI summary is available.
FALLBACK_CHARS = 200


def fallback_text(text, max_chars=FALLBACK_CHARS):
    """Truncate text to max_chars with ellipsis."""
    if not text:
        return ""
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def build_github_summary_map(summaries_data):
    """Build a {item_url: ai_summary} lookup."""
    summaries = (summaries_data.get("summaries")
                 if isinstance(summaries_data, dict) else None)
    if not isinstance(summaries, list):
        return {}
    result = {}
    for item in summaries:
        if not isinstance(item, dict):
            continue
        url = (item.get("item_url") or item.get("pr_url")
               or item.get("issue_url") or "")
        if url:
            result[url] = item.get("ai_summary", "")
    return result


def build_tool_highlights(highlights_data):
    """Build (tool_id->note map, overall string) from ai_highlights.json."""
    if not highlights_data or not isinstance(highlights_data, dict):
        return {}, ""
    per_tool = highlights_data.get("per_tool") or {}
    overall = highlights_data.get("highlights") or ""
    if not isinstance(per_tool, dict):
        per_tool = {}
    return per_tool, overall


def repo_display_name(repo_key, per_repo):
    """Look up a human-friendly name for a repo from metadata.per_repo."""
    for entry in per_repo or []:
        if entry.get('repo') == repo_key and entry.get('name'):
            return entry['name']
    return repo_key


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
        lines.append(f"  {fallback_text(body_text)}")

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


def version_key(v):
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


def merge_tool_updates(updates):
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
        # Representative = highest version, compared numerically as a version
        # tuple — the old string compare ranked "0.9.0" above "0.10.0" and
        # could attach the older release's link/published_at/body to the
        # collapsed entry. Entries arrive newest→oldest, so strict > keeps
        # the newest published_at among equal versions.
        best = entries[0]
        for u in entries[1:]:
            if version_key(u.get("version")) > version_key(best.get("version")):
                best = u
        merged_entry = dict(best)
        versions = info["versions"]
        if len(versions) > 1:
            # Collector supplies versions oldest→newest; entries are
            # newest→oldest, so sort defensively by version tuple.
            try:
                versions = sorted(versions, key=version_key)
            except Exception:
                pass
            merged_entry["versions"] = versions
            merged_entry["version"] = versions[-1]
        else:
            merged_entry["version"] = best.get("version")
        merged.append(merged_entry)
    return merged


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
    updates = merge_tool_updates(updates)
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
