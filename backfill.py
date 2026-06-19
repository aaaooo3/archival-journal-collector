#!/usr/bin/env python3
"""Backfill past issues of both journals into ``output/`` for the static site.

Unlike ``watcher.py`` (which only tracks the *latest* issue for change
detection), this enumerates each journal's archive and collects the most recent
``--count`` issues — writing a Markdown + JSON digest per issue. Issues whose
output already exists are skipped, so re-runs are cheap and resumable.

``state.json`` is left untouched: backfill is about populating history, not
about "what's the newest issue we've seen".

Usage
-----
    python backfill.py                       # last 10 issues of each journal
    python backfill.py --count 20            # last 20 each
    python backfill.py --count 5 --journal kjas
    python backfill.py --all                 # entire archive (slow; polite delays)
"""

from __future__ import annotations

import argparse
import logging

from build_site import build_site
from render import write_issue
from sources import SOURCES
from sources.base import ExtractionError, make_session
from tooling import OUTPUT_DIR, SITE_DIR, setup_logging

DEFAULT_COUNT = 10


def _output_exists(key: str, issue_id: str) -> bool:
    safe_id = issue_id.replace("/", "-").replace(" ", "")
    return (OUTPUT_DIR / f"{key}_{safe_id}.json").exists()


def backfill_journal(key: str, count: int | None, logger: logging.Logger) -> int:
    """Collect up to ``count`` most recent issues of one journal. Returns count written."""
    source = SOURCES[key]
    session = make_session()

    try:
        urls = source.list_issue_urls(session, logger, limit=count)
    except ExtractionError as exc:
        logger.error("[%s] could not list issues: %s", key, exc)
        return 0

    written = 0
    for url in urls:
        # Skip issues we already have, without fetching them.
        issue_id = source.issue_id_from_url(url) if hasattr(source, "issue_id_from_url") else None
        if issue_id and _output_exists(key, issue_id):
            logger.info("[%s] skip %s (already collected)", key, issue_id)
            continue
        try:
            issue = source.fetch_issue(session, url, logger)
        except ExtractionError as exc:
            logger.error("[%s] extraction failed for %s: %s", key, url, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - keep going through the archive
            logger.exception("[%s] unexpected error for %s: %s", key, url, exc)
            continue

        if _output_exists(key, issue.issue_id):
            logger.info("[%s] skip %s (already collected)", key, issue.issue_id)
            continue
        write_issue(issue, OUTPUT_DIR, logger)
        written += 1

    logger.info("[%s] backfill wrote %d new issue(s)", key, written)
    return written


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--count", type=int, default=DEFAULT_COUNT,
        help=f"most recent issues per journal to collect (default: {DEFAULT_COUNT})",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="collect the entire archive (overrides --count; slow)",
    )
    parser.add_argument(
        "--journal", choices=list(SOURCES.keys()) + ["all"], default="all",
        help="which journal to backfill (default: all)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logger = setup_logging("backfill")
    journals = list(SOURCES.keys()) if args.journal == "all" else [args.journal]
    count = None if args.all else args.count

    total = 0
    for key in journals:
        total += backfill_journal(key, count, logger)

    logger.info("Backfill complete: %d new issue(s) across %s", total, journals)
    try:
        build_site(OUTPUT_DIR, SITE_DIR, logger)
    except Exception as exc:  # noqa: BLE001
        logger.exception("site build failed: %s", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
