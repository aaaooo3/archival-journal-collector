"""Offline validation of parsers, state logic and rendering.

These tests do NOT hit the network — they feed saved HTML fixtures through the
real parsing functions, monkeypatching ``fetch_html`` to return fixture text.
This verifies the selector/meta-tag extraction logic, the "is newer" state
comparison, and the Markdown renderer end-to-end.

Run:  python -m pytest -q     (or)     python tests/test_offline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import logging

from bs4 import BeautifulSoup

from sources import jksarm, kjas
from sources.base import make_session
from render import render_issue_markdown
import watcher

FIX = Path(__file__).resolve().parent / "fixtures"
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("test")


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_jksarm(monkeypatch):
    monkeypatch.setattr(jksarm, "fetch_html", lambda *a, **k: _read("jksarm_sample.html"))
    issue = jksarm.fetch_latest_issue(make_session(), log)
    assert issue.issue_id == "27-2"
    assert issue.sort_key == (27, 2)
    assert len(issue.articles) == 2
    a0 = issue.articles[0]
    assert a0.title == "기록관리 메타데이터 표준화 연구"
    assert a0.authors == ["홍길동", "김영희"]
    assert a0.pages == "1-25"
    assert a0.doi == "10.14404/JKSARM.2027.27.2.001"
    assert "메타데이터" in a0.abstract
    assert a0.url.endswith("/a01")
    md = render_issue_markdown(issue)
    assert "제27권 제2호" in md and "DOI" in md


def test_kjas(monkeypatch):
    details = {
        "https://www.koreascience.kr/article/JAKO2027000001.page": _read("kjas_detail_1.html"),
        "https://www.koreascience.kr/article/JAKO2027000002.page": _read("kjas_detail_2.html"),
    }

    def fake_fetch(session, url, logger, **kw):
        if url == kjas.URL:
            return _read("kjas_list_sample.html")
        return details[url]

    monkeypatch.setattr(kjas, "fetch_html", fake_fetch)
    issue = kjas.fetch_latest_issue(make_session(), log)
    assert issue.issue_id == "83"
    assert issue.sort_key == (83,)
    assert len(issue.articles) == 2
    a0 = issue.articles[0]
    assert a0.title == "공공기관 기록평가의 쟁점과 과제"
    assert a0.authors == ["박민수", "정수진"]
    assert a0.pages == "7-38"
    assert a0.doi == "10.20923/kjas.2027.83.007"
    assert "기록평가" in a0.abstract
    # second article uses the .abstract body fallback
    assert "디지털 아카이브" in issue.articles[1].abstract


def test_is_newer():
    assert watcher.is_newer(jksarm, (27, 2), None) is True
    assert watcher.is_newer(jksarm, (27, 2), "27-1") is True
    assert watcher.is_newer(jksarm, (27, 2), "27-2") is False
    assert watcher.is_newer(jksarm, (27, 1), "27-2") is False
    assert watcher.is_newer(kjas, (84,), "83") is True
    assert watcher.is_newer(kjas, (83,), "83") is False


# Allow plain `python tests/test_offline.py` without pytest installed.
if __name__ == "__main__":
    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

    test_jksarm(_MP())
    test_kjas(_MP())
    test_is_newer()
    print("\nALL OFFLINE TESTS PASSED")
