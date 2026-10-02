# -*- coding: utf-8 -*-
"""Загрузка и разбор книги Project Gutenberg.

Скачивает текст один раз с зеркала (data/sources/pgN.txt), вырезает служебную
шапку, лицензию и оглавление, делит на главы и абзацы. Возвращает «очищенный»
текст и разметку с позициями (в символах очищенного текста) — на эти позиции
ссылаются метаданные чанков.
"""
import hashlib
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "data" / "sources"
MIRROR = "https://gutenberg.pglaf.org"

BOOK = {
    "id": 2701,
    "slug": "moby",
    "title": "Moby-Dick; or, The Whale",
    "short": "Moby-Dick",
    "author": "Herman Melville",
    "source": "gutenberg:2701",
    "url": "https://www.gutenberg.org/ebooks/2701",
}

CHAPTER_RE = re.compile(r"^CHAPTER (\d+)\. (.+?)\s*$", re.M)
SENTENCE_END_RE = re.compile(r"""[.!?]["'”’)\]]*$""")


def source_path(book_id):
    return SOURCES_DIR / "pg{}.txt".format(book_id)


def fetch(book_id, log=print):
    """Скачивает книгу с зеркала Gutenberg, если её ещё нет на диске."""
    path = source_path(book_id)
    if path.is_file():
        return path
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    d = str(book_id)
    url = "{}/{}/{}/{}/{}/{}-0.txt".format(MIRROR, d[0], d[1], d[2], d, d)
    log("fetch", "скачиваю {} …".format(url))
    req = urllib.request.Request(url, headers={"User-Agent": "doc-index-day21"})
    with urllib.request.urlopen(req, timeout=60) as r:
        path.write_bytes(r.read())
    log("fetch", "сохранено {} ({} КБ)".format(path.name, path.stat().st_size // 1024))
    return path


def _unwrap(par):
    """Склеивает строки абзаца (Gutenberg переносит строки на ~70 символах)."""
    return re.sub(r"\s*\n\s*", " ", par.strip())


def parse(raw, max_chapters=None):
    """Очищает текст и делит на главы и абзацы.

    Возвращает dict:
      text      — очищенный текст: «CHAPTER N. Title» + абзацы через пустую строку
      chapters  — [{num, title, label, start, body_start, end}]
      paragraphs— [{chapter, start, end}]
    """
    raw = raw.replace("\r\n", "\n").lstrip("﻿")
    start = raw.find("*** START OF")
    start = raw.find("\n", start) + 1 if start != -1 else 0
    end = raw.find("*** END OF")
    body = raw[start:end if end != -1 else len(raw)]

    # оглавление тоже содержит «CHAPTER 1. Loomings.» — тело начинается со ВТОРОГО вхождения
    heads = list(CHAPTER_RE.finditer(body))
    body_start = [h for h in heads if h.group(1) == "1"][-1].start()
    body_heads = [h for h in heads if h.start() >= body_start]
    seen, chapter_heads = set(), []
    for h in body_heads:
        n = int(h.group(1))
        if n in seen:
            continue
        seen.add(n)
        chapter_heads.append(h)
    if max_chapters:
        chapter_heads = chapter_heads[:max_chapters + 1]  # +1 — чтобы знать конец последней главы

    epilogue = body.find("\nEpilogue\n")
    parts = []
    for i, h in enumerate(chapter_heads):
        if max_chapters and i == max_chapters:
            break
        nxt = chapter_heads[i + 1].start() if i + 1 < len(chapter_heads) else (epilogue if epilogue != -1 else len(body))
        parts.append((int(h.group(1)), h.group(2).strip(), body[h.end():nxt]))

    text_parts, chapters, paragraphs, pos = [], [], [], 0
    for num, title, chunk in parts:
        heading = "CHAPTER {}. {}".format(num, title)
        ch = {"num": num, "title": title.rstrip("."), "label": "Chapter {}. {}".format(num, title.rstrip(".")),
              "start": pos}
        text_parts.append(heading + "\n\n")
        pos += len(heading) + 2
        ch["body_start"] = pos
        pars = [_unwrap(p) for p in re.split(r"\n\s*\n", chunk) if p.strip()]
        for p in pars:
            paragraphs.append({"chapter": num, "start": pos, "end": pos + len(p)})
            text_parts.append(p + "\n\n")
            pos += len(p) + 2
        ch["end"] = pos
        chapters.append(ch)
    text = "".join(text_parts)
    return {"text": text, "chapters": chapters, "paragraphs": paragraphs,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def load(max_chapters=None, log=print):
    path = fetch(BOOK["id"], log)
    parsed = parse(path.read_text(encoding="utf-8"), max_chapters)
    return dict(BOOK, **parsed)


def chapters_in(chapters, start, end):
    """Номера глав, которые пересекает диапазон [start, end)."""
    return [c["num"] for c in chapters if start < c["end"] and end > c["start"]]


def is_sentence_end(text, pos):
    """True, если в позиции pos (конец фрагмента) заканчивается предложение."""
    tail = text[max(0, pos - 6):pos].rstrip()
    return bool(tail) and bool(SENTENCE_END_RE.search(tail))


def is_sentence_start(text, pos):
    """True, если с позиции pos начинается предложение (начало абзаца или после конца предложения)."""
    if pos == 0:
        return True
    before = text[max(0, pos - 8):pos]
    if "\n" in before[-2:]:
        return True
    return is_sentence_end(text, pos)
