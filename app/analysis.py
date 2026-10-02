# -*- coding: utf-8 -*-
"""Поиск по индексу и сравнение стратегий нарезки.

Поиск — полный перебор: вектор вопроса · векторы всех чанков стратегии
(векторы нормализованы, поэтому скалярное произведение = косинус).
Для нескольких сотен чанков это доли секунды и без numpy.
"""
import json
import statistics
import time
from pathlib import Path

import chunking
import embed
import store

EVAL_FILE = Path(__file__).resolve().parent.parent / "eval" / "questions.json"
STRATEGIES = ["fixed", "structure"]
RANGE = (150, 450)       # «рекомендуемый» размер чанка в токенах
HIST_STEP = 50
_qcache = {}             # (model, вопрос) -> вектор


def query_vector(q):
    key = (embed.MODEL, q)
    if key not in _qcache:
        _qcache[key] = embed.embed_one(q)
    return _qcache[key]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def rank(q, strategy, k=10):
    qv = query_vector(q)
    scored = [(_dot(qv, c["vector"]), c) for c in store.vectors(strategy)]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k]


def _public(c, score=None):
    d = {k: v for k, v in c.items() if k != "vector"}
    if score is not None:
        d["score"] = round(score, 4)
        d["similarity"] = round((1 + score) / 2, 4)  # косинус −1..1 → 0..1
    return d


def search(q, k=5, strategies=None):
    t0 = time.time()
    query_vector(q)
    t_embed = time.time() - t0
    out = {}
    for s in strategies or STRATEGIES:
        out[s] = [_public(c, sc) for sc, c in rank(q, s, k)]
    return {"query": q, "results": out, "embed_ms": round(t_embed * 1000),
            "total_ms": round((time.time() - t0) * 1000)}


# ═════════════════════════════ сравнение стратегий ═════════════════════════════

def load_questions():
    doc = store.get_document()
    questions = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    if not doc:
        return questions
    text = doc["text"]
    spans = {c["num"]: text[c["start"]:c["end"]].lower() for c in doc["chapters"]}
    for q in questions:
        missing = [e for e in q["evidence"]
                   if not any(e.lower() in spans.get(n, "") for n in q["chapters"])]
        q["verified"] = not missing
        q["missing"] = missing
    return questions


