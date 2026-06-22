#!/usr/bin/env python3
"""수집 논문을 비슷한 주제로 묶기 — 무료·로컬(Ollama 다국어 임베딩).

각 논문(제목+초록)을 Ollama 임베딩 모델(기본 ``bge-m3``, 다국어)로 벡터화한 뒤
k-means로 군집화하고, 각 군집에 한국어 주제 라벨을 붙여 ``articles[].topic`` 에
저장한다. 다국어 임베딩이라 국문(국내)·영문(해외) 논문이 같은 주제로 묶인다.

사용:
    ollama serve
    ollama pull bge-m3                 # 임베딩 모델(다국어, 무료) — 처음 한 번
    python3 cluster.py                 # 주제 개수 자동
    python3 cluster.py --topics 8      # 주제 8개로 고정
    python3 cluster.py --label-model qwen2.5:7b   # 주제 이름 짓는 모델(있으면)

라벨은 ``--label-model`` 의 LLM으로 한국어 이름을 붙이며, 없거나 실패하면 키워드로
대체한다. 임베딩은 생성이 아니라 벡터화라 번역보다 훨씬 빠르다.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import math
import random
import re

import requests

import build_site
from summarize import DEFAULT_OLLAMA_HOST, ollama_available, ollama_generate
from summarizer import _tokens  # Korean/English content-word tokenizer
from tooling import OUTPUT_DIR, SITE_DIR, setup_logging

EMBED_MODEL = "bge-m3"          # 다국어 임베딩(무료). 대안: nomic-embed-text(영어 위주)
LABEL_MODEL = "qwen2.5:7b"
EMBED_TIMEOUT = 60

_LABEL_SYSTEM = ("당신은 기록학·아카이브 분야 논문 묶음에 '구체적인' 소주제 이름을 붙이는 "
                 "도우미입니다. 한국어로만, 설명 없이 라벨만 답하세요.")
_LABEL_PROMPT = (
    "다음은 한 군집에 속한 학술 논문 제목들입니다. 이 묶음을 다른 묶음과 구별되게 하는 "
    "'구체적인' 공통 소주제를 한국어 2~6단어 라벨로 지으세요.\n"
    "주의: '기록관리', '아카이브', '기록학', '연구', '관리 방안'처럼 분야 전체를 뜻하는 "
    "포괄적 단어만으로 답하지 마세요. 논문들의 실제 대상·방법·영역(예: 전자기록 평가, "
    "구술사 수집, 공동체 아카이브, 디지털 보존, 기록물 분류체계 등)을 드러내세요.\n"
    "라벨만 출력(따옴표·설명·문장 금지):\n\n{titles}"
)


# --- Ollama embeddings ------------------------------------------------------


def embed(host: str, model: str, text: str) -> list[float]:
    resp = requests.post(f"{host.rstrip('/')}/api/embeddings",
                         json={"model": model, "prompt": text}, timeout=EMBED_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("embedding") or []


def _model_installed(host: str, model: str) -> bool:
    try:
        names = [m.get("name", "") for m in
                 requests.get(f"{host.rstrip('/')}/api/tags", timeout=5).json().get("models", [])]
    except Exception:  # noqa: BLE001
        return True
    return model in names or f"{model}:latest" in names or any(n.startswith(model + ":") for n in names)


# --- k-means (pure Python; vectors are L2-normalised so dot == cosine) -------


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def kmeans(vectors: list[list[float]], k: int, iters: int = 40, seed: int = 0):
    """반환: (각 점의 군집번호, 군집 중심 벡터들)."""
    n = len(vectors)
    random.seed(seed)
    centroids = [vectors[i][:] for i in random.sample(range(n), k)]
    assign = [0] * n
    for _ in range(iters):
        changed = False
        for i, v in enumerate(vectors):
            best = max(range(k), key=lambda c: _dot(v, centroids[c]))
            if best != assign[i]:
                assign[i] = best
                changed = True
        for c in range(k):
            members = [vectors[i] for i in range(n) if assign[i] == c]
            if members:
                dim = len(members[0])
                centroids[c] = _normalize([sum(m[d] for m in members) / len(members) for d in range(dim)])
        if not changed:
            break
    return assign, centroids


def _topics_for(vec, centroids, labels: dict[int, str], ratio: float, max_topics: int) -> list[str]:
    """주 주제 + 유사도가 주 주제의 ``ratio`` 이상인 군집들(최대 ``max_topics``개)."""
    sims = sorted(((c, _dot(vec, centroids[c])) for c in labels), key=lambda x: x[1], reverse=True)
    if not sims:
        return []
    primary = sims[0][1]
    chosen: list[str] = []
    for c, sim in sims:
        if len(chosen) >= max_topics:
            break
        if not chosen or sim >= primary * ratio:
            lab = labels[c]
            if lab not in chosen:
                chosen.append(lab)
    return chosen


# --- data ------------------------------------------------------------------


def _article_text(art: dict) -> str:
    parts = [art.get("title_ko") or art.get("title", ""),
             (art.get("abstract_ko") or art.get("abstract", ""))[:1200]]
    return " ".join(p for p in parts if p).strip()


def _keyword_label(titles: list[str]) -> str:
    freq: dict[str, int] = {}
    for t in titles:
        for w in set(_tokens(t)):
            freq[w] = freq.get(w, 0) + 1
    top = sorted(freq, key=lambda w: freq[w], reverse=True)[:2]
    return " · ".join(top) if top else "기타"


def _llm_label(host: str, model: str, titles: list[str], logger: logging.Logger) -> str:
    prompt = _LABEL_PROMPT.format(titles="\n".join(f"- {t}" for t in titles[:12]))
    try:
        out = ollama_generate(host, model, prompt, system=_LABEL_SYSTEM, num_predict=30).strip()
    except Exception:  # noqa: BLE001
        return ""
    out = re.sub(r'^["\'\s]+|["\'\s]+$', "", out.splitlines()[0] if out else "")
    return out[:40]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--topics", type=int, default=0, help="주제(군집) 개수 (0=자동)")
    p.add_argument("--model", default=EMBED_MODEL, help=f"임베딩 모델 (기본 {EMBED_MODEL})")
    p.add_argument("--label-model", default=LABEL_MODEL, help=f"주제 이름 짓는 LLM (기본 {LABEL_MODEL})")
    p.add_argument("--ollama-host", default=DEFAULT_OLLAMA_HOST)
    p.add_argument("--multi-ratio", type=float, default=0.90,
                   help="보조 주제 추가 기준(주 주제 유사도 대비 비율; 낮출수록 한 논문에 주제가 더 많이 붙음). 1.0이면 단일 주제")
    p.add_argument("--max-topics", type=int, default=3, help="논문당 최대 주제 수 (기본 3)")
    args = p.parse_args(argv)
    logger = setup_logging("cluster", logfile=False, simple=True)

    if not ollama_available(args.ollama_host, logger):
        logger.error("Ollama 서버에 연결할 수 없습니다 (%s). 'ollama serve' 후 다시.", args.ollama_host)
        return 2
    if not _model_installed(args.ollama_host, args.model):
        logger.error("임베딩 모델 '%s' 이(가) 없습니다. 먼저:  ollama pull %s", args.model, args.model)
        return 3

    # 1) 논문 모으기 (파일·인덱스 참조 보존)
    refs: list[tuple[str, int]] = []
    texts: list[str] = []
    titles: list[str] = []
    files = {f: json.loads(open(f, encoding="utf-8").read()) for f in sorted(glob.glob(str(OUTPUT_DIR / "*.json")))}
    for path, data in files.items():
        for idx, art in enumerate(data.get("articles", [])):
            txt = _article_text(art)
            if txt:
                refs.append((path, idx))
                texts.append(txt)
                titles.append(art.get("title_ko") or art.get("title", ""))
    if len(texts) < 4:
        logger.error("군집화할 논문이 너무 적습니다 (%d편).", len(texts))
        return 1

    # 2) 임베딩 (캐시 사용 — --topics 만 바꿔 재실행하면 다시 임베딩 안 함)
    cache_path = OUTPUT_DIR / ".embed_cache.json"
    cache: dict[str, list[float]] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}

    def _key(txt: str) -> str:
        return f"{args.model}:" + hashlib.sha1(txt.encode("utf-8")).hexdigest()

    logger.info("임베딩 중… (%d편, 모델 %s)", len(texts), args.model)
    vectors: list[list[float]] = []
    embedded = 0
    for txt in texts:
        k = _key(txt)
        if k in cache:
            vectors.append(cache[k])
            continue
        try:
            v = _normalize(embed(args.ollama_host, args.model, txt))
        except Exception as exc:  # noqa: BLE001
            logger.error("임베딩 실패 (%s) — 중단", type(exc).__name__)
            return 4
        cache[k] = v
        vectors.append(v)
        embedded += 1
        if embedded % 25 == 0:
            logger.info("  %d개 임베딩…", embedded)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    logger.info("  (신규 임베딩 %d편, 캐시 재사용 %d편)", embedded, len(texts) - embedded)

    # 3) 군집화
    k = args.topics or max(4, min(12, round(math.sqrt(len(texts) / 2))))
    logger.info("군집화: 주제 %d개", k)
    assign, centroids = kmeans(vectors, k)

    # 4) 주제 라벨 (라벨에 구분자 '|' 가 들어가지 않게 정리)
    use_llm = _model_installed(args.ollama_host, args.label_model)
    labels: dict[int, str] = {}
    for c in range(k):
        c_titles = [titles[i] for i in range(len(titles)) if assign[i] == c]
        if not c_titles:
            continue
        label = _llm_label(args.ollama_host, args.label_model, c_titles, logger) if use_llm else ""
        labels[c] = (label or _keyword_label(c_titles)).replace("|", "/")
        logger.info("  [주제] %s  (%d편)", labels[c], len(c_titles))

    # 5) 각 논문에 주제(들) 저장 — 여러 군집에 가까우면 보조 주제도 함께. 사이트 재생성.
    multi = 0
    for ref_i, (path, idx) in enumerate(refs):
        tops = _topics_for(vectors[ref_i], centroids, labels, args.multi_ratio, args.max_topics) or ["기타"]
        if len(tops) > 1:
            multi += 1
        art = files[path]["articles"][idx]
        art["topic"] = tops[0]      # 주 주제(하위호환)
        art["topics"] = tops        # 전체 주제 목록
    for path, data in files.items():
        open(path, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    logger.info("주제 %d개로 묶음 완료. (여러 주제 배정 %d편)", len(labels), multi)
    build_site.build_site(OUTPUT_DIR, SITE_DIR, logger)
    logger.info("사이트 재생성 완료. (git add -A && git commit && git push 로 저장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
