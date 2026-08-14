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

import io_utils
import narrative

# Shared normalizers — the SINGLE implementation lives in narrative.py.
# This script normalizes on write and generate_unified_report re-normalizes
# on read; the two used to keep verbatim copies that could drift apart.
_normalize_topics = narrative.normalize_topics
_normalize_str_list = narrative.normalize_str_list
_normalize_other = narrative.normalize_other
_normalize_podcast_episodes = narrative.normalize_podcast_episodes


def load_json_safe(path):
    """Load JSON, return (data, ok). Print a warning on failure.

    UnicodeDecodeError is caught too: it is a ValueError (NOT an OSError),
    so an editor sub-agent writing GBK used to escape as a traceback.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), True
    except FileNotFoundError:
        print(f"WARNING: missing {path}; skipping.", file=sys.stderr)
        return {}, False
    except json.JSONDecodeError as e:
        print(f"WARNING: corrupt {path} ({e}); skipping.", file=sys.stderr)
        return {}, False
    except (UnicodeDecodeError, OSError) as e:
        print(f"WARNING: cannot read {path} ({type(e).__name__}: {e}); "
              f"skipping.", file=sys.stderr)
        return {}, False


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
    if not isinstance(articles, dict):
        articles = {}
    if not isinstance(podcasts, dict):
        podcasts = {}

    # Non-destructive merge: each file contributes ONLY its own keys.
    # articles -> overview, article_topics, other
    # podcasts -> podcast_episodes, podcast_other (new per-episode format);
    #             podcast_topics kept as a legacy fallback for the renderer.
    merged = {
        "overview": (articles.get("overview") or "").strip(),
        "article_topics": _normalize_topics(articles.get("article_topics")),
        "podcast_topics": _normalize_topics(podcasts.get("podcast_topics")),
        "podcast_episodes": _normalize_podcast_episodes(
            podcasts.get("podcast_episodes")),
        "podcast_other": _normalize_str_list(podcasts.get("podcast_other")),
        "other": _normalize_other(articles.get("other")),
    }

    out = Path(args.output)
    io_utils.save_json_atomic(out, merged)

    n_art_bad = _warn_unscannable("article", merged["article_topics"])
    n_pod_bad = _warn_unscannable("podcast", merged["podcast_topics"])
    other_unscannable = isinstance(merged["other"], str) and bool(merged["other"])
    if other_unscannable:
        print("WARNING: `other` is a paragraph, not a bullet array — "
              "editor did not comply; consider rerunning step 2d.",
              file=sys.stderr)
    # The podcast editor should produce per-episode output. Warn if neither
    # the new (podcast_episodes) nor the legacy (podcast_topics) track came
    # back — a silent empty podcast section is the failure this catches.
    if not merged["podcast_episodes"] and not merged["podcast_topics"]:
        print("WARNING: no podcast narrative produced (podcast_episodes and "
              "podcast_topics both empty); consider rerunning step 2d-2.",
              file=sys.stderr)

    print(f"merged -> {out}")
    print(f"  overview: {'yes' if merged['overview'] else 'no'}")
    print(f"  article_topics: {len(merged['article_topics'])}"
          + (f" ({n_art_bad} unscannable)" if n_art_bad else ""))
    print(f"  podcast_episodes: {len(merged['podcast_episodes'])}")
    print(f"  podcast_other: {len(merged['podcast_other'])}")
    print(f"  podcast_topics: {len(merged['podcast_topics'])} (legacy fallback)"
          + (f" ({n_pod_bad} unscannable)" if n_pod_bad else ""))
    if isinstance(merged["other"], list):
        print(f"  other: {len(merged['other'])} bullets")
    elif merged["other"]:
        print("  other: (paragraph, unscannable)")
    else:
        print("  other: no")


if __name__ == "__main__":
    main()
