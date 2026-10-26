# 명령어 모음 (CHEATSHEET)

> 다 **터미널**에서, **프로젝트 폴더 안**에서 실행합니다.
> 코드 블록을 복사할 때 **`#` 뒤 설명은 빼고** 명령만 붙여넣으세요(zsh 에러 방지).

---

## 0) 매번 시작할 때 (3줄)

```
cd ~/archival-journal-collector
source .venv/bin/activate
git pull --no-rebase --no-edit
```
- 폴더로 이동 → 파이썬 환경 켜기 → 최신 코드 받기.
- `(.venv)` 가 줄 앞에 보이면 환경이 켜진 상태입니다.

---

## 1) 새 호 나왔는지 확인·수집

```
python3 watcher.py --once
```
- 5개 학회지(국내 2 + 해외 3)를 확인해 새 호만 수집하고, 사이트를 자동 갱신.
- 특정 학회지만: `python3 watcher.py --once --journal kjas`

## 2) 결과 보기

```
open "$PWD/site/index.html"
```
- 브라우저에 페이지로 뜸. (GitHub 웹에서 열면 코드로 보이니 이 명령으로 여세요.)

## 3) 저장하기 (변경사항을 GitHub에)

```
git add -A
git commit -m "업데이트"
git push
```
- `push`가 거부되면 → `git pull --no-rebase --no-edit` 먼저 한 뒤 다시 `git push`.

---

## 4) 관심 논문 요약

먼저 번호 확인:
```
python3 summarize.py --list
```

방식 택1 (7번 논문 예시):
```
python3 summarize.py --num 7            # 추출 요약 (설치 0, 즉시, 무료)
./summarize_local.sh --num 7            # 생성형 요약 (Ollama, 더 자연스러움)
python3 app.py                          # 버튼 콘솔 (브라우저 localhost:8000)
```
- 여러 편: `--num 2 5 7`
- 생성형은 `ollama serve` 가 떠 있어야 함(아래 8번).

**내가 가진 PDF로 요약** (페이월 해외 논문 등 — 본문이 막혀 다운로드가 안 될 때):
```
python3 summarize.py --num 1 --pdf ~/Downloads/논문.pdf                 # 추출(무료·즉시)
python3 summarize.py --num 1 --pdf ~/Downloads/논문.pdf --engine ollama # 생성형(Ollama)
```
- 목록 번호(`--num`)는 **그 PDF가 어느 논문인지** 알려주는 용도(한 편만 지정).
- 소장한 PDF를 직접 읽어 요약하므로 다운로드 차단·페이월과 무관합니다.
- 영어 논문도 요약됩니다(국문/영문 자동 판별).

## 5) 해외 논문 한국어 번역

```
ollama serve
python3 translate.py --titles-only --model qwen2.5:3b   # 제목만, 빠름(추천)
python3 translate.py                                    # 제목+초록 전부(느림)
```
- 제목은 국문+영문 부제로, 초록은 국문+영문 토글로 표시.
- 느리면: `--titles-only`(초록 제외) 또는 `--model qwen2.5:3b`(가벼운 모델).
- 한 저널만: `--journal archrec`

## 6) 초록 보강 (해외 논문 초록 채우기)

```
python3 enrich_abstracts.py
```
- OpenAlex·Semantic Scholar(무료 API)로 빈 초록을 채움. 막힌 곳은 건너뜀.

## 7) 과거 호 채우기

```
python3 backfill.py --journal jksarm --count 10     # 국내
python3 backfill.py --journal amarch --count 5      # 해외(amarch/archivaria/archrec)
```
- 이미 있는 호는 건너뜀(멱등적). `--count` 로 호 수 조절.

## 8) Ollama (생성형 요약·번역에 필요, 무료·로컬)

```
ollama serve                 # 서버 실행 (맥 앱 설치 시 보통 자동)
ollama pull qwen2.5:7b       # 모델 받기 (처음 한 번)
ollama pull qwen2.5:3b       # 더 가벼운 모델(빠름)
```

## 9) 기록학연구 제목이 영어로 나올 때

```
python3 fix_kjas_titles.py
```
- 영문으로 남은 kjas 제목을 국문으로 교체(멱등적, 여러 번 안전).

## 10) 주제어(키워드) 달기 — 논문별 태그·필터

**추천: 논문별 주제어 뽑기** (각 논문을 LLM이 읽고 그 논문만의 키워드 3~5개):
```
ollama serve
python3 keywords.py
```
- 각 논문에 **구체적인 주제어(🏷)** 가 붙고, 사이트 위쪽에 **주제 필터**(자주 나오는 키워드)가 생김.
- 멱등적(캐시). 한 학회지만: `--journal kjas`, 다시 뽑기: `--force`, 개수: `--max-keywords 4`

**대안: 비슷한 주제로 묶기**(군집 — 포괄적 라벨):
```
ollama serve
ollama pull bge-m3                 # 다국어 임베딩 모델(무료), 처음 한 번
python3 cluster.py --topics 15
```
- 논문을 묶어 묶음에 이름 하나를 붙임. 라벨이 포괄적이라 보통 `keywords.py`가 더 나음.

---

## 문제 해결

| 증상 | 해결 |
|------|------|
| `push` 거부 `(fetch first)` | `git pull --no-rebase --no-edit` 후 다시 `git push`. site 충돌 시 `python3 build_site.py` 로 다시 만들고 add/commit/push. |
| `zsh: unknown file attribute` / `invalid int value: '#'` | 명령 뒤 `# 설명`까지 붙여넣어서 그럼. **`#` 앞 명령만** 입력. |
| `open … no application` | 절대경로로: `open "$PWD/site/index.html"` |
| `Ollama 서버에 연결할 수 없습니다` | `ollama serve` 실행 후 다시. |
| `모델 … 설치돼 있지 않습니다` | `ollama pull qwen2.5:7b` (또는 해당 모델). |
| 번역에 중국어 섞임 | 자동 재시도됨. 계속이면 `--model qwen2.5:14b` (RAM 여유 시). |
| clone 인증 실패 | `gh auth login` (계정 aaaooo3) 후 `gh repo clone aaaooo3/archival-journal-collector`. |

---

## 가장 자주 쓰는 흐름 (요약)

```
cd ~/archival-journal-collector && source .venv/bin/activate && git pull --no-rebase --no-edit
python3 watcher.py --once
open "$PWD/site/index.html"
git add -A && git commit -m "업데이트" && git push
```
