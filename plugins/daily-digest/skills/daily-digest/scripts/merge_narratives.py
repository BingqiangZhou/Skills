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
    """Coerce a topics list into [{title, narrative}], dropping items[]."""
    out = []
    if not isinstance(raw, list):
        return out
    for t in raw:
        if not isinstance(t, dict):
            continue
        title = (t.get("title") or "").strip()
        narrative = (t.get("narrative") or "").strip()
        if title or narrative:
            out.append({"title": title, "narrative": narrative})
    return out


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

    print(f"merged -> {out}")
    print(f"  overview: {'yes' if merged['overview'] else 'no'}")
    print(f"  article_topics: {len(merged['article_topics'])}")
    print(f"  podcast_topics: {len(merged['podcast_topics'])}")
    print(f"  other: {'yes' if merged['other'] else 'no'}")


if __name__ == "__main__":
    main()
