"""Free, local, extractive summariser (no paid API, no model download).

Implements TextRank over sentences: split text into sentences, weight words
with TF-IDF, score sentence similarity (cosine), run PageRank, and return the
top-ranked sentences in their original order. This is *extractive* — every
sentence in the summary is copied verbatim from the source, nothing generated.

Tuned lightly for Korean academic text (sentence splitting on "…다.", a small
particle stripper and stopword list), with English handled too.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# Common Korean function words / academic filler to ignore when scoring.
_STOPWORDS = set(
    "그리고 그러나 또한 따라서 그래서 하지만 또는 및 등 이 그 저 것 수 들 더 본 이를 "
    "통해 위해 대한 대해 관한 관련 있다 없다 있는 없는 한다 하는 하여 하고 되는 된다 되어 "
    "같은 경우 연구 결과 분석 제시 통한 위한 으로써 이러한 그러한 우리 매우 가장 또 즉 "
    "the a an of to in and or for on with as is are be this that by from at it its".split()
)

# Stripped from the end of a Korean token so inflected forms match (crude stemming).
_PARTICLES = sorted(
    ["으로서", "으로써", "에서는", "에게서", "으로", "에서", "에게", "까지", "부터", "이라",
     "라는", "와의", "과의", "에는", "에도", "이나", "거나", "을", "를", "이", "가", "은",
     "는", "에", "의", "로", "도", "만", "와", "과", "께", "라"],
    key=len, reverse=True,
)


def _stem(word: str) -> str:
    if re.fullmatch(r"[가-힣]+", word):
        for p in _PARTICLES:
            if word.endswith(p) and len(word) - len(p) >= 2:
                return word[: -len(p)]
    return word


def _tokens(sentence: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z]{2,}|[가-힣]{2,}", sentence.lower())
    out = []
    for w in raw:
        w = _stem(w)
        if len(w) >= 2 and w not in _STOPWORDS:
            out.append(w)
    return out


# Inline noise stripped from the running text.
_NOISE = [
    re.compile(r"https?://\S+"),
    re.compile(r"10\.\d{4,9}/\S+"),                 # DOI
    re.compile(r"[pe]?ISSN\s*[\d-]+", re.IGNORECASE),
]
# A normalised line (page numbers removed) repeated this many times is a
# running header/footer and is dropped.
_REPEAT_THRESHOLD = 3


def clean_text(text: str) -> str:
    """Strip references, repeated headers/footers and inline noise."""
    # Cut everything from the references section onward (kills citation noise).
    for marker in ("참고문헌", "References", "REFERENCES", "참 고 문 헌"):
        idx = text.find(marker)
        if idx > len(text) * 0.4:  # only if it's plausibly near the end
            text = text[:idx]
            break

    lines = [ln.strip() for ln in text.splitlines()]
    norm = lambda ln: re.sub(r"\d+", "", ln)  # ignore page numbers when matching
    freq = Counter(norm(ln) for ln in lines if len(ln) > 8)
    kept = [ln for ln in lines if ln and freq[norm(ln)] < _REPEAT_THRESHOLD]

    text = " ".join(kept)
    for pat in _NOISE:
        text = pat.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    """Split Korean/English text into sentences, dropping non-prose fragments."""
    rough = re.split(r"(?<=[.!?])\s+", text)
    sents: list[str] = []
    seen: set[str] = set()
    for s in rough:
        s = s.strip()
        # Strip a leading bracketed label ("[가설 2]", "[표 1]") and any leading
        # list/section numbering or stray table-stat run ("2) ", "4.1.4 ", "2.90 1.14 - ").
        s = re.sub(r"^\[[^\]]{0,15}\]\s*", "", s)
        s = re.sub(r"^[\s\d.)\]\-~·•%]+", "", s).strip()
        if len(s) < 25:
            continue
        letters = re.findall(r"[가-힣a-zA-Z]", s)
        if len(letters) < 15:
            continue
        # Skip tables / figures / table-of-contents style fragments.
        if re.match(r"^[<\[]?\s*(표|그림|Table|Figure)\s*\d", s):
            continue
        if len(re.findall(r"\d\s*\)", s)) >= 3:        # "1) … 2) … 3) …" TOC lines
            continue
        digits = len(re.findall(r"\d", s))
        if digits > len(letters):                       # mostly numbers (tables)
            continue
        key = re.sub(r"\s+", "", s)[:60]                # near-duplicate guard
        if key in seen:
            continue
        seen.add(key)
        sents.append(s)
    return sents


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[w] * b[w] for w in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _tfidf_vectors(sentences: list[str]) -> list[dict[str, float]]:
    n = len(sentences)
    toks = [_tokens(s) for s in sentences]
    df: dict[str, int] = {}
    for t in toks:
        for w in set(t):
            df[w] = df.get(w, 0) + 1
    vectors: list[dict[str, float]] = []
    for t in toks:
        tf: dict[str, int] = {}
        for w in t:
            tf[w] = tf.get(w, 0) + 1
        vec = {w: (c / len(t)) * math.log((n + 1) / (df[w])) for w, c in tf.items()} if t else {}
        vectors.append(vec)
    return vectors


def _pagerank(sim: list[list[float]], damping: float = 0.85, iters: int = 60) -> list[float]:
    n = len(sim)
    if n == 0:
        return []
    out = [sum(row) for row in sim]
    scores = [1.0 / n] * n
    for _ in range(iters):
        new = [(1 - damping) / n] * n
        for i in range(n):
            if out[i] == 0:
                continue
            share = damping * scores[i] / out[i]
            for j in range(n):
                if sim[i][j]:
                    new[j] += share * sim[i][j]
        scores = new
    return scores


def summarize(text: str, max_sentences: int = 6, max_input_sentences: int = 400) -> list[str]:
    """Return up to ``max_sentences`` key sentences (verbatim), in reading order."""
    sentences = split_sentences(clean_text(text))
    if not sentences:
        return []
    if len(sentences) <= max_sentences:
        return sentences
    sentences = sentences[:max_input_sentences]

    vectors = _tfidf_vectors(sentences)
    n = len(sentences)
    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            s = _cosine(vectors[i], vectors[j])
            sim[i][j] = sim[j][i] = s

    scores = _pagerank(sim)
    ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)[:max_sentences]
    return [sentences[i] for i in sorted(ranked)]
