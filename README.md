# archival-journal-collector

두 기록학 학회지(**한국기록관리학회지**, **기록학연구**)의 공개 페이지에서 새 호가
나오면 논문 목록(제목·저자·페이지·DOI·초록·원문링크)을 모아 **검색되는 웹페이지**로
정리하고, 원하는 논문은 **무료·로컬로 요약**까지 해주는 개인용 도구입니다.

| 키 | 학회지 | 수집 방식 |
|----|--------|-----------|
| `jksarm` | 한국기록관리학회지 | 사이트 스크레이핑 (초록·PDF 공개) |
| `kjas` | 기록학연구 | 사이트 스크레이핑 (초록·PDF 공개) |
| `amarch` | The American Archivist | Crossref API |
| `archivaria` | Archivaria | Crossref API |
| `archrec` | Archives and Records | Crossref API |

> 해외 저널은 **Crossref(무료 공개 API)** 로 메타데이터(제목·저자·DOI·호·발행일·링크)를
> 가져옵니다. 다만 페이월 저널이 많아 **초록이 비어 있거나 PDF 요약이 안 될 수 있습니다**
> (메타데이터는 안정적). 다른 저널을 추가하려면 `sources/__init__.py` 의 `_CROSSREF_SOURCES`
> 에 `CrossrefSource("키", "이름", "ISSN")` 한 줄만 더하면 됩니다.

---

## ⚡ 빠른 시작 (자주 쓰는 명령)

> 터미널에서 프로젝트 폴더 안에서 실행합니다. **`#` 뒤 설명은 빼고** 명령만 입력하세요
> (zsh에서는 설명까지 붙여넣으면 에러납니다).

```bash
python3 watcher.py --once            # 새 호 나왔는지 확인·수집
python3 summarize.py --list          # 논문에 번호 매겨 보기
python3 app.py                       # 버튼으로 요약하는 웹콘솔 (localhost:8000)
open site/index.html                 # 결과 페이지를 브라우저로 열기 (macOS)
```

요약만 빠르게 (터미널):
```bash
./summarize_local.sh --num 7         # 7번 논문 생성형 요약(Ollama, 알아서 준비)
python3 summarize.py --num 7         # 7번 논문 추출 요약(설치 0, 즉시)
```

---

## 처음 한 번만 (설치)

```bash
# 1) 코드 받기 — GitHub 로그인은 gh CLI가 가장 쉬움
brew install gh && gh auth login     # 브라우저로 로그인 (계정: aaaooo3)
gh repo clone aaaooo3/archival-journal-collector
cd archival-journal-collector

# 2) 파이썬 환경
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
- Python 3.10+ 권장. 이후엔 새 터미널마다 `source .venv/bin/activate` 한 번만.
- 생성형 요약을 쓰려면 [Ollama](https://ollama.com)도 설치 (아래 "요약" 참고).

---

## 평소 사용법

### 1. 새 호 수집
```bash
python3 watcher.py --once                  # 두 학회지 모두
python3 watcher.py --once --journal kjas   # 한 곳만
```
- 새 호가 있을 때만 `output/`에 저장하고, 끝나면 `site/index.html`을 자동 갱신합니다.
- 과거 호도 채우려면: `python3 backfill.py --count 10` (각 학회지 최근 10호).

### 2. 관심 논문 요약 (무료·로컬, 비용 0)
논문을 **장(서론·방법·결과·결론…)별로** 요약합니다. 두 가지 방식:

| 방식 | 명령 | 특징 |
|------|------|------|
| **추출** | `python3 summarize.py --num 7` | 설치 0·즉시. 원문 핵심 문장 발췌 |
| **생성형** | `./summarize_local.sh --num 7` | 더 자연스러움. Ollama(로컬 LLM) 필요 |

번호는 `python3 summarize.py --list`로 확인합니다. 여러 편은 `--num 2 5 7`.

**생성형(Ollama) 준비** — 한 번만:
```bash
ollama serve                 # 서버 실행(맥 앱 설치 시 자동으로 떠 있음)
ollama pull qwen2.5:7b       # 한국어 모델 받기(수 GB, 처음 한 번)
```
그 뒤 `./summarize_local.sh --num 7` 이 알아서 서버·모델을 확인하고 요약합니다.
직접 실행도 가능: `python3 summarize.py --engine ollama --num 7`.

### 3. 버튼으로 요약 (웹 콘솔)
```bash
python3 app.py               # → http://localhost:8000
```
브라우저에서 논문 목록의 **[요약]** 버튼 클릭 → 그 자리에서 요약, **[게시]** 버튼으로
GitHub에 push. 콘솔은 *내 PC에서 켜둔 동안만* 동작합니다(요약은 로컬에서 처리).

### 4. 결과 보기
- **내 Mac에서**: `open site/index.html` (또는 Finder에서 더블클릭) → 페이지로 뜸.
  - ⚠️ GitHub 웹에서 이 파일을 클릭하면 *코드*로 보입니다(렌더링 안 됨). 파일을
    직접 열거나 아래 Pages를 쓰세요.
- **어디서나(폰 등)**: GitHub Pages로 게시 → 공개 URL.
  - 최초 1회: 저장소 **Settings → Pages → Source = "GitHub Actions"**.
  - 이후 `git push`(또는 콘솔 [게시]) 하면 자동 배포되어
    `https://aaaooo3.github.io/archival-journal-collector/` 에서 열람.
  - ⚠️ Pages는 **공개**됩니다(URL 알면 누구나). 비공개로 두려면 게시하지 말고 로컬 파일로만 보기.

