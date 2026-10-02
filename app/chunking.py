# -*- coding: utf-8 -*-
"""Две стратегии нарезки текста на чанки.

fixed     — окно фиксированного размера (в токенах) с перекрытием; граница сдвигается
            к ближайшему пробелу, чтобы не резать слово. Структуру книги не знает:
            чанк может начаться посреди предложения и захватить две главы.
structure — по структуре: главы не пересекает, внутри главы набирает целые абзацы;
            длинный абзац делит по предложениям, сверхдлинное предложение — по словам.
            Перекрытие начинается с начала предложения.

Размер в токенах оценивается как символы / chars_per_token; коэффициент калибруется
по ответам модели эмбеддингов (см. embed.calibrate).
"""
import re

from book import chapters_in, is_sentence_end, is_sentence_start

PARAMS = {
    "fixed": {"size": 400, "overlap": 60},
    "structure": {"min": 150, "max": 400, "overlap": 60},
}

VERSION = {"fixed": 1, "structure": 2}  # версия алгоритма — входит в отпечаток варианта

STRATEGIES = {
    "fixed": {"title": "Фиксированный размер",
              "about": "окно {size} токенов, перекрытие {overlap}, граница по пробелу"},
    "structure": {"title": "По структуре",
                  "about": "главы не пересекает; абзацы → предложения → слова; {min}–{max} токенов, "
                           "перекрытие ≈{overlap} с начала предложения"},
}

SENT_SPLIT_RE = re.compile(r"""(?<=[.!?])["'”’)\]]*\s+(?=["“‘'(]?[A-Z0-9—])""")


def describe(strategy):
    s = STRATEGIES[strategy]
    return {"id": strategy, "title": s["title"], "about": s["about"].format(**PARAMS[strategy]),
            "params": PARAMS[strategy]}


class Tok:
    """Оценка числа токенов по символам."""

    def __init__(self, chars_per_token):
        self.cpt = chars_per_token

    def count(self, n_chars):
        return max(1, round(n_chars / self.cpt))

    def chars(self, n_tokens):
        return int(n_tokens * self.cpt)


# ═════════════════════════════ fixed ═════════════════════════════

def _back_to_space(text, pos, lo):
    """Сдвигает позицию назад к пробелу (но не левее lo), чтобы не резать слово."""
    if pos >= len(text):
        return len(text)
    p = pos
    while p > lo and not text[p - 1].isspace():
        p -= 1
    return p if p > lo else pos


