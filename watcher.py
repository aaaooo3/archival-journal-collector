#!/usr/bin/env python3
"""archival-journal-collector — entry point.

Watches two Korean archival journals and, when a *new* issue appears, writes a
Markdown digest (title / authors / pages / DOI / abstract / full-text link) of
that issue. No AI summarisation is performed.

Usage
-----
    python watcher.py --once                 # check all journals once and exit
    python watcher.py --once --journal kjas  # check only 기록학연구
    python watcher.py                        # run continuously (default 6h loop)
    python watcher.py --interval 3600        # continuous, custom interval (s)

State (``state.json``) records the last processed issue per journal; only
strictly newer issues are processed. If nothing is new, no file is written —
only a log line is emitted.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from build_site import build_site
from render import write_issue
from sources import SOURCES
from sources.base import ExtractionError, make_session

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
OUTPUT_DIR = ROOT / "output"
SITE_DIR = ROOT / "site"
LOG_DIR = ROOT / "logs"
DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours


# --- Logging ----------------------------------------------------------------


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("collector")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:  # avoid duplicate handlers on repeated calls
        return logger

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(LOG_DIR / "watcher.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console)
    return logger


# --- State ------------------------------------------------------------------


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def is_newer(source, fetched_sort_key: tuple, last_issue_id: str | None) -> bool:
    """True if the fetched issue is strictly newer than the recorded one."""
    if not last_issue_id:
        return True  # nothing recorded yet -> treat current latest as new
    return fetched_sort_key > source.sort_key(last_issue_id)


# --- Core -------------------------------------------------------------------


def process_source(key: str, state: dict, logger: logging.Logger) -> bool:
    """Check one journal. Returns True if a new issue was written."""
    source = SOURCES[key]
    session = make_session()
    recorded = (state.get(key) or {}).get("last_issue_id")

    try:
        issue = source.fetch_latest_issue(session, logger)
    except ExtractionError as exc:
        logger.error("[%s] extraction failed: %s", key, exc)
        return False
    except Exception as exc:  # noqa: BLE001 - want a clear log, keep other journals running
        logger.exception("[%s] unexpected error: %s", key, exc)
        return False

    if not is_newer(source, issue.sort_key, recorded):
        logger.info(
            "[%s] no new issue (latest %s, already processed %s)",
            key, issue.issue_id, recorded,
        )
        return False

    write_issue(issue, OUTPUT_DIR, logger)
    state[key] = {
        "last_issue_id": issue.issue_id,
        "last_issue_label": issue.issue_label,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)
    logger.info("[%s] NEW issue processed: %s", key, issue.issue_label)
    return True


def run_once(journals: list[str], logger: logging.Logger) -> int:
    state = load_state()
    new_count = 0
    for key in journals:
        if process_source(key, state, logger):
            new_count += 1
    logger.info("Run complete: %d new issue(s) across %s", new_count, journals)

    # Rebuild the static site so it always reflects the current output/.
    try:
        build_site(OUTPUT_DIR, SITE_DIR, logger)
    except Exception as exc:  # noqa: BLE001 - site build must not fail the run
        logger.exception("site build failed: %s", exc)

    return new_count


# --- CLI --------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--once", action="store_true",
        help="run a single check then exit (otherwise loop continuously)",
    )
    parser.add_argument(
        "--journal", choices=list(SOURCES.keys()) + ["all"], default="all",
        help="which journal to check (default: all)",
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
        help="seconds between checks in continuous mode (default: 21600)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logger = setup_logging()
    journals = list(SOURCES.keys()) if args.journal == "all" else [args.journal]

    if args.once:
        run_once(journals, logger)
        return 0

    logger.info("Starting continuous watch (interval %ds) for %s", args.interval, journals)
    try:
        while True:
            run_once(journals, logger)
            logger.info("Sleeping %ds until next check", args.interval)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Interrupted; exiting.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