def structure_metrics(strategy):
    chunks = store.list_chunks(strategy)
    if not chunks:
        return None
    t = [c["tokens"] for c in chunks]
    n = len(chunks)
    total = sum(t)
    overlap = sum(c["overlap_tokens"] for c in chunks)
    hist = {}
    for x in t:
        b = (x // HIST_STEP) * HIST_STEP
        hist[b] = hist.get(b, 0) + 1
    v = store.get_variant(strategy)
    return {
        "chunks": n,
        "tokens_total": total,
        "tokens_min": min(t), "tokens_max": max(t),
        "tokens_avg": round(total / n), "tokens_median": round(statistics.median(t)),
        "tokens_stdev": round(statistics.pstdev(t)),
        "in_range": round(sum(RANGE[0] <= x <= RANGE[1] for x in t) / n, 3),
        "overlap_tokens": overlap, "overlap_share": round(overlap / total, 3),
        "starts_mid_sentence": round(sum(c["starts_mid_sentence"] for c in chunks) / n, 3),
        "ends_mid_sentence": round(sum(c["ends_mid_sentence"] for c in chunks) / n, 3),
        "cross_chapter": round(sum(len(c["chapters"]) > 1 for c in chunks) / n, 3),
        "chunks_per_chapter": round(n / len({x for c in chunks for x in c["chapters"]}), 1),
        "histogram": [{"from": b, "to": b + HIST_STEP, "count": hist[b]} for b in sorted(hist)],
        "seconds": v["seconds"] if v else None,
        "model_tokens": v["model_tokens"] if v else None,
        "dim": v["dim"] if v else None,
    }


def retrieval_metrics(strategy, questions, k=10):
    rows, ranks = [], []
    for q in questions:
        top = rank(q["q"], strategy, k)
        place = next((i + 1 for i, (_, c) in enumerate(top) if set(c["chapters"]) & set(q["chapters"])), None)
        best = top[0][1] if top else None
        rows.append({"id": q["id"], "rank": place, "top_section": best and best["section"],
                     "top_chunk": best and best["chunk_id"], "top_score": top and round(top[0][0], 4)})
        if q.get("verified", True):
            ranks.append((q["lang"], place))

    def hit(n, lang=None):
        sel = [p for l, p in ranks if lang in (None, l)]
        return round(sum(1 for p in sel if p and p <= n) / len(sel), 3) if sel else None

    mrr = round(sum(1 / p for _, p in ranks if p) / len(ranks), 3) if ranks else None
    return {"hit1": hit(1), "hit3": hit(3), "hit5": hit(5), "mrr10": mrr,
            "hit1_ru": hit(1, "ru"), "hit1_en": hit(1, "en"), "questions": len(ranks), "rows": rows}


def compare():
    t0 = time.time()
    questions = load_questions()
    verified = [q for q in questions if q.get("verified", True)]
    out = {"strategies": {}, "questions": [], "range": RANGE}
    for s in STRATEGIES:
        sm = structure_metrics(s)
        if not sm:
            continue
        rm = retrieval_metrics(s, verified)
        out["strategies"][s] = dict(chunking.describe(s), structure=sm, retrieval={k: v for k, v in rm.items() if k != "rows"})
        for row in rm["rows"]:
            row["strategy"] = s
        out.setdefault("_rows", []).extend(rm["rows"])
    rows = out.pop("_rows", [])
    for q in questions:
        entry = {k: q[k] for k in ("id", "q", "lang", "chapters", "evidence")}
        entry.update(verified=q.get("verified", True), missing=q.get("missing", []))
        entry["by"] = {r["strategy"]: r for r in rows if r["id"] == q["id"]}
        out["questions"].append(entry)
    out["unverified"] = [q["id"] for q in questions if not q.get("verified", True)]
    out["conclusion"] = conclusion(out["strategies"])
    out["ms"] = round((time.time() - t0) * 1000)
    return out


def _pct(x):
    return "{}%".format(round(x * 100))


def conclusion(st):
    if len(st) < 2:
        return []
    f, s = st["fixed"], st["structure"]
    fs, ss, fr, sr = f["structure"], s["structure"], f["retrieval"], s["retrieval"]
    lines = []
    lines.append("Целостность: fixed обрывает посреди предложения {} чанков и захватывает две главы в {}; "
                 "structure — {} и {} соответственно.".format(
                     _pct(fs["ends_mid_sentence"]), _pct(fs["cross_chapter"]),
                     _pct(ss["ends_mid_sentence"]), _pct(ss["cross_chapter"])))
    lines.append("Размеры: fixed почти одинаковые (σ = {} токенов, медиана {}), structure гуляют сильнее "
                 "(σ = {}, от {} до {}) — подстраиваются под абзацы.".format(
                     fs["tokens_stdev"], fs["tokens_median"], ss["tokens_stdev"], ss["tokens_min"], ss["tokens_max"]))
    better = "structure" if (sr["mrr10"] or 0) > (fr["mrr10"] or 0) else "fixed" if (fr["mrr10"] or 0) > (sr["mrr10"] or 0) else None
    lines.append("Поиск по {} контрольным вопросам: hit@1 {} vs {}, hit@5 {} vs {}, MRR@10 {} vs {} (fixed vs structure){}.".format(
        fr["questions"], _pct(fr["hit1"]), _pct(sr["hit1"]), _pct(fr["hit5"]), _pct(sr["hit5"]),
        fr["mrr10"], sr["mrr10"], "" if not better else " — лучше " + better))
    lines.append("Русские вопросы к английскому тексту: hit@1 {} (fixed) и {} (structure); английские: {} и {}.".format(
        _pct(fr["hit1_ru"] or 0), _pct(sr["hit1_ru"] or 0), _pct(fr["hit1_en"] or 0), _pct(sr["hit1_en"] or 0)))
    return lines
