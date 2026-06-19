"""Journal source parsers.

Each source module exposes a uniform interface (see ``sources/base.py``):
``KEY``, ``NAME``, ``URL``, ``fetch_latest_issue(session, logger)`` and
``sort_key(issue_id)``.
"""

from . import jksarm, kjas

# Registry consumed by watcher.py — keyed by the stable source KEY.
SOURCES = {
    jksarm.KEY: jksarm,
    kjas.KEY: kjas,
}

__all__ = ["SOURCES", "jksarm", "kjas"]
