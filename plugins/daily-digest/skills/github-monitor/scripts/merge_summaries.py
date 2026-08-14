#!/usr/bin/env python3
"""Merge per-batch AI summary files into a single ai_summaries.json.

Each batch file (ai_summaries_batch_N.json) produced by the summarization
sub-agents has the shape:

    {"summaries": [ {"item_url": "...", "ai_summary": "..."}, ... ]}

(Older batches that key on `pr_url` / `issue_url` are still accepted for
backward compatibility.)

This script globs all such files, merges their `summaries` arrays, drops
entries whose item_url is empty or whose ai_summary is blank, and
de-duplicates by item_url (last one wins). Missing or corrupt batch files are
skipped with a warning instead of aborting the merge.

Usage:
    python merge_summaries.py \\
        --batch-dir {project_root}/workspaces/daily-digests/data/github-monitor \\
        --output   {project_root}/workspaces/daily-digests/data/github-monitor/ai_summaries.json

Options:
    --pattern   glob pattern for batch files
                (default: ai_summaries_batch_*.json)
"""

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import save_json_atomic  # noqa: E402


def item_url(item):
    """Return the best available URL key for a summary entry.

    Prefers the generic `item_url`; falls back to the legacy per-type keys
    (`pr_url` / `issue_url`) so old batch files still merge cleanly.
    """
    return (item.get("item_url")
            or item.get("pr_url")
            or item.get("issue_url")
            or "").strip()


def load_json(path):
    """Load a JSON file. Returns (data, error). error is None on success.

    UnicodeDecodeError is caught too: it is a ValueError (NOT an OSError),
    so a sub-agent writing GBK used to escape this loader as a traceback.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, str(e)


def main():
    parser = argparse.ArgumentParser(
        description="Merge per-batch AI summary files into one ai_summaries.json"
    )
    parser.add_argument(
        "--batch-dir", required=True,
        help="Directory containing ai_summaries_batch_*.json files"
    )
    parser.add_argument(
        "--pattern", default="ai_summaries_batch_*.json",
        help="Glob pattern for batch files (default: ai_summaries_batch_*.json)"
    )
    parser.add_argument(
        "-o", "--output", required=True,
        help="Output merged JSON path"
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    batch_glob = str(Path(args.batch_dir) / args.pattern)
    batch_files = sorted(glob.glob(batch_glob))

    if not batch_files:
        print(f"No batch files matching {batch_glob}", file=sys.stderr)
        print("Nothing to merge. The report generator will fall back to "
              "raw descriptions.", file=sys.stderr)
        sys.exit(0)

    print(f"Merging {len(batch_files)} batch file(s)...")

    merged = {}          # item_url -> summary entry (dedup, last wins)
    total_blank = 0
    skipped_files = 0

    for path in batch_files:
        data, err = load_json(path)
        if err is not None:
            print(f"  WARNING: skipping {path}: {err}", file=sys.stderr)
            skipped_files += 1
            continue

        summaries = data.get("summaries") if isinstance(data, dict) else None
        if not isinstance(summaries, list):
            print(f"  WARNING: skipping {path}: no 'summaries' array",
                  file=sys.stderr)
            skipped_files += 1
            continue

        kept = 0
        for item in summaries:
            if not isinstance(item, dict):
                continue
            url = item_url(item)
            text = (item.get("ai_summary") or "").strip()
            if not url:
                total_blank += 1
                continue
            if not text:
                # Keep the entry keyed by url but with empty text so it's
                # visibly missing; the report falls back to description.
                total_blank += 1
            merged[url] = item
            kept += 1
        print(f"  {Path(path).name}: {kept} entries")

    # Build the output list, preserving a stable order (sorted by url).
    out_summaries = [merged[u] for u in sorted(merged.keys())]
    output = {"summaries": out_summaries}

    output_path = Path(args.output)
    save_json_atomic(output_path, output)

    print(f"\nMerged {len(out_summaries)} unique summaries into {output_path}")
    if total_blank:
        print(f"  ({total_blank} entries had blank/missing url or summary)")
    if skipped_files:
        print(f"  ({skipped_files} batch file(s) skipped due to errors)")


if __name__ == "__main__":
    main()
