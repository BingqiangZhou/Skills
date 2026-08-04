#!/usr/bin/env python3
"""Split a collector's latest_updates.json into sub-agent summarization batches.

Each batch is a small JSON array (capped item count + truncated text fields) so
that a summarization sub-agent receives a bounded input and reliably produces a
small JSON output.

Two source schemas are supported (selected via --source):

  --source rss
      Reads workspaces/daily-digests/rss/latest_updates.json produced by rss-monitor.
      Items carry a ``source`` field (wechat / tech / podcast). Batches are
      grouped by source so each sub-agent handles one content type. Each entry
      keeps url, title, the per-source name field, and a truncated ``full_text``
      (or ``shownotes``).

  --source github
      Reads workspaces/daily-digests/github-monitor/latest_updates.json produced by
      github-monitor. Each entry carries item_type / item_number / item_url /
      title / author / repo / and a truncated ``body_text``.

The text cap (default 1500 chars) and batch size (default 10) match the inline
recipes that used to live in the sibling SKILL.md files, so sub-agent prompts
remain valid.

Output files are named ``{prefix}_batch_{N}.json`` in --output-dir.
"""

import argparse
import json
import sys
from pathlib import Path


TEXT_CAP = 1500          # max chars of full_text/body_text put into a batch
DEFAULT_BATCH_SIZE = 10  # items per batch


def load_json(path):
    """Load a JSON file. Exit with error on missing/corrupt input."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: input file not found: {path}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: cannot parse JSON in {path}: {e}", file=sys.stderr)
        sys.exit(1)


def _truncate(text, cap=TEXT_CAP):
    """Return text trimmed to cap chars (or '' if falsy)."""
    if not text:
        return ""
    return text[:cap]


def prepare_rss_batches(updates, batch_size):
    """Group RSS updates by source and split into batches.

    Returns a list of (batch_index, batch_list) pairs. Each batch_list element
    is a dict with the fields a summarization sub-agent needs.
    """
    # Group by source (wechat / tech / podcast), preserving discovery order.
    groups = {}
    order = []
    for u in updates:
        src = u.get("source", "other")
        if src not in groups:
            groups[src] = []
            order.append(src)
        groups[src].append(u)

    batches = []
    for src in order:
        items = groups[src]
        for i in range(0, len(items), batch_size):
            chunk = items[i:i + batch_size]
            batch = []
            for u in chunk:
                text = u.get("full_text") or u.get("shownotes") or ""
                entry = {
                    "source": src,
                    "url": u.get("url", ""),
                    "title": u.get("title", ""),
                }
                if src == "wechat":
                    entry["account_name"] = u.get("account_name", "")
                elif src == "podcast":
                    entry["podcast_name"] = u.get("podcast_name", "")
                entry["full_text"] = _truncate(text)
                batch.append(entry)
            batches.append(batch)
    return batches


def prepare_github_batches(updates, batch_size):
    """Split GitHub updates into flat batches (already newest-first).

    Each entry carries item_type so the sub-agent can phrase PR vs issue
    summaries correctly.
    """
    batches = []
    for i in range(0, len(updates), batch_size):
        chunk = updates[i:i + batch_size]
        batch = [{
            "item_type": u.get("type", ""),
            "item_number": u.get("item_number"),
            "item_url": u.get("html_url", ""),
            "title": u.get("title", ""),
            "author": u.get("author", ""),
            "repo": u.get("repo", ""),
            "body_text": _truncate(u.get("body_text", "")),
        } for u in chunk]
        batches.append(batch)
    return batches


def main():
    parser = argparse.ArgumentParser(
        description="Split latest_updates.json into sub-agent summarization batches"
    )
    parser.add_argument("--source", required=True, choices=["rss", "github"],
                        help="Which collector's schema to read")
    parser.add_argument("-i", "--input", required=True,
                        help="Input latest_updates.json path")
    parser.add_argument("--output-dir", required=True,
                        help="Directory to write batch files into")
    parser.add_argument("--prefix", required=True,
                        help="Filename prefix, e.g. 'rss' -> rss_batch_0.json")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Items per batch (default: {DEFAULT_BATCH_SIZE})")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    data = load_json(args.input)
    updates = data.get("updates", [])
    if not updates:
        print(f"No updates in {args.input}; wrote 0 batches.")
        return

    if args.source == "rss":
        batches = prepare_rss_batches(updates, args.batch_size)
    else:
        batches = prepare_github_batches(updates, args.batch_size)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for idx, batch in enumerate(batches):
        out_path = output_dir / f"{args.prefix}_batch_{idx}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False)

    print(f"Saved {len(batches)} batch(es) of <= {args.batch_size} items "
          f"to {output_dir} (prefix '{args.prefix}', {len(updates)} items total).")


if __name__ == "__main__":
    main()
