#!/usr/bin/env python3
"""Merge per-batch AI summary files into a single ai_summaries.json.

Each batch file (ai_summaries_batch_N.json or *_batch_N.json) produced by the
summarization sub-agents has the shape:

    {"summaries": [ {"article_url": "...", "ai_summary": "..."}, ... ]}

or (podcast/tech variant) a flat dict:

    {"episode_url_or_any_url": "summary text", ...}

This script globs all such files, merges their entries, drops entries whose
key URL is empty or whose summary text is blank, and de-duplicates by URL
(last one wins). Missing or corrupt batch files are skipped with a warning
instead of aborting the merge.

Usage:
    python merge_summaries.py \\
        --batch-dir {project_root}/workspaces/rss \\
        --output   {project_root}/workspaces/rss/ai_summaries.json

Options:
    --pattern   glob pattern for batch files
                (default: *batch_*.json, catches wechat_batch_N, tech_batch_N,
                podcast_batch_N, ai_summaries_batch_N, etc.)
"""

import argparse
import glob
import json
import sys
from pathlib import Path


# Recognized key fields that hold the item URL across sources.
_URL_FIELDS = ("article_url", "episode_url", "url", "link")

# Recognized summary text fields across sources.
_SUMMARY_FIELDS = ("ai_summary", "summary", "summaries")


def load_json(path):
    """Load a JSON file. Returns (data, error). error is None on success."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as e:
        return None, str(e)


def _extract_entries(data):
    """Extract (url, summary_text, category) tuples from a batch file's data.

    Handles two shapes:
    1. {"summaries": [ {"article_url": "...", "ai_summary": "...", "category": "..."}, ... ]}
       (wechat/tech style)
    2. {"url_or_episode_url": "summary text", ...}
       (podcast style — flat dict keyed by URL)

    The category field is optional and only present for tech-source summaries.
    """
    entries = []

    if not isinstance(data, dict):
        return entries

    # Shape 1: has a "summaries" array
    summaries = data.get("summaries")
    if isinstance(summaries, list):
        for item in summaries:
            if not isinstance(item, dict):
                continue
            url = ""
            for field in _URL_FIELDS:
                url = (item.get(field) or "").strip()
                if url:
                    break
            text = ""
            for field in _SUMMARY_FIELDS:
                text = (item.get(field) or "").strip()
                if text:
                    break
            category = item.get("category", "")
            entries.append((url, text, category))
        return entries

    # Shape 2: flat dict keyed by URL
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        url = key.strip()
        text = value.strip() if isinstance(value, str) else ""
        entries.append((url, text, ""))

    return entries


def main():
    parser = argparse.ArgumentParser(
        description="Merge per-batch AI summary files into one ai_summaries.json"
    )
    parser.add_argument(
        "--batch-dir", required=True,
        help="Directory containing batch summary files",
    )
    parser.add_argument(
        "--pattern", default="*batch_*.json",
        help="Glob pattern for batch files (default: *batch_*.json)",
    )
    parser.add_argument(
        "-o", "--output", required=True,
        help="Output merged JSON path",
    )
    args = parser.parse_args()

    batch_glob = str(Path(args.batch_dir) / args.pattern)
    batch_files = sorted(glob.glob(batch_glob))

    if not batch_files:
        print(f"No batch files matching {batch_glob}", file=sys.stderr)
        print("Nothing to merge. The report generator will fall back to "
              "raw descriptions.", file=sys.stderr)
        sys.exit(0)

    print(f"Merging {len(batch_files)} batch file(s)...")

    merged = {}          # url -> {"ai_summary": str, "category": str} (dedup, last wins)
    total_seen = 0
    total_blank = 0
    skipped_files = 0

    for path in batch_files:
        data, err = load_json(path)
        if err is not None:
            print(f"  WARNING: skipping {path}: {err}", file=sys.stderr)
            skipped_files += 1
            continue

        entries = _extract_entries(data)
        if not entries:
            print(f"  WARNING: skipping {path}: no recognizable entries",
                  file=sys.stderr)
            skipped_files += 1
            continue

        kept = 0
        for url, text, category in entries:
            total_seen += 1
            if not url:
                total_blank += 1
                continue
            if not text:
                # Keep the entry keyed by url but with empty text so it's
                # visibly missing; the report falls back to description.
                total_blank += 1
            merged[url] = {"ai_summary": text, "category": category or ""}
            kept += 1
        print(f"  {Path(path).name}: {kept} entries")

    # Build the output list, preserving a stable order (sorted by url).
    out_summaries = [{"url": url,
                      "ai_summary": merged[url]["ai_summary"],
                      "category": merged[url]["category"]}
                     for url in sorted(merged.keys())]
    output = {"summaries": out_summaries}

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nMerged {len(out_summaries)} unique summaries into {output_path}")
    if total_blank:
        print(f"  ({total_blank} entries had blank/missing url or summary)")
    if skipped_files:
        print(f"  ({skipped_files} batch file(s) skipped due to errors)")


if __name__ == "__main__":
    main()
