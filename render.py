"""Render an :class:`Issue` to Markdown and write it under ``output/``.

No AI summarisation is performed — the abstract is reproduced verbatim from the
source page, per spec.
"""

from __future__ import annotations

import logging
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


def write_issue(issue: Issue, output_dir: str | Path, logger: logging.Logger) -> Path:
    """Render ``issue`` and write ``output/<key>_<issue_id>.md``; return the path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_id = issue.issue_id.replace("/", "-").replace(" ", "")
    path = output_dir / f"{issue.journal_key}_{safe_id}.md"
    path.write_text(render_issue_markdown(issue), encoding="utf-8")
    logger.info("Wrote %s (%d articles)", path, len(issue.articles))
    return path
