#!/usr/bin/env python3
"""Split merged RSS AI summaries into articles / podcasts tracks.

Step 2d-0 of the daily-digest workflow. The merged ``rss_ai_summaries.json``
mixes all three RSS sources (wechat + tech + podcast); each editor sub-agent
only needs its own track, so this script looks up each summary URL's ``source``
in the RSS collector output and partitions the summaries into:

  - ``rss_articles_summaries.json``  — wechat + tech
  - ``rss_podcasts_summaries.json``  — podcast

This is the standalone-script replacement for the inline ``python - <<'PY'``
heredoc that used to live in ``references/editor-prompts.md`` §2d-0, so the
split runs reliably across environments (no shell heredoc / quoting pitfalls)
and prints counts the orchestrator can use as a guardrail.

Exits non-zero only on unreadable input; a zero exit with ``podcasts: 0`` when
the collector had podcasts is a SIGNAL to investigate, not an error here.
"""

import argparse
import json
import sys
from pathlib import Path


def load_json(path):
    """Load JSON, exit on missing/corrupt input."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: input file not found: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: cannot parse JSON in {path}: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Split rss_ai_summaries.json into articles / podcasts tracks."
    )
    parser.add_argument("--rss-input", required=True,
                        help="RSS latest_updates.json (for source lookup by url)")
    parser.add_argument("--summaries", required=True,
                        help="Merged rss_ai_summaries.json to split")
    parser.add_argument("--output-dir", required=True,
                        help="Directory to write rss_articles_summaries.json "
                             "and rss_podcasts_summaries.json")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    rss_data = load_json(args.rss_input)
    summaries_data = load_json(args.summaries)

    # url -> {source, podcast meta} map from the collector output. The source
    # tag is authoritative; podcast_name + title are carried through so the
    # per-episode editor keeps each episode's identity (show + title) instead
    # of clustering different shows' episodes into content-theme buckets.
    url_meta = {}
    for u in rss_data.get("updates", []):
        url = (u.get("url") or "").strip()
        if not url:
            continue
        url_meta[url] = {
            "source": u.get("source", ""),
            "podcast_name": u.get("podcast_name", ""),
            "title": u.get("title", ""),
        }

    articles, podcasts = [], []
    unknown = 0
    for e in summaries_data.get("summaries", []):
        url = (e.get("url") or "").strip()
        meta = url_meta.get(url, {})
        source = meta.get("source", "")
        rec = {
            "url": url,
            "ai_summary": e.get("ai_summary", ""),
            "category": e.get("category", ""),
            "source": source,
        }
        if source == "podcast":
            rec["podcast_name"] = meta.get("podcast_name", "")
            rec["title"] = meta.get("title", "")
            podcasts.append(rec)
        else:
            articles.append(rec)
            if not source:
                unknown += 1

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "rss_articles_summaries.json", "w", encoding="utf-8") as f:
        json.dump({"summaries": articles}, f, ensure_ascii=False, indent=2)
    with open(out_dir / "rss_podcasts_summaries.json", "w", encoding="utf-8") as f:
        json.dump({"summaries": podcasts}, f, ensure_ascii=False, indent=2)

    # Also report how many podcasts the collector saw, so the orchestrator can
    # guard against a split that lost all podcasts despite real podcast items.
    collector_podcasts = sum(1 for u in rss_data.get("updates", [])
                             if u.get("source") == "podcast")
    print(f"articles: {len(articles)}  podcasts: {len(podcasts)}")
    print(f"collector podcasts: {collector_podcasts}")
    if unknown:
        print(f"WARNING: {unknown} summary URL(s) had no matching source in "
              f"collector output; routed to articles.", file=sys.stderr)
    if collector_podcasts > 0 and len(podcasts) == 0:
        print(f"WARNING: collector saw {collector_podcasts} podcasts but the "
              f"split produced 0 podcast summaries — do NOT run the podcasts "
              f"editor with empty input; investigate the mismatch first.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
