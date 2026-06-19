# archival-journal-collector

두 기록학 학회지의 **공개 페이지를 긁어, 새 호(issue)가 나왔을 때만** 각 논문의
제목·저자·페이지·DOI·초록·원문링크를 **마크다운으로 정리**하는 도구입니다.
AI 요약은 하지 않으며, 초록은 원문 그대로 옮깁니다.

| 키 | 학회지 | 페이지 | 호 체계 |
|----|--------|--------|---------|
| `jksarm` | 한국기록관리학회지 | <https://accesson.kr/jksarm/> | 권–호 (예: 제27권 제2호) |
| `kjas` | 기록학연구 | <https://www.koreascience.kr/journal/HGKRBZ.page?lang=ko> | 통권 호수 (예: 통권 제83호) |

- 서버 렌더링 페이지라 브라우저 없이 `requests` + `beautifulsoup4` + `lxml`만 사용합니다.
- `jksarm`은 메인 페이지에 최신호가 초록까지 렌더링되어 한 번의 fetch로 처리합니다.
- `kjas`는 최신호 목차를 읽은 뒤, 각 논문 상세 페이지의 표준 `<meta name="citation_*">`
  태그(Highwire/Google Scholar 규약)에서 정보를 추출합니다.

## 구조

```
watcher.py            진입점 (argparse: --once / --journal / --interval)
sources/
  base.py             HTTP 세션·재시도·지연, 데이터 모델(Article/Issue), ExtractionError
  jksarm.py           한국기록관리학회지 파서 (셀렉터 상수 SEL_*/RE_*)
  kjas.py             기록학연구 파서 (citation_* meta + 목록 셀렉터)
render.py             Issue -> 마크다운(.md) + 구조화 JSON(.json) 저장
build_site.py         output/ 데이터 -> 단일 페이지 정적 사이트(site/index.html)
backfill.py           과거 호 채우기 (각 학회지 최근 N개 호 수집)
summarize.py          관심 논문 선택 -> PDF 다운로드 -> 추출 요약 -> 저장
summarizer.py         무료·로컬 추출 요약기 (TextRank, 생성형 아님)
state.json            학회지별 마지막 처리 호 기록
output/               생성된 마크다운·JSON (예: kjas_88.md/.json, jksarm_26-2.md/.json)
output/summaries/     논문별 추출 요약 JSON (DOI 기준)
site/                 생성된 정적 웹페이지 (index.html, 서버 불필요)
logs/                 watcher.log
tests/                네트워크 없이 픽스처로 파서/상태/렌더 검증
requirements.txt
.github/workflows/collect.yml   GitHub Actions 자동화 옵션
```

## 웹페이지(프론트)

수집한 데이터를 검색·필터가 되는 **단일 HTML 페이지**로 봅니다. 외부 라이브러리·
서버 없이 동작하는 자체 완결형 파일이라 그냥 브라우저로 열면 됩니다.

```bash
python build_site.py        # output/ 의 .json(없으면 .md)을 읽어 site/index.html 생성
# 브라우저로 site/index.html 열기
```

- `watcher.py` 실행이 끝날 때마다 `site/index.html`이 자동으로 다시 생성됩니다.
- 두 학회지의 모든 호·논문을 한 페이지에 모으고, **학회지 필터**와
  **제목·저자·초록 검색**을 제공합니다.
- 데이터 원본은 `render.py`가 쓰는 `output/<키>_<호>.json`(구조화 데이터)이며,
  사람이 읽는 `.md`와 함께 저장됩니다.

> 웹에 공개(URL)하고 싶으면 나중에 저장소 **Settings → Pages**에서
> "Deploy from a branch"의 폴더를 `/site`로 지정하면 됩니다. 코드 수정은 필요 없습니다.

## 관심 논문 요약 (무료·로컬, API 비용 없음)

수집된 목록에서 관심 있는 논문을 골라 **원문 PDF를 내려받아 핵심 문장을 뽑아**
요약합니다. 유료 LLM API나 모델 다운로드 없이, 로컬에서 도는 **추출 요약(TextRank)**
이라 비용이 발생하지 않습니다. 생성형이 아니라 **원문 문장을 그대로 발췌**합니다.

```bash
python summarize.py --list                       # 논문에 번호를 매겨 출력
python summarize.py --num 2 5                     # 목록 2, 5번 논문 요약
python summarize.py 10.20923/kjas.2026.88.005     # DOI로 직접 지정
python summarize.py --num 3 --sentences 8         # 더 긴 요약(문장 수 지정)
python summarize.py --num 3 --force               # 기존 요약 덮어쓰기
```

- 요약은 `output/summaries/<DOI>.json`에 저장되고, 사이트를 다시 빌드하면 해당 논문
  카드에 **📝 요약** 블록으로 표시됩니다(검색에도 포함).
- PDF는 정중하게(User-Agent·지연·재시도) 받아오며, PDF가 공개돼 있지 않은 논문은
  명확한 메시지와 함께 건너뜁니다.
- 추출 요약 특성상 표·머리글 같은 잡음은 정제하지만, 발췌 문장은 원문 그대로라
  완벽한 문장이 아닐 수 있습니다(생성형 요약과 다름).

