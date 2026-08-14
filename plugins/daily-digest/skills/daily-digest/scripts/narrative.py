"""Narrative track: normalize editor output and render the report's body.

Split out of generate_unified_report.py (which had grown to ~950 lines as the
merge of three former per-collector report scripts). This module owns the
RSS-driven half of the report — the editor sub-agents' ``digest_narrative``
payload and its rendering:

- normalizers (``normalize_topics`` / ``normalize_str_list`` /
  ``normalize_podcast_episodes`` / ``normalize_other``) are the SINGLE shared
  implementation: merge_narratives.py normalizes on write and the report
  generator re-normalizes on read, and the two used to keep verbatim copies
  that could drift apart.
- ``build_narrative`` maps a raw narrative payload onto the render tuple.
- renderers draw the 今日概览 / 今日重点 / 播客精选 / 其他动态 sections.
"""


def normalize_topics(raw_topics):
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


def normalize_str_list(raw):
    """Coerce into a list of stripped non-empty strings. Always a list."""
    if isinstance(raw, list):
        return [b.strip() for b in raw if isinstance(b, str) and b.strip()]
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    return []


def normalize_podcast_episodes(raw):
    """Coerce podcast_episodes into a list of {show, title, summary, url}.

    Accepts ``show`` or ``podcast_name`` as the show key (defensive). Drops
    entries missing both show and title. Carries ``url`` for optional linking.
    """
    out = []
    if not isinstance(raw, list):
        return out
    for e in raw:
        if not isinstance(e, dict):
            continue
        show = (e.get("show") or e.get("podcast_name") or "").strip()
        title = (e.get("title") or "").strip()
        summary = (e.get("summary") or "").strip()
        url = (e.get("url") or "").strip()
        if not (show or title):
            continue
        out.append({"show": show, "title": title,
                    "summary": summary, "url": url})
    return out


def normalize_other(raw):
    """Coerce the ``other`` roundup into a list of bullet strings.

    The editor is asked to emit ``other`` as an array of short bullets; a
    list is kept (empties stripped). A legacy/non-compliant string is
    returned as-is so the renderer's paragraph safety net still works and
    callers can warn that the silent fallback engaged.
    """
    if isinstance(raw, list):
        return [b.strip() for b in raw if isinstance(b, str) and b.strip()]
    if isinstance(raw, str):
        s = raw.strip()
        return s if s else ""
    return ""


def build_narrative(narrative_data):
    """Normalize the editor sub-agent's digest_narrative.json output.

    Supports two payload shapes:

    - **Split** (articles + podcasts independent): top-level ``overview``,
      ``other``, ``article_topics``, plus the podcast track
      (``podcast_episodes``/``podcast_other`` per-episode format, with
      ``podcast_topics`` kept as a legacy fallback).
    - **Legacy/flat**: top-level ``overview``, ``other``, ``topics`` list
      (kept for backward compatibility).

    Returns ``(overview, article_topics, podcast_topics, podcast_episodes,
    podcast_other, other)``. For the legacy shape, all topics land in
    ``article_topics`` and the podcast fields are empty. ``other`` is a list
    of bullet strings when the editor complied with the scannable format,
    else a legacy paragraph string (or ""). Tolerates a missing/partial
    payload by returning empty values.
    """
    if not isinstance(narrative_data, dict):
        return "", [], [], [], [], ""

    overview = (narrative_data.get("overview") or "").strip()
    other = normalize_other(narrative_data.get("other"))

    if "article_topics" in narrative_data or "podcast_topics" in narrative_data:
        article_topics = normalize_topics(narrative_data.get("article_topics"))
        podcast_topics = normalize_topics(narrative_data.get("podcast_topics"))
    else:
        article_topics = normalize_topics(narrative_data.get("topics"))
        podcast_topics = []

    podcast_episodes = normalize_podcast_episodes(
        narrative_data.get("podcast_episodes"))
    podcast_other = normalize_str_list(narrative_data.get("podcast_other"))
    return (overview, article_topics, podcast_topics,
            podcast_episodes, podcast_other, other)


def fallback_overview(digest_highlights_text, tool_overall, rss_insight):
    """Best-effort overview string when no narrative payload is available."""
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


def expected_podcast_count(rss_data):
    """Count ``source == "podcast"`` items in the RSS collector output.

    Used to tell whether the podcast narrative *should* have been produced, so
    the report can warn loudly instead of silently omitting the section when
    the editor sub-agent failed or produced empty output.
    """
    if not rss_data:
        return 0
    return sum(1 for u in (rss_data.get("updates") or [])
               if u.get("source") == "podcast")


def render_topic_list(lines, heading, topics, missing_placeholder=None):
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


def _fmt_episode(title, summary):
    """One episode as 'title：summary' (whichever parts are present)."""
    if title and summary:
        return f"{title}：{summary}"
    return title or summary


def render_podcast_episodes(lines, heading, episodes, other_episodes,
                            legacy_topics, missing_placeholder=None):
    """Render the podcast section as a PER-EPISODE list.

    Preferred shape: ``episodes`` (list of {show, title, summary, url}) → each
    episode is its own line. Episodes from the SAME show are grouped under the
    show name (a bold parent + nested episode bullets); single-episode shows
    stay flat as ``- **show** · title：summary``. Trivial episodes
    (``other_episodes``) print as a short "其他播客" tail.

    Fallbacks so the section is never silently lost:
    - empty ``episodes`` but legacy ``legacy_topics`` present → render the old
      topic-narrative list (backward compat);
    - both empty + a ``missing_placeholder`` → emit the heading + warning;
    - both empty + no placeholder → no-op.
    """
    if episodes:
        lines.append(f"## {heading}")
        lines.append("")
        # Group by show, preserving first-seen order. Episodes with no show
        # are rendered as standalone bullets (never bucketed under an empty
        # parent label).
        groups = {}
        order = []
        standalone = []
        for ep in episodes:
            show = (ep.get("show") or "").strip()
            title = (ep.get("title") or "").strip()
            summary = (ep.get("summary") or "").strip()
            body = _fmt_episode(title, summary)
            if not show:
                if body:
                    standalone.append(body)
                continue
            if show not in groups:
                groups[show] = []
                order.append(show)
            groups[show].append(body)
        for show in order:
            eps = groups[show]
            if len(eps) == 1:
                lines.append(f"- **{show}** · {eps[0]}"
                             if eps[0] else f"- **{show}**")
            else:
                lines.append(f"- **{show}**")
                for body in eps:
                    lines.append(f"  - {body}" if body else "  -")
        for body in standalone:
            lines.append(f"- {body}")
        if other_episodes:
            lines.append("")
            lines.append("- **其他播客**")
            for line in other_episodes:
                line = (line or "").strip()
                if line:
                    lines.append(f"  - {line}")
        lines.append("")
        lines.append("---")
        lines.append("")
        return

    # Legacy fallback: old podcast_topics theme structure.
    if legacy_topics:
        render_topic_list(lines, heading, legacy_topics)
        return

    # Nothing produced.
    if missing_placeholder is None:
        return
    lines.append(f"## {heading}")
    lines.append("")
    lines.append(missing_placeholder)
    lines.append("")
    lines.append("---")
    lines.append("")
