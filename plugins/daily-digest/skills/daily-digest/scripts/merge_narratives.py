#!/usr/bin/env python3
"""Merge the articles + podcasts editor outputs into digest_narrative.json.

Step 2d-merge of the daily-digest workflow. The two editor sub-agents write to
SEPARATE files (``digest_narrative_articles.json`` and
``digest_narrative_podcasts.json``) to avoid any write race; this script merges
them into the single ``digest_narrative.json`` consumed by the report generator.

Why a dedicated script (vs. the old inline ``python - <<'PY'`` heredoc):

  - **Non-destructive field merge.** The old ``merged.update(json.load(...))``
    would let an articles file that (against the prompt) carried an extra
    ``"podcast_topics": []`` key clobber the real podcast topics. Here each
    file only contributes the keys it owns, so an unexpected key in one file
    can never overwrite the other file's content.
  - **Resilient.** A missing/corrupt file is skipped with a warning instead of
    crashing; the report generator then renders a visible placeholder.
  - **Observable.** Prints exactly what merged, so a lost track is obvious.
"""

import argparse
import json
import sys
from pathlib import Path


def load_json_safe(path):
    """Load JSON, return (data, ok). Print a warning on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), True
    except FileNotFoundError:
        print(f"WARNING: missing {path}; skipping.", file=sys.stderr)
        return {}, False
    except json.JSONDecodeError as e:
        print(f"WARNING: corrupt {path} ({e}); skipping.", file=sys.stderr)
        return {}, False


def _normalize_topics(raw):
    """Coerce a topics list into [{title, lead, bullets, narrative}].

    Drops the audit-only `items[]`. Carries the new scannable fields
    (`lead`, `bullets`) through to the renderer; also keeps the legacy
    `narrative` string so old editor output still renders as a paragraph
    when `bullets` is absent.
    """
    out = []
    if not isinstance(raw, list):
        return out
    for t in raw:
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


def _warn_unscannable(track, topics):
    """Warn about topics that fell back to a narrative paragraph.

    A topic carrying ``narrative`` but no ``bullets`` means the editor
    ignored the scannable (lead + bullets) format; the renderer will then
    silently fall back to the dense paragraph. Surface each such topic so
    the orchestrator can rerun that editor instead of shipping an
    unscannable report. Returns the count of unscannable topics.
    """
    count = 0
    for t in topics:
        if not t.get("bullets") and t.get("narrative"):
            count += 1
            title = t.get("title") or "(untitled)"
            print(
                f"WARNING: {track} topic \"{title}\" has narrative but no "
                f"bullets — editor likely ignored scannable format; consider "
                f"rerunning step 2d.",
                file=sys.stderr,
            )
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Merge digest_narrative_articles.json + "
                    "digest_narrative_podcasts.json into digest_narrative.json."
    )
    parser.add_argument("--input-dir", required=True,
                        help="Directory holding the two editor output files")
    parser.add_argument("--output", required=True,
                        help="Output digest_narrative.json path")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    base = Path(args.input_dir)
    articles_path = base / "digest_narrative_articles.json"
    podcasts_path = base / "digest_narrative_podcasts.json"

    articles, _ = load_json_safe(articles_path)
    podcasts, _ = load_json_safe(podcasts_path)

    # Non-destructive merge: each file contributes ONLY its own keys.
    # articles -> overview, article_topics, other
    # podcasts -> podcast_topics
    merged = {
        "overview": (articles.get("overview") or "").strip(),
        "article_topics": _normalize_topics(articles.get("article_topics")),
        "podcast_topics": _normalize_topics(podcasts.get("podcast_topics")),
        "other": (articles.get("other") or "").strip(),
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    n_art_bad = _warn_unscannable("article", merged["article_topics"])
    n_pod_bad = _warn_unscannable("podcast", merged["podcast_topics"])

    print(f"merged -> {out}")
    print(f"  overview: {'yes' if merged['overview'] else 'no'}")
    print(f"  article_topics: {len(merged['article_topics'])}"
          + (f" ({n_art_bad} unscannable)" if n_art_bad else ""))
    print(f"  podcast_topics: {len(merged['podcast_topics'])}"
          + (f" ({n_pod_bad} unscannable)" if n_pod_bad else ""))
    print(f"  other: {'yes' if merged['other'] else 'no'}")


if __name__ == "__main__":
    main()
