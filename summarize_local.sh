#!/usr/bin/env bash
# 로컬(내 PC)에서 생성형 장별 요약을 쉽게 실행하는 헬퍼 (macOS/Linux).
#
# 하는 일: Ollama 서버가 떠 있는지 확인(없으면 기동) → 모델이 없으면 받기 →
#          python summarize.py --engine ollama 실행.
#
# 사용법:
#   ./summarize_local.sh --list           # 논문 번호 보기
#   ./summarize_local.sh --num 2 5        # 2,5번 논문 생성형 요약
#   OLLAMA_MODEL=qwen2.5:3b ./summarize_local.sh --num 3   # 가벼운 모델로
#
# summarize.py 에 넘길 인자는 그대로 뒤에 붙이면 됩니다.
set -euo pipefail

MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
HOST="${OLLAMA_HOST:-http://localhost:11434}"
cd "$(dirname "$0")"

if ! command -v ollama >/dev/null 2>&1; then
  echo "❌ Ollama가 설치돼 있지 않습니다. https://ollama.com 에서 설치하세요." >&2
  exit 1
fi

# 서버가 응답하지 않으면 백그라운드로 기동.
if ! curl -sf "$HOST/api/tags" >/dev/null 2>&1; then
  echo "▶ Ollama 서버 기동 중..."
  ollama serve >/tmp/ollama_serve.log 2>&1 &
  for _ in $(seq 1 30); do
    curl -sf "$HOST/api/tags" >/dev/null 2>&1 && break
    sleep 1
  done
fi

# 모델이 없으면 받기 (수 GB, 처음 한 번).
if ! ollama list 2>/dev/null | grep -q "${MODEL%%:*}"; then
  echo "▶ 모델 다운로드: $MODEL (처음 한 번, 시간이 걸립니다)"
  ollama pull "$MODEL"
fi

echo "▶ 생성형 장별 요약 실행 (모델 $MODEL)"
python3 summarize.py --engine ollama --model "$MODEL" "$@"
