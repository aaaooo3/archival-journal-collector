"""Journal source parsers.

Each source exposes a uniform (duck-typed) interface — see ``sources/base.py``:
``KEY``, ``NAME``, ``URL``, ``fetch_latest_issue(session, logger)``,
``sort_key(issue_id)``, and for backfill ``list_issue_urls`` / ``fetch_issue`` /
``issue_id_from_url``. Korean journals are scraping modules; international ones
are :class:`CrossrefSource` instances configured by ISSN.
"""

from . import jksarm, kjas
from .crossref import CrossrefSource

# International journals via Crossref (free, no key). Add more here by ISSN.
_CROSSREF_SOURCES = [
    CrossrefSource("amarch", "The American Archivist", "0360-9081"),
    CrossrefSource("archivaria", "Archivaria", "0318-6954"),
    CrossrefSource("archrec", "Archives and Records", "2325-7962"),
]

# Registry consumed by watcher.py — keyed by the stable source KEY.
SOURCES: dict = {
    jksarm.KEY: jksarm,
    kjas.KEY: kjas,
}
for _s in _CROSSREF_SOURCES:
    SOURCES[_s.KEY] = _s

__all__ = ["SOURCES", "jksarm", "kjas", "CrossrefSource"]
