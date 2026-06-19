"""Free, local, extractive summariser (no paid API, no model download).

Produces a *section-by-section* summary so the whole paper can be grasped:
the PDF text is split into its numbered chapters (1. 서론, 2. …, 결론), and
within each chapter the key sentences are selected with TextRank (TF-IDF
cosine similarity + PageRank). Every sentence is copied verbatim from the
source — nothing is generated.

Tuned lightly for Korean academic text (sentence splitting on "…다.", a small
particle stripper and stopword list); English is handled too.
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

# Inline noise stripped from the running text.
_NOISE = [
    re.compile(r"https?://\S+"),
    re.compile(r"10\.\d{4,9}/\S+"),                 # DOI
    re.compile(r"[pe]?ISSN\s*[\d-]+", re.IGNORECASE),
]
# A normalised line (page numbers removed) repeated this many times is a
# running header/footer and is dropped.
_REPEAT_THRESHOLD = 3


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


def _clean_lines(text: str) -> list[str]:
    """Return cleaned text lines: references cut, repeated headers/footers and
    inline noise removed, blank lines dropped — line structure preserved so
    section headings remain detectable."""
    # Some PDFs interleave NUL/control bytes between glyphs (e.g. "1.\x00 서\x00 론");
    # normalise them to spaces, but keep newlines so headings stay on their line.
    text = re.sub(r"[\x00-\x09\x0b\x0c\x0e-\x1f]", " ", text)

    for marker in ("참고문헌", "References", "REFERENCES", "참 고 문 헌"):
        idx = text.find(marker)
        if idx > len(text) * 0.4:
            text = text[:idx]
            break

    lines = [ln.strip() for ln in text.splitlines()]
    norm = lambda ln: re.sub(r"\d+", "", ln)
    freq = Counter(norm(ln) for ln in lines if len(ln) > 8)

    out: list[str] = []
    for ln in lines:
        if not ln or freq[norm(ln)] >= _REPEAT_THRESHOLD:
            continue
        for pat in _NOISE:
            ln = pat.sub(" ", ln)
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            out.append(ln)
    return out


def clean_text(text: str) -> str:
    """Flat cleaned text (one string) — used when no sections are detected."""
    return " ".join(_clean_lines(text))


# A real top-level chapter heading: "1. 서론", "5. 결론" — a short line with a
# single number and a Korean title (excludes inline enumerations like
# "1. 목적, 2. 정의" and table rows).
_HEADING = re.compile(r"^(\d{1,2})\.\s+(.{1,36})$")


def _is_heading(line: str) -> tuple[int, str] | None:
    m = _HEADING.match(line)
    if not m:
        return None
    title = m.group(2)
    if "," in line:                                  # "1. 목적, 2. 정의" enumeration
        return None
    if len(re.findall(r"\d+[.)]", line)) > 1:        # more than one numbered item
        return None
    if not re.search(r"[가-힣]", title):             # title must have Korean
        return None
    return int(m.group(1)), line


def split_into_sections(lines: list[str]) -> list[tuple[int, str, str]]:
    """Split cleaned lines into (number, heading, body) per top-level chapter.

    Chapter headings appear twice (table of contents + body); for each chapter
    number we keep the occurrence with the most prose (the real body).
    """
    marks = [(i, *h) for i, ln in enumerate(lines) if (h := _is_heading(ln))]
    if len(marks) < 2:
        return []

    segments: list[tuple[int, str, str]] = []
    for idx, (li, num, heading) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        body = " ".join(lines[li + 1: end])
        segments.append((num, heading, body))

    best: dict[int, tuple[int, str, str]] = {}
    for num, heading, body in segments:
        if num not in best or len(body) > len(best[num][2]):
            best[num] = (num, heading, body)
    return [best[k] for k in sorted(best)]


def split_sentences(text: str) -> list[str]:
    """Split Korean/English text into sentences, dropping non-prose fragments."""
    rough = re.split(r"(?<=[.!?])\s+", text)
    sents: list[str] = []
    seen: set[str] = set()
    for s in rough:
        s = s.strip()
        s = re.sub(r"^\[[^\]]{0,15}\]\s*", "", s)         # leading "[가설 2]" label
        s = re.sub(r"^[\s\d.)\]\-~·•%]+", "", s).strip()  # leading numbering / stat run
        if len(s) < 25:
            continue
        letters = re.findall(r"[가-힣a-zA-Z]", s)
        if len(letters) < 15:
            continue
        # Drop English-dominant lines (e.g. the trailing English abstract /
        # "Keywords:") in these Korean papers.
        ko = len(re.findall(r"[가-힣]", s))
        en = len(re.findall(r"[A-Za-z]", s))
        if ko < 3 and en > 15:
            continue
        if re.match(r"^[<\[]?\s*(표|그림|Table|Figure)\s*\d", s):
            continue
        if len(re.findall(r"\d\s*\)", s)) >= 3:
            continue
        if len(re.findall(r"\d", s)) > len(letters):
            continue
        key = re.sub(r"\s+", "", s)[:60]
        if key in seen:
            continue
        seen.add(key)
        sents.append(s)
    return sents


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
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
        vec = {w: (c / len(t)) * math.log((n + 1) / df[w]) for w, c in tf.items()} if t else {}
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


def _textrank_pick(sentences: list[str], k: int, max_input: int = 250) -> list[str]:
    """Pick the k highest-ranked sentences (verbatim), returned in reading order."""
    if len(sentences) <= k:
        return sentences
    sentences = sentences[:max_input]
    vectors = _tfidf_vectors(sentences)
    n = len(sentences)
    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            s = _cosine(vectors[i], vectors[j])
            sim[i][j] = sim[j][i] = s
    scores = _pagerank(sim)
    ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)[:k]
    return [sentences[i] for i in sorted(ranked)]


def section_bodies(text: str) -> list[tuple[str, str]]:
    """Return [(heading, cleaned prose)] per chapter — input for a generative LLM.

    Same chapter detection as :func:`summarize_by_section`, but instead of
    picking sentences it returns each chapter's de-noised prose so an external
    summariser (e.g. a local Ollama model) can rewrite it.
    """
    out: list[tuple[str, str]] = []
    for _num, heading, body in split_into_sections(_clean_lines(text)):
        prose = " ".join(split_sentences(body))
        if prose:
            out.append((heading, prose))
    return out


def summarize(text: str, max_sentences: int = 6) -> list[str]:
    """Flat extractive summary (fallback when chapters can't be detected)."""
    return _textrank_pick(split_sentences(clean_text(text)), max_sentences)


def summarize_by_section(
    text: str, per_section: int = 5, min_per_section: int = 2,
) -> list[dict]:
    """Section-by-section summary: [{"heading", "sentences"}, ...].

    Returns [] if the document has no detectable chapter structure (caller then
    falls back to :func:`summarize`).
    """
    sections = split_into_sections(_clean_lines(text))
    if not sections:
        return []

    result: list[dict] = []
    for _num, heading, body in sections:
        sents = split_sentences(body)
        if not sents:
            continue
        # Scale the number of kept sentences to the chapter's length.
        k = min(per_section, max(min_per_section, len(sents) // 5))
        result.append({"heading": heading, "sentences": _textrank_pick(sents, k)})
    return result