> 참고: 본 도구의 기본 수집 단계는 여전히 **초록을 원문 그대로** 옮기며 AI 생성
> 요약을 하지 않습니다. 위 요약 기능은 사용자가 직접 고른 논문에 한해, 별도 명령으로
> 비용 없이 핵심 문장을 발췌해 주는 보조 기능입니다.

## 설치

```bash
git clone <repo-url>
cd archival-journal-collector
python3 -m venv .venv && source .venv/bin/activate   # 윈도우: .venv\Scripts\activate
pip install -r requirements.txt
```

Python 3.10+ 권장.

## 사용법

```bash
# 두 학회지 모두 한 번 확인하고 종료
python watcher.py --once

# 특정 학회지만
python watcher.py --once --journal jksarm
python watcher.py --once --journal kjas

# 상시 감시 (기본 6시간 간격 루프)
python watcher.py
python watcher.py --interval 3600        # 1시간 간격
```

동작:

1. `state.json`에서 학회지별 마지막 처리 호를 읽습니다.
2. 최신호를 파싱해 **기록된 호보다 새 호일 때만** 처리합니다.
   - `jksarm`: (권, 호) 튜플 비교 — 예: (27,2) > (27,1)
   - `kjas`: (통권) 정수 비교 — 예: (84,) > (83,)
3. 새 호면 `output/<키>_<호>.md`를 쓰고 `state.json`을 갱신합니다.
   **신규가 없으면 파일을 만들지 않고 로그만 남깁니다.**
4. 모든 요청은 User-Agent를 설정하고 요청 사이 약 2.5초 대기합니다.
   추출 실패 시 어떤 셀렉터에서 실패했는지 명확히 로깅합니다(`logs/watcher.log`).

### 첫 실행 동작
`state.json`이 비어 있으면(최초) 현재 최신호 1개를 "신규"로 보고 처리한 뒤
기록합니다. 이후에는 더 새로운 호가 나올 때만 처리합니다.

## 테스트 (네트워크 불필요)

저장된 HTML 픽스처로 파서·상태 비교·렌더링을 검증합니다.

```bash
python tests/test_offline.py
# 또는
pip install pytest && python -m pytest -q
```

## 자동 실행 등록

### macOS / Linux — cron

`crontab -e` 후 추가 (가상환경 경로는 본인 환경에 맞게 수정):

```cron
# 매일 09:00(KST)에 한 번 확인
0 9 * * * cd /path/to/archival-journal-collector && /path/to/.venv/bin/python watcher.py --once >> logs/cron.log 2>&1
```

### macOS — launchd (대안)
`~/Library/LaunchAgents/com.user.journalcollector.plist`를 만들고 `launchctl load`로
등록할 수도 있습니다. 간단히 하려면 위 cron 방식을 권장합니다.

### Windows — 작업 스케줄러(Task Scheduler)

PowerShell로 등록:

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\path\to\.venv\Scripts\python.exe" `
            -Argument "watcher.py --once" -WorkingDirectory "C:\path\to\archival-journal-collector"
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
Register-ScheduledTask -TaskName "ArchivalJournalCollector" -Action $action -Trigger $trigger
```

또는 작업 스케줄러 GUI에서:
1. "기본 작업 만들기" → 트리거: 매일 09:00
2. 동작: 프로그램 시작 →
   - 프로그램: `C:\path\to\.venv\Scripts\python.exe`
   - 인수: `watcher.py --once`
   - 시작 위치: 프로젝트 폴더 경로

### GitHub Actions (자동화 옵션)

`.github/workflows/collect.yml`이 포함되어 있습니다. 매일 한 번 `--once`를 실행하고
**새 호가 생기면 `output/`와 `state.json`을 레포에 커밋·푸시**합니다.
Actions 탭의 *Run workflow*로 수동 실행도 가능합니다. 일정은 `cron:` 값을 수정하세요
(GitHub Actions는 UTC 기준).

## 셀렉터 유지보수

사이트 HTML이 바뀌면 추출이 실패할 수 있습니다. 셀렉터는 각 소스 모듈 상단의
상수에 모여 있습니다:

- `sources/jksarm.py`: `SEL_ARTICLE_ITEMS`, `SEL_TITLE`, `SEL_AUTHORS`,
  `SEL_PAGES`, `SEL_ABSTRACT`, `SEL_DOI`, `SEL_ISSUE_HEADER`, `RE_*`
- `sources/kjas.py`: `SEL_LATEST_ISSUE_BLOCK`, `SEL_ARTICLE_LINKS`,
  `SEL_ISSUE_HEADER`, `META_*`(Highwire 표준 — 안정적), `SEL_ABSTRACT_BODY`

각 항목은 여러 후보 셀렉터를 순서대로 시도하며, 모두 실패하면
`logs/watcher.log`에 어떤 셀렉터를 시도했는지 남기고 `ExtractionError`를 발생시킵니다.

> **참고**: 본 저장소의 셀렉터는 각 플랫폼의 일반적 구조와 표준 메타태그 규약을
> 토대로 작성되었습니다. 두 호스트에 대한 네트워크 접근이 가능한 환경에서 첫
> 라이브 실행으로 각 필드가 채워지는지 확인하고, 필요하면 위 상수만 조정하세요.
> (`kjas`의 `citation_*` 경로는 플랫폼 표준이라 변동 가능성이 낮습니다.)