### 5. 변경사항 저장(push)
```bash
git pull --no-rebase --no-edit       # 항상 push 전에 먼저! (충돌 예방)
git add -A && git commit -m "요약/수집 갱신"
git push
```

---

## 문제 해결 (자주 막히는 것)

| 증상 | 원인 / 해결 |
|------|------------|
| `Password authentication is not supported` (clone 실패) | GitHub는 비번 로그인 폐지. `gh auth login` 으로 로그인하거나 개인 토큰(PAT) 사용. 사용자명은 `jin`이 아니라 GitHub 계정 `aaaooo3`. |
| `zsh: unknown file attribute` / `invalid int value: '#'` | 명령 뒤 `# 설명`까지 붙여넣어서 생긴 오류. **`#` 앞부분 명령만** 입력. |
| `! [rejected] ... (fetch first)` (push 거부) | 원격에 새 커밋이 있음. `git pull --no-rebase --no-edit` 후 다시 `git push`. `site/index.html` 충돌 시 `python3 build_site.py` 로 다시 만들고 `git add -A && git commit --no-edit && git push`. |
| `Ollama 서버에 연결할 수 없습니다` | 터미널에서 `ollama serve` 실행 후 다시 시도. 또는 `--engine extractive` 로 추출 요약. |
| 요약 일부가 **중국어/영어**로 나옴 | qwen 모델 특성. 프롬프트에서 한국어를 강제했지만 가끔 발생 — 해당 논문만 `--force`로 재요약하거나 `--model gemma2:9b` 등 다른 모델 시도. |
| `No module named flask` (app.py) | `pip install -r requirements.txt` (또는 `pip install flask`). |
| 사이트가 빈 화면 | 보통 브라우저가 아주 옛 버전이거나 파일이 깨진 경우. `python3 build_site.py`로 다시 생성 후 열기. |

---

## 파일 지도

```
watcher.py            진입점: 새 호 확인·수집 (--once / --journal / --interval)
backfill.py           과거 호 채우기 (--count N / --all)
summarize.py          논문 요약 CLI (--num / --engine extractive|ollama)
summarizer.py         요약 엔진: 추출(TextRank) + 장 분할
summarize_local.sh    생성형 요약 헬퍼(macOS/Linux): Ollama 준비 후 summarize 실행
app.py                로컬 요약 웹콘솔(Flask, 버튼 UI) — http://localhost:8000
render.py             Issue → 마크다운(.md) + 구조화 JSON(.json)
build_site.py         output/ → 검색되는 단일 페이지 site/index.html
sources/
  base.py             HTTP 세션·재시도·지연, 데이터 모델, ExtractionError
  jksarm.py           한국기록관리학회지 파서 (셀렉터 상수 SEL_*/RE_*)
  kjas.py             기록학연구 파서 (citation_* 메타 + 목록 셀렉터)
state.json            학회지별 마지막 처리 호 기록
output/               수집 데이터(.md/.json) + summaries/ (논문별 요약 JSON)
site/index.html       생성된 웹페이지 (서버 불필요, 자체 완결형)
tests/                네트워크 없이 동작하는 오프라인 테스트
.github/workflows/    collect.yml(자동 수집), pages.yml(Pages 배포)
```

---

## 더 알아두면 좋은 것

**테스트 (네트워크 불필요)**
```bash
python3 tests/test_offline.py        # 또는: pip install pytest && python -m pytest -q
```

**자동 수집 (선택)** — `.github/workflows/collect.yml` 이 매일 `--once`를 돌려 새 호가
생기면 `output/`·`site/`·`state.json`을 커밋·push 합니다. 일정은 `cron:` 값으로 조정
(UTC 기준). 로컬에서 정기 실행하려면 cron 등록:
```cron
0 9 * * * cd /path/to/archival-journal-collector && .venv/bin/python watcher.py --once >> logs/cron.log 2>&1
```

**사이트 HTML이 바뀌어 수집이 실패하면** — 셀렉터는 각 소스 모듈 상단 상수에 모여
있습니다. `sources/jksarm.py`(`SEL_*`/`RE_*`), `sources/kjas.py`(`SEL_*`, `META_*`).
여러 후보를 순서대로 시도하며, 모두 실패하면 `logs/watcher.log`에 어떤 셀렉터를
시도했는지 남기고 멈춥니다.

**요약 원칙** — 기본 수집은 초록을 원문 그대로 옮기고 AI 생성을 하지 않습니다.
요약 기능은 *사용자가 직접 고른 논문에 한해* 별도로 실행하는 보조 기능이며, 추출은
원문 발췌, 생성형은 로컬 LLM(Ollama)이라 **어느 쪽도 비용이 들지 않습니다**.
