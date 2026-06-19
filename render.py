"""Render an :class:`Issue` to Markdown and write it under ``output/``.

No AI summarisation is performed — the abstract is reproduced verbatim from the
source page, per spec.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path

from sources.base import Article, Issue


def _format_article(index: int, art: Article) -> str:
    lines: list[str] = []
    lines.append(f"### {index}. {art.title}")
    lines.append("")
    authors = ", ".join(art.authors) if art.authors else "—"
    lines.append(f"- **저자**: {authors}")
    lines.append(f"- **페이지**: {art.pages or '—'}")
    if art.doi:
        lines.append(f"- **DOI**: [{art.doi}](https://doi.org/{art.doi})")
    else:
        lines.append("- **DOI**: —")
    if art.url:
        lines.append(f"- **원문 링크**: {art.url}")
    else:
        lines.append("- **원문 링크**: —")
    lines.append("")
    lines.append("**초록**")
    lines.append("")
    lines.append(art.abstract if art.abstract else "_(초록 없음)_")
    lines.append("")
    return "\n".join(lines)


def render_issue_markdown(issue: Issue) -> str:
    """Build the full Markdown document for one issue."""
    out: list[str] = []
    out.append(f"# {issue.journal_name} — {issue.issue_label}")
    out.append("")
    out.append(f"- **학회지**: {issue.journal_name}")
    out.append(f"- **호**: {issue.issue_label} (`{issue.issue_id}`)")
    out.append(f"- **출처**: {issue.source_url}")
    out.append(f"- **수집일**: {date.today().isoformat()}")
    out.append(f"- **논문 수**: {len(issue.articles)}")
    out.append("")
    out.append("---")
    out.append("")
    for i, art in enumerate(issue.articles, start=1):
        out.append(_format_article(i, art))
        out.append("---")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def issue_to_dict(issue: Issue) -> dict:
    """Serialise an :class:`Issue` to a JSON-friendly dict (lossless).

    Used as the canonical data source for the static site, which avoids the
    lossy Markdown round-trip (e.g. "Last, First" author names contain commas).
    """
    data = asdict(issue)
    data["collected"] = date.today().isoformat()
    data.pop("sort_key", None)  # tuple; ordering is re-derived from issue_id
    return data


def write_issue(issue: Issue, output_dir: str | Path, logger: logging.Logger) -> Path:
    """Render ``issue`` and write the Markdown digest plus a JSON sidecar.

    Writes ``output/<key>_<issue_id>.md`` (human-readable) and
    ``output/<key>_<issue_id>.json`` (structured, the site's data source);
    returns the Markdown path.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_id = issue.issue_id.replace("/", "-").replace(" ", "")
    stem = f"{issue.journal_key}_{safe_id}"

    path = output_dir / f"{stem}.md"
    path.write_text(render_issue_markdown(issue), encoding="utf-8")

    json_path = output_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps(issue_to_dict(issue), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    logger.info("Wrote %s and %s (%d articles)", path, json_path, len(issue.articles))
    return path