def _skip_space(text, pos):
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def chunk_fixed(book, tok):
    text = book["text"]
    size, overlap = tok.chars(PARAMS["fixed"]["size"]), tok.chars(PARAMS["fixed"]["overlap"])
    chunks, start, body_start = [], 0, 0
    while start < len(text):
        end = _back_to_space(text, min(start + size, len(text)), start + size // 2)
        end_trim = end
        while end_trim > start and text[end_trim - 1].isspace():
            end_trim -= 1
        chunks.append(_make(book, tok, "fixed", len(chunks), start, body_start, end_trim))
        if end >= len(text):
            break
        nxt = _skip_space(text, _back_to_space(text, end - overlap, start + 1))
        start, body_start = nxt, _skip_space(text, end)
    return chunks


# ═════════════════════════════ structure ═════════════════════════════

def _units(text, start, end, tok, max_chars):
    """Делит абзац [start, end) на единицы ≤ max_chars: целиком, по предложениям или по словам."""
    if end - start <= max_chars:
        return [(start, end)]
    units, s = [], start
    sentences = []
    for m in SENT_SPLIT_RE.finditer(text, start, end):
        sentences.append((s, m.start()))
        s = m.end()
    sentences.append((s, end))
    for a, b in sentences:
        if b - a <= max_chars:
            units.append((a, b))
            continue
        while a < b:  # сверхдлинное предложение — по словам
            cut = _back_to_space(text, min(a + max_chars, b), a + max_chars // 2)
            units.append((a, cut if cut < b else b))
            a = _skip_space(text, cut)
    return units


def _sentence_overlap(text, lo, hi, tok, target):
    """Начало перекрытия: последние предложения отрезка [lo, hi) общей длиной ≈ target символов.
    Возвращает позицию начала; если последнее предложение длиннее 1,5×target — режет по слову."""
    best = None
    for m in SENT_SPLIT_RE.finditer(text, lo, hi):
        if hi - m.end() <= target * 1.5:
            best = m.end()
            break
    if best is None or hi - best < target * 0.4:
        cut = _skip_space(text, _back_to_space(text, hi - target, lo))
        return cut
    return best


def chunk_structure(book, tok):
    text = book["text"]
    p = PARAMS["structure"]
    min_c, max_c, ov_c = tok.chars(p["min"]), tok.chars(p["max"]), tok.chars(p["overlap"])
    body_max = max_c - ov_c  # тело чанка + перекрытие укладываются в max
    chunks = []
    for ch in book["chapters"]:
        units = []
        for par in book["paragraphs"]:
            if par["chapter"] == ch["num"]:
                units.extend(_units(text, par["start"], par["end"], tok, body_max))
        groups, cur = [], []
        for u in units:
            grown = u[1] - cur[0][0] if cur else 0
            if cur and grown > body_max and (cur[-1][1] - cur[0][0] >= min_c or grown > max_c):
                groups.append(cur)
                cur = []
            cur.append(u)
        if cur:
            # короткий хвост главы приклеиваем к предыдущему чанку, если не выходит сильно за max
            if groups and cur[-1][1] - cur[0][0] < min_c and cur[-1][1] - groups[-1][0][0] + ov_c <= max_c * 1.15:
                groups[-1].extend(cur)
            else:
                groups.append(cur)
        prev_end = None
        for g in groups:
            body_start, end = g[0][0], g[-1][1]
            start = body_start
            if prev_end is not None:
                start = _sentence_overlap(text, max(ch["body_start"], prev_end - ov_c * 3), prev_end, tok, ov_c)
            chunks.append(_make(book, tok, "structure", len(chunks), start, body_start, end, chapter=ch))
            prev_end = end
    return chunks


# ═════════════════════════════ общее ═════════════════════════════

def _make(book, tok, strategy, seq, start, body_start, end, chapter=None):
    text = book["text"]
    nums = [chapter["num"]] if chapter else chapters_in(book["chapters"], start, end)
    labels = {c["num"]: c["label"] for c in book["chapters"]}
    section = labels[nums[0]] if len(nums) == 1 else "{} → {}".format(labels[nums[0]], labels[nums[-1]])
    chunk_text = text[start:end]
    return {
        "chunk_id": "{}-{}-{:04d}".format(strategy, book["slug"], seq),
        "strategy": strategy,
        "seq": seq,
        "source": book["source"],
        "title": book["title"],
        "author": book["author"],
        "section": section,
        "chapters": nums,
        "start": start,
        "body_start": max(start, body_start),
        "end": end,
        "chars": end - start,
        "tokens": tok.count(end - start),
        "overlap_tokens": tok.count(max(0, body_start - start)) if body_start > start else 0,
        "starts_mid_sentence": not is_sentence_start(text, start),
        "ends_mid_sentence": not is_sentence_end(text, end),
        "text": chunk_text,
    }


def embed_text(chunk, book):
    """Текст, который уходит в модель эмбеддингов. structure знает главу — добавляем её в заголовок,
    fixed главы не знает — только название книги."""
    if chunk["strategy"] == "structure":
        return "{} — {}\n\n{}".format(book["short"], chunk["section"], chunk["text"])
    return "{}\n\n{}".format(book["short"], chunk["text"])


def run(strategy, book, tok):
    return chunk_fixed(book, tok) if strategy == "fixed" else chunk_structure(book, tok)
